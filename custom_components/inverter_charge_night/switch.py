"""Switch platform for Inverter Charge Night."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_AUTO_EFFICIENT_CHARGE, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            InverterChargeNightSwitch(coordinator, entry),
            AutoEfficientChargeSwitch(coordinator, entry),
        ]
    )


class InverterChargeNightSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable the integration."""

    _attr_translation_key = "enabled"
    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-charging-wireless"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_enabled"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title or "Inverter Charge Night",
            "manufacturer": "Custom Integration",
            "model": "Inverter Charge Night",
        }

    @property
    def is_on(self) -> bool:
        """Return if the integration is enabled."""
        return self.coordinator.is_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the integration."""
        if self.coordinator.is_enabled:
            return

        _LOGGER.info("Enabling Inverter Charge Night")
        self.coordinator.is_enabled = True
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the integration and reset settings."""
        if not self.coordinator.is_enabled:
            return

        _LOGGER.info("Disabling Inverter Charge Night")
        self.coordinator.is_enabled = False
        
        # Reset settings when disabled
        try:
            await self.coordinator._reset_settings()
        except Exception as e:
            _LOGGER.error("Error resetting settings while disabling: %s", e, exc_info=True)
        self.coordinator.is_active = False
        self.coordinator.override_soc = None  # Clear override when disabled
        self.coordinator.minimum_calculated_soc = None  # Clear minimum when disabled
        self.coordinator._remove_battery_soc_listener()  # Remove listener when disabled
        self.coordinator._remove_inverter_min_soc_listener()  # Remove inverter listener when disabled
        self.coordinator._stop_periodic_verification()  # Stop periodic verification when disabled
        self.async_write_ha_state()


class AutoEfficientChargeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable auto efficient charge finder."""

    _attr_translation_key = "auto_efficient_charge_finder"
    _attr_has_entity_name = True
    _attr_icon = "mdi:flash-auto"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the auto efficient charge switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_auto_efficient_charge"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title or "Inverter Charge Night",
            "manufacturer": "Custom Integration",
            "model": "Inverter Charge Night",
        }

    @property
    def is_on(self) -> bool:
        """Return if auto efficient charge finder is enabled."""
        return self.coordinator.auto_efficient_charge

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable auto efficient charge finder."""
        if self.coordinator.auto_efficient_charge:
            return
        self.coordinator.auto_efficient_charge = True
        self.coordinator._auto_missing_entities_logged = False
        data = dict(self._entry.data)
        data[CONF_AUTO_EFFICIENT_CHARGE] = True
        self.hass.config_entries.async_update_entry(self._entry, data=data)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable auto efficient charge finder."""
        if not self.coordinator.auto_efficient_charge:
            return
        self.coordinator.auto_efficient_charge = False
        self.coordinator._reset_auto_test_state()
        data = dict(self._entry.data)
        data[CONF_AUTO_EFFICIENT_CHARGE] = False
        self.hass.config_entries.async_update_entry(self._entry, data=data)
        self.async_write_ha_state()

