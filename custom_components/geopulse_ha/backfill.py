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
from .sender import attribute_battery, fix_time, state_to_point

_LOGGER = logging.getLogger(__name__)


def _history(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[State]:
    """Read recorded states from the recorder (runs in the recorder executor).

    Includes attribute-only updates: a tracker that moves while staying `not_home`
    only changes its coordinates.
    """
    from homeassistant.components.recorder import history

    states = history.get_significant_states(
        hass,
        start,
        end,
        entity_ids=[entity_id],
        include_start_time_state=False,
        significant_changes_only=False,
        no_attributes=False,
    )
    return list(states.get(entity_id, []))


async def async_load_points(
    hass: HomeAssistant,
    entity_id: str,
    start: datetime,
    end: datetime,
    *,
    send_battery: bool,
    fix_attribute: str | None = None,
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
    last_fix: datetime | None = None
    for state in states:
        if from_geopulse(hass, state):
            continue
        fix = fix_time(state, fix_attribute)
        if fix is not None and last_fix is not None and fix <= last_fix:
            continue  # same rule as live forwarding: only newer fixes
        battery = attribute_battery(state) if send_battery else None
        point = state_to_point(
            state,
            battery=battery,
            use_last_reported=False,
            fix_attribute=fix_attribute,
        )
        if point is not None:
            points.append(point)
            if fix is not None:
                last_fix = fix
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
