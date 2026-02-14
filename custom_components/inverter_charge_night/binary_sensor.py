"""Binary sensor platform for Inverter Charge Night."""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
if TYPE_CHECKING:  # pragma: no cover
    from . import InverterChargeNightConfigEntry, InverterChargeNightCoordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InverterChargeNightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator = entry.runtime_data
    async_add_entities([ActiveWindowBinarySensor(coordinator, entry)])


class ActiveWindowBinarySensor(CoordinatorEntity["InverterChargeNightCoordinator"], BinarySensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Binary sensor indicating if we're in the active window."""

    _attr_translation_key = "active"
    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-four"

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_active"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title or "Inverter Charge Night",
            "manufacturer": "Custom Integration",
            "model": "Inverter Charge Night",
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data
        self._attr_is_on = bool(data.get("is_active", False))
        self.async_write_ha_state()

