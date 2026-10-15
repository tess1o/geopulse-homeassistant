"""GeoPulse activity events as an event entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GeoPulseConfigEntry, GeoPulseRuntimeData
from .const import EVENT_TYPES, SIGNAL_ACTIVITY_EVENT
from .entity import GeoPulsePlainEntity
from .helpers import account_device_info

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeoPulseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the activity event entity."""
    runtime = entry.runtime_data
    if runtime.events is not None:
        async_add_entities([GeoPulseActivityEvent(entry.entry_id, runtime)])


class GeoPulseActivityEvent(GeoPulsePlainEntity, EventEntity):
    """Fires when GeoPulse reports a geofence, stay, trip or tracking event."""

    _attr_translation_key = "activity"
    _attr_event_types = EVENT_TYPES

    def __init__(self, entry_id: str, runtime: GeoPulseRuntimeData) -> None:
        """Initialize the entity."""
        self._entry_id = entry_id
        self._attr_unique_id = f"{runtime.user_id}_activity_event"
        self._attr_device_info = account_device_info(
            runtime.user_id, runtime.display_name, runtime.url
        )

    async def async_added_to_hass(self) -> None:
        """Listen for events from the poller."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ACTIVITY_EVENT.format(self._entry_id),
                self._handle_event,
            )
        )

    @callback
    def _handle_event(self, event_type: str, attributes: dict[str, Any]) -> None:
        if event_type not in self.event_types:
            return
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
