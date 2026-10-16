"""The account's own location and friends sharing theirs, as device trackers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GeoPulseConfigEntry, GeoPulseRuntimeData
from .const import DOMAIN
from .coordinator import FriendsCoordinator, StatusCoordinator
from .entity import GeoPulseAccountEntity, friend_device_info
from .models import Friend, LastPoint

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeoPulseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the account's tracker and friend trackers."""
    runtime = entry.runtime_data
    if runtime.status:
        async_add_entities([AccountTracker(runtime.status, runtime)])
    coordinator = runtime.friends
    if coordinator is None:
        return

    known: set[str] = set()

    @callback
    def _sync() -> None:
        friends = coordinator.data or {}
        new = [
            friend_id
            for friend_id, friend in friends.items()
            if friend_id not in known and friend.shares_live_location
        ]
        if new:
            known.update(new)
            async_add_entities(
                FriendTracker(coordinator, runtime, friend_id) for friend_id in new
            )
        _remove_unfriended(hass, entry, runtime, set(friends))

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


@callback
def _remove_unfriended(
    hass: HomeAssistant,
    entry: GeoPulseConfigEntry,
    runtime: GeoPulseRuntimeData,
    current: set[str],
) -> None:
    """Delete devices of friends who are no longer in the friends list."""
    dev_reg = dr.async_get(hass)
    prefix = f"{runtime.user_id}_friend_"
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and identifier.startswith(prefix):
                if identifier.removeprefix(prefix) not in current:
                    dev_reg.async_update_device(
                        device.id, remove_config_entry_id=entry.entry_id
                    )
                break


class AccountTracker(GeoPulseAccountEntity[StatusCoordinator], TrackerEntity):
    """Where GeoPulse last saw the account owner, from any of their location sources.

    It can feed a person entity and Home Assistant zones when locations reach
    GeoPulse from another app, such as OwnTracks. The forwarder never sends it back.
    """

    # Trackers are diagnostic by default, but this is the account's main location.
    _attr_entity_category = None

    def __init__(
        self, coordinator: StatusCoordinator, runtime: GeoPulseRuntimeData
    ) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator, runtime, "location")

    @property
    def _point(self) -> LastPoint | None:
        point = self.coordinator.data.last_point
        if point is None or point.latitude is None or point.longitude is None:
            return None
        return point

    @property
    def available(self) -> bool:
        """Available once GeoPulse has a point with a position."""
        return super().available and self._point is not None

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return the latitude."""
        point = self._point
        return point.latitude if point else None

    @property
    def longitude(self) -> float | None:
        """Return the longitude."""
        point = self._point
        return point.longitude if point else None

    @property
    def location_accuracy(self) -> float:
        """Return the accuracy in meters (0 when unknown)."""
        point = self._point
        return (point.accuracy or 0) if point else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when and from where GeoPulse got the point."""
        point = self._point
        if point is None:
            return {}
        return {
            "last_seen": point.timestamp.isoformat() if point.timestamp else None,
            "received_at": point.received_at.isoformat() if point.received_at else None,
            # Not `source_type`: trackers already use that for "gps".
            "location_source": point.source_type,
        }


class FriendTracker(CoordinatorEntity[FriendsCoordinator], TrackerEntity):
    """A friend who shares their live location."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "friend"
    # Trackers are diagnostic by default, but a friend's location is the main thing
    # on their device, next to Activity and Place.
    _attr_entity_category = None

    def __init__(
        self,
        coordinator: FriendsCoordinator,
        runtime: GeoPulseRuntimeData,
        friend_id: str,
    ) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator)
        self._friend_id = friend_id
        friend = coordinator.data[friend_id]
        self._attr_unique_id = f"{runtime.user_id}_friend_{friend_id}"
        self._attr_device_info = friend_device_info(
            runtime, friend_id, friend.full_name
        )

    @property
    def _friend(self) -> Friend | None:
        return (self.coordinator.data or {}).get(self._friend_id)

    @property
    def available(self) -> bool:
        """Unavailable when the friend stops sharing or is removed."""
        friend = self._friend
        return super().available and friend is not None and friend.has_location

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return the latitude."""
        friend = self._friend
        return friend.latitude if friend else None

    @property
    def longitude(self) -> float | None:
        """Return the longitude."""
        friend = self._friend
        return friend.longitude if friend else None

    @property
    def location_accuracy(self) -> float:
        """Return the accuracy in meters (0 when unknown)."""
        friend = self._friend
        return (friend.accuracy or 0) if friend else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the friend was seen and what they are doing."""
        friend = self._friend
        if friend is None:
            return {}
        return {
            "last_seen": friend.last_seen.isoformat() if friend.last_seen else None,
            "activity": friend.activity.lower() if friend.activity else None,
            "activity_duration_s": friend.activity_duration_seconds,
            "last_location": friend.last_location,
        }
