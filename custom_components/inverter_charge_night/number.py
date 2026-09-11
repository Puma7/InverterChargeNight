"""Number platform for Inverter Charge Night."""
from __future__ import annotations

import logging

from homeassistant.const import EntityCategory

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import InverterChargeNightConfigEntry, InverterChargeNightCoordinator
from .entity import InverterChargeNightEntity
from .const import (
    CONF_USER_MIN_SOC,
    CONF_USER_MAX_SOC,
)

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InverterChargeNightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    coordinator = entry.runtime_data
    async_add_entities([MinSOCOverrideNumber(coordinator, entry)])


class MinSOCOverrideNumber(InverterChargeNightEntity, NumberEntity):
    """Number entity for manual SOC override."""

    _attr_translation_key = "min_soc_override"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-settings"
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, entry, "min_soc_override")
        self._override_value: float | None = None

    @property
    def native_value(self) -> float | None:
        """Return the override value or calculated SOC."""
        if self.coordinator.override_soc is None:
            if self._override_value is not None:
                self._override_value = None
            value = self.coordinator.data.get("calculated_soc")
            return float(value) if value is not None else None
        if self._override_value is not None:
            return self._override_value
        value = self.coordinator.data.get("calculated_soc")
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the override value."""
        user_min_soc = float(self.coordinator.config.get(CONF_USER_MIN_SOC, 8.0))
        user_max_soc = float(self.coordinator.config.get(CONF_USER_MAX_SOC, 100.0))
        
        rounded_value = float(int(round(value)))
        clamped_value = max(user_min_soc, min(rounded_value, user_max_soc))
        
        if clamped_value != value:
            _LOGGER.warning(
                "Override value %.1f%% clamped to %.1f%% to respect user bounds [%.1f%%, %.1f%%]",
                value,
                clamped_value,
                user_min_soc,
                user_max_soc,
            )
        
        value = clamped_value
        self._override_value = value
        self.coordinator.override_soc = value
        self.coordinator.target_reached = False
        
        if self.coordinator.minimum_calculated_soc is None or value < self.coordinator.minimum_calculated_soc:
            self.coordinator.minimum_calculated_soc = value
        
        self.async_write_ha_state()
        
        if self.coordinator.is_active and self.coordinator.is_enabled:
            # The refresh applies the override through the mode-correct control
            # path (charge or discharge); calling the charge path here directly
            # would switch grid charging on even in discharge mode.
            await self.coordinator.async_request_refresh()
