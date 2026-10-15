"""Describe GeoPulse events in the logbook (Activity)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_ICON,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import BUS_EVENT, DOMAIN, NAME

ICONS = {
    "geofence_enter": "mdi:map-marker-check",
    "geofence_leave": "mdi:map-marker-remove",
    "stay_started": "mdi:map-marker",
    "stay_ended": "mdi:map-marker-off",
    "trip_started": "mdi:car",
    "trip_ended": "mdi:flag-checkered",
    "tracking_lost": "mdi:crosshairs-off",
    "tracking_resumed": "mdi:crosshairs-gps",
}

MOVEMENT = {
    "CAR": "by car",
    "WALK": "on foot",
    "WALKING": "on foot",
    "RUNNING": "running",
    "BICYCLE": "by bicycle",
    "CYCLING": "by bicycle",
    "TRAIN": "by train",
    "FLIGHT": "by plane",
}


def _local(value: Any) -> datetime | None:
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    return dt_util.as_local(parsed) if parsed else None


def _time(value: Any, same_day_as: datetime | None = None) -> str | None:
    """Local HH:MM, with the date when it is not today (or not `same_day_as`)."""
    moment = _local(value)
    if moment is None:
        return None
    reference = (same_day_as or dt_util.now()).date()
    if moment.date() == reference:
        return f"{moment:%H:%M}"
    return f"{moment:%b} {moment.day} {moment:%H:%M}"


def _span(start: Any, end: Any) -> str | None:
    """ "(08:44–08:53)", or "(08:44)" when only one end is known."""
    first = _time(start)
    last = _time(end, same_day_as=_local(start))
    if first and last:
        return f"({first}–{last})"
    if first or last:
        return f"({first or last})"
    return None


def _duration(seconds: Any) -> str | None:
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return None
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def _join(*parts: str | None) -> str:
    return " ".join(part for part in parts if part)


def describe(data: dict[str, Any]) -> tuple[str | None, str]:
    """Return (subject name or None for the account owner, message)."""
    kind = data.get("type")
    at = _span(data.get("occurred_at"), None)
    geofence = kind in ("geofence_enter", "geofence_leave")
    # Stay and trip events are about the account owner unless marked otherwise
    # (friends); geofence events always say whom they are about.
    is_self = data.get("subject_is_self", not geofence)
    subject = None if is_self else data.get("subject_name")
    if geofence:
        verb = "entered" if kind == "geofence_enter" else "left"
        message = _join(verb, data.get("geofence_name") or "a geofence", at)
    elif kind == "stay_started":
        message = _join(
            "stay started at",
            data.get("place_name") or "an unknown place",
            _span(data.get("started_at") or data.get("occurred_at"), None),
        )
    elif kind == "stay_ended":
        place = data.get("place_name") or "an unknown place"
        message = _join(
            f"stay at {place} ended,",
            _duration(data.get("duration_seconds")),
            _span(data.get("started_at"), data.get("ended_at")),
        )
    elif kind == "trip_started":
        message = _join(
            "trip started",
            MOVEMENT.get(str(data.get("movement_type")).upper()),
            _span(data.get("started_at") or data.get("occurred_at"), None),
        )
    elif kind == "trip_ended":
        meters = data.get("distance_meters")
        distance = (
            f"{meters / 1000:.1f} km" if isinstance(meters, (int, float)) else None
        )
        message = _join(
            "trip ended," if distance else "trip ended",
            distance,
            MOVEMENT.get(str(data.get("movement_type")).upper()),
            _span(data.get("started_at"), data.get("ended_at")),
        )
    elif kind == "tracking_lost":
        last = _time(data.get("last_point_at"))
        message = _join("tracking lost", f"(last point {last})" if last else at)
    elif kind == "tracking_resumed":
        message = _join(
            "tracking resumed after",
            _duration(data.get("duration_seconds")) or "a gap",
            _span(data.get("lost_at"), data.get("resumed_at")),
        )
    else:
        message = _join(str(kind or "event").replace("_", " "), at)
    if data.get("stale"):
        message = f"{message}, reported late"
    return subject, message


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe GeoPulse bus events."""

    @callback
    def async_describe(event: Event) -> dict[str, str]:
        data = event.data
        account = NAME
        event_entity: str | None = None
        entry = hass.config_entries.async_get_entry(data.get("entry_id", ""))
        if entry is not None:
            if entry.state is ConfigEntryState.LOADED:
                account = entry.runtime_data.display_name
            event_entity = data.get("entity_id") or er.async_get(
                hass
            ).async_get_entity_id(
                "event", DOMAIN, f"{data.get('user_id')}_activity_event"
            )
        subject, message = describe(data)
        description = {
            LOGBOOK_ENTRY_NAME: subject or account,
            LOGBOOK_ENTRY_MESSAGE: message,
            LOGBOOK_ENTRY_ICON: ICONS.get(data.get("type", ""), "mdi:map-marker-path"),
        }
        if event_entity:
            description[LOGBOOK_ENTRY_ENTITY_ID] = event_entity
        return description

    async_describe_event(DOMAIN, BUS_EVENT, async_describe)
