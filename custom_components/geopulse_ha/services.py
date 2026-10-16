"""Services for GeoPulse."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .backfill import async_load_points, async_send_points
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_END,
    ATTR_START,
    BACKFILL_MAX_DAYS,
    DOMAIN,
    SERVICE_BACKFILL,
    SERVICE_FLUSH_QUEUE,
)
from .helpers import is_geopulse_entity

if TYPE_CHECKING:
    from . import GeoPulseConfigEntry

BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_START): cv.datetime,
        vol.Optional(ATTR_END): cv.datetime,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)
FLUSH_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(value)


def _loaded_entries(hass: HomeAssistant) -> list[GeoPulseConfigEntry]:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _sending_entry(
    hass: HomeAssistant, entry_id: str | None, entity_id: str
) -> GeoPulseConfigEntry:
    """Pick the config entry that should receive a backfill."""
    entries = [entry for entry in _loaded_entries(hass) if entry.runtime_data.can_send]
    if entry_id is not None:
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    forwarding = [
        entry
        for entry in entries
        if entry.runtime_data.forwarder
        and entity_id in entry.runtime_data.forwarder.entity_ids
    ]
    if len(forwarding) == 1:
        return forwarding[0]
    if len(entries) == 1:
        return entries[0]
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="entry_required" if entries else "no_sending_entry",
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the GeoPulse services."""

    async def backfill(call: ServiceCall) -> ServiceResponse:
        entity_id: str = call.data[ATTR_ENTITY_ID]
        start = _as_utc(call.data[ATTR_START])
        end = _as_utc(call.data.get(ATTR_END) or dt_util.utcnow())
        if end <= start:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_range"
            )
        if end - start > timedelta(days=BACKFILL_MAX_DAYS):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="range_too_long",
                translation_placeholders={"days": str(BACKFILL_MAX_DAYS)},
            )
        if is_geopulse_entity(hass, entity_id):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="geopulse_entity",
                translation_placeholders={"entity_id": entity_id},
            )
        entry = _sending_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID), entity_id)
        runtime = entry.runtime_data
        points = await async_load_points(
            hass, entity_id, start, end, send_battery=runtime.send_battery
        )
        summary = await async_send_points(runtime.api, points)
        if not call.return_response:
            return None
        return {
            "points": len(points),
            "accepted": summary.accepted,
            "saved": summary.saved,
            "duplicate": summary.duplicate,
            "filtered": summary.filtered,
        }

    async def flush_queue(call: ServiceCall) -> None:
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        for entry in _loaded_entries(hass):
            if entry_id is not None and entry.entry_id != entry_id:
                continue
            if forwarder := entry.runtime_data.forwarder:
                await forwarder.async_flush(force=True)

    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL,
        backfill,
        schema=BACKFILL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FLUSH_QUEUE, flush_queue, schema=FLUSH_SCHEMA
    )
