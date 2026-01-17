"""Number platform for Inverter Charge Night."""

import logging

from homeassistant.const import EntityCategory

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_USER_MIN_SOC,
    CONF_USER_MAX_SOC,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MinSOCOverrideNumber(coordinator, entry)])


class MinSOCOverrideNumber(CoordinatorEntity, NumberEntity):
    """Number entity for manual SOC override."""

    _attr_translation_key = "min_soc_override"
    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:battery-settings"
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_min_soc_override"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title or "Inverter Charge Night",
            "manufacturer": "Custom Integration",
            "model": "Inverter Charge Night",
        }
        self._override_value: float | None = None

    @property
    def native_value(self) -> float | None:
        """Return the override value or calculated SOC."""
        if self.coordinator.override_soc is None:
            if self._override_value is not None:
                self._override_value = None
            data = self.coordinator.data
            return data.get("calculated_soc")
        if self._override_value is not None:
            return self._override_value
        data = self.coordinator.data
        return data.get("calculated_soc")

    async def async_set_native_value(self, value: float) -> None:
        """Set the override value."""
        # CRITICAL: Clamp override value to user-defined min/max bounds
        # This ensures override respects the same bounds as calculated SOC
        user_min_soc = float(self.coordinator.config.get(CONF_USER_MIN_SOC, 8.0))
        user_max_soc = float(self.coordinator.config.get(CONF_USER_MAX_SOC, 100.0))
        
        # Round to whole percent and clamp to user bounds
        rounded_value = float(int(round(value)))
        clamped_value = max(user_min_soc, min(rounded_value, user_max_soc))
        
        # Log warning if value was clamped
        if clamped_value != value:
            _LOGGER.warning(
                "Override value %.1f%% clamped to %.1f%% to respect user bounds [%.1f%%, %.1f%%]",
                value,
                clamped_value,
                user_min_soc,
                user_max_soc,
            )
        
        # Use clamped value
        value = clamped_value
        self._override_value = value
        # Store override in coordinator so it can be used for target checks
        self.coordinator.override_soc = value
        self.coordinator.target_reached = False  # Reset target reached when override changes
        
        # CRITICAL: Update minimum SOC if override is lower than current minimum
        if self.coordinator.minimum_calculated_soc is None or value < self.coordinator.minimum_calculated_soc:
            self.coordinator.minimum_calculated_soc = value
        
        self.async_write_ha_state()
        
        # If active and enabled, apply the override
        if self.coordinator.is_active and self.coordinator.is_enabled:
            await self.coordinator._control_kostal(value)
            # Request refresh to check if target is already reached
            await self.coordinator.async_request_refresh()

