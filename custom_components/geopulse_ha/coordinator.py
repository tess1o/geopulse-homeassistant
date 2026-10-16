"""Data update coordinators for GeoPulse."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone, tzinfo
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import repairs
from .api import GeoPulseApi, GeoPulseAuthError, GeoPulseError
from .const import (
    DOMAIN,
    FRIENDS_INTERVAL,
    HEALTH_INTERVAL,
    MIN_SERVER_MAJOR,
    SERVER_SLOW_INTERVAL,
)
from .helpers import server_major
from .models import (
    ApiToken,
    CurrentSource,
    CurrentStatus,
    Friend,
    HealthStatus,
    VersionStatus,
)

if TYPE_CHECKING:
    from . import GeoPulseConfigEntry

_LOGGER = logging.getLogger(__name__)


async def _call[T](func: Callable[[], Awaitable[T]]) -> T:
    """Run an API call, translating errors for the coordinator."""
    try:
        return await func()
    except GeoPulseAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GeoPulseError as err:
        raise UpdateFailed(f"GeoPulse request failed: {err}") from err


class GeoPulseCoordinator[T](DataUpdateCoordinator[T]):
    """Base coordinator bound to one config entry."""

    config_entry: GeoPulseConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GeoPulseConfigEntry,
        api: GeoPulseApi,
        name: str,
        interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {name}",
            update_interval=interval,
        )
        self.api = api


class StatusCoordinator(GeoPulseCoordinator[CurrentStatus]):
    """Polls `/users/me/status`."""

    # Midnight in the user's timezone of the day the `today` totals are for.
    day_start: datetime | None = None

    async def _async_update_data(self) -> CurrentStatus:
        status = await _call(self.api.status)
        self.day_start = await _day_start(status)
        return status


# Java's fixed-offset zone ids, which zoneinfo doesn't know: `Z`, `+03:00`,
# `UTC+3`, `GMT+03:00`, `UT-0530`. GeoPulse sends `Z` for a user without a timezone.
_OFFSET_ZONE = re.compile(r"(?:UTC|GMT|UT)?([+-])(\d{1,2})(?::?(\d{2}))?(?::?\d{2})?")


async def _day_start(status: CurrentStatus) -> datetime | None:
    today = status.today
    if today is None or today.day is None:
        return None
    return datetime.combine(today.day, time(), await _user_timezone(status.timezone))


async def _user_timezone(name: str | None) -> tzinfo:
    """Return the user's timezone, or UTC when it can't be read.

    The daily totals need a `last_reset`, so a wrong midnight is better than none.
    """
    if not name or name == "Z":
        return dt_util.UTC
    if match := _OFFSET_ZONE.fullmatch(name):
        sign, hours, minutes = match.groups()
        offset = timedelta(hours=int(hours), minutes=int(minutes or 0))
        return dt_timezone(-offset if sign == "-" else offset)
    # Loading a timezone reads files, so it must not run in the event loop.
    if timezone := await dt_util.async_get_time_zone(name):
        return timezone
    _LOGGER.debug("Unknown GeoPulse timezone %s, using UTC", name)
    return dt_util.UTC


class FriendsCoordinator(GeoPulseCoordinator[dict[str, Friend]]):
    """Polls `/friends`, keyed by friend user id."""

    def __init__(
        self, hass: HomeAssistant, entry: GeoPulseConfigEntry, api: GeoPulseApi
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, entry, api, "friends", FRIENDS_INTERVAL)

    async def _async_update_data(self) -> dict[str, Friend]:
        friends = await _call(self.api.friends)
        return {friend.friend_id: friend for friend in friends}


@dataclass(slots=True)
class ServerData:
    """Server health, version, token and ingest status."""

    health: HealthStatus | None = None
    version: VersionStatus | None = None
    token: ApiToken | None = None
    source: CurrentSource | None = None


class ServerCoordinator(GeoPulseCoordinator[ServerData]):
    """Checks health and ingest status every minute, version and token every 30.

    The ingest status is only read for a token that can send locations; a read-only
    token has no location source.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GeoPulseConfigEntry,
        api: GeoPulseApi,
        *,
        fetch_source: bool,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, entry, api, "server", HEALTH_INTERVAL)
        self._fetch_source = fetch_source
        self._slow_refreshed: datetime | None = None
        self.data = ServerData()

    async def _async_update_data(self) -> ServerData:
        previous = self.data or ServerData()
        data = ServerData(
            version=previous.version, token=previous.token, source=previous.source
        )
        try:
            data.health = await self.api.health()
        except GeoPulseError as err:
            _LOGGER.debug("GeoPulse health check failed: %s", err)
            data.health = None
            return data

        if self._fetch_source:
            try:
                data.source = await self.api.current_source()
            except GeoPulseAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except GeoPulseError as err:
                _LOGGER.debug("GeoPulse ingest status refresh failed: %s", err)

        now = dt_util.utcnow()
        if (
            self._slow_refreshed is None
            or now - self._slow_refreshed >= SERVER_SLOW_INTERVAL
        ):
            try:
                await self._slow_refresh(data)
            except GeoPulseAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except GeoPulseError as err:
                _LOGGER.debug("GeoPulse status refresh failed: %s", err)
            else:
                self._slow_refreshed = now
        return data

    async def _slow_refresh(self, data: ServerData) -> None:
        data.version = await self.api.version_status()
        major = server_major(data.version.current_version)
        repairs.set_issue(
            self.hass,
            self.config_entry,
            repairs.ISSUE_SERVER_TOO_OLD,
            active=major is not None and major < MIN_SERVER_MAJOR,
            placeholders={"version": data.version.current_version or "?"},
        )

        data.token = await self.api.current_token()
        repairs.check_token_expiry(self.hass, self.config_entry, data.token)


def diagnostics_state(
    coordinator: DataUpdateCoordinator[Any] | None,
) -> dict[str, Any] | None:
    """Return a short summary of a coordinator for diagnostics."""
    if coordinator is None:
        return None
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception)
        if coordinator.last_exception
        else None,
        "update_interval": str(coordinator.update_interval),
    }
