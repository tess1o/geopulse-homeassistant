"""Tests for the backfill service and diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.geopulse_ha.const import DOMAIN

from .conftest import API, TOKEN, make_entry, mock_geopulse, setup_entry

TRACKER = "device_tracker.pixel_8"
START = datetime(2026, 10, 1, tzinfo=UTC)


def _history(count: int) -> list[State]:
    return [
        State(
            TRACKER,
            "not_home",
            {"latitude": 49 + i / 1000, "longitude": 24.0, "gps_accuracy": 5},
            last_updated=START + timedelta(minutes=i),
        )
        for i in range(count)
    ] + [State(TRACKER, "unavailable", {}, last_updated=START)]


@pytest.fixture
def recorder_loaded(hass: HomeAssistant) -> None:
    """Pretend the recorder is running."""
    hass.config.components.add("recorder")


@pytest.mark.usefixtures("recorder_loaded")
async def test_backfill(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """History is mapped and sent in batches of 500."""
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry(options={"trackers": [TRACKER]}))

    with (
        patch(
            "custom_components.geopulse_ha.backfill.get_instance", create=True
        ) as get_instance,
        patch("homeassistant.components.recorder.get_instance", get_instance),
    ):

        async def run(func, *args):
            return func(*args)

        get_instance.return_value.async_add_executor_job = run
        with patch(
            "custom_components.geopulse_ha.backfill._history",
            return_value=_history(1200),
        ):
            response = await hass.services.async_call(
                DOMAIN,
                "backfill",
                {
                    "entity_id": TRACKER,
                    "start": START,
                    "end": START + timedelta(days=2),
                },
                blocking=True,
                return_response=True,
            )

    batches = [
        data
        for method, url, data, _ in aioclient_mock.mock_calls
        if method.lower() == "post" and str(url).endswith("/batch")
    ]
    assert [len(batch) for batch in batches] == [500, 500, 200]
    assert batches[0][0]["timestamp"] == START.isoformat()
    assert batches[0][0]["location"]["accuracy"] == 5
    assert response == {
        "points": 1200,
        "accepted": 3,
        "saved": 3,
        "duplicate": 0,
        "filtered": 0,
    }


@pytest.mark.usefixtures("recorder_loaded")
async def test_backfill_range_limits(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The range must be positive and at most 31 days."""
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry(options={"trackers": [TRACKER]}))
    for end, key in (
        (START - timedelta(hours=1), "invalid_range"),
        (START + timedelta(days=32), "range_too_long"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN,
                "backfill",
                {"entity_id": TRACKER, "start": START, "end": end},
                blocking=True,
            )
        assert err.value.translation_key == key


async def test_backfill_read_only_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A read-only account cannot receive a backfill."""
    mock_geopulse(aioclient_mock, scopes=["read"])
    await setup_entry(hass, make_entry(scopes=["read"], options={}))
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "backfill",
            {"entity_id": TRACKER, "start": START, "end": START + timedelta(days=1)},
            blocking=True,
        )
    assert err.value.translation_key == "no_sending_entry"


async def test_diagnostics_redaction(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client: ClientSessionGenerator,
) -> None:
    """Diagnostics hide the token, the host, names and precise coordinates."""
    assert await async_setup_component(hass, "diagnostics", {})
    mock_geopulse(aioclient_mock)
    entry = make_entry()
    await setup_entry(hass, entry)

    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = str(diag)
    assert TOKEN not in text
    assert "geopulse.local" not in text
    assert "alice@example.com" not in text
    assert diag["entry"]["data"]["url"] == "http://**REDACTED**:8080"
    assert diag["status"]["stay"]["latitude"] == 49.84
    assert diag["status"]["stay"]["longitude"] == 24.03
    assert diag["server"]["version"]["current_version"] == "2.0.0"
    assert diag["scopes"] == ["read", "gps:ingest"]
    assert diag["events"]["geofence_event_id"] == 100
    assert diag["events"]["previous_status"]["place_name"] == "Home"
    assert diag["forwarder"]["queue_length"] == 0
    assert diag["coordinators"]["status"]["last_update_success"] is True


async def test_url_path_is_ignored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """API calls go to /api/v1 at the server root."""
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry())
    assert all(str(call[1]).startswith(API) for call in aioclient_mock.mock_calls)


@pytest.mark.usefixtures("recorder_loaded")
async def test_backfill_geopulse_tracker(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A GeoPulse tracker's history is already in GeoPulse."""
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry())
    tracker = "device_tracker.geopulse_alice_location"
    assert er.async_get(hass).async_get(tracker) is not None
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "backfill",
            {"entity_id": tracker, "start": START, "end": START + timedelta(days=1)},
            blocking=True,
        )
    assert err.value.translation_key == "geopulse_entity"


@pytest.mark.usefixtures("recorder_loaded")
async def test_backfill_skips_person_states_from_geopulse(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A person's history leaves out the states it took from a GeoPulse tracker."""
    mock_geopulse(aioclient_mock)
    await setup_entry(hass, make_entry())
    history = [
        State(
            "person.alice",
            "not_home",
            {"latitude": 49.1, "longitude": 24.0, "source": TRACKER},
            last_updated=START,
        ),
        State(
            "person.alice",
            "not_home",
            {
                "latitude": 49.2,
                "longitude": 24.0,
                "source": "device_tracker.geopulse_alice_location",
            },
            last_updated=START + timedelta(minutes=1),
        ),
    ]
    with (
        patch(
            "custom_components.geopulse_ha.backfill.get_instance", create=True
        ) as get_instance,
        patch("homeassistant.components.recorder.get_instance", get_instance),
        patch("custom_components.geopulse_ha.backfill._history", return_value=history),
    ):

        async def run(func, *args):
            return func(*args)

        get_instance.return_value.async_add_executor_job = run
        response = await hass.services.async_call(
            DOMAIN,
            "backfill",
            {
                "entity_id": "person.alice",
                "start": START,
                "end": START + timedelta(days=1),
            },
            blocking=True,
            return_response=True,
        )
    assert response["points"] == 1
