"""Tests for the logbook descriptions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Event, HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.geopulse_ha.const import BUS_EVENT, DOMAIN
from custom_components.geopulse_ha.logbook import async_describe_events, describe

from .conftest import USER_ID, make_entry, mock_geopulse, setup_entry

TODAY = "2026-10-14T{}:00+00:00"


@pytest.fixture(autouse=True)
async def utc_and_frozen(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Show times in UTC, on 2026-10-14."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(datetime.fromisoformat("2026-10-14T12:00:00+00:00"))


@pytest.mark.parametrize(
    ("data", "subject", "message"),
    [
        (
            {
                "type": "trip_ended",
                "started_at": TODAY.format("08:44"),
                "ended_at": TODAY.format("08:53"),
                "distance_meters": 3912,
                "movement_type": "CAR",
            },
            None,
            "trip ended, 3.9 km by car (08:44–08:53)",
        ),
        (
            {
                "type": "stay_started",
                "place_name": "Потік Збоїща",
                "started_at": TODAY.format("08:53"),
            },
            None,
            "stay started at Потік Збоїща (08:53)",
        ),
        (
            {
                "type": "stay_ended",
                "place_name": "Home",
                "started_at": "2026-10-13T22:10:00+00:00",
                "ended_at": TODAY.format("08:30"),
                "duration_seconds": 37200,
            },
            None,
            "stay at Home ended, 10 h 20 min (Oct 13 22:10–Oct 14 08:30)",
        ),
        (
            {
                "type": "geofence_enter",
                "geofence_name": "Office",
                "subject_name": "Bob",
                "subject_is_self": False,
                "occurred_at": TODAY.format("09:02"),
            },
            "Bob",
            "entered Office (09:02)",
        ),
        (
            {
                "type": "geofence_leave",
                "geofence_name": "Office",
                "subject_name": "Alice",
                "subject_is_self": True,
                "occurred_at": TODAY.format("17:30"),
            },
            None,
            "left Office (17:30)",
        ),
        (
            {
                "type": "stay_started",
                "place_name": "Café Central",
                "started_at": TODAY.format("08:48"),
                "subject_name": "Bob",
                "subject_is_self": False,
            },
            "Bob",
            "stay started at Café Central (08:48)",
        ),
        (
            {
                "type": "trip_started",
                "movement_type": "WALK",
                "started_at": TODAY.format("10:00"),
            },
            None,
            "trip started on foot (10:00)",
        ),
        (
            {"type": "tracking_lost", "last_point_at": TODAY.format("11:00")},
            None,
            "tracking lost (last point 11:00)",
        ),
        (
            {
                "type": "tracking_resumed",
                "lost_at": TODAY.format("11:00"),
                "resumed_at": TODAY.format("11:45"),
                "duration_seconds": 2700,
                "stale": True,
            },
            None,
            "tracking resumed after 45 min (11:00–11:45), reported late",
        ),
    ],
)
def test_describe(data: dict[str, Any], subject: str | None, message: str) -> None:
    """Each event type becomes a readable line with the time it happened."""
    assert describe(data) == (subject, message)


async def test_logbook_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The bus event is named after the account and links to the event entity."""
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    describers: dict[tuple[str, str], Any] = {}
    async_describe_events(
        hass,
        lambda domain, event_type, fn: describers.__setitem__((domain, event_type), fn),
    )
    describer = describers[(DOMAIN, BUS_EVENT)]
    entry_data = describer(
        Event(
            BUS_EVENT,
            {
                "type": "stay_started",
                "place_name": "Gym",
                "started_at": TODAY.format("18:00"),
                "entry_id": entry.entry_id,
                "user_id": USER_ID,
            },
        )
    )
    assert entry_data == {
        "name": "Alice",
        "message": "stay started at Gym (18:00)",
        "icon": "mdi:map-marker",
        "entity_id": "event.geopulse_alice_geopulse_activity",
    }
