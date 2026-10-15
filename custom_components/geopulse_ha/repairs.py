"""Repair issues raised by GeoPulse."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    ISSUE_CANNOT_SEND_LOCATIONS,
    ISSUE_FULL_ACCESS_TOKEN,
    ISSUE_SERVER_TOO_OLD,
    ISSUE_SOURCE_DISABLED,
    ISSUE_SOURCE_NOT_CONNECTED,
    ISSUE_TOKEN_EXPIRING,
    SCOPE_ALL,
    TOKEN_EXPIRY_WARNING,
)
from .models import ApiToken

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

ALL_ISSUES = (
    ISSUE_FULL_ACCESS_TOKEN,
    ISSUE_TOKEN_EXPIRING,
    ISSUE_CANNOT_SEND_LOCATIONS,
    ISSUE_SOURCE_DISABLED,
    ISSUE_SOURCE_NOT_CONNECTED,
    ISSUE_SERVER_TOO_OLD,
)

_SEVERITY = {
    ISSUE_FULL_ACCESS_TOKEN: ir.IssueSeverity.WARNING,
    ISSUE_TOKEN_EXPIRING: ir.IssueSeverity.WARNING,
    ISSUE_CANNOT_SEND_LOCATIONS: ir.IssueSeverity.ERROR,
    ISSUE_SOURCE_DISABLED: ir.IssueSeverity.ERROR,
    ISSUE_SOURCE_NOT_CONNECTED: ir.IssueSeverity.ERROR,
    ISSUE_SERVER_TOO_OLD: ir.IssueSeverity.ERROR,
}


def issue_id(issue: str, entry: ConfigEntry) -> str:
    """Return the per-entry issue id."""
    return f"{issue}_{entry.entry_id}"


def set_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    issue: str,
    *,
    active: bool,
    placeholders: dict[str, str] | None = None,
) -> None:
    """Create or delete a repair issue for an entry."""
    if not active:
        ir.async_delete_issue(hass, DOMAIN, issue_id(issue, entry))
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(issue, entry),
        is_fixable=False,
        is_persistent=False,
        severity=_SEVERITY[issue],
        translation_key=issue,
        translation_placeholders={"title": entry.title, **(placeholders or {})},
    )


def check_full_access(
    hass: HomeAssistant, entry: ConfigEntry, scopes: list[str]
) -> None:
    """Warn when the token has full access."""
    set_issue(hass, entry, ISSUE_FULL_ACCESS_TOKEN, active=SCOPE_ALL in scopes)


def check_token_expiry(
    hass: HomeAssistant, entry: ConfigEntry, token: ApiToken
) -> None:
    """Warn when the token expires within 14 days."""
    expires = token.expires_at
    active = expires is not None and expires - dt_util.utcnow() < TOKEN_EXPIRY_WARNING
    set_issue(
        hass,
        entry,
        ISSUE_TOKEN_EXPIRING,
        active=active,
        placeholders={"expires": expires.isoformat() if expires else ""},
    )


def clear_all(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove every issue of an entry."""
    for issue in ALL_ISSUES:
        ir.async_delete_issue(hass, DOMAIN, issue_id(issue, entry))
