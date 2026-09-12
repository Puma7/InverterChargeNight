"""Switch platform for Inverter Charge Night."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import InverterChargeNightConfigEntry, InverterChargeNightCoordinator
from .entity import InverterChargeNightEntity
from .const import CONF_AUTO_EFFICIENT_CHARGE

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InverterChargeNightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            InverterChargeNightSwitch(coordinator, entry),
            AutoEfficientChargeSwitch(coordinator, entry),
            SkipNextSwitch(coordinator, entry),
        ]
    )


class InverterChargeNightSwitch(InverterChargeNightEntity, SwitchEntity):
    """Switch to enable/disable the integration."""

    _attr_translation_key = "enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry, "enabled")

    @property
    def is_on(self) -> bool:
        """Return if the integration is enabled."""
        return bool(self.coordinator.is_enabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the integration."""
        if self.coordinator.is_enabled:
            return

        _LOGGER.info("Enabling Inverter Charge Night")
        self.coordinator.is_enabled = True
        self.coordinator._persist_state()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the integration and reset settings."""
        if not self.coordinator.is_enabled:
            return

        _LOGGER.info("Disabling Inverter Charge Night")
        self.coordinator.is_enabled = False

        try:
            await self.coordinator._reset_settings()
        except Exception as e:
            _LOGGER.error("Error resetting settings while disabling: %s", e, exc_info=True)
        self.coordinator.is_active = False
        self.coordinator.override_soc = None
        self.coordinator.initial_calculated_soc = None
        self.coordinator.minimum_calculated_soc = None
        self.coordinator._remove_battery_soc_listener()
        self.coordinator._remove_inverter_min_soc_listener()
        self.coordinator._remove_grid_import_listener()
        await self.coordinator._stop_periodic_verification()
        self.coordinator._persist_state()
        self.async_write_ha_state()


class SkipNextSwitch(InverterChargeNightEntity, SwitchEntity):
    """Switch to skip the next window cycle for 24 hours."""

    _attr_translation_key = "skip_next"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the skip next switch."""
        super().__init__(coordinator, entry, "skip_next")

    @property
    def is_on(self) -> bool:
        """Return if skip next is active."""
        return bool(self.coordinator.skip_next)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Activate skip next (24-hour override)."""
        if self.coordinator.skip_next:
            return

        _LOGGER.info("Skip next activated - integration will skip for 24 hours")
        self.coordinator.skip_next = True
        self.coordinator._schedule_skip_next_expiry()
        self.coordinator._persist_state()

        if self.coordinator.is_active:
            _LOGGER.info("Currently active - ending window due to skip next")
            from homeassistant.util import dt as dt_util
            await self.coordinator._on_window_end(dt_util.now())

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Deactivate skip next."""
        if not self.coordinator.skip_next:
            return

        _LOGGER.info("Skip next deactivated")
        self.coordinator.skip_next = False
        self.coordinator._cancel_skip_next_expiry()
        self.coordinator._persist_state()
        self.async_write_ha_state()
        await self.coordinator._check_current_window()


class AutoEfficientChargeSwitch(InverterChargeNightEntity, SwitchEntity):
    """Switch to enable/disable auto efficient charge finder."""

    _attr_translation_key = "auto_efficient_charge_finder"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the auto efficient charge switch."""
        super().__init__(coordinator, entry, "auto_efficient_charge")

    @property
    def is_on(self) -> bool:
        """Return if auto efficient charge finder is enabled."""
        return bool(self.coordinator.auto_efficient_charge)

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
        # Aborting the finder must not leave its test value on the inverter
        await self.coordinator._reset_ac_charge_limit()
        data = dict(self._entry.data)
        data[CONF_AUTO_EFFICIENT_CHARGE] = False
        self.hass.config_entries.async_update_entry(self._entry, data=data)
        self.async_write_ha_state()
