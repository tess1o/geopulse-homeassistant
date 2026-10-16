"""Tests for forwarding Home Assistant locations to GeoPulse."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.geopulse_ha.const import (
    CONF_HEARTBEAT_MINUTES,
    CONF_TRACKERS,
    DOMAIN,
    QUEUE_MAX_POINTS,
)
from custom_components.geopulse_ha.sender import state_to_point

from .conftest import API, make_entry, mock_geopulse, setup_entry

TRACKER = "device_tracker.pixel_8"
BATCH_URL = f"{API}/gps/ingest/home-assistant/batch"
HOME = {"latitude": 49.84, "longitude": 24.03, "gps_accuracy": 8, "altitude": 296}


def _posted(aioclient_mock: AiohttpClientMocker) -> list[dict[str, Any]]:
    """All points posted to the batch endpoint, in order."""
    points: list[dict[str, Any]] = []
    for method, url, data, _headers in aioclient_mock.mock_calls:
        if method.lower() == "post" and str(url) == BATCH_URL:
            points.extend(data)
    return points


def _entry(**options: Any) -> MockConfigEntry:
    return make_entry(options={CONF_TRACKERS: [TRACKER], **options})


async def _flush(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float = 3
) -> None:
    await hass.async_block_till_done()  # let state listeners queue their points
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# ------------------------------------------------------------------ mapping


def test_mapping_full() -> None:
    """All known values are mapped."""
    now = datetime(2026, 10, 14, 9, 15, 30, tzinfo=UTC)
    state = State(
        TRACKER,
        "home",
        {**HOME, "speed": 2.7, "battery_level": 82},
        last_updated=now,
        last_reported=now,
    )
    point = state_to_point(state, battery=82)
    assert point == {
        "device_id": "pixel_8",
        "timestamp": "2026-10-14T09:15:30+00:00",
        "location": {
            "latitude": 49.84,
            "longitude": 24.03,
            "accuracy": 8.0,
            "altitude": 296.0,
            "speed": 2.7,
        },
        "battery": {"level": 82},
    }


def test_mapping_omits_unknown_values() -> None:
    """Missing or non-numeric values are left out, never sent as 0."""
    state = State(
        "person.alice",
        "not_home",
        {"latitude": 49.84, "longitude": 24.03, "altitude": None, "speed": "fast"},
    )
    point = state_to_point(state)
    assert point["location"] == {"latitude": 49.84, "longitude": 24.03}
    assert "battery" not in point
    assert point["device_id"] == "alice"


@pytest.mark.parametrize(
    ("state", "attrs"),
    [("unavailable", HOME), ("unknown", HOME), ("home", {"gps_accuracy": 5})],
)
def test_mapping_skips(state: str, attrs: dict[str, Any]) -> None:
    """States without a position are skipped."""
    assert state_to_point(State(TRACKER, state, attrs)) is None


def test_mapping_uses_last_reported() -> None:
    """The timestamp is last_reported when newer than last_updated."""
    updated = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    reported = updated + timedelta(minutes=20)
    state = State(TRACKER, "home", HOME, last_updated=updated, last_reported=reported)
    assert state_to_point(state)["timestamp"] == reported.isoformat()
    assert (
        state_to_point(state, use_last_reported=False)["timestamp"]
        == updated.isoformat()
    )


# ------------------------------------------------------------------ forwarding


async def test_forwards_location_changes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Location changes are batched and sent; unchanged reports are not."""
    hass.states.async_set(TRACKER, "home", HOME)
    mock_geopulse(aioclient_mock)
    entry = _entry(**{CONF_HEARTBEAT_MINUTES: 0})
    await setup_entry(hass, entry)

    hass.states.async_set(TRACKER, "home", HOME)  # unchanged: no point
    hass.states.async_set(TRACKER, "not_home", {**HOME, "latitude": 49.85})
    hass.states.async_set(
        TRACKER, "not_home", {**HOME, "latitude": 49.86, "battery_level": 77}
    )
    hass.states.async_set(
        TRACKER, "not_home", {**HOME, "latitude": 49.86, "battery_level": 76}
    )
    await hass.async_block_till_done()
    assert _posted(aioclient_mock) == []

    await _flush(hass, freezer)
    points = _posted(aioclient_mock)
    assert [p["location"]["latitude"] for p in points] == [49.85, 49.86]
    assert points[1]["battery"] == {"level": 77}
    forwarder = entry.runtime_data.forwarder
    assert forwarder.queue == []
    assert TRACKER in forwarder.last_sent

    sensor = hass.states.get("sensor.pixel_8_last_sent")
    assert sensor.state != "unknown"
    assert sensor.attributes["queue_length"] == 0
    assert sensor.attributes["last_error"] is None


async def test_first_state_after_creation_is_skipped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A state with no previous state (startup) does not create a point."""
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, _entry())
    hass.states.async_set(TRACKER, "home", HOME)
    await _flush(hass, freezer)
    assert _posted(aioclient_mock) == []


async def test_battery_from_device_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Battery comes from a battery sensor on the tracker's device."""
    other = MockConfigEntry(domain="mobile_app")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("mobile_app", "pixel")}
    )
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "device_tracker",
        "mobile_app",
        "pixel",
        device_id=device.id,
        suggested_object_id="pixel_8",
    )
    ent_reg.async_get_or_create(
        "sensor",
        "mobile_app",
        "pixel_battery",
        device_id=device.id,
        original_device_class="battery",
        suggested_object_id="pixel_8_battery",
    )
    hass.states.async_set("sensor.pixel_8_battery", "64")
    hass.states.async_set(TRACKER, "home", HOME)

    mock_geopulse(aioclient_mock)
    await setup_entry(hass, _entry())
    hass.states.async_set(TRACKER, "not_home", {**HOME, "latitude": 49.9})
    await _flush(hass, freezer)
    assert _posted(aioclient_mock)[0]["battery"] == {"level": 64}


async def test_send_battery_off(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Battery is omitted when the option is off."""
    hass.states.async_set(TRACKER, "home", HOME)
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, _entry(send_battery=False))
    hass.states.async_set(
        TRACKER, "not_home", {**HOME, "latitude": 49.9, "battery_level": 50}
    )
    await _flush(hass, freezer)
    assert "battery" not in _posted(aioclient_mock)[0]


async def test_heartbeat(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An unchanged report re-sends the position once the heartbeat is due."""
    hass.states.async_set(TRACKER, "home", HOME)
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, _entry(**{CONF_HEARTBEAT_MINUTES: 15}))

    # First report: nothing was ever sent, so the heartbeat is due.
    hass.states.async_set(TRACKER, "home", HOME)
    await _flush(hass, freezer)
    assert len(_posted(aioclient_mock)) == 1

    # Within 15 minutes: no re-send.
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set(TRACKER, "home", HOME)
    await _flush(hass, freezer)
    assert len(_posted(aioclient_mock)) == 1

    # After 15 minutes: re-sent, with the report time as timestamp.
    freezer.tick(timedelta(minutes=11))
    hass.states.async_set(TRACKER, "home", HOME)
    await _flush(hass, freezer)
    points = _posted(aioclient_mock)
    assert len(points) == 2
    assert points[1]["timestamp"] > points[0]["timestamp"]


async def test_heartbeat_off(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Heartbeat 0 never re-sends unchanged positions."""
    hass.states.async_set(TRACKER, "home", HOME)
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, _entry(**{CONF_HEARTBEAT_MINUTES: 0}))
    freezer.tick(timedelta(hours=2))
    hass.states.async_set(TRACKER, "home", HOME)
    await _flush(hass, freezer)
    assert _posted(aioclient_mock) == []


# ------------------------------------------------------------------ delivery


async def test_backoff_and_recovery(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Failures keep points queued and retry with backoff."""
    hass.states.async_set(TRACKER, "home", HOME)
    aioclient_mock.post(BATCH_URL, status=503)
    mock_geopulse(aioclient_mock)
    entry = _entry()
    await setup_entry(hass, entry)
    forwarder = entry.runtime_data.forwarder

    hass.states.async_set(TRACKER, "not_home", {**HOME, "latitude": 49.9})
    await _flush(hass, freezer)
    assert len(forwarder.queue) == 1
    assert forwarder.last_error
    attempts = len(_posted(aioclient_mock))

    # New points during backoff don't trigger an immediate retry.
    hass.states.async_set(TRACKER, "not_home", {**HOME, "latitude": 49.95})
    await _flush(hass, freezer)
    assert len(_posted(aioclient_mock)) == attempts

    # The server recovers; the 30 s retry delivers both points.
    aioclient_mock.clear_requests()
    mock_geopulse(aioclient_mock)
    await _flush(hass, freezer, 30)
    assert [p["location"]["latitude"] for p in _posted(aioclient_mock)] == [49.9, 49.95]
    assert forwarder.queue == []
    assert forwarder.last_error is None


async def test_unauthorized_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A 401 from ingest pauses sending and starts reauth."""
    hass.states.async_set(TRACKER, "home", HOME)
    aioclient_mock.post(BATCH_URL, status=401, json={"code": "UNAUTHORIZED"})
    mock_geopulse(aioclient_mock)
    entry = _entry()
    await setup_entry(hass, entry)

    hass.states.async_set(TRACKER, "not_home", {**HOME, "latitude": 49.9})
    await _flush(hass, freezer)
    forwarder = entry.runtime_data.forwarder
    assert forwarder.paused == "auth"
    assert len(forwarder.queue) == 1
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


@pytest.mark.parametrize(
    ("code", "issue"),
    [
        ("API_TOKEN_SCOPE_REQUIRED", "cannot_send_locations"),
        ("GPS_SOURCE_DISABLED", "source_disabled"),
        ("GPS_SOURCE_NOT_CONNECTED", "source_not_connected"),
    ],
)
async def test_forbidden_raises_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    code: str,
    issue: str,
) -> None:
    """A 403 pauses sending, raises a repair issue and clears it once fixed."""
    hass.states.async_set(TRACKER, "home", HOME)
    aioclient_mock.post(
        BATCH_URL, status=403, json={"code": code, "requiredScope": "gps:ingest"}
    )
    mock_geopulse(aioclient_mock)
    entry = _entry()
    await setup_entry(hass, entry)

    hass.states.async_set(TRACKER, "not_home", {**HOME, "latitude": 49.9})
    await _flush(hass, freezer)
    forwarder = entry.runtime_data.forwarder
    assert forwarder.paused == "forbidden"
    assert len(forwarder.queue) == 1
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"{issue}_{entry.entry_id}")
    assert not hass.config_entries.flow.async_progress()

    # Fixed in GeoPulse: the 15-minute check delivers and clears the issue.
    aioclient_mock.clear_requests()
    mock_geopulse(aioclient_mock)
    await _flush(hass, freezer, 900)
    assert forwarder.queue == []
    assert forwarder.paused is None
    assert not issues.async_get_issue(DOMAIN, f"{issue}_{entry.entry_id}")


async def test_batch_too_large_is_split(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 413 halves the batch size and retries."""
    calls: list[int] = []

    async def batch(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import (
            AiohttpClientMockResponse,
        )

        calls.append(len(data))
        status = 413 if len(data) > 250 else 200
        return AiohttpClientMockResponse(
            method, url, status=status, json={"accepted": len(data)}
        )

    aioclient_mock.post(BATCH_URL, side_effect=batch)
    mock_geopulse(aioclient_mock)
    entry = _entry()
    await setup_entry(hass, entry)
    forwarder = entry.runtime_data.forwarder
    point = {
        "device_id": "x",
        "timestamp": "2026-10-14T00:00:00+00:00",
        "location": {"latitude": 1, "longitude": 2},
    }
    forwarder.queue = [{"entity_id": TRACKER, "point": point} for _ in range(600)]
    await forwarder.async_flush(force=True)
    assert calls[:2] == [500, 250]
    assert forwarder.queue == []


async def test_queue_cap(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The queue drops the oldest points beyond 10 000."""
    mock_geopulse(aioclient_mock)
    entry = _entry()
    await setup_entry(hass, entry)
    forwarder = entry.runtime_data.forwarder
    forwarder.paused = "forbidden"
    forwarder.enqueue(
        [{"entity_id": TRACKER, "point": {"n": i}} for i in range(QUEUE_MAX_POINTS + 5)]
    )
    assert len(forwarder.queue) == QUEUE_MAX_POINTS
    assert forwarder.queue[0]["point"] == {"n": 5}
    assert "dropping the 5 oldest" in caplog.text


async def test_queue_survives_restart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Unsent points are persisted on unload and sent after the next setup."""
    hass.states.async_set(TRACKER, "home", HOME)
    aioclient_mock.post(BATCH_URL, exc=TimeoutError)
    mock_geopulse(aioclient_mock)
    entry = _entry()
    await setup_entry(hass, entry)
    hass.states.async_set(TRACKER, "not_home", {**HOME, "latitude": 49.9})
    await _flush(hass, freezer)

    assert await hass.config_entries.async_unload(entry.entry_id)
    stored = hass_storage[f"{DOMAIN}.queue.{entry.entry_id}"]["data"]
    assert [item["point"]["location"]["latitude"] for item in stored["points"]] == [
        49.9
    ]

    aioclient_mock.clear_requests()
    mock_geopulse(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _flush(hass, freezer)
    assert [p["location"]["latitude"] for p in _posted(aioclient_mock)] == [49.9]
    assert entry.runtime_data.forwarder.queue == []


async def test_flush_queue_service(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The flush_queue service sends immediately."""
    mock_geopulse(aioclient_mock)
    entry = _entry()
    await setup_entry(hass, entry)
    forwarder = entry.runtime_data.forwarder
    forwarder.queue = [{"entity_id": TRACKER, "point": {"device_id": "pixel_8"}}]
    await hass.services.async_call(DOMAIN, "flush_queue", {}, blocking=True)
    assert forwarder.queue == []
    assert _posted(aioclient_mock) == [{"device_id": "pixel_8"}]


async def test_removed_tracker_device_is_cleaned_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Removing a tracker from the options removes its device."""
    mock_geopulse(aioclient_mock)
    entry = _entry()
    await setup_entry(hass, entry)
    dev_reg = dr.async_get(hass)
    assert len(dr.async_entries_for_config_entry(dev_reg, entry.entry_id)) == 3

    hass.config_entries.async_update_entry(entry, options={CONF_TRACKERS: []})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    names = {d.name for d in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)}
    assert names == {"GeoPulse Alice", "Bob"}


async def test_person_updates_from_geopulse_are_not_forwarded(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A person forwards its phone's updates, not those taken from GeoPulse."""
    person = "person.alice"
    phone = {**HOME, "source": TRACKER}
    hass.states.async_set(person, "home", phone)
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry(options={CONF_TRACKERS: [person]}))
    geopulse_tracker = "device_tracker.geopulse_alice_location"
    assert hass.states.get(geopulse_tracker) is not None

    hass.states.async_set(
        person,
        "not_home",
        {**HOME, "latitude": 49.90, "source": geopulse_tracker},
    )
    hass.states.async_set(person, "not_home", {**phone, "latitude": 49.95})
    await _flush(hass, freezer)
    assert [p["location"]["latitude"] for p in _posted(aioclient_mock)] == [49.95]
