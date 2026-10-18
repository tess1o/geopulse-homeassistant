"""Tests for reading backfill history from a real recorder."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.typing import RecorderInstanceContextManager

from custom_components.geopulse_ha.backfill import _history

TRACKER = "device_tracker.pixel_8"


@pytest.fixture
async def mock_recorder_before_hass(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Prepare the recorder database before hass starts."""


async def test_history_includes_attribute_changes(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Moving while the state stays `not_home` is part of the history."""
    start = dt_util.utcnow()
    for latitude in (49.1, 49.2, 49.3):
        hass.states.async_set(
            TRACKER, "not_home", {"latitude": latitude, "longitude": 24.0}
        )
        await async_wait_recording_done(hass)
    states = await get_instance(hass).async_add_executor_job(
        _history, hass, TRACKER, start, dt_util.utcnow() + timedelta(seconds=1)
    )
    assert [state.attributes["latitude"] for state in states] == [49.1, 49.2, 49.3]
