"""Tests for setup, entities and repairs."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.geopulse_ha.const import DOMAIN

from .conftest import (
    API,
    USER_ID,
    make_entry,
    mock_geopulse,
    setup_entry,
    token_payload,
)


async def test_setup_creates_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Status, server and friend entities reflect the API."""
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get("sensor.geopulse_alice_activity").state == "stay"
    assert (
        hass.states.get("sensor.geopulse_alice_activity").attributes["since"]
        == "2026-10-14T07:00:00+00:00"
    )
    place = hass.states.get("sensor.geopulse_alice_place")
    assert place.state == "Home"
    assert place.attributes["city"] == "Lviv"
    assert place.attributes["favorite_id"] == 7
    assert hass.states.get("sensor.geopulse_alice_activity_since").state == (
        "2026-10-14T07:00:00+00:00"
    )
    assert (
        float(hass.states.get("sensor.geopulse_alice_activity_duration").state) == 120
    )
    assert hass.states.get("sensor.geopulse_alice_distance_today").state == "12.345"
    assert hass.states.get("sensor.geopulse_alice_trips_today").state == "3"
    assert hass.states.get("sensor.geopulse_alice_stays_today").state == "4"
    assert hass.states.get("sensor.geopulse_alice_places_today").state == "2"
    assert float(hass.states.get("sensor.geopulse_alice_moving_time_today").state) == 30
    assert hass.states.get("sensor.geopulse_alice_trip_distance").state == "unavailable"
    last_point = hass.states.get("sensor.geopulse_alice_last_point")
    assert last_point.state == "2026-10-14T08:59:00+00:00"
    assert last_point.attributes["source_type"] == "HOME_ASSISTANT"
    assert hass.states.get("binary_sensor.geopulse_alice_on_trip").state == "off"
    assert hass.states.get("binary_sensor.geopulse_alice_tracking").state == "on"
    assert hass.states.get("binary_sensor.geopulse_alice_server").state == "on"
    assert hass.states.get("sensor.geopulse_alice_ingest_status").state == "ok"
    assert hass.states.get("sensor.geopulse_alice_last_data_received").state == (
        "2026-10-14T08:59:02+00:00"
    )

    update = hass.states.get("update.geopulse_alice_geopulse_server")
    assert update.state == "on"
    assert update.attributes["installed_version"] == "2.0.0"
    assert update.attributes["latest_version"] == "2.1.0"
    assert update.attributes["supported_features"] == 0

    assert hass.states.get("event.geopulse_alice_geopulse_activity") is not None
    assert hass.states.get("device_tracker.bob").state == "not_home"
    assert hass.states.get("sensor.bob_battery").state == "55"

    (device,) = [
        device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        if (DOMAIN, USER_ID) in device.identifiers
    ]
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert device.configuration_url == "http://geopulse.local:8080"

    entity = er.async_get(hass).async_get("sensor.geopulse_alice_activity")
    assert entity.unique_id == f"{USER_ID}_activity"


async def test_trip_status(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Trip sensors are filled during a trip."""
    mock_geopulse(
        aioclient_mock,
        status={
            "state": "TRIP",
            "trip": {
                "since": "2026-10-14T08:30:00Z",
                "durationSeconds": 900,
                "distanceMeters": 4200,
                "movementType": "CAR",
            },
        },
    )
    await setup_entry(hass, make_entry())
    on_trip = hass.states.get("binary_sensor.geopulse_alice_on_trip")
    assert on_trip.state == "on"
    assert on_trip.attributes == {
        **on_trip.attributes,
        "distance_m": 4200,
        "movement_type": "CAR",
    }
    assert hass.states.get("sensor.geopulse_alice_trip_distance").state == "4.2"
    assert hass.states.get("sensor.geopulse_alice_place").state == "unknown"
    assert float(hass.states.get("sensor.geopulse_alice_activity_duration").state) == 15


async def test_data_gap(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Tracking is off during a data gap."""
    mock_geopulse(
        aioclient_mock,
        status={
            "state": "DATA_GAP",
            "dataGap": {"since": "2026-10-14T06:00:00Z", "durationSeconds": 3600},
        },
    )
    await setup_entry(hass, make_entry())
    assert hass.states.get("binary_sensor.geopulse_alice_tracking").state == "off"
    assert hass.states.get("sensor.geopulse_alice_activity").state == "data_gap"


async def test_ingest_only_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A sender-only token creates no status sensors."""
    mock_geopulse(aioclient_mock, scopes=["gps:ingest"])
    entry = make_entry(
        scopes=["gps:ingest"], options={"trackers": ["device_tracker.pixel_8"]}
    )
    await setup_entry(hass, entry)
    assert hass.states.get("sensor.geopulse_alice_activity") is None
    assert hass.states.get("event.geopulse_alice_geopulse_activity") is None
    assert hass.states.get("binary_sensor.geopulse_alice_server") is not None
    assert hass.states.get("sensor.geopulse_alice_ingest_status").state == "ok"
    assert hass.states.get("sensor.device_tracker_pixel_8_last_sent") is not None
    assert not any(
        str(call[1]).endswith(("/users/me/status", "/friends"))
        for call in aioclient_mock.mock_calls
    )


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 at setup starts reauth."""
    mock_geopulse(aioclient_mock, token_status=401)
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_server_unreachable_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A connection error at setup retries later."""
    aioclient_mock.get(f"{API}/api-tokens/current", exc=TimeoutError)
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_status_failure_makes_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A 5xx makes the status entities unavailable."""
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry())
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/users/me/status", status=502)
    mock_geopulse(aioclient_mock)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.geopulse_alice_activity").state == "unavailable"


async def test_repairs(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Full-access and expiring tokens raise repair issues."""
    aioclient_mock.get(
        f"{API}/api-tokens/current",
        json=token_payload(["all"], expiresAt="2026-10-20T00:00:00Z"),
    )
    mock_geopulse(aioclient_mock, scopes=["all"])
    entry = make_entry(scopes=["all"])
    await setup_entry(hass, entry)
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"full_access_token_{entry.entry_id}")
    assert issues.async_get_issue(DOMAIN, f"token_expiring_{entry.entry_id}")

    await hass.config_entries.async_remove(entry.entry_id)
    assert not issues.async_get_issue(DOMAIN, f"full_access_token_{entry.entry_id}")


async def test_unload(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The entry unloads cleanly."""
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_ingest_status_refreshes_every_minute(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Data received after setup shows up on the next minute's poll."""
    aioclient_mock.get(
        f"{API}/gps/sources/current",
        json={"id": "source-1", "type": "HOME_ASSISTANT", "active": True},
    )
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry())
    assert hass.states.get("sensor.geopulse_alice_last_data_received").state == (
        "unknown"
    )
    assert hass.states.get("sensor.geopulse_alice_ingest_status").state == "no_data"

    aioclient_mock.clear_requests()
    mock_geopulse(aioclient_mock)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.geopulse_alice_last_data_received").state == (
        "2026-10-14T08:59:02+00:00"
    )
    assert hass.states.get("sensor.geopulse_alice_ingest_status").state == "ok"
    # Version and token are still on the 30-minute schedule.
    assert not any(
        str(call[1]).endswith(("/system/version/status", "/api-tokens/current"))
        for call in aioclient_mock.mock_calls
    )
