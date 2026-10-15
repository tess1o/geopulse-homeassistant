"""Binary sensors for GeoPulse."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GeoPulseConfigEntry, GeoPulseRuntimeData
from .coordinator import ServerCoordinator, StatusCoordinator
from .entity import GeoPulseAccountEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeoPulseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GeoPulse binary sensors."""
    runtime = entry.runtime_data
    entities: list[BinarySensorEntity] = [ServerOnlineSensor(runtime.server, runtime)]
    if runtime.status:
        entities.append(OnTripSensor(runtime.status, runtime))
        entities.append(TrackingSensor(runtime.status, runtime))
    async_add_entities(entities)


class OnTripSensor(GeoPulseAccountEntity[StatusCoordinator], BinarySensorEntity):
    """On while GeoPulse reports a trip."""

    def __init__(
        self, coordinator: StatusCoordinator, runtime: GeoPulseRuntimeData
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, runtime, "on_trip")

    @property
    def is_on(self) -> bool:
        """Return True while on a trip."""
        return self.coordinator.data.state == "TRIP"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return trip details."""
        trip = self.coordinator.data.trip if self.is_on else None
        return {
            "distance_m": trip.distance_meters if trip else None,
            "movement_type": trip.movement_type if trip else None,
        }


class TrackingSensor(GeoPulseAccountEntity[StatusCoordinator], BinarySensorEntity):
    """Off while GeoPulse reports a data gap."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self, coordinator: StatusCoordinator, runtime: GeoPulseRuntimeData
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, runtime, "tracking")

    @property
    def is_on(self) -> bool:
        """Return False during a data gap."""
        return self.coordinator.data.state != "DATA_GAP"


class ServerOnlineSensor(GeoPulseAccountEntity[ServerCoordinator], BinarySensorEntity):
    """On while the GeoPulse server reports UP."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: ServerCoordinator, runtime: GeoPulseRuntimeData
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, runtime, "server")

    @property
    def is_on(self) -> bool:
        """Return True if the server and database are up."""
        health = self.coordinator.data.health
        return health is not None and health.is_up

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw statuses."""
        health = self.coordinator.data.health
        return {
            "status": health.status if health else None,
            "database": health.database if health else None,
        }
