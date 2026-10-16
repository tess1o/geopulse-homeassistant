"""Typed views of GeoPulse API payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any


def parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp; naive values are treated as UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _int(value: Any) -> int | None:
    number = _float(value)
    return None if number is None else int(number)


@dataclass(slots=True)
class VersionStatus:
    """`GET /system/version/status`."""

    current_version: str | None
    latest_version: str | None
    update_available: bool
    release_url: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> VersionStatus:
        data = data or {}
        return cls(
            current_version=data.get("currentVersion"),
            latest_version=data.get("latestVersion"),
            update_available=bool(data.get("updateAvailable")),
            release_url=data.get("releaseUrl"),
        )


@dataclass(slots=True)
class HealthStatus:
    """`GET /system/health`."""

    status: str | None
    database: str | None

    @property
    def is_up(self) -> bool:
        return self.status == "UP"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HealthStatus:
        data = data or {}
        return cls(status=data.get("status"), database=data.get("database"))


@dataclass(slots=True)
class ApiToken:
    """`GET /api-tokens/current`."""

    id: str
    user_id: str
    user_email: str | None
    name: str | None
    scopes: list[str]
    status: str | None
    expires_at: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApiToken:
        return cls(
            id=str(data["id"]),
            user_id=str(data["userId"]),
            user_email=data.get("userEmail"),
            name=data.get("name"),
            scopes=[str(scope) for scope in data.get("scopes") or []],
            status=data.get("status"),
            expires_at=parse_dt(data.get("expiresAt")),
        )


@dataclass(slots=True)
class GeoPulseUser:
    """`GET /users/me`."""

    user_id: str
    full_name: str | None
    email: str | None
    timezone: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeoPulseUser:
        return cls(
            user_id=str(data["userId"]),
            full_name=data.get("fullName"),
            email=data.get("email"),
            timezone=data.get("timezone"),
        )


@dataclass(slots=True)
class StayStatus:
    """The current stay."""

    place_name: str | None
    favorite_id: int | None
    location_source: str | None
    since: datetime | None
    duration_seconds: int | None
    latitude: float | None
    longitude: float | None
    city: str | None
    country: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StayStatus | None:
        if not data:
            return None
        return cls(
            place_name=data.get("placeName"),
            favorite_id=_int(data.get("favoriteId")),
            location_source=data.get("locationSource"),
            since=parse_dt(data.get("since")),
            duration_seconds=_int(data.get("durationSeconds")),
            latitude=_float(data.get("latitude")),
            longitude=_float(data.get("longitude")),
            city=data.get("city"),
            country=data.get("country"),
        )


@dataclass(slots=True)
class TripStatus:
    """The current trip."""

    since: datetime | None
    duration_seconds: int | None
    distance_meters: int | None
    movement_type: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TripStatus | None:
        if not data:
            return None
        return cls(
            since=parse_dt(data.get("since")),
            duration_seconds=_int(data.get("durationSeconds")),
            distance_meters=_int(data.get("distanceMeters")),
            movement_type=data.get("movementType"),
        )


@dataclass(slots=True)
class DataGapStatus:
    """The current data gap."""

    since: datetime | None
    duration_seconds: int | None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DataGapStatus | None:
        if not data:
            return None
        return cls(
            since=parse_dt(data.get("since")),
            duration_seconds=_int(data.get("durationSeconds")),
        )


@dataclass(slots=True)
class LastPoint:
    """The latest GPS point GeoPulse stored."""

    timestamp: datetime | None
    received_at: datetime | None
    latitude: float | None
    longitude: float | None
    accuracy: float | None
    battery: float | None
    source_type: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> LastPoint | None:
        if not data:
            return None
        return cls(
            timestamp=parse_dt(data.get("timestamp")),
            received_at=parse_dt(data.get("receivedAt")),
            latitude=_float(data.get("latitude")),
            longitude=_float(data.get("longitude")),
            accuracy=_float(data.get("accuracy")),
            battery=_float(data.get("battery")),
            source_type=data.get("sourceType"),
        )


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@dataclass(slots=True)
class TodaySummary:
    """Totals for today in the user's timezone."""

    distance_meters: int | None
    trip_count: int | None
    stay_count: int | None
    unique_places: int | None
    moving_seconds: int | None
    # The day these totals are for, in the user's timezone.
    day: date | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TodaySummary | None:
        if not data:
            return None
        return cls(
            day=_date(data.get("date")),
            distance_meters=_int(data.get("distanceMeters")),
            trip_count=_int(data.get("tripCount")),
            stay_count=_int(data.get("stayCount")),
            unique_places=_int(data.get("uniquePlaces")),
            moving_seconds=_int(data.get("movingSeconds")),
        )


@dataclass(slots=True)
class CurrentStatus:
    """`GET /users/me/status`."""

    state: str
    stay: StayStatus | None
    trip: TripStatus | None
    data_gap: DataGapStatus | None
    last_point: LastPoint | None
    today: TodaySummary | None
    # IANA name of the user's timezone, which `today` uses.
    timezone: str | None = None

    @property
    def current_since(self) -> datetime | None:
        """Start of the current stay, trip or data gap."""
        item = self.current_item
        return item.since if item else None

    @property
    def current_duration(self) -> int | None:
        """Duration of the current stay, trip or data gap in seconds."""
        item = self.current_item
        return item.duration_seconds if item else None

    @property
    def current_item(self) -> StayStatus | TripStatus | DataGapStatus | None:
        return {"STAY": self.stay, "TRIP": self.trip, "DATA_GAP": self.data_gap}.get(
            self.state
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CurrentStatus:
        return cls(
            state=str(data.get("state") or "UNKNOWN"),
            stay=StayStatus.from_dict(data.get("stay")),
            trip=TripStatus.from_dict(data.get("trip")),
            data_gap=DataGapStatus.from_dict(data.get("dataGap")),
            last_point=LastPoint.from_dict(data.get("lastPoint")),
            today=TodaySummary.from_dict(data.get("today")),
            timezone=data.get("timezone"),
        )


@dataclass(slots=True)
class TimelineItem:
    """A stay, trip or data gap of `GET /timeline`.

    `kind` uses the status states: `STAY`, `TRIP` or `DATA_GAP`. Ids change on every
    timeline rebuild, so items are identified by kind and start.
    """

    kind: str
    start: datetime
    end: datetime | None = None
    ongoing: bool = False
    place_name: str | None = None
    favorite_id: int | None = None
    location_source: str | None = None
    city: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_meters: int | None = None
    movement_type: str | None = None

    @classmethod
    def stay(cls, data: dict[str, Any]) -> TimelineItem | None:
        start = parse_dt(data.get("timestamp"))
        if start is None:
            return None
        return cls(
            kind="STAY",
            start=start,
            place_name=data.get("locationName"),
            favorite_id=_int(data.get("favoriteId")),
            city=data.get("city"),
            country=data.get("country"),
            latitude=_float(data.get("latitude")),
            longitude=_float(data.get("longitude")),
        )

    @classmethod
    def trip(cls, data: dict[str, Any]) -> TimelineItem | None:
        start = parse_dt(data.get("timestamp"))
        if start is None:
            return None
        return cls(
            kind="TRIP",
            start=start,
            distance_meters=_int(data.get("distanceMeters")),
            movement_type=data.get("movementType"),
        )

    @classmethod
    def data_gap(cls, data: dict[str, Any]) -> TimelineItem | None:
        start = parse_dt(data.get("startTime"))
        if start is None:
            return None
        return cls(
            kind="DATA_GAP",
            start=start,
            end=parse_dt(data.get("endTime")),
            ongoing=bool(data.get("ongoing")),
        )


@dataclass(slots=True)
class Timeline:
    """`GET /timeline`: stays, trips and data gaps, ordered by start."""

    items: list[TimelineItem]

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Timeline:
        data = data or {}
        items = [
            *(TimelineItem.stay(item) for item in data.get("stays") or []),
            *(TimelineItem.trip(item) for item in data.get("trips") or []),
            *(TimelineItem.data_gap(item) for item in data.get("dataGaps") or []),
        ]
        return cls(
            items=sorted(
                (item for item in items if item is not None),
                key=lambda item: item.start,
            )
        )


@dataclass(slots=True)
class GeofenceEvent:
    """One item of `GET /geofences/events`."""

    id: int
    event_type: str
    occurred_at: datetime | None
    rule_id: int | None
    rule_name: str | None
    subject_user_id: str | None
    subject_name: str | None
    latitude: float | None
    longitude: float | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeofenceEvent:
        subject = data.get("subjectUserId")
        return cls(
            id=int(data["id"]),
            event_type=str(data.get("eventType") or ""),
            occurred_at=parse_dt(data.get("occurredAt")),
            rule_id=_int(data.get("ruleId")),
            rule_name=data.get("ruleName"),
            subject_user_id=str(subject) if subject is not None else None,
            subject_name=data.get("subjectDisplayName"),
            latitude=_float(data.get("pointLat")),
            longitude=_float(data.get("pointLon")),
        )


@dataclass(slots=True)
class GeofenceEventsPage:
    """A page of `GET /geofences/events`."""

    items: list[GeofenceEvent]
    total_elements: int

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> GeofenceEventsPage:
        data = data or {}
        items = [GeofenceEvent.from_dict(item) for item in data.get("items") or []]
        return cls(
            items=items,
            total_elements=_int(data.get("totalElements")) or len(items),
        )


@dataclass(slots=True)
class Friend:
    """One item of `GET /friends`."""

    friend_id: str
    full_name: str | None
    latitude: float | None
    longitude: float | None
    accuracy: float | None
    battery: float | None
    last_seen: datetime | None
    activity: str | None
    activity_duration_seconds: int | None
    last_location: str | None
    shares_live_location: bool

    @property
    def has_location(self) -> bool:
        return (
            self.shares_live_location
            and self.latitude is not None
            and self.longitude is not None
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Friend:
        return cls(
            friend_id=str(data["friendId"]),
            full_name=data.get("fullName") or data.get("email"),
            latitude=_float(data.get("lastLatitude")),
            longitude=_float(data.get("lastLongitude")),
            accuracy=_float(data.get("lastAccuracy")),
            battery=_float(data.get("lastBattery")),
            last_seen=parse_dt(data.get("lastSeen")),
            activity=data.get("latestActivityType"),
            activity_duration_seconds=_int(data.get("latestActivityDurationSeconds")),
            last_location=data.get("lastLocation"),
            shares_live_location=bool(data.get("friendSharesLiveLocation")),
        )


@dataclass(slots=True)
class IngestStatus:
    """Ingest health of a GPS source."""

    last_received_at: datetime | None
    last_saved_at: datetime | None
    last_saved_point_timestamp: datetime | None
    last_rejected_at: datetime | None
    last_rejection_code: str | None
    last_rejection_params: dict[str, Any] | None
    last_auth_failure_at: datetime | None
    last_auth_failure_code: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> IngestStatus:
        data = data or {}
        return cls(
            last_received_at=parse_dt(data.get("lastReceivedAt")),
            last_saved_at=parse_dt(data.get("lastSavedAt")),
            last_saved_point_timestamp=parse_dt(data.get("lastSavedPointTimestamp")),
            last_rejected_at=parse_dt(data.get("lastRejectedAt")),
            last_rejection_code=data.get("lastRejectionCode"),
            last_rejection_params=data.get("lastRejectionParams"),
            last_auth_failure_at=parse_dt(data.get("lastAuthFailureAt")),
            last_auth_failure_code=data.get("lastAuthFailureCode"),
        )


@dataclass(slots=True)
class CurrentSource:
    """`GET /gps/sources/current`: the source connected to the calling token."""

    id: str
    type: str | None
    active: bool
    ingest_status: IngestStatus

    @property
    def state(self) -> str:
        """Summarize the ingest health as one enum value."""
        if not self.active:
            return "disabled"
        status = self.ingest_status
        if status.last_received_at is None:
            return "no_data"
        latest = max(
            (
                (status.last_saved_at, "ok"),
                (status.last_rejected_at, "rejected"),
                (status.last_auth_failure_at, "auth_failed"),
            ),
            key=lambda item: item[0] or datetime.min.replace(tzinfo=UTC),
        )
        return latest[1] if latest[0] is not None else "ok"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CurrentSource:
        return cls(
            id=str(data.get("id")),
            type=data.get("type"),
            active=bool(data.get("active", True)),
            ingest_status=IngestStatus.from_dict(data.get("ingestStatus")),
        )


@dataclass(slots=True)
class IngestSummary:
    """Response of the batch ingest endpoint."""

    accepted: int = 0
    saved: int = 0
    duplicate: int = 0
    filtered: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> IngestSummary:
        data = data or {}
        return cls(
            accepted=_int(data.get("accepted")) or 0,
            saved=_int(data.get("saved")) or 0,
            duplicate=_int(data.get("duplicate")) or 0,
            filtered=_int(data.get("filtered")) or 0,
        )
