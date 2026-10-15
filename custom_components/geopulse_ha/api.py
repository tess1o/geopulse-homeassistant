"""Async client for the GeoPulse REST API.

This module has no Home Assistant imports, so it can be tested and reused on its own.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .models import (
    ApiToken,
    CurrentSource,
    CurrentStatus,
    Friend,
    GeofenceEventsPage,
    GeoPulseUser,
    HealthStatus,
    IngestSummary,
    Timeline,
    VersionStatus,
)

REQUEST_TIMEOUT = 20


class GeoPulseError(Exception):
    """Base error for GeoPulse API calls."""


class GeoPulseConnectionError(GeoPulseError):
    """The server could not be reached, timed out or answered with 5xx."""


class GeoPulseAuthError(GeoPulseError):
    """The token is invalid, expired or revoked (401)."""


class GeoPulseForbiddenError(GeoPulseError):
    """The request was refused (403)."""

    def __init__(self, code: str | None, required_scope: str | None = None) -> None:
        """Initialize the error."""
        super().__init__(code or "forbidden")
        self.code = code
        self.required_scope = required_scope


class GeoPulseScopeError(GeoPulseForbiddenError):
    """The token lacks a scope (403 API_TOKEN_SCOPE_REQUIRED)."""


class GeoPulseSourceDisabledError(GeoPulseForbiddenError):
    """The GPS source connected to the token is disabled (403 GPS_SOURCE_DISABLED)."""


class GeoPulseSourceNotConnectedError(GeoPulseForbiddenError):
    """No GPS source is connected to the token (403 GPS_SOURCE_NOT_CONNECTED)."""


class GeoPulseNotFoundError(GeoPulseError):
    """The resource does not exist (404)."""


class GeoPulseRequestTooLargeError(GeoPulseError):
    """The batch was too large (413)."""


class GeoPulseResponseError(GeoPulseError):
    """Any other unexpected response."""


def normalize_url(url: str) -> str:
    """Return scheme://host[:port] for a user-entered URL, without any path."""
    url = url.strip()
    if "://" not in url:
        url = f"http://{url}"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Invalid GeoPulse URL: {url}")
    return f"{parts.scheme}://{parts.netloc}"


def _instant(value: datetime) -> str:
    """Format an ISO-8601 UTC instant for a query string."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _error_fields(body: Any) -> tuple[str | None, str | None]:
    """Extract the error code and required scope from a problem response."""
    if not isinstance(body, dict):
        return None, None
    code = body.get("code")
    scope = body.get("requiredScope")
    if scope is None and isinstance(body.get("parameters"), dict):
        scope = body["parameters"].get("requiredScope")
    return code, scope


class GeoPulseApi:
    """GeoPulse API client bound to one server and one API token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base = normalize_url(url)
        self._token = token

    @property
    def url(self) -> str:
        """Return the normalized base URL."""
        return self._base

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        auth: bool = True,
        allow_503: bool = False,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                resp = await self._session.request(
                    method,
                    f"{self._base}/api/v1{path}",
                    params=params,
                    json=json,
                    headers=headers,
                )
                async with resp:
                    status = resp.status
                    try:
                        body = await resp.json(content_type=None)
                    except ValueError, aiohttp.ContentTypeError:
                        body = None
        except (TimeoutError, aiohttp.ClientError) as err:
            raise GeoPulseConnectionError(str(err) or type(err).__name__) from err

        if status < 300 or (allow_503 and status == 503):
            return body
        code, scope = _error_fields(body)
        if status == 401:
            raise GeoPulseAuthError(code or "unauthorized")
        if status == 403:
            if code == "API_TOKEN_SCOPE_REQUIRED":
                raise GeoPulseScopeError(code, scope)
            if code == "GPS_SOURCE_DISABLED":
                raise GeoPulseSourceDisabledError(code)
            if code == "GPS_SOURCE_NOT_CONNECTED":
                raise GeoPulseSourceNotConnectedError(code)
            raise GeoPulseForbiddenError(code, scope)
        if status == 404:
            raise GeoPulseNotFoundError(path)
        if status == 413:
            raise GeoPulseRequestTooLargeError(code or "too_large")
        if status >= 500:
            raise GeoPulseConnectionError(f"Server error {status}")
        raise GeoPulseResponseError(f"Unexpected response {status} for {path}")

    async def version_status(self) -> VersionStatus:
        """Return the server version and update status (public)."""
        return VersionStatus.from_dict(
            await self._request("GET", "/system/version/status", auth=False)
        )

    async def health(self) -> HealthStatus:
        """Return the server health (public); 503 means DOWN."""
        return HealthStatus.from_dict(
            await self._request("GET", "/system/health", auth=False, allow_503=True)
        )

    async def current_token(self) -> ApiToken:
        """Return the calling API token.

        A 404 means the credential is not an API token, which is an auth problem
        for this integration.
        """
        try:
            data = await self._request("GET", "/api-tokens/current")
        except GeoPulseNotFoundError as err:
            raise GeoPulseAuthError("not_an_api_token") from err
        return ApiToken.from_dict(data)

    async def me(self) -> GeoPulseUser:
        """Return the account the token belongs to (needs `read`)."""
        return GeoPulseUser.from_dict(await self._request("GET", "/users/me"))

    async def status(self) -> CurrentStatus:
        """Return the current activity status (needs `read`)."""
        return CurrentStatus.from_dict(await self._request("GET", "/users/me/status"))

    async def timeline(self, start: datetime, end: datetime) -> Timeline:
        """Return the stays, trips and data gaps overlapping a range (needs `read`)."""
        return Timeline.from_dict(
            await self._request(
                "GET",
                "/timeline",
                params={"from": _instant(start), "to": _instant(end)},
            )
        )

    async def geofence_events(
        self, after_id: int | None = None, size: int = 200
    ) -> GeofenceEventsPage:
        """Return geofence events (needs `read`).

        With `after_id`, only newer events, oldest first; without it, newest first.
        """
        params: dict[str, Any] = {"size": size}
        if after_id is not None:
            params["afterId"] = after_id
        return GeofenceEventsPage.from_dict(
            await self._request("GET", "/geofences/events", params=params)
        )

    async def friends(self) -> list[Friend]:
        """Return the friends list (needs `read`)."""
        data = await self._request("GET", "/friends")
        return [Friend.from_dict(item) for item in data or []]

    async def current_source(self) -> CurrentSource | None:
        """Return the GPS source connected to this token, or None if there is none."""
        try:
            data = await self._request("GET", "/gps/sources/current")
        except GeoPulseNotFoundError:
            return None
        return CurrentSource.from_dict(data)

    async def ingest_batch(self, points: list[dict[str, Any]]) -> IngestSummary:
        """Send up to 1000 points (needs `gps:ingest`)."""
        return IngestSummary.from_dict(
            await self._request("POST", "/gps/ingest/home-assistant/batch", json=points)
        )
