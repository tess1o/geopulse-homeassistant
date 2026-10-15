"""Diagnostics for GeoPulse."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

from . import GeoPulseConfigEntry
from .const import CONF_DISPLAY_NAME
from .coordinator import diagnostics_state

TO_REDACT = {
    CONF_TOKEN,
    CONF_DISPLAY_NAME,
    "full_name",
    "user_email",
    "email",
    "subject_name",
    "last_rejection_params",
}
COORDINATE_KEYS = {"latitude", "longitude"}


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{REDACTED}{port}"


def _plain(value: Any) -> Any:
    """Turn dataclasses and datetimes into JSON-friendly values, rounding coordinates."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, dict):
        return {
            key: round(item, 2)
            if key in COORDINATE_KEYS and isinstance(item, float)
            else _plain(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GeoPulseConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    server = runtime.server.data
    data = {**entry.data, CONF_URL: _redact_url(entry.data[CONF_URL])}

    forwarder = runtime.forwarder
    events = runtime.events
    result = {
        "entry": {
            "data": data,
            "options": dict(entry.options),
            "title": REDACTED,
        },
        "scopes": runtime.scopes,
        "server": {
            "version": _plain(server.version),
            "health": _plain(server.health),
            "token": _plain(server.token),
            "source": _plain(server.source),
        },
        "coordinators": {
            "server": diagnostics_state(runtime.server),
            "status": diagnostics_state(runtime.status),
            "friends": diagnostics_state(runtime.friends),
        },
        "status": _plain(runtime.status.data) if runtime.status else None,
        "friends": len(runtime.friends.data or {}) if runtime.friends else None,
        "forwarder": {
            "trackers": forwarder.entity_ids,
            "queue_length": forwarder.queue_length(),
            "paused": forwarder.paused,
            "last_error": forwarder.last_error,
            "last_sent": _plain(forwarder.last_sent),
        }
        if forwarder
        else None,
        "events": {
            "previous_status": {
                "kind": events.previous.kind,
                "since": _plain(events.previous.start),
                "place_name": events.previous.place_name,
                "seen": _plain(events.previous_seen),
            }
            if events.previous
            else None,
            "status_last_success": _plain(events.status_success),
            "status_error": events.status_error,
            "geofence_event_id": events.geofence_id,
            "geofence_last_success": _plain(events.geofence_success),
            "geofence_error": events.geofence_error,
        }
        if events
        else None,
    }
    return async_redact_data(result, TO_REDACT)
