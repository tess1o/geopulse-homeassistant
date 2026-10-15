"""Base entities for GeoPulse."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import NAME
from .helpers import account_device_info, account_identifier, friend_identifier

if TYPE_CHECKING:
    from . import GeoPulseRuntimeData


class GeoPulseAccountEntity[C: DataUpdateCoordinator[Any]](CoordinatorEntity[C]):
    """An entity on the "GeoPulse account" device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: C, runtime: GeoPulseRuntimeData, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{runtime.user_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = account_device_info(
            runtime.user_id, runtime.display_name, runtime.url
        )


def friend_device_info(
    runtime: GeoPulseRuntimeData, friend_id: str, name: str | None
) -> DeviceInfo:
    """Device info of a friend."""
    return DeviceInfo(
        identifiers={friend_identifier(runtime.user_id, friend_id)},
        name=name or "GeoPulse friend",
        manufacturer=NAME,
        model="Friend",
        **via_account_device(runtime),
    )


# HA 2026.8 replaced `via_device` (the parent's identifier) with `via_device_id` (its
# registry id); `via_device` stops working in 2027.8, and HA before 2026.8 rejects
# `via_device_id`.
_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__optional_keys__


def via_account_device(runtime: GeoPulseRuntimeData) -> DeviceInfo:
    """Device info linking a device to the "GeoPulse account" device."""
    if _HAS_VIA_DEVICE_ID and runtime.account_device_id is not None:
        return DeviceInfo(via_device_id=runtime.account_device_id)
    return DeviceInfo(via_device=account_identifier(runtime.user_id))


class GeoPulsePlainEntity(Entity):
    """A non-coordinator entity (forwarder, events)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
