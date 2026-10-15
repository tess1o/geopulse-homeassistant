"""Derive GeoPulse events and dispatch them to Home Assistant.

GeoPulse has no events feed. Stays, trips and tracking lost/resumed come from
comparing successive `/users/me/status` polls, with `/timeline` filling in the items
in between. Geofence enter/leave come from polling `/geofences/events`. Friends' stays
and trips come from comparing successive `/friends` polls, without filling in.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import GeoPulseApi, GeoPulseAuthError, GeoPulseError
from .const import (
    BUS_EVENT,
    CATCH_UP_MAX_AGE,
    CATCH_UP_MAX_ITEMS,
    DOMAIN,
    EVENT_STALE_AFTER,
    GEOFENCE_PAGE_SIZE,
    OUTAGE_AFTER,
    SAME_ITEM_TOLERANCE,
    SIGNAL_ACTIVITY_EVENT,
)
from .helpers import account_identifier, friend_identifier
from .models import CurrentStatus, Friend, GeofenceEvent, TimelineItem

if TYPE_CHECKING:
    from . import GeoPulseConfigEntry
    from .coordinator import FriendsCoordinator, StatusCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
MAX_GEOFENCE_PAGES_PER_POLL = 50

STARTED = {"STAY": "stay_started", "TRIP": "trip_started", "DATA_GAP": "tracking_lost"}
ENDED = {"STAY": "stay_ended", "TRIP": "trip_ended", "DATA_GAP": "tracking_resumed"}
GEOFENCE_TYPES = {"ENTER": "geofence_enter", "LEAVE": "geofence_leave"}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def status_item(status: CurrentStatus | None) -> TimelineItem | None:
    """The current stay, trip or data gap; None for `UNKNOWN`/`NO_DATA`."""
    if status is None:
        return None
    if status.state == "STAY" and status.stay and status.stay.since:
        stay = status.stay
        return TimelineItem(
            kind="STAY",
            start=stay.since,
            place_name=stay.place_name,
            favorite_id=stay.favorite_id,
            location_source=stay.location_source,
            city=stay.city,
            country=stay.country,
            latitude=stay.latitude,
            longitude=stay.longitude,
        )
    if status.state == "TRIP" and status.trip and status.trip.since:
        trip = status.trip
        return TimelineItem(
            kind="TRIP",
            start=trip.since,
            distance_meters=trip.distance_meters,
            movement_type=trip.movement_type,
        )
    if status.state == "DATA_GAP" and status.data_gap and status.data_gap.since:
        return TimelineItem(kind="DATA_GAP", start=status.data_gap.since)
    return None


def friend_item(friend: Friend, now: datetime) -> TimelineItem | None:
    """A friend's current stay or trip; None while GeoPulse doesn't know it.

    GeoPulse counts the duration up to the friend's last point, so the start is
    `lastSeen` minus the duration.
    """
    kind = (friend.activity or "").upper()
    if kind not in ("STAY", "TRIP") or friend.activity_duration_seconds is None:
        return None
    start = (friend.last_seen or now) - timedelta(
        seconds=friend.activity_duration_seconds
    )
    return TimelineItem(
        kind=kind,
        start=start,
        place_name=friend.last_location if kind == "STAY" else None,
    )


def _same_start(a: TimelineItem, b: TimelineItem) -> bool:
    return a.kind == b.kind and abs(a.start - b.start) <= SAME_ITEM_TOLERANCE


def _same_place(a: TimelineItem, b: TimelineItem) -> bool:
    if a.favorite_id is not None or b.favorite_id is not None:
        return a.favorite_id == b.favorite_id
    return a.place_name == b.place_name


def same_item(a: TimelineItem, b: TimelineItem) -> bool:
    """Same kind, start within the tolerance and, for stays, the same place."""
    return _same_start(a, b) and (a.kind != "STAY" or _same_place(a, b))


def _merge(base: TimelineItem, update: TimelineItem) -> TimelineItem:
    """`update`'s values, falling back to `base` where `update` has none."""
    return replace(
        base,
        **{
            field.name: getattr(update, field.name)
            for field in fields(update)
            if getattr(update, field.name) is not None
        },
    )


def _item_to_store(item: TimelineItem) -> dict[str, Any]:
    data = asdict(item)
    data["start"] = _iso(item.start)
    data["end"] = _iso(item.end)
    return data


def _item_from_store(data: Any) -> TimelineItem | None:
    if not isinstance(data, dict):
        return None
    start = _parse(data.get("start"))
    if start is None or data.get("kind") not in STARTED:
        return None
    known = {field.name for field in fields(TimelineItem)}
    values = {key: value for key, value in data.items() if key in known}
    values["start"] = start
    values["end"] = _parse(data.get("end"))
    return TimelineItem(**values)


def item_details(item: TimelineItem, end: datetime | None) -> dict[str, Any]:
    """Snake_case event data for a stay, trip or data gap."""
    data: dict[str, Any] = {"started_at": _iso(item.start)}
    if end is not None:
        data["ended_at"] = _iso(end)
        data["duration_seconds"] = max(0, int((end - item.start).total_seconds()))
    if item.kind == "STAY":
        data.update(
            place_name=item.place_name,
            favorite_id=item.favorite_id,
            location_source=item.location_source,
            city=item.city,
            country=item.country,
            latitude=item.latitude,
            longitude=item.longitude,
        )
    elif item.kind == "TRIP":
        data["movement_type"] = item.movement_type
        if end is not None:
            data["distance_meters"] = item.distance_meters
    else:
        data["last_point_at"] = data["lost_at"] = _iso(item.start)
        if end is not None:
            data["resumed_at"] = _iso(end)
    return {key: value for key, value in data.items() if value is not None}


def build_sequence(
    previous: TimelineItem,
    between: list[TimelineItem],
    current: TimelineItem,
) -> list[tuple[str, datetime, dict[str, Any]]]:
    """Events from the end of `previous` to the start of `current`, in order.

    An item ends where the next one starts; a data gap ends at its `end` when known.
    """
    events: list[tuple[str, datetime, dict[str, Any]]] = []
    chain = [previous, *between, current]
    for index, item in enumerate(chain):
        if index > 0:
            events.append((STARTED[item.kind], item.start, item_details(item, None)))
        if index < len(chain) - 1:
            end = item.end or chain[index + 1].start
            events.append((ENDED[item.kind], end, item_details(item, end)))
    return events


class EventDetector:
    """Derives activity events, fires bus events and feeds the event entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GeoPulseConfigEntry,
        api: GeoPulseApi,
        user_id: str,
        *,
        status: StatusCoordinator,
        geofence_interval: timedelta,
        friends: FriendsCoordinator | None = None,
    ) -> None:
        """Initialize the detector."""
        self.hass = hass
        self.entry = entry
        self.api = api
        self.user_id = user_id
        self.status = status
        self.friends = friends
        self.geofence_interval = geofence_interval
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.events.{entry.entry_id}"
        )
        # Last real status item (stay, trip or data gap) and when it was last seen.
        self.previous: TimelineItem | None = None
        self.previous_seen: datetime | None = None
        self.status_success: datetime | None = None
        self.geofence_id: int | None = None
        self.geofence_success: datetime | None = None
        # Each friend's last known stay or trip, by friend id.
        self.friends_previous: dict[str, TimelineItem] = {}
        self.friends_success: datetime | None = None
        self.status_error: str | None = None
        self.geofence_error: str | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._status_lock = asyncio.Lock()
        self._geofence_lock = asyncio.Lock()

    async def async_start(self) -> None:
        """Load the stored state and start detecting."""
        stored = await self._store.async_load() or {}
        self.previous = _item_from_store(stored.get("previous"))
        self.previous_seen = _parse(stored.get("previous_seen"))
        self.status_success = _parse(stored.get("status_success"))
        geofence_id = stored.get("geofence_id")
        self.geofence_id = geofence_id if isinstance(geofence_id, int) else None
        self.geofence_success = _parse(stored.get("geofence_success"))
        friends = stored.get("friends")
        for friend_id, data in (friends if isinstance(friends, dict) else {}).items():
            if item := _item_from_store(data):
                self.friends_previous[friend_id] = item
        self.friends_success = _parse(stored.get("friends_success"))

        self._unsubs.append(self.status.async_add_listener(self._status_updated))
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._poll_interval,
                self.geofence_interval,
                name=f"{DOMAIN} geofence events {self.entry.entry_id}",
                cancel_on_shutdown=True,
            )
        )
        # The coordinators already refreshed once during setup.
        self._status_updated()
        if self.friends is not None:
            self._unsubs.append(self.friends.async_add_listener(self._friends_updated))
            self._friends_updated()
        self.entry.async_create_background_task(
            self.hass, self.async_poll_geofences(), f"{DOMAIN} first geofence poll"
        )

    async def async_stop(self) -> None:
        """Stop detecting and persist the state."""
        while self._unsubs:
            self._unsubs.pop()()
        await self._save()

    async def _save(self) -> None:
        await self._store.async_save(self._storage_data())

    @callback
    def _storage_data(self) -> dict[str, Any]:
        return {
            "previous": _item_to_store(self.previous) if self.previous else None,
            "previous_seen": _iso(self.previous_seen),
            "status_success": _iso(self.status_success),
            "geofence_id": self.geofence_id,
            "geofence_success": _iso(self.geofence_success),
            "friends": {
                friend_id: _item_to_store(item)
                for friend_id, item in self.friends_previous.items()
            },
            "friends_success": _iso(self.friends_success),
        }

    # Stays, trips and tracking lost/resumed

    @callback
    def _status_updated(self) -> None:
        if not self.status.last_update_success or self.status.data is None:
            return
        self.entry.async_create_background_task(
            self.hass, self.async_process_status(), f"{DOMAIN} status events"
        )

    async def async_process_status(self) -> None:
        """Compare the latest status with the stored one and fire what changed."""
        async with self._status_lock:
            now = dt_util.utcnow()
            current = status_item(self.status.data)
            try:
                if current is not None:
                    await self._detect(current, now)
            except GeoPulseAuthError:
                # Keep the stored status; the next poll retries the catch-up.
                self.status_error = "auth"
                self.entry.async_start_reauth(self.hass)
                return
            except GeoPulseError as err:
                self.status_error = str(err)
                _LOGGER.debug("Fetching the GeoPulse timeline failed: %s", err)
                return
            self.status_success = now
            self.status_error = None
            await self._save()

    async def _detect(self, current: TimelineItem, now: datetime) -> None:
        previous, seen = self.previous, self.previous_seen
        if previous is not None and same_item(previous, current):
            # Keep the details current (a trip's distance grows, places get names).
            self.previous = _merge(previous, current)
            self.previous_seen = now
            return
        # First run, or rebuilt history (import, timeline edit): fire nothing.
        events = (
            await self._catch_up(previous, seen, current, now)
            if previous is not None and current.start >= previous.start
            else []
        )
        # Only store the new status once the catch-up succeeded, so a failed
        # timeline call is retried on the next poll.
        self.previous, self.previous_seen = current, now
        for event_type, occurred_at, data in events:
            self._fire(event_type, occurred_at, data, now, self.status_success)

    async def _catch_up(
        self,
        previous: TimelineItem,
        seen: datetime | None,
        current: TimelineItem,
        now: datetime,
    ) -> list[tuple[str, datetime, dict[str, Any]]]:
        """Events from `previous` to `current`, filled in from `/timeline`."""
        between: list[TimelineItem] = []
        if seen is not None and now - seen <= CATCH_UP_MAX_AGE:
            timeline = await self.api.timeline(previous.start, now)
            for item in timeline.items:
                if _same_start(item, previous):
                    # The timeline has the final values (distance, gap end).
                    previous = _merge(previous, item)
                elif (
                    not item.ongoing
                    and previous.start < item.start < current.start
                    and not _same_start(item, current)
                ):
                    between.append(item)
            if len(between) > CATCH_UP_MAX_ITEMS:
                between = []
        return build_sequence(previous, between, current)

    # Friends' stays and trips

    @callback
    def _friends_updated(self) -> None:
        if self.friends is None or not self.friends.last_update_success:
            return
        now = dt_util.utcnow()
        friends = self.friends.data or {}
        for friend in friends.values():
            self._detect_friend(friend, now)
        # Forget friends who were removed.
        for friend_id in set(self.friends_previous) - set(friends):
            del self.friends_previous[friend_id]
        self.friends_success = now
        self._store.async_delay_save(self._storage_data, 5)

    @callback
    def _detect_friend(self, friend: Friend, now: datetime) -> None:
        current = friend_item(friend, now)
        if current is None:
            # Activity unknown: keep the last known stay or trip.
            return
        previous = self.friends_previous.get(friend.friend_id)
        self.friends_previous[friend.friend_id] = current
        # First sighting, the same stay or trip, or rebuilt history: fire nothing.
        if (
            previous is None
            or _same_start(previous, current)
            or current.start < previous.start
        ):
            return
        subject = {
            "subject_user_id": friend.friend_id,
            "subject_name": friend.full_name,
            "subject_is_self": False,
        }
        targets = self._friend_targets(friend.friend_id)
        for event_type, occurred_at, data in build_sequence(previous, [], current):
            self._fire(
                event_type,
                occurred_at,
                {**data, **subject},
                now,
                self.friends_success,
                targets=targets,
            )

    @callback
    def _friend_targets(self, friend_id: str) -> dict[str, str]:
        """The friend's activity sensor and device, when registered."""
        targets: dict[str, str] = {}
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{self.user_id}_friend_{friend_id}_activity"
        )
        if entity_id:
            targets["entity_id"] = entity_id
        identifier = friend_identifier(self.user_id, friend_id)
        for device in dr.async_entries_for_config_entry(
            dr.async_get(self.hass), self.entry.entry_id
        ):
            if identifier in device.identifiers:
                targets["device_id"] = device.id
                break
        return targets

    # Geofence enter/leave

    async def _poll_interval(self, _now: datetime) -> None:
        await self.async_poll_geofences()

    async def async_poll_geofences(self) -> None:
        """Fetch new geofence events; on the first run only remember the newest id."""
        if self._geofence_lock.locked():
            return
        async with self._geofence_lock:
            try:
                await self._poll_geofences()
            except GeoPulseAuthError:
                self.geofence_error = "auth"
                self.entry.async_start_reauth(self.hass)
            except GeoPulseError as err:
                self.geofence_error = str(err)
                _LOGGER.debug("Polling GeoPulse geofence events failed: %s", err)
            await self._save()

    async def _poll_geofences(self) -> None:
        now = dt_util.utcnow()
        if self.geofence_id is None:
            page = await self.api.geofence_events(size=1)
            # Ids start at 1, so 0 picks up every event created from now on.
            self.geofence_id = page.items[0].id if page.items else 0
            self.geofence_success = now
            return

        previous_success = self.geofence_success
        for _ in range(MAX_GEOFENCE_PAGES_PER_POLL):
            page = await self.api.geofence_events(
                after_id=self.geofence_id, size=GEOFENCE_PAGE_SIZE
            )
            for event in page.items:
                self._fire_geofence(event, now, previous_success)
                self.geofence_id = max(self.geofence_id, event.id)
            if not page.items or page.total_elements <= len(page.items):
                break
        self.geofence_success = now
        self.geofence_error = None

    @callback
    def _fire_geofence(
        self, event: GeofenceEvent, now: datetime, previous_success: datetime | None
    ) -> None:
        event_type = GEOFENCE_TYPES.get(event.event_type.upper())
        if event_type is None or event.occurred_at is None:
            return
        data = {
            "geofence_id": event.rule_id,
            "geofence_name": event.rule_name,
            "subject_user_id": event.subject_user_id,
            "subject_name": event.subject_name,
            "subject_is_self": event.subject_user_id == self.user_id,
            "latitude": event.latitude,
            "longitude": event.longitude,
        }
        self._fire(
            event_type,
            event.occurred_at,
            {key: value for key, value in data.items() if value is not None},
            now,
            previous_success,
        )

    # Dispatch

    @callback
    def _fire(
        self,
        event_type: str,
        occurred_at: datetime,
        data: dict[str, Any],
        now: datetime,
        previous_success: datetime | None,
        *,
        targets: dict[str, str] | None = None,
    ) -> None:
        """Fire the bus event; account events also trigger the event entity.

        Friends' events (with `targets`) only fire the bus event, so automations on
        the event entity keep reacting to the account owner only.
        """
        stale = now - occurred_at > EVENT_STALE_AFTER or (
            previous_success is not None
            and now - previous_success > OUTAGE_AFTER
            and occurred_at < previous_success
        )
        payload = {
            **data,
            # entity_id and device_id put the event on the event entity's and the
            # account device's Activity, and help automations filter by account.
            **(self._targets() if targets is None else targets),
            "type": event_type,
            "occurred_at": _iso(occurred_at),
            "entry_id": self.entry.entry_id,
            "user_id": self.user_id,
            "stale": stale,
        }
        self.hass.bus.async_fire(BUS_EVENT, payload)
        if not stale and targets is None:
            async_dispatcher_send(
                self.hass,
                SIGNAL_ACTIVITY_EVENT.format(self.entry.entry_id),
                event_type,
                {**data, "occurred_at": payload["occurred_at"]},
            )

    @callback
    def _targets(self) -> dict[str, str]:
        """The account's event entity and device, when registered."""
        targets: dict[str, str] = {}
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "event", DOMAIN, f"{self.user_id}_activity_event"
        )
        if entity_id:
            targets["entity_id"] = entity_id
        identifier = account_identifier(self.user_id)
        for device in dr.async_entries_for_config_entry(
            dr.async_get(self.hass), self.entry.entry_id
        ):
            if identifier in device.identifiers:
                targets["device_id"] = device.id
                break
        return targets
