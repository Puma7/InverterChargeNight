"""Select platform for Inverter Charge Night."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import InverterChargeNightConfigEntry, InverterChargeNightCoordinator
from .entity import InverterChargeNightEntity
from .const import (
    CONF_OPERATION_MODE,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
)

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InverterChargeNightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform."""
    coordinator = entry.runtime_data
    async_add_entities([OperationModeSelect(coordinator, entry)])


class OperationModeSelect(InverterChargeNightEntity, SelectEntity):
    """Select entity for choosing between night charge and morning discharge."""

    _attr_translation_key = "operation_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [MODE_NIGHT_CHARGE, MODE_MORNING_DISCHARGE]

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, entry, "operation_mode")

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

        # The coordinator owns the teardown of the running window; the options
        # flow reaches the same method, so both ways behave identically.
        await self.coordinator.async_apply_operation_mode(option)

        data = dict(self._entry.data)
        data[CONF_OPERATION_MODE] = option
        self.hass.config_entries.async_update_entry(self._entry, data=data)
        self.async_write_ha_state()
        await self.coordinator._check_current_window()
        await self.coordinator.async_request_refresh()
