"""Sensors for GeoPulse."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GeoPulseConfigEntry, GeoPulseRuntimeData
from .const import NAME, SIGNAL_FORWARDER_UPDATED
from .coordinator import (
    FriendsCoordinator,
    ServerCoordinator,
    ServerData,
    StatusCoordinator,
)
from .entity import (
    GeoPulseAccountEntity,
    GeoPulsePlainEntity,
    friend_device_info,
    via_account_device,
)
from .helpers import tracker_identifier
from .models import CurrentStatus, Friend
from .sender import LocationForwarder

PARALLEL_UPDATES = 0

ACTIVITY_OPTIONS = ["stay", "trip", "data_gap", "unknown", "no_data"]
FRIEND_ACTIVITY_OPTIONS = ["stay", "trip"]
INGEST_OPTIONS = ["ok", "rejected", "auth_failed", "no_data", "disabled"]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True, kw_only=True)
class StatusSensorDescription(SensorEntityDescription):
    """Describes a sensor fed by `/users/me/status`."""

    value_fn: Callable[[CurrentStatus], StateType | datetime]
    attrs_fn: Callable[[CurrentStatus], dict[str, Any]] | None = None
    available_fn: Callable[[CurrentStatus], bool] | None = None


def _activity(status: CurrentStatus) -> str:
    state = status.state.lower()
    return state if state in ACTIVITY_OPTIONS else "unknown"


def _place_attrs(status: CurrentStatus) -> dict[str, Any]:
    stay = status.stay if status.state == "STAY" else None
    if stay is None:
        return {}
    return {
        "favorite_id": stay.favorite_id,
        "location_source": stay.location_source,
        "city": stay.city,
        "country": stay.country,
        "latitude": stay.latitude,
        "longitude": stay.longitude,
    }


def _last_point_attrs(status: CurrentStatus) -> dict[str, Any]:
    point = status.last_point
    if point is None:
        return {}
    return {
        "received_at": _iso(point.received_at),
        "source_type": point.source_type,
        "accuracy": point.accuracy,
        "battery": point.battery,
    }


STATUS_SENSORS: tuple[StatusSensorDescription, ...] = (
    StatusSensorDescription(
        key="activity",
        device_class=SensorDeviceClass.ENUM,
        options=ACTIVITY_OPTIONS,
        value_fn=_activity,
        attrs_fn=lambda s: {"since": _iso(s.current_since)},
    ),
    StatusSensorDescription(
        key="place",
        value_fn=lambda s: s.stay.place_name if s.state == "STAY" and s.stay else None,
        attrs_fn=_place_attrs,
    ),
    StatusSensorDescription(
        key="activity_since",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.current_since,
    ),
    StatusSensorDescription(
        key="activity_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda s: s.current_duration,
    ),
    StatusSensorDescription(
        key="trip_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        value_fn=lambda s: s.trip.distance_meters if s.trip else None,
        available_fn=lambda s: s.state == "TRIP" and s.trip is not None,
    ),
    StatusSensorDescription(
        key="today_distance",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        value_fn=lambda s: s.today.distance_meters if s.today else None,
    ),
    StatusSensorDescription(
        key="today_trips",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.today.trip_count if s.today else None,
    ),
    StatusSensorDescription(
        key="today_stays",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.today.stay_count if s.today else None,
    ),
    StatusSensorDescription(
        key="today_places",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.today.unique_places if s.today else None,
    ),
    StatusSensorDescription(
        key="today_moving_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda s: s.today.moving_seconds if s.today else None,
    ),
    StatusSensorDescription(
        key="last_point",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.last_point.timestamp if s.last_point else None,
        attrs_fn=_last_point_attrs,
    ),
)


@dataclass(frozen=True, kw_only=True)
class ServerSensorDescription(SensorEntityDescription):
    """Describes a sensor fed by the server coordinator's ingest status."""

    value_fn: Callable[[ServerData], StateType | datetime]
    attrs_fn: Callable[[ServerData], dict[str, Any]] | None = None


def _ingest_attrs(data: ServerData) -> dict[str, Any]:
    if data.source is None:
        return {}
    status = data.source.ingest_status
    return {
        "last_saved_at": _iso(status.last_saved_at),
        "last_saved_point_timestamp": _iso(status.last_saved_point_timestamp),
        "last_rejected_at": _iso(status.last_rejected_at),
        "last_rejection_code": status.last_rejection_code,
        "last_rejection_params": status.last_rejection_params,
        "last_auth_failure_at": _iso(status.last_auth_failure_at),
        "last_auth_failure_code": status.last_auth_failure_code,
    }


SERVER_SENSORS: tuple[ServerSensorDescription, ...] = (
    ServerSensorDescription(
        key="ingest_status",
        device_class=SensorDeviceClass.ENUM,
        options=INGEST_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.source.state if d.source else None,
        attrs_fn=_ingest_attrs,
    ),
    ServerSensorDescription(
        key="last_data_received",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: (
            d.source.ingest_status.last_received_at if d.source else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeoPulseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GeoPulse sensors."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        ServerSensor(runtime.server, runtime, description)
        for description in SERVER_SENSORS
    ]
    if runtime.status:
        entities.extend(
            StatusSensor(runtime.status, runtime, description)
            for description in STATUS_SENSORS
        )
    if runtime.forwarder:
        entities.extend(
            LastSentSensor(runtime, runtime.forwarder, entity_id)
            for entity_id in runtime.forwarder.entity_ids
        )
    async_add_entities(entities)

    if runtime.friends:
        _setup_friend_sensors(entry, runtime, runtime.friends, async_add_entities)


class StatusSensor(GeoPulseAccountEntity[StatusCoordinator], SensorEntity):
    """A sensor from the current status."""

    entity_description: StatusSensorDescription

    def __init__(
        self,
        coordinator: StatusCoordinator,
        runtime: GeoPulseRuntimeData,
        description: StatusSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, runtime, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return if the value is available."""
        if not super().available:
            return False
        fn = self.entity_description.available_fn
        return fn is None or fn(self.coordinator.data)

    @property
    def native_value(self) -> StateType | datetime:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None


class ServerSensor(GeoPulseAccountEntity[ServerCoordinator], SensorEntity):
    """A sensor from the token's GPS source."""

    entity_description: ServerSensorDescription

    def __init__(
        self,
        coordinator: ServerCoordinator,
        runtime: GeoPulseRuntimeData,
        description: ServerSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, runtime, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Available once the connected source is known."""
        return super().available and self.coordinator.data.source is not None

    @property
    def native_value(self) -> StateType | datetime:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None


class LastSentSensor(GeoPulsePlainEntity, SensorEntity):
    """When a point of a forwarded tracker was last delivered."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_sent"

    def __init__(
        self,
        runtime: GeoPulseRuntimeData,
        forwarder: LocationForwarder,
        entity_id: str,
    ) -> None:
        """Initialize the sensor."""
        self._forwarder = forwarder
        self._source = entity_id
        self._entry_id = forwarder.entry.entry_id
        self._attr_unique_id = f"{runtime.user_id}_tracker_{entity_id}_last_sent"
        state = forwarder.hass.states.get(entity_id)
        name = state.name if state else entity_id
        self._attr_device_info = DeviceInfo(
            identifiers={tracker_identifier(runtime.user_id, entity_id)},
            name=name,
            manufacturer=NAME,
            model="Forwarded tracker",
            **via_account_device(runtime),
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to forwarder updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_FORWARDER_UPDATED.format(self._entry_id),
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> datetime | None:
        """Return when a point was last delivered."""
        return self._forwarder.last_sent.get(self._source)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return queue details."""
        return {
            "source_entity_id": self._source,
            "queue_length": self._forwarder.queue_length(self._source),
            "last_error": self._forwarder.last_error,
        }


@callback
def _setup_friend_sensors(
    entry: GeoPulseConfigEntry,
    runtime: GeoPulseRuntimeData,
    coordinator: FriendsCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add activity sensors for sharing friends, and battery when it is known."""
    known: set[str] = set()
    known_battery: set[str] = set()

    @callback
    def _add_new() -> None:
        entities: list[SensorEntity] = []
        for friend_id, friend in (coordinator.data or {}).items():
            if not friend.shares_live_location:
                continue
            if friend_id not in known:
                known.add(friend_id)
                entities.append(FriendActivitySensor(coordinator, runtime, friend_id))
                entities.append(
                    FriendActivityDurationSensor(coordinator, runtime, friend_id)
                )
                entities.append(FriendPlaceSensor(coordinator, runtime, friend_id))
            if friend_id not in known_battery and friend.battery is not None:
                known_battery.add(friend_id)
                entities.append(FriendBatterySensor(coordinator, runtime, friend_id))
        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class FriendSensor(CoordinatorEntity[FriendsCoordinator], SensorEntity):
    """A sensor on a friend's device, available while they share their location."""

    _attr_has_entity_name = True
    key: str

    def __init__(
        self,
        coordinator: FriendsCoordinator,
        runtime: GeoPulseRuntimeData,
        friend_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._friend_id = friend_id
        friend = coordinator.data[friend_id]
        self._attr_unique_id = f"{runtime.user_id}_friend_{friend_id}_{self.key}"
        self._attr_device_info = friend_device_info(
            runtime, friend_id, friend.full_name
        )

    @property
    def _friend(self) -> Friend | None:
        return (self.coordinator.data or {}).get(self._friend_id)

    @property
    def available(self) -> bool:
        """Available while the friend shares their location."""
        friend = self._friend
        return super().available and friend is not None and friend.shares_live_location


class FriendActivitySensor(FriendSensor):
    """What a friend is doing: staying somewhere or on a trip."""

    key = "activity"
    _attr_translation_key = "friend_activity"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = FRIEND_ACTIVITY_OPTIONS

    @property
    def native_value(self) -> str | None:
        """Return `stay` or `trip`, or None when GeoPulse doesn't know."""
        friend = self._friend
        activity = (friend.activity or "").lower() if friend else ""
        return activity if activity in FRIEND_ACTIVITY_OPTIONS else None


class FriendActivityDurationSensor(FriendSensor):
    """How long a friend has been in the current stay or trip."""

    key = "activity_duration"
    _attr_translation_key = "friend_activity_duration"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> int | None:
        """Return the duration in seconds."""
        friend = self._friend
        return friend.activity_duration_seconds if friend else None


class FriendPlaceSensor(FriendSensor):
    """Where a friend is staying; None while they are on a trip."""

    key = "place"
    _attr_translation_key = "friend_place"

    @property
    def native_value(self) -> str | None:
        """Return the place name GeoPulse gives for the friend's last point."""
        friend = self._friend
        if friend is None or (friend.activity or "").upper() != "STAY":
            return None
        return friend.last_location


class FriendBatterySensor(FriendSensor):
    """Battery level of a friend's tracking device."""

    key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        """Available while the friend shares a battery level."""
        friend = self._friend
        return super().available and friend is not None and friend.battery is not None

    @property
    def native_value(self) -> int | None:
        """Return the battery level."""
        friend = self._friend
        if friend is None or friend.battery is None:
            return None
        return round(friend.battery)
