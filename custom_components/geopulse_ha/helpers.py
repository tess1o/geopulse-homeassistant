"""Small shared helpers."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN, NAME, SCOPE_ALL, SCOPE_INGEST, SCOPE_READ


def has_read_scope(scopes: Iterable[str]) -> bool:
    """Return True if the token may read data."""
    scopes = set(scopes)
    return SCOPE_READ in scopes or SCOPE_ALL in scopes


def has_ingest_scope(scopes: Iterable[str]) -> bool:
    """Return True if the token may send locations."""
    scopes = set(scopes)
    return SCOPE_INGEST in scopes or SCOPE_ALL in scopes


def account_identifier(user_id: str) -> tuple[str, str]:
    """Device registry identifier of the account device."""
    return (DOMAIN, user_id)


def tracker_identifier(user_id: str, entity_id: str) -> tuple[str, str]:
    """Device registry identifier of a forwarded tracker."""
    return (DOMAIN, f"{user_id}_tracker_{entity_id}")


def friend_identifier(user_id: str, friend_id: str) -> tuple[str, str]:
    """Device registry identifier of a friend."""
    return (DOMAIN, f"{user_id}_friend_{friend_id}")


def account_device_info(user_id: str, display_name: str, url: str) -> DeviceInfo:
    """Device info of the "GeoPulse account" device."""
    return DeviceInfo(
        identifiers={account_identifier(user_id)},
        entry_type=DeviceEntryType.SERVICE,
        manufacturer=NAME,
        model="GeoPulse account",
        name=f"{NAME} {display_name}",
        configuration_url=url,
    )


_CAMEL_RE = re.compile(r"(?<=[a-z0-9])([A-Z])")


def snake_case(key: str) -> str:
    """Convert a camelCase key to snake_case."""
    return _CAMEL_RE.sub(r"_\1", key).lower()


def snake_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a dict with snake_case keys (top level only)."""
    return {snake_case(key): value for key, value in data.items()}


def server_major(version: str | None) -> int | None:
    """Return the major version number of a GeoPulse version string."""
    if not version:
        return None
    match = re.match(r"\D*(\d+)", version)
    return int(match.group(1)) if match else None
