"""Send recorder history to GeoPulse."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError

from .api import (
    GeoPulseApi,
    GeoPulseAuthError,
    GeoPulseError,
    GeoPulseForbiddenError,
)
from .const import BACKFILL_RETRIES, BATCH_SIZE, DOMAIN
from .helpers import from_geopulse
from .models import IngestSummary
from .sender import attribute_battery, state_to_point

_LOGGER = logging.getLogger(__name__)


def _history(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[State]:
    """Read state changes from the recorder (runs in the recorder executor)."""
    from homeassistant.components.recorder import history

    states = history.state_changes_during_period(
        hass,
        start,
        end,
        entity_id=entity_id,
        no_attributes=False,
        include_start_time_state=False,
    )
    return list(states.get(entity_id, []))


async def async_load_points(
    hass: HomeAssistant,
    entity_id: str,
    start: datetime,
    end: datetime,
    *,
    send_battery: bool,
) -> list[dict[str, Any]]:
    """Map the recorded history of a location entity to GeoPulse points."""
    if "recorder" not in hass.config.components:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="recorder_not_available"
        )
    from homeassistant.components.recorder import get_instance

    states = await get_instance(hass).async_add_executor_job(
        _history, hass, entity_id, start, end
    )
    points: list[dict[str, Any]] = []
    for state in states:
        if from_geopulse(hass, state):
            continue
        battery = attribute_battery(state) if send_battery else None
        point = state_to_point(state, battery=battery, use_last_reported=False)
        if point is not None:
            points.append(point)
    return points


async def async_send_points(
    api: GeoPulseApi, points: list[dict[str, Any]]
) -> IngestSummary:
    """Send points in batches, retrying transient failures a few times."""
    total = IngestSummary()
    for start in range(0, len(points), BATCH_SIZE):
        batch = points[start : start + BATCH_SIZE]
        for attempt, delay in enumerate((*BACKFILL_RETRIES, None)):
            try:
                result = await api.ingest_batch(batch)
                break
            except (GeoPulseAuthError, GeoPulseForbiddenError) as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="backfill_rejected",
                    translation_placeholders={"error": str(err), "sent": str(start)},
                ) from err
            except GeoPulseError as err:
                if delay is None:
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="backfill_failed",
                        translation_placeholders={
                            "error": str(err),
                            "sent": str(start),
                        },
                    ) from err
                _LOGGER.debug(
                    "Backfill batch failed (attempt %s): %s", attempt + 1, err
                )
                await asyncio.sleep(delay)
        total.accepted += result.accepted
        total.saved += result.saved
        total.duplicate += result.duplicate
        total.filtered += result.filtered
    return total
