"""The GeoPulse integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import repairs
from .api import GeoPulseApi, GeoPulseAuthError, GeoPulseError
from .const import (
    CONF_DISPLAY_NAME,
    CONF_FRIENDS,
    CONF_GEOFENCE_INTERVAL,
    CONF_HEARTBEAT_MINUTES,
    CONF_SCOPES,
    CONF_SEND_BATTERY,
    CONF_STATUS_INTERVAL,
    CONF_TRACKERS,
    DEFAULT_FRIENDS,
    DEFAULT_GEOFENCE_INTERVAL,
    DEFAULT_HEARTBEAT_MINUTES,
    DEFAULT_SEND_BATTERY,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MIN_GEOFENCE_INTERVAL,
    MIN_STATUS_INTERVAL,
    NAME,
)
from .coordinator import FriendsCoordinator, ServerCoordinator, StatusCoordinator
from .events import EventDetector
from .helpers import (
    account_device_info,
    account_identifier,
    friend_identifier,
    has_ingest_scope,
    has_read_scope,
    tracker_identifier,
)
from .sender import LocationForwarder
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type GeoPulseConfigEntry = ConfigEntry[GeoPulseRuntimeData]


@dataclass(slots=True)
class GeoPulseRuntimeData:
    """Objects shared by the platforms of one config entry."""

    api: GeoPulseApi
    user_id: str
    display_name: str
    url: str
    scopes: list[str]
    server: ServerCoordinator
    status: StatusCoordinator | None = None
    friends: FriendsCoordinator | None = None
    events: EventDetector | None = None
    forwarder: LocationForwarder | None = None
    send_battery: bool = DEFAULT_SEND_BATTERY
    # Device registry id of the "GeoPulse account" device, the parent of tracker
    # and friend devices.
    account_device_id: str | None = None

    @property
    def can_read(self) -> bool:
        """Return True if the token has `read`."""
        return has_read_scope(self.scopes)

    @property
    def can_send(self) -> bool:
        """Return True if the token has `gps:ingest`."""
        return has_ingest_scope(self.scopes)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GeoPulseConfigEntry) -> bool:
    """Set up GeoPulse from a config entry."""
    session = async_get_clientsession(hass, entry.data.get(CONF_VERIFY_SSL, True))
    api = GeoPulseApi(session, entry.data[CONF_URL], entry.data[CONF_TOKEN])

    try:
        token = await api.current_token()
    except GeoPulseAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GeoPulseError as err:
        raise ConfigEntryNotReady(f"Cannot reach GeoPulse: {err}") from err
    if token.status not in (None, "ACTIVE") or token.user_id != entry.unique_id:
        raise ConfigEntryAuthFailed("The token is not active for this account")

    if entry.data.get(CONF_SCOPES) != token.scopes:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_SCOPES: token.scopes}
        )

    options = entry.options
    user_id = token.user_id
    runtime = GeoPulseRuntimeData(
        api=api,
        user_id=user_id,
        display_name=entry.data.get(CONF_DISPLAY_NAME) or token.user_email or NAME,
        url=api.url,
        scopes=token.scopes,
        server=ServerCoordinator(
            hass, entry, api, fetch_source=has_ingest_scope(token.scopes)
        ),
        send_battery=options.get(CONF_SEND_BATTERY, DEFAULT_SEND_BATTERY),
    )
    await runtime.server.async_config_entry_first_refresh()
    runtime.account_device_id = (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id,
            **account_device_info(user_id, runtime.display_name, runtime.url),
        )
        .id
    )

    friends_enabled = options.get(CONF_FRIENDS, DEFAULT_FRIENDS)
    if runtime.can_read:
        status_interval = max(
            MIN_STATUS_INTERVAL,
            int(options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL)),
        )
        runtime.status = StatusCoordinator(
            hass, entry, api, "status", timedelta(seconds=status_interval)
        )
        await runtime.status.async_config_entry_first_refresh()
        if friends_enabled:
            runtime.friends = FriendsCoordinator(hass, entry, api)
            await runtime.friends.async_config_entry_first_refresh()

    trackers: list[str] = (
        list(options.get(CONF_TRACKERS, [])) if runtime.can_send else []
    )
    if runtime.can_send:
        runtime.forwarder = LocationForwarder(
            hass,
            entry,
            api,
            trackers,
            heartbeat_minutes=int(
                options.get(CONF_HEARTBEAT_MINUTES, DEFAULT_HEARTBEAT_MINUTES)
            ),
            send_battery=runtime.send_battery,
        )
    if runtime.status:
        geofence_interval = max(
            MIN_GEOFENCE_INTERVAL,
            int(options.get(CONF_GEOFENCE_INTERVAL, DEFAULT_GEOFENCE_INTERVAL)),
        )
        runtime.events = EventDetector(
            hass,
            entry,
            api,
            user_id,
            status=runtime.status,
            geofence_interval=timedelta(seconds=geofence_interval),
            friends=runtime.friends,
        )

    entry.runtime_data = runtime
    repairs.check_full_access(hass, entry, token.scopes)
    repairs.check_token_expiry(hass, entry, token)
    _remove_stale_devices(
        hass, entry, user_id, trackers, keep_friends=runtime.friends is not None
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if runtime.forwarder:
        await runtime.forwarder.async_start()
    if runtime.events:
        await runtime.events.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GeoPulseConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = entry.runtime_data
        if runtime.events:
            await runtime.events.async_stop()
        if runtime.forwarder:
            await runtime.forwarder.async_stop()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: GeoPulseConfigEntry) -> None:
    """Delete the stored queue and event state when the entry is removed."""
    for kind in ("queue", "events"):
        await Store(hass, 1, f"{DOMAIN}.{kind}.{entry.entry_id}").async_remove()
    repairs.clear_all(hass, entry)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GeoPulseConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing tracker and friend devices that are no longer in use."""
    runtime = entry.runtime_data
    if account_identifier(runtime.user_id) in device.identifiers:
        return False
    if runtime.forwarder and any(
        tracker_identifier(runtime.user_id, entity_id) in device.identifiers
        for entity_id in runtime.forwarder.entity_ids
    ):
        return False
    if runtime.friends and runtime.friends.data:
        return not any(
            friend_identifier(runtime.user_id, friend_id) in device.identifiers
            for friend_id, friend in runtime.friends.data.items()
            if friend.shares_live_location
        )
    return True


def _remove_stale_devices(
    hass: HomeAssistant,
    entry: GeoPulseConfigEntry,
    user_id: str,
    trackers: list[str],
    *,
    keep_friends: bool,
) -> None:
    """Drop tracker devices that are no longer forwarded, and friends if disabled."""
    dev_reg = dr.async_get(hass)
    wanted = {tracker_identifier(user_id, entity_id) for entity_id in trackers}
    tracker_prefix = f"{user_id}_tracker_"
    friend_prefix = f"{user_id}_friend_"
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            stale_tracker = (
                identifier.startswith(tracker_prefix)
                and (domain, identifier) not in wanted
            )
            stale_friend = identifier.startswith(friend_prefix) and not keep_friends
            if stale_tracker or stale_friend:
                dev_reg.async_update_device(
                    device.id, remove_config_entry_id=entry.entry_id
                )
                break
