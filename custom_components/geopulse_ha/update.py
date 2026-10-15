"""GeoPulse server update entity (informational, no install action)."""

from __future__ import annotations

from homeassistant.components.update import UpdateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GeoPulseConfigEntry, GeoPulseRuntimeData
from .const import NAME
from .coordinator import ServerCoordinator
from .entity import GeoPulseAccountEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeoPulseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the update entity."""
    runtime = entry.runtime_data
    async_add_entities([GeoPulseUpdate(runtime.server, runtime)])


class GeoPulseUpdate(GeoPulseAccountEntity[ServerCoordinator], UpdateEntity):
    """Shows whether a newer GeoPulse server release exists."""

    _attr_title = NAME

    def __init__(
        self, coordinator: ServerCoordinator, runtime: GeoPulseRuntimeData
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, runtime, "geopulse")

    @property
    def available(self) -> bool:
        """Available once the version is known."""
        return super().available and self.coordinator.data.version is not None

    @property
    def installed_version(self) -> str | None:
        """Return the running version."""
        version = self.coordinator.data.version
        return version.current_version if version else None

    @property
    def latest_version(self) -> str | None:
        """Return the latest release, or the running one when unknown."""
        version = self.coordinator.data.version
        if version is None:
            return None
        if version.update_available and version.latest_version:
            return version.latest_version
        return version.current_version

    @property
    def release_url(self) -> str | None:
        """Return the link to the release notes."""
        version = self.coordinator.data.version
        return version.release_url if version else None
