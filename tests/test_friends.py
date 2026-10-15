"""Tests for friends on the map."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from .conftest import FRIEND, USER_ID, make_entry, mock_geopulse, setup_entry

CAROL = {
    **FRIEND,
    "friendId": "cccccccc-0000-0000-0000-000000000000",
    "fullName": "Carol",
    "lastBattery": None,
    "lastAccuracy": None,
    "latestActivityType": "TRIP",
}


async def _refresh(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    friends: list[dict],
) -> None:
    aioclient_mock.clear_requests()
    mock_geopulse(aioclient_mock, friends=friends)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_friend_tracker(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A friend sharing their location is a GPS tracker."""
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry())
    state = hass.states.get("device_tracker.bob")
    assert state.attributes["latitude"] == 50.45
    assert state.attributes["longitude"] == 30.52
    assert state.attributes["gps_accuracy"] == 12
    assert state.attributes["source_type"] == "gps"
    assert state.attributes["last_seen"] == "2026-10-14T08:58:00+00:00"
    assert state.attributes["activity"] == "stay"
    assert state.attributes["activity_duration_s"] == 600
    # Shown with Activity and Place, not under Diagnostic.
    entry_reg = er.async_get(hass).async_get("device_tracker.bob")
    assert entry_reg.entity_category is None

    activity = hass.states.get("sensor.bob_activity")
    assert activity.state == "stay"
    assert activity.attributes["options"] == ["stay", "trip"]
    duration = hass.states.get("sensor.bob_activity_duration")
    assert float(duration.state) == 10
    assert duration.attributes["unit_of_measurement"] == "min"
    assert hass.states.get("sensor.bob_place").state == "Café Central"
    assert state.attributes["last_location"] == "Café Central"


async def test_friends_added_stop_sharing_and_removed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """New friends appear, non-sharing friends go unavailable, removed ones vanish."""
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    await _refresh(hass, aioclient_mock, freezer, [FRIEND, CAROL])
    carol = hass.states.get("device_tracker.carol")
    assert carol.attributes["gps_accuracy"] == 0
    assert hass.states.get("sensor.carol_battery") is None
    assert hass.states.get("sensor.carol_activity").state == "trip"
    # No place while on a trip.
    assert hass.states.get("sensor.carol_place").state == "unknown"

    # GeoPulse doesn't know what Bob is doing yet.
    unknown = {**FRIEND, "latestActivityType": None}
    await _refresh(hass, aioclient_mock, freezer, [unknown, CAROL])
    assert hass.states.get("sensor.bob_activity").state == "unknown"

    stopped = {**FRIEND, "friendSharesLiveLocation": False, "lastLatitude": None}
    await _refresh(hass, aioclient_mock, freezer, [stopped, CAROL])
    assert hass.states.get("device_tracker.bob").state == "unavailable"
    assert hass.states.get("sensor.bob_battery").state == "unavailable"
    assert hass.states.get("sensor.bob_activity").state == "unavailable"
    assert hass.states.get("sensor.bob_activity_duration").state == "unavailable"

    await _refresh(hass, aioclient_mock, freezer, [CAROL])
    dev_reg = dr.async_get(hass)
    names = {d.name for d in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)}
    assert "Bob" not in names
    assert "Carol" in names
    assert hass.states.get("device_tracker.bob") is None


async def test_friends_disabled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With friends off, no trackers are created and /friends is not called."""
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry(options={"trackers": [], "friends": False}))
    assert hass.states.get("device_tracker.bob") is None
    assert not any(
        str(call[1]).endswith("/friends") for call in aioclient_mock.mock_calls
    )


async def test_friend_device_hangs_off_account(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Friend devices link to the account device without the deprecated via_device."""
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)
    devices = {
        identifier: device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for _domain, identifier in device.identifiers
    }
    account = devices[USER_ID]
    assert entry.runtime_data.account_device_id == account.id
    assert devices[f"{USER_ID}_friend_{FRIEND['friendId']}"].via_device_id == account.id
    assert "via_device" not in caplog.text
