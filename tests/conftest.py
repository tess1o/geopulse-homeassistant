"""Shared fixtures for GeoPulse tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.geopulse_ha.const import (
    CONF_DISPLAY_NAME,
    CONF_SCOPES,
    CONF_TRACKERS,
    CONF_USER_ID,
    DOMAIN,
)

URL = "http://geopulse.local:8080"
API = f"{URL}/api/v1"
TOKEN = "gp_sa_test_token"
USER_ID = "11111111-2222-3333-4444-555555555555"
TOKEN_ID = "99999999-8888-7777-6666-555555555555"
FRIEND_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BOTH_SCOPES = ["read", "gps:ingest"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in every test."""


def token_payload(scopes: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    """Return an /api-tokens/current response."""
    return {
        "id": TOKEN_ID,
        "userId": USER_ID,
        "userEmail": "alice@example.com",
        "name": "Home Assistant",
        "scopes": BOTH_SCOPES if scopes is None else scopes,
        "status": "ACTIVE",
        "expiresAt": None,
        **extra,
    }


STATUS_STAY = {
    "generatedAt": "2026-10-14T09:00:00Z",
    "timezone": "Europe/Kyiv",
    "state": "STAY",
    "stay": {
        "placeName": "Home",
        "favoriteId": 7,
        "locationSource": "FAVORITE",
        "since": "2026-10-14T07:00:00Z",
        "durationSeconds": 7200,
        "latitude": 49.8419,
        "longitude": 24.0315,
        "city": "Lviv",
        "country": "Ukraine",
    },
    "lastPoint": {
        "timestamp": "2026-10-14T08:59:00Z",
        "receivedAt": "2026-10-14T08:59:02Z",
        "latitude": 49.8421,
        "longitude": 24.0318,
        "accuracy": 8.0,
        "battery": 82.0,
        "sourceType": "HOME_ASSISTANT",
    },
    "today": {
        "date": "2026-10-14",
        "distanceMeters": 12345,
        "tripCount": 3,
        "stayCount": 4,
        "uniquePlaces": 2,
        "movingSeconds": 1800,
    },
}

FRIEND = {
    "userId": USER_ID,
    "friendId": FRIEND_ID,
    "fullName": "Bob",
    "email": "bob@example.com",
    "lastLatitude": 50.45,
    "lastLongitude": 30.52,
    "lastAccuracy": 12.0,
    "lastBattery": 55.0,
    "lastSeen": "2026-10-14T08:58:00Z",
    "latestActivityType": "STAY",
    "latestActivityDurationSeconds": 600,
    "lastLocation": "Café Central",
    "friendSharesLiveLocation": True,
}

SOURCE = {
    "id": "source-1",
    "type": "HOME_ASSISTANT",
    "active": True,
    "ingestStatus": {
        "lastReceivedAt": "2026-10-14T08:59:02Z",
        "lastSavedAt": "2026-10-14T08:59:02Z",
    },
}


def mock_geopulse(
    aioclient_mock: AiohttpClientMocker,
    *,
    scopes: list[str] | None = None,
    version: str = "2.0.0",
    status: dict[str, Any] | None = None,
    friends: list[dict[str, Any]] | None = None,
    geofence_events: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
    token_status: int = 200,
) -> None:
    """Register responses for every endpoint the integration calls."""
    aioclient_mock.get(
        f"{API}/system/version/status",
        json={
            "currentVersion": version,
            "latestVersion": "2.1.0",
            "updateAvailable": True,
            "releaseUrl": "https://github.com/tess1o/geopulse/releases/tag/v2.1.0",
        },
    )
    aioclient_mock.get(f"{API}/system/health", json={"status": "UP", "database": "UP"})
    aioclient_mock.get(
        f"{API}/api-tokens/current",
        status=token_status,
        json=token_payload(scopes) if token_status == 200 else {"code": "UNAUTHORIZED"},
    )
    aioclient_mock.get(
        f"{API}/users/me",
        json={"userId": USER_ID, "fullName": "Alice", "email": "alice@example.com"},
    )
    aioclient_mock.get(f"{API}/users/me/status", json=status or STATUS_STAY)
    aioclient_mock.get(f"{API}/friends", json=[FRIEND] if friends is None else friends)
    aioclient_mock.get(
        f"{API}/geofences/events",
        json=geofence_events
        or {
            "items": [{"id": 100, "eventType": "ENTER"}],
            "page": 0,
            "size": 1,
            "totalElements": 1,
        },
    )
    aioclient_mock.get(
        f"{API}/timeline",
        json=timeline or {"stays": [], "trips": [], "dataGaps": []},
    )
    aioclient_mock.get(f"{API}/gps/sources/current", json=SOURCE)
    aioclient_mock.post(
        f"{API}/gps/ingest/home-assistant/batch",
        json={"accepted": 1, "saved": 1, "duplicate": 0, "filtered": 0},
    )


def make_entry(
    scopes: list[str] | None = None,
    options: dict[str, Any] | None = None,
    entry_id: str = "entry_alice",
) -> MockConfigEntry:
    """Return a config entry for Alice."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        title="GeoPulse (Alice)",
        unique_id=USER_ID,
        data={
            CONF_URL: URL,
            CONF_TOKEN: TOKEN,
            CONF_VERIFY_SSL: True,
            CONF_USER_ID: USER_ID,
            CONF_DISPLAY_NAME: "Alice",
            CONF_SCOPES: BOTH_SCOPES if scopes is None else scopes,
        },
        options={CONF_TRACKERS: []} if options is None else options,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up an entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def mock_setup_entry() -> Generator[None]:
    """Skip the real setup when testing the config flow."""
    with patch("custom_components.geopulse_ha.async_setup_entry", return_value=True):
        yield
