"""Select platform for Inverter Charge Night."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import InverterChargeNightCoordinator
from .const import (
    CONF_OPERATION_MODE,
    DOMAIN,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform."""
    coordinator: InverterChargeNightCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OperationModeSelect(coordinator, entry)])


class OperationModeSelect(CoordinatorEntity[InverterChargeNightCoordinator], SelectEntity):
    """Select entity for choosing between night charge and morning discharge."""

    _attr_translation_key = "operation_mode"
    _attr_has_entity_name = True
    _attr_icon = "mdi:swap-horizontal"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [MODE_NIGHT_CHARGE, MODE_MORNING_DISCHARGE]

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_operation_mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title or "Inverter Charge Night",
            "manufacturer": "Custom Integration",
            "model": "Inverter Charge Night",
        }

    @property
    def current_option(self) -> str:
        """Return the current operation mode."""
        return str(self.coordinator.operation_mode)

    async def async_select_option(self, option: str) -> None:
        """Handle mode change."""
        if option not in (MODE_NIGHT_CHARGE, MODE_MORNING_DISCHARGE):
            return
        if self.coordinator.operation_mode == option:
            return

        old_mode = self.coordinator.operation_mode
        _LOGGER.info("Switching operation mode from %s to %s", old_mode, option)

        if self.coordinator.is_active:
            try:
                await self.coordinator._reset_settings()
            except Exception as e:
                _LOGGER.error("Error resetting settings during mode switch: %s", e, exc_info=True)
            self.coordinator.is_active = False
            self.coordinator.target_reached = False
            self.coordinator.initial_calculated_soc = None
            self.coordinator.minimum_calculated_soc = None
            self.coordinator.override_soc = None
            self.coordinator._remove_battery_soc_listener()
            self.coordinator._remove_inverter_min_soc_listener()
            self.coordinator._stop_periodic_verification()

        self.coordinator.operation_mode = option
        data = dict(self._entry.data)
        data[CONF_OPERATION_MODE] = option
        self.hass.config_entries.async_update_entry(self._entry, data=data)
        self.async_write_ha_state()
        await self.coordinator._check_current_window()
        await self.coordinator.async_request_refresh()
