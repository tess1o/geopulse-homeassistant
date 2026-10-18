"""Forward Home Assistant location entities to GeoPulse."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import math
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_BATTERY_LEVEL,
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
    split_entity_id,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import repairs
from .api import (
    GeoPulseApi,
    GeoPulseAuthError,
    GeoPulseError,
    GeoPulseForbiddenError,
    GeoPulseRequestTooLargeError,
    GeoPulseSourceDisabledError,
    GeoPulseSourceNotConnectedError,
)
from .const import (
    BACKOFF_SECONDS,
    BATCH_SIZE,
    DOMAIN,
    FIX_TIME_MAX_FUTURE,
    FLUSH_DEBOUNCE_SECONDS,
    ISSUE_CANNOT_SEND_LOCATIONS,
    ISSUE_SOURCE_DISABLED,
    ISSUE_SOURCE_NOT_CONNECTED,
    QUEUE_MAX_POINTS,
    SIGNAL_FORWARDER_UPDATED,
    UNLOAD_FLUSH_TIMEOUT,
)
from .helpers import from_geopulse

if TYPE_CHECKING:
    from . import GeoPulseConfigEntry

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
LOCATION_ATTRS = (ATTR_LATITUDE, ATTR_LONGITUDE, ATTR_GPS_ACCURACY, "altitude")
PAUSE_AUTH = "auth"
PAUSE_FORBIDDEN = "forbidden"


def _number(value: Any) -> float | None:
    """Return a finite float, or None for missing or non-numeric values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _battery(value: Any) -> int | None:
    number = _number(value)
    if number is None or not 0 <= number <= 100:
        return None
    return round(number)


def fix_time(state: State, attribute: str | None) -> datetime | None:
    """Time of the tracker's own fix, read from an attribute; None if unusable.

    Accepts a datetime, Unix seconds (or milliseconds) and ISO 8601 text. Times
    without a time zone are local, like iCloud3's `located`.
    """
    if not attribute:
        return None
    value = state.attributes.get(attribute)
    if isinstance(value, datetime):
        moment = value
    elif (number := _number(value)) is not None:
        if number <= 0:
            return None
        if number > 1e11:
            number /= 1000
        try:
            moment = dt_util.utc_from_timestamp(number)
        except OverflowError, OSError, ValueError:
            return None
    elif isinstance(value, str):
        if (moment := dt_util.parse_datetime(value)) is None:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt_util.get_default_time_zone())
    moment = dt_util.as_utc(moment)
    if moment > dt_util.utcnow() + FIX_TIME_MAX_FUTURE:
        return None
    return moment


def state_to_point(
    state: State,
    *,
    battery: int | None = None,
    use_last_reported: bool = True,
    fix_attribute: str | None = None,
) -> dict[str, Any] | None:
    """Map a location state to a GeoPulse point; None if it has no position.

    The timestamp is the fix time from `fix_attribute` when the state has a usable
    one, else when Home Assistant last updated (or confirmed) the state.
    """
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    attrs = state.attributes
    latitude = _number(attrs.get(ATTR_LATITUDE))
    longitude = _number(attrs.get(ATTR_LONGITUDE))
    if latitude is None or longitude is None:
        return None

    timestamp = fix_time(state, fix_attribute)
    if timestamp is None:
        timestamp = state.last_updated
        if use_last_reported and state.last_reported > timestamp:
            timestamp = state.last_reported

    location: dict[str, float] = {"latitude": latitude, "longitude": longitude}
    for key, attr in (
        ("accuracy", ATTR_GPS_ACCURACY),
        ("altitude", "altitude"),
        ("speed", "speed"),
    ):
        value = _number(attrs.get(attr))
        if value is not None:
            location[key] = value

    point: dict[str, Any] = {
        "device_id": split_entity_id(state.entity_id)[1],
        "timestamp": dt_util.as_utc(timestamp).isoformat(),
        "location": location,
    }
    if battery is not None:
        point["battery"] = {"level": battery}
    return point


def attribute_battery(state: State) -> int | None:
    """Battery level from the state's own attributes."""
    return _battery(state.attributes.get(ATTR_BATTERY_LEVEL))


def _location_changed(old: State, new: State, fix_attribute: str | None = None) -> bool:
    # A new fix at the same spot is still a new point.
    attrs = (*LOCATION_ATTRS, fix_attribute) if fix_attribute else LOCATION_ATTRS
    return any(old.attributes.get(attr) != new.attributes.get(attr) for attr in attrs)


class LocationForwarder:
    """Listens to tracker states, queues points persistently and sends them in batches."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GeoPulseConfigEntry,
        api: GeoPulseApi,
        entity_ids: list[str],
        *,
        heartbeat_minutes: int,
        send_battery: bool,
        fix_attribute: str | None = None,
    ) -> None:
        """Initialize the forwarder."""
        self.hass = hass
        self.entry = entry
        self.api = api
        self.entity_ids = list(entity_ids)
        self.heartbeat = (
            timedelta(minutes=heartbeat_minutes) if heartbeat_minutes > 0 else None
        )
        self.send_battery = send_battery
        self.fix_attribute = fix_attribute

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.queue.{entry.entry_id}"
        )
        self.queue: list[dict[str, Any]] = []
        self.last_sent: dict[str, datetime] = {}
        # Latest fix time queued per tracker, when the fix time attribute is used.
        self.last_fix: dict[str, datetime] = {}
        self._last_enqueued: dict[str, datetime] = {}
        self.last_error: str | None = None
        self.paused: str | None = None

        self._unsubs: list[CALLBACK_TYPE] = []
        self._flush_handle: CALLBACK_TYPE | None = None
        self._backoff_index = 0
        self._batch_size = BATCH_SIZE
        self._flush_lock = asyncio.Lock()
        self._stopping = False

    # ---------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        """Load the persisted queue and subscribe to the trackers."""
        stored = await self._store.async_load() or {}
        self.queue = [item for item in stored.get("points", []) if "point" in item]
        for key, target in (("last_sent", self.last_sent), ("last_fix", self.last_fix)):
            for entity_id, value in (stored.get(key) or {}).items():
                if (parsed := dt_util.parse_datetime(value)) is not None:
                    target[entity_id] = parsed

        if self.entity_ids:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, self.entity_ids, self._handle_state_change
                )
            )
            if self.heartbeat:
                self._unsubs.append(
                    async_track_state_report_event(
                        self.hass, self.entity_ids, self._handle_state_report
                    )
                )
        if self.queue:
            self._schedule_flush(FLUSH_DEBOUNCE_SECONDS)

    async def async_stop(self) -> None:
        """Unsubscribe, try a final flush and persist what is left."""
        self._stopping = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_flush()
        if self.queue and self.paused is None:
            try:
                async with asyncio.timeout(UNLOAD_FLUSH_TIMEOUT):
                    await self.async_flush()
            except TimeoutError:
                _LOGGER.debug("Final flush timed out; %s points kept", len(self.queue))
        await self._store.async_save(self._storage_data())

    # ---------------------------------------------------------------- listeners

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if old_state is None or new_state is None or not self.hass.is_running:
            return
        if _location_changed(
            old_state, new_state, self.fix_attribute
        ) or old_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            self._enqueue_state(new_state)
        else:
            # Only non-location attributes changed (battery, …): counts as a report.
            self._maybe_heartbeat(new_state)

    @callback
    def _handle_state_report(self, event: Event[EventStateReportedData]) -> None:
        if self.hass.is_running:
            self._maybe_heartbeat(event.data["new_state"])

    @callback
    def _maybe_heartbeat(self, state: State) -> None:
        if self.heartbeat is None:
            return
        last = self._last_enqueued.get(state.entity_id) or self.last_sent.get(
            state.entity_id
        )
        if last is None or dt_util.utcnow() - last >= self.heartbeat:
            self._enqueue_state(state)

    # ---------------------------------------------------------------- queueing

    def battery_for(self, state: State) -> int | None:
        """Battery from the state, else from a battery sensor on the same device."""
        if not self.send_battery:
            return None
        if (level := attribute_battery(state)) is not None:
            return level
        ent_reg = er.async_get(self.hass)
        entry = ent_reg.async_get(state.entity_id)
        if entry is None or entry.device_id is None:
            return None
        for candidate in er.async_entries_for_device(ent_reg, entry.device_id):
            if candidate.domain != "sensor" or candidate.disabled:
                continue
            device_class = candidate.device_class or candidate.original_device_class
            if device_class != SensorDeviceClass.BATTERY:
                continue
            sensor = self.hass.states.get(candidate.entity_id)
            if sensor is not None and (level := _battery(sensor.state)) is not None:
                return level
        return None

    @callback
    def _enqueue_state(self, state: State) -> None:
        if from_geopulse(self.hass, state):
            return
        fix = fix_time(state, self.fix_attribute)
        last_fix = self.last_fix.get(state.entity_id)
        if fix is not None and last_fix is not None and fix <= last_fix:
            # The same fix again, or an older one the tracker went back to.
            _LOGGER.debug(
                "Skipping %s: fix from %s is not newer than %s",
                state.entity_id,
                fix,
                last_fix,
            )
            return
        point = state_to_point(
            state, battery=self.battery_for(state), fix_attribute=self.fix_attribute
        )
        if point is None:
            return
        if fix is not None:
            self.last_fix[state.entity_id] = fix
        self._last_enqueued[state.entity_id] = dt_util.utcnow()
        self.enqueue([{"entity_id": state.entity_id, "point": point}])

    @callback
    def enqueue(self, items: list[dict[str, Any]]) -> None:
        """Add queue items ({entity_id, point}) and schedule a flush."""
        self.queue.extend(items)
        overflow = len(self.queue) - QUEUE_MAX_POINTS
        if overflow > 0:
            _LOGGER.warning(
                "GeoPulse queue is full; dropping the %s oldest points", overflow
            )
            del self.queue[:overflow]
        self._store.async_delay_save(self._storage_data, 10)
        self._notify()
        # While backing off, leave the scheduled retry alone.
        if self.paused is None and self._backoff_index == 0:
            self._schedule_flush(FLUSH_DEBOUNCE_SECONDS)

    def _storage_data(self) -> dict[str, Any]:
        return {
            "points": self.queue,
            "last_sent": {
                key: value.isoformat() for key, value in self.last_sent.items()
            },
            "last_fix": {
                key: value.isoformat() for key, value in self.last_fix.items()
            },
        }

    def queue_length(self, entity_id: str | None = None) -> int:
        """Number of queued points, optionally for one entity."""
        if entity_id is None:
            return len(self.queue)
        return sum(1 for item in self.queue if item.get("entity_id") == entity_id)

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(
            self.hass, SIGNAL_FORWARDER_UPDATED.format(self.entry.entry_id)
        )

    # ---------------------------------------------------------------- flushing

    @callback
    def _schedule_flush(self, delay: float) -> None:
        if self._stopping:
            return
        self._cancel_flush()
        self._flush_handle = async_call_later(self.hass, delay, self._flush_later)

    @callback
    def _cancel_flush(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle()
            self._flush_handle = None

    async def _flush_later(self, _now: datetime) -> None:
        self._flush_handle = None
        await self.async_flush()

    async def async_flush(self, *, force: bool = False) -> None:
        """Send the queue in batches until it is empty or a send fails."""
        if force:
            self._cancel_flush()
            if self.paused == PAUSE_FORBIDDEN:
                self.paused = None
        if self.paused is not None:
            return
        async with self._flush_lock:
            while self.queue:
                batch = self.queue[: self._batch_size]
                try:
                    await self.api.ingest_batch([item["point"] for item in batch])
                except GeoPulseRequestTooLargeError:
                    if self._batch_size == 1:
                        self._fail("Point rejected as too large")
                        return
                    self._batch_size = max(1, self._batch_size // 2)
                    continue
                except GeoPulseAuthError as err:
                    self.paused = PAUSE_AUTH
                    self.last_error = f"auth: {err}"
                    self._notify()
                    self.entry.async_start_reauth(self.hass)
                    return
                except GeoPulseForbiddenError as err:
                    self._handle_forbidden(err)
                    return
                except GeoPulseError as err:
                    self._fail(str(err))
                    return
                self._delivered(batch)
            self._backoff_index = 0
            self._batch_size = BATCH_SIZE

    def _delivered(self, batch: list[dict[str, Any]]) -> None:
        sent = {id(item) for item in batch}
        self.queue = [item for item in self.queue if id(item) not in sent]
        now = dt_util.utcnow()
        for item in batch:
            if entity_id := item.get("entity_id"):
                self.last_sent[entity_id] = now
        self.last_error = None
        repairs.set_issue(
            self.hass, self.entry, ISSUE_CANNOT_SEND_LOCATIONS, active=False
        )
        repairs.set_issue(self.hass, self.entry, ISSUE_SOURCE_DISABLED, active=False)
        repairs.set_issue(
            self.hass, self.entry, ISSUE_SOURCE_NOT_CONNECTED, active=False
        )
        self._store.async_delay_save(self._storage_data, 10)
        self._notify()

    def _fail(self, message: str) -> None:
        delay = BACKOFF_SECONDS[min(self._backoff_index, len(BACKOFF_SECONDS) - 1)]
        self._backoff_index += 1
        self.last_error = message
        _LOGGER.debug(
            "Sending to GeoPulse failed (%s); retrying in %ss", message, delay
        )
        self._notify()
        self._schedule_flush(delay)

    def _handle_forbidden(self, err: GeoPulseForbiddenError) -> None:
        if isinstance(err, GeoPulseSourceDisabledError):
            issue = ISSUE_SOURCE_DISABLED
        elif isinstance(err, GeoPulseSourceNotConnectedError):
            issue = ISSUE_SOURCE_NOT_CONNECTED
        else:
            issue = ISSUE_CANNOT_SEND_LOCATIONS
        repairs.set_issue(self.hass, self.entry, issue, active=True)
        self.paused = PAUSE_FORBIDDEN
        self.last_error = f"forbidden: {err.code or 'forbidden'}"
        self._notify()
        # Keep the points queued and check again at the longest backoff.
        self._flush_handle = async_call_later(
            self.hass, BACKOFF_SECONDS[-1], self._retry_forbidden
        )

    async def _retry_forbidden(self, _now: datetime) -> None:
        self._flush_handle = None
        await self.async_flush(force=True)
