"""Shared base entity for Inverter Charge Night."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import InverterChargeNightCoordinator
from .const import DOMAIN


class InverterChargeNightEntity(CoordinatorEntity[InverterChargeNightCoordinator]):
    """Base class that attaches every entity to the integration's device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: InverterChargeNightCoordinator,
        entry: ConfigEntry,
        unique_id_suffix: str,
    ) -> None:
        """Initialize the entity with its unique ID and device info."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or "Inverter Charge Night",
            manufacturer="Inverter Charge Night",
            model="Charge window scheduler",
            configuration_url="https://github.com/Puma7/InverterChargeNight",
        )
