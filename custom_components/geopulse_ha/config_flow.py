"""Config flow for GeoPulse."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    GeoPulseApi,
    GeoPulseAuthError,
    GeoPulseConnectionError,
    GeoPulseError,
    normalize_url,
)
from .const import (
    CONF_DISPLAY_NAME,
    CONF_FRIENDS,
    CONF_GEOFENCE_INTERVAL,
    CONF_HEARTBEAT_MINUTES,
    CONF_SCOPES,
    CONF_SEND_BATTERY,
    CONF_STATUS_INTERVAL,
    CONF_TRACKERS,
    CONF_USER_ID,
    DEFAULT_FRIENDS,
    DEFAULT_GEOFENCE_INTERVAL,
    DEFAULT_HEARTBEAT_MINUTES,
    DEFAULT_SEND_BATTERY,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MIN_GEOFENCE_INTERVAL,
    MIN_SERVER_MAJOR,
    MIN_STATUS_INTERVAL,
    NAME,
)
from .helpers import has_ingest_scope, has_read_scope, server_major

_LOGGER = logging.getLogger(__name__)

TRACKER_DOMAINS = ["device_tracker", "person"]


class CannotConnect(Exception):
    """The server could not be reached."""


class UnsupportedVersion(Exception):
    """The server is older than GeoPulse 2."""


class InvalidAuth(Exception):
    """The token was rejected."""


class InsufficientScope(Exception):
    """The token has neither `read` nor `gps:ingest`."""


@dataclass(slots=True)
class ValidatedAccount:
    """What we learned about the server and token."""

    url: str
    user_id: str
    display_name: str
    title: str
    scopes: list[str]


async def validate_account(
    hass: HomeAssistant, url: str, token: str, verify_ssl: bool
) -> ValidatedAccount:
    """Validate the server and token; raise one of the flow errors on failure."""
    try:
        url = normalize_url(url)
    except ValueError as err:
        raise CannotConnect from err
    api = GeoPulseApi(async_get_clientsession(hass, verify_ssl), url, token)

    try:
        version = await api.version_status()
    except GeoPulseError as err:
        raise CannotConnect from err
    major = server_major(version.current_version)
    if major is not None and major < MIN_SERVER_MAJOR:
        raise UnsupportedVersion

    try:
        current = await api.current_token()
    except GeoPulseAuthError as err:
        raise InvalidAuth from err
    except GeoPulseConnectionError as err:
        raise CannotConnect from err
    except GeoPulseError as err:
        raise InvalidAuth from err

    if current.status not in (None, "ACTIVE"):
        raise InvalidAuth
    if not has_read_scope(current.scopes) and not has_ingest_scope(current.scopes):
        raise InsufficientScope

    display_name = current.user_email or current.name or NAME
    if has_read_scope(current.scopes):
        try:
            user = await api.me()
        except GeoPulseAuthError as err:
            raise InvalidAuth from err
        except GeoPulseError as err:
            raise CannotConnect from err
        display_name = user.full_name or user.email or display_name

    return ValidatedAccount(
        url=url,
        user_id=current.user_id,
        display_name=display_name,
        title=f"{NAME} ({display_name})",
        scopes=current.scopes,
    )


def _errors_for(err: Exception) -> dict[str, str]:
    if isinstance(err, CannotConnect):
        return {"base": "cannot_connect"}
    if isinstance(err, UnsupportedVersion):
        return {"base": "unsupported_version"}
    if isinstance(err, InvalidAuth):
        return {"base": "invalid_auth"}
    if isinstance(err, InsufficientScope):
        return {"base": "insufficient_scope"}
    return {"base": "unknown"}


FLOW_ERRORS = (CannotConnect, UnsupportedVersion, InvalidAuth, InsufficientScope)

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): URL_SELECTOR,
            vol.Required(CONF_TOKEN): TOKEN_SELECTOR,
            vol.Required(
                CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)
            ): BooleanSelector(),
        }
    )


def _trackers_selector() -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain=TRACKER_DOMAINS, multiple=True))


class GeoPulseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GeoPulse."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._account: ValidatedAccount | None = None
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GeoPulseOptionsFlow:
        """Return the options flow."""
        return GeoPulseOptionsFlow()

    async def _validate(
        self, user_input: dict[str, Any], url: str | None = None
    ) -> tuple[ValidatedAccount | None, dict[str, str]]:
        try:
            account = await validate_account(
                self.hass,
                url or user_input[CONF_URL],
                user_input[CONF_TOKEN],
                user_input.get(CONF_VERIFY_SSL, True),
            )
        except FLOW_ERRORS as err:
            return None, _errors_for(err)
        except Exception:
            _LOGGER.exception("Unexpected error while validating GeoPulse")
            return None, {"base": "unknown"}
        return account, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the server URL and token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            account, errors = await self._validate(user_input)
            if account:
                await self.async_set_unique_id(account.user_id)
                self._abort_if_unique_id_configured()
                self._account = account
                self._data = {
                    CONF_URL: account.url,
                    CONF_TOKEN: user_input[CONF_TOKEN],
                    CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    CONF_USER_ID: account.user_id,
                    CONF_DISPLAY_NAME: account.display_name,
                    CONF_SCOPES: account.scopes,
                }
                if has_ingest_scope(account.scopes):
                    return await self.async_step_trackers()
                return await self.async_step_read_only()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema({}), user_input
            ),
            errors=errors,
        )

    async def async_step_trackers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the Home Assistant trackers to forward."""
        assert self._account
        if user_input is not None:
            return self.async_create_entry(
                title=self._account.title,
                data=self._data,
                options={CONF_TRACKERS: user_input.get(CONF_TRACKERS, [])},
            )
        return self.async_show_form(
            step_id="trackers",
            data_schema=vol.Schema(
                {vol.Optional(CONF_TRACKERS, default=[]): _trackers_selector()}
            ),
            description_placeholders={"account": self._account.display_name},
        )

    async def async_step_read_only(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain that forwarding is disabled for a token without `gps:ingest`."""
        assert self._account
        if user_input is not None:
            return self.async_create_entry(
                title=self._account.title, data=self._data, options={}
            )
        return self.async_show_form(
            step_id="read_only",
            data_schema=vol.Schema({}),
            description_placeholders={"account": self._account.display_name},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after a 401."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account, errors = await self._validate(
                {
                    CONF_TOKEN: user_input[CONF_TOKEN],
                    CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL, True),
                },
                url=entry.data[CONF_URL],
            )
            if account:
                await self.async_set_unique_id(account.user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_TOKEN: user_input[CONF_TOKEN],
                        CONF_DISPLAY_NAME: account.display_name,
                        CONF_SCOPES: account.scopes,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the URL or token of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account, errors = await self._validate(user_input)
            if account:
                await self.async_set_unique_id(account.user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: account.url,
                        CONF_TOKEN: user_input[CONF_TOKEN],
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                        CONF_DISPLAY_NAME: account.display_name,
                        CONF_SCOPES: account.scopes,
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(entry.data),
            errors=errors,
        )


class GeoPulseOptionsFlow(OptionsFlowWithReload):
    """Options for GeoPulse; saving reloads the entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        scopes: list[str] = self.config_entry.data.get(CONF_SCOPES, [])
        can_read = has_read_scope(scopes)
        can_send = has_ingest_scope(scopes)

        fields: dict[Any, Any] = {}
        if can_send:
            fields[
                vol.Optional(CONF_TRACKERS, default=options.get(CONF_TRACKERS, []))
            ] = _trackers_selector()
            fields[
                vol.Required(
                    CONF_HEARTBEAT_MINUTES,
                    default=options.get(
                        CONF_HEARTBEAT_MINUTES, DEFAULT_HEARTBEAT_MINUTES
                    ),
                )
            ] = NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=240,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            )
            fields[
                vol.Required(
                    CONF_SEND_BATTERY,
                    default=options.get(CONF_SEND_BATTERY, DEFAULT_SEND_BATTERY),
                )
            ] = BooleanSelector()
        if can_read:
            fields[
                vol.Required(
                    CONF_STATUS_INTERVAL,
                    default=options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
                )
            ] = NumberSelector(
                NumberSelectorConfig(
                    min=MIN_STATUS_INTERVAL,
                    max=3600,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            )
            fields[
                vol.Required(
                    CONF_GEOFENCE_INTERVAL,
                    default=options.get(
                        CONF_GEOFENCE_INTERVAL, DEFAULT_GEOFENCE_INTERVAL
                    ),
                )
            ] = NumberSelector(
                NumberSelectorConfig(
                    min=MIN_GEOFENCE_INTERVAL,
                    max=3600,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            )
            fields[
                vol.Required(
                    CONF_FRIENDS, default=options.get(CONF_FRIENDS, DEFAULT_FRIENDS)
                )
            ] = BooleanSelector()

        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))
