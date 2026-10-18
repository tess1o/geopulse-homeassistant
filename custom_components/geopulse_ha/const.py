"""Constants for the GeoPulse integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "geopulse_ha"
NAME: Final = "GeoPulse"
MIN_SERVER_MAJOR: Final = 2

# Config entry data
CONF_USER_ID: Final = "user_id"
CONF_DISPLAY_NAME: Final = "display_name"
CONF_SCOPES: Final = "scopes"

# Options
CONF_TRACKERS: Final = "trackers"
CONF_HEARTBEAT_MINUTES: Final = "heartbeat_minutes"
CONF_STATUS_INTERVAL: Final = "status_interval"
CONF_GEOFENCE_INTERVAL: Final = "geofence_interval"
CONF_FRIENDS: Final = "friends"
CONF_SEND_BATTERY: Final = "send_battery"
CONF_FIX_TIME_ATTRIBUTE: Final = "fix_time_attribute"

DEFAULT_HEARTBEAT_MINUTES: Final = 15
DEFAULT_STATUS_INTERVAL: Final = 60
MIN_STATUS_INTERVAL: Final = 30
DEFAULT_GEOFENCE_INTERVAL: Final = 30
MIN_GEOFENCE_INTERVAL: Final = 15
DEFAULT_FRIENDS: Final = True
DEFAULT_SEND_BATTERY: Final = True

FRIENDS_INTERVAL: Final = timedelta(seconds=60)
HEALTH_INTERVAL: Final = timedelta(seconds=60)
SERVER_SLOW_INTERVAL: Final = timedelta(minutes=30)

# Token scopes
SCOPE_ALL: Final = "all"
SCOPE_READ: Final = "read"
SCOPE_INGEST: Final = "gps:ingest"

# Sender
QUEUE_MAX_POINTS: Final = 10_000
BATCH_SIZE: Final = 500
FLUSH_DEBOUNCE_SECONDS: Final = 2.0
BACKOFF_SECONDS: Final = (30, 60, 300, 900)
UNLOAD_FLUSH_TIMEOUT: Final = 5.0
# A tracker fix time further ahead than this is treated as invalid.
FIX_TIME_MAX_FUTURE: Final = timedelta(minutes=5)

# Backfill
BACKFILL_MAX_DAYS: Final = 31
BACKFILL_RETRIES: Final = (2, 5, 10)

# Events
GEOFENCE_PAGE_SIZE: Final = 200
# Timeline rebuilds can shift the start of a stay or trip slightly.
SAME_ITEM_TOLERANCE: Final = timedelta(minutes=2)
# Catch-up limits: beyond these only <previous>_ended and <current>_started fire.
CATCH_UP_MAX_AGE: Final = timedelta(hours=24)
CATCH_UP_MAX_ITEMS: Final = 20
# A fresh stay_started is normally 10-15 minutes old (minimum stay duration plus
# the 5-minute timeline update), so don't go lower.
EVENT_STALE_AFTER: Final = timedelta(minutes=60)
# A poll more than this after the previous successful one follows an outage.
OUTAGE_AFTER: Final = timedelta(minutes=15)
BUS_EVENT: Final = f"{DOMAIN}_event"
EVENT_TYPES: Final = [
    "geofence_enter",
    "geofence_leave",
    "stay_started",
    "stay_ended",
    "trip_started",
    "trip_ended",
    "tracking_lost",
    "tracking_resumed",
]

# Repair issues
ISSUE_FULL_ACCESS_TOKEN: Final = "full_access_token"
ISSUE_TOKEN_EXPIRING: Final = "token_expiring"
ISSUE_CANNOT_SEND_LOCATIONS: Final = "cannot_send_locations"
ISSUE_SOURCE_DISABLED: Final = "source_disabled"
ISSUE_SOURCE_NOT_CONNECTED: Final = "source_not_connected"
ISSUE_SERVER_TOO_OLD: Final = "server_too_old"
TOKEN_EXPIRY_WARNING: Final = timedelta(days=14)

# Services
SERVICE_BACKFILL: Final = "backfill"
SERVICE_FLUSH_QUEUE: Final = "flush_queue"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_START: Final = "start"
ATTR_END: Final = "end"

# Dispatcher signals (format with entry_id)
SIGNAL_FORWARDER_UPDATED: Final = f"{DOMAIN}_forwarder_updated_{{}}"
SIGNAL_ACTIVITY_EVENT: Final = f"{DOMAIN}_activity_event_{{}}"
