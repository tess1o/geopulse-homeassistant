"""Tests for deriving GeoPulse events."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.geopulse_ha import events as events_module
from custom_components.geopulse_ha.const import BUS_EVENT, DOMAIN

from .conftest import (
    API,
    FRIEND,
    STATUS_STAY,
    USER_ID,
    make_entry,
    mock_geopulse,
    setup_entry,
)

GEOFENCE_URL = f"{API}/geofences/events"
TIMELINE_URL = f"{API}/timeline"
EVENT_ENTITY = "event.geopulse_alice_geopulse_activity"
STORE_KEY = f"{DOMAIN}.events.entry_alice"
NOW = datetime.fromisoformat("2026-10-14T09:00:00+00:00")


def _at(hhmm: str) -> str:
    return f"2026-10-14T{hhmm}:00+00:00"


def _stay(since: str, name: str = "Home", favorite_id: int | None = 7) -> dict:
    return {
        **STATUS_STAY,
        "state": "STAY",
        "stay": {
            **STATUS_STAY["stay"],
            "placeName": name,
            "favoriteId": favorite_id,
            "since": since,
        },
    }


def _trip(since: str, distance: int = 1200) -> dict:
    return {
        **STATUS_STAY,
        "state": "TRIP",
        "stay": None,
        "trip": {"since": since, "distanceMeters": distance, "movementType": "CAR"},
    }


def _gap(since: str) -> dict:
    return {
        **STATUS_STAY,
        "state": "DATA_GAP",
        "stay": None,
        "dataGap": {"since": since, "durationSeconds": 600},
    }


def _state(state: str) -> dict:
    return {**STATUS_STAY, "state": state, "stay": None}


def _stored(
    hass_storage: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    seen: datetime | None = None,
    success: datetime | None = None,
    geofence_id: int | None = 100,
) -> None:
    seen = seen or NOW - timedelta(minutes=1)
    hass_storage[STORE_KEY] = {
        "version": 1,
        "key": STORE_KEY,
        "data": {
            "previous": previous,
            "previous_seen": seen.isoformat(),
            "status_success": (success or seen).isoformat(),
            "geofence_id": geofence_id,
            "geofence_success": (success or seen).isoformat(),
        },
    }


HOME_SINCE_7 = {
    "kind": "STAY",
    "start": _at("07:00"),
    "place_name": "Home",
    "favorite_id": 7,
}


def _requests(aioclient_mock: AiohttpClientMocker, url: str) -> list[dict[str, str]]:
    return [
        dict(request_url.query)
        for method, request_url, _data, _headers in aioclient_mock.mock_calls
        if method.lower() == "get" and str(request_url).split("?")[0] == url
    ]


def _types(bus: list) -> list[str]:
    return [event.data["type"] for event in bus]


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = make_entry()
    await setup_entry(hass, entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _new_status(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    status: dict[str, Any],
    **mocks: Any,
) -> None:
    aioclient_mock.clear_requests()
    mock_geopulse(aioclient_mock, status=status, **mocks)
    await entry.runtime_data.status.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture(autouse=True)
def frozen(freezer: FrozenDateTimeFactory) -> None:
    """Run every test at 09:00 UTC."""
    freezer.move_to(NOW)


async def test_first_run_does_not_fire(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """Without stored state, the status and the newest geofence event are stored."""
    mock_geopulse(aioclient_mock, status=_stay(_at("07:00")))
    bus = async_capture_events(hass, BUS_EVENT)
    await _setup(hass)

    assert bus == []
    assert hass.states.get(EVENT_ENTITY).state == "unknown"
    assert _requests(aioclient_mock, GEOFENCE_URL) == [{"size": "1"}]
    assert _requests(aioclient_mock, TIMELINE_URL) == []
    stored = hass_storage[STORE_KEY]["data"]
    assert stored["geofence_id"] == 100
    assert stored["previous"]["kind"] == "STAY"
    assert stored["previous"]["place_name"] == "Home"


async def test_items_in_between_are_filled_in(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """Stays, trips and finished data gaps between two polls fire in order."""
    _stored(hass_storage, previous=HOME_SINCE_7)
    timeline = {
        "stays": [
            {"timestamp": _at("07:01"), "locationName": "Home", "favoriteId": 7},
            {"timestamp": _at("08:20"), "locationName": "Bakery"},
            {"timestamp": _at("08:48"), "locationName": "Gym", "favoriteId": 9},
        ],
        "trips": [
            {
                "timestamp": _at("08:10"),
                "distanceMeters": 1500,
                "movementType": "WALK",
            },
            {"timestamp": _at("08:40"), "distanceMeters": 3912, "movementType": "CAR"},
        ],
        "dataGaps": [
            {"startTime": _at("08:25"), "endTime": _at("08:35"), "ongoing": False},
            # Ongoing gaps are the current state, which the status reports.
            {"startTime": _at("08:45"), "endTime": None, "ongoing": True},
        ],
    }
    mock_geopulse(
        aioclient_mock, status=_stay(_at("08:48"), "Gym", 9), timeline=timeline
    )
    bus = async_capture_events(hass, BUS_EVENT)
    entry = await _setup(hass)

    assert _requests(aioclient_mock, TIMELINE_URL) == [
        {"from": "2026-10-14T07:00:00Z", "to": "2026-10-14T09:00:00Z"}
    ]
    assert [(e.data["type"], e.data["occurred_at"][11:16]) for e in bus] == [
        ("stay_ended", "08:10"),
        ("trip_started", "08:10"),
        ("trip_ended", "08:20"),
        ("stay_started", "08:20"),
        ("stay_ended", "08:25"),
        ("tracking_lost", "08:25"),
        ("tracking_resumed", "08:35"),
        ("trip_started", "08:40"),
        ("trip_ended", "08:48"),
        ("stay_started", "08:48"),
    ]
    home_ended = bus[0].data
    assert home_ended["place_name"] == "Home"
    # The timeline's start (07:01) wins over the status poll's (07:00).
    assert home_ended["started_at"] == _at("07:01")
    assert home_ended["duration_seconds"] == 69 * 60
    assert bus[4].data["place_name"] == "Bakery"
    assert bus[6].data["duration_seconds"] == 600
    car = bus[8].data
    assert car["distance_meters"] == 3912
    assert car["movement_type"] == "CAR"
    assert not any(event.data["stale"] for event in bus)
    assert bus[0].data["entry_id"] == entry.entry_id
    assert bus[0].data["user_id"] == USER_ID
    assert bus[0].data["entity_id"] == EVENT_ENTITY
    assert bus[0].data["device_id"] == next(
        device.id
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        if (DOMAIN, USER_ID) in device.identifiers
    )
    state = hass.states.get(EVENT_ENTITY)
    assert state.attributes["event_type"] == "stay_started"
    assert state.attributes["place_name"] == "Gym"
    assert state.attributes["occurred_at"] == _at("08:48")
    assert hass_storage[STORE_KEY]["data"]["previous"]["place_name"] == "Gym"


async def test_same_item_fires_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """A start shifted by less than 2 minutes at the same place is the same stay."""
    _stored(hass_storage, previous=HOME_SINCE_7)
    mock_geopulse(aioclient_mock, status=_stay(_at("07:01")))
    bus = async_capture_events(hass, BUS_EVENT)
    await _setup(hass)

    assert bus == []
    assert _requests(aioclient_mock, TIMELINE_URL) == []


async def test_rebuilt_history_fires_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """An item starting earlier than the stored one means the timeline was rebuilt."""
    _stored(hass_storage, previous=HOME_SINCE_7)
    mock_geopulse(aioclient_mock, status=_trip(_at("06:00")))
    bus = async_capture_events(hass, BUS_EVENT)
    await _setup(hass)

    assert bus == []
    assert _requests(aioclient_mock, TIMELINE_URL) == []
    assert hass_storage[STORE_KEY]["data"]["previous"]["kind"] == "TRIP"


async def test_unknown_and_no_data_are_ignored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """UNKNOWN and NO_DATA never fire and never replace the stored status."""
    _stored(hass_storage, previous=HOME_SINCE_7)
    mock_geopulse(aioclient_mock, status=_state("UNKNOWN"))
    bus = async_capture_events(hass, BUS_EVENT)
    entry = await _setup(hass)
    assert hass_storage[STORE_KEY]["data"]["previous"]["place_name"] == "Home"

    # Back to the same stay after the timeline was regenerated.
    await _new_status(hass, aioclient_mock, entry, _stay(_at("07:00")))
    assert bus == []
    assert _requests(aioclient_mock, TIMELINE_URL) == []


async def test_no_data_then_first_real_state_is_a_first_run(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """Going from no stored state to a real state fires nothing."""
    mock_geopulse(aioclient_mock, status=_state("NO_DATA"))
    bus = async_capture_events(hass, BUS_EVENT)
    entry = await _setup(hass)
    assert hass_storage[STORE_KEY]["data"]["previous"] is None

    await _new_status(hass, aioclient_mock, entry, _trip(_at("08:50")))
    assert bus == []
    assert hass_storage[STORE_KEY]["data"]["previous"]["kind"] == "TRIP"


async def test_data_gap_status(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """A DATA_GAP status starts with tracking_lost and ends with tracking_resumed."""
    _stored(hass_storage, previous=HOME_SINCE_7)
    mock_geopulse(aioclient_mock, status=_gap(_at("08:30")))
    bus = async_capture_events(hass, BUS_EVENT)
    entry = await _setup(hass)

    assert _types(bus) == ["stay_ended", "tracking_lost"]
    assert bus[1].data["last_point_at"] == _at("08:30")

    timeline = {
        "stays": [{"timestamp": _at("08:55"), "locationName": "Home"}],
        "dataGaps": [
            {"startTime": _at("08:30"), "endTime": _at("08:52"), "ongoing": False}
        ],
    }
    await _new_status(
        hass, aioclient_mock, entry, _stay(_at("08:55")), timeline=timeline
    )
    assert _types(bus)[2:] == ["tracking_resumed", "stay_started"]
    resumed = bus[2].data
    assert resumed["lost_at"] == _at("08:30")
    assert resumed["resumed_at"] == _at("08:52")
    assert resumed["duration_seconds"] == 22 * 60


async def test_catch_up_limits(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """After more than 24 hours, only <previous>_ended and <current>_started fire."""
    _stored(
        hass_storage,
        previous={**HOME_SINCE_7, "start": "2026-10-12T07:00:00+00:00"},
        seen=NOW - timedelta(hours=25),
    )
    mock_geopulse(aioclient_mock, status=_trip(_at("08:50")))
    bus = async_capture_events(hass, BUS_EVENT)
    await _setup(hass)

    assert _requests(aioclient_mock, TIMELINE_URL) == []
    assert _types(bus) == ["stay_ended", "trip_started"]
    # Both happened after the last successful poll, 10 minutes ago.
    assert [event.data["stale"] for event in bus] == [False, False]


async def test_too_many_items_in_between(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """More than 20 items in between are skipped."""
    _stored(hass_storage, previous=HOME_SINCE_7)
    start = datetime.fromisoformat(_at("07:30"))
    trips = [
        {"timestamp": (start + timedelta(minutes=2 * index)).isoformat()}
        for index in range(21)
    ]
    mock_geopulse(aioclient_mock, status=_trip(_at("08:50")), timeline={"trips": trips})
    bus = async_capture_events(hass, BUS_EVENT)
    await _setup(hass)

    assert _types(bus) == ["stay_ended", "trip_started"]


async def test_staleness(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """Old events and events from before an outage only fire the bus event."""
    _stored(
        hass_storage,
        previous=HOME_SINCE_7,
        seen=NOW - timedelta(minutes=50),
        success=NOW - timedelta(minutes=20),
    )
    timeline = {
        "trips": [{"timestamp": _at("07:50")}, {"timestamp": _at("08:45")}],
        "stays": [{"timestamp": _at("08:20"), "locationName": "Office"}],
    }
    mock_geopulse(aioclient_mock, status=_trip(_at("08:45")), timeline=timeline)
    bus = async_capture_events(hass, BUS_EVENT)
    await _setup(hass)

    assert [(e.data["type"], e.data["stale"]) for e in bus] == [
        ("stay_ended", True),  # 70 minutes ago
        ("trip_started", True),
        ("trip_ended", True),  # 40 minutes ago, but before the outage ended
        ("stay_started", True),
        ("stay_ended", False),  # after the last successful poll
        ("trip_started", False),
    ]
    state = hass.states.get(EVENT_ENTITY)
    assert state.attributes["event_type"] == "trip_started"


async def test_timeline_failure_retries_next_poll(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """When /timeline fails, nothing fires and the next poll catches up."""
    _stored(hass_storage, previous=HOME_SINCE_7)
    aioclient_mock.get(TIMELINE_URL, status=500)
    mock_geopulse(aioclient_mock, status=_trip(_at("08:50")))
    bus = async_capture_events(hass, BUS_EVENT)
    entry = await _setup(hass)
    assert bus == []
    assert entry.runtime_data.events.status_error
    assert hass_storage[STORE_KEY]["data"]["previous"]["kind"] == "STAY"

    await _new_status(hass, aioclient_mock, entry, _trip(_at("08:50")))
    assert _types(bus) == ["stay_ended", "trip_started"]


async def test_geofence_events(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New geofence events fire oldest first, paging after the stored id."""
    monkeypatch.setattr(events_module, "GEOFENCE_PAGE_SIZE", 2)
    _stored(hass_storage, previous=HOME_SINCE_7, geofence_id=100)

    def _event(event_id: int, kind: str, subject: str, name: str) -> dict:
        return {
            "id": event_id,
            "eventType": kind,
            "occurredAt": _at("08:58"),
            "ruleId": 3,
            "ruleName": "Office",
            "subjectUserId": subject,
            "subjectDisplayName": name,
            "pointLat": 49.84,
            "pointLon": 24.03,
        }

    aioclient_mock.get(
        GEOFENCE_URL,
        params={"afterId": "100", "size": "2"},
        json={
            "items": [
                _event(101, "ENTER", USER_ID, "Alice"),
                _event(102, "LEAVE", USER_ID, "Alice"),
            ],
            "totalElements": 3,
        },
    )
    aioclient_mock.get(
        GEOFENCE_URL,
        params={"afterId": "102", "size": "2"},
        json={"items": [_event(103, "ENTER", "friend", "Bob")], "totalElements": 1},
    )
    aioclient_mock.get(
        GEOFENCE_URL,
        params={"afterId": "103", "size": "2"},
        json={"items": [], "totalElements": 0},
    )
    mock_geopulse(aioclient_mock, status=_stay(_at("07:00")))
    bus = async_capture_events(hass, BUS_EVENT)
    await _setup(hass)

    assert [q["afterId"] for q in _requests(aioclient_mock, GEOFENCE_URL)] == [
        "100",
        "102",
    ]
    assert _types(bus) == ["geofence_enter", "geofence_leave", "geofence_enter"]
    first = bus[0].data
    assert first["geofence_name"] == "Office"
    assert first["geofence_id"] == 3
    assert first["subject_is_self"] is True
    assert first["latitude"] == 49.84
    assert first["stale"] is False
    assert bus[2].data["subject_is_self"] is False
    assert bus[2].data["subject_name"] == "Bob"
    state = hass.states.get(EVENT_ENTITY)
    assert state.attributes["event_type"] == "geofence_enter"
    assert state.attributes["subject_name"] == "Bob"
    assert hass_storage[STORE_KEY]["data"]["geofence_id"] == 103

    # The id persists and the next poll continues from it.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _requests(aioclient_mock, GEOFENCE_URL)[-1]["afterId"] == "103"


async def test_geofence_first_run_without_events(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """With no geofence events yet, 0 is stored so the first real one fires."""
    mock_geopulse(aioclient_mock, geofence_events={"items": [], "totalElements": 0})
    await _setup(hass)
    assert hass_storage[STORE_KEY]["data"]["geofence_id"] == 0


async def test_geofence_first_run_failure_stores_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """A failed first call must not store 0, or the whole history would replay."""
    aioclient_mock.get(GEOFENCE_URL, status=500)
    mock_geopulse(aioclient_mock)
    await _setup(hass)
    assert hass_storage[STORE_KEY]["data"]["geofence_id"] is None


async def test_friend_stay_and_trip_events(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
) -> None:
    """A friend's activity change fires bus events about them, not the entity."""
    mock_geopulse(aioclient_mock)
    bus = async_capture_events(hass, BUS_EVENT)
    entry = await _setup(hass)
    # First sighting: Bob's stay at Café Central since 08:48 is only stored.
    assert bus == []
    stored = hass_storage[STORE_KEY]["data"]["friends"][FRIEND["friendId"]]
    assert stored["start"] == _at("08:48")

    async def _friends(friend: dict[str, Any]) -> None:
        aioclient_mock.clear_requests()
        mock_geopulse(aioclient_mock, friends=[friend])
        await entry.runtime_data.friends.async_refresh()
        await hass.async_block_till_done()

    # Still the same stay, seen later.
    await _friends(
        {
            **FRIEND,
            "lastSeen": "2026-10-14T08:59:00Z",
            "latestActivityDurationSeconds": 660,
        }
    )
    assert bus == []

    on_trip = {
        **FRIEND,
        "lastSeen": "2026-10-14T08:59:30Z",
        "latestActivityType": "TRIP",
        "latestActivityDurationSeconds": 120,
    }
    await _friends(on_trip)
    assert [(e.data["type"], e.data["occurred_at"]) for e in bus] == [
        ("stay_ended", "2026-10-14T08:57:30+00:00"),
        ("trip_started", "2026-10-14T08:57:30+00:00"),
    ]
    ended = bus[0].data
    assert ended["place_name"] == "Café Central"
    assert ended["duration_seconds"] == 570
    assert ended["subject_name"] == "Bob"
    assert ended["subject_user_id"] == FRIEND["friendId"]
    assert ended["subject_is_self"] is False
    assert ended["stale"] is False
    assert ended["entity_id"] == "sensor.bob_activity"
    friend_device = (DOMAIN, f"{USER_ID}_friend_{FRIEND['friendId']}")
    assert ended["device_id"] == next(
        device.id
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        if friend_device in device.identifiers
    )
    # Friends' events don't trigger the account's event entity.
    assert hass.states.get(EVENT_ENTITY).state == "unknown"

    # An unknown activity keeps the stored trip.
    await _friends({**on_trip, "latestActivityType": None})
    assert len(bus) == 2
    detector = entry.runtime_data.events
    assert detector.friends_previous[FRIEND["friendId"]].kind == "TRIP"
