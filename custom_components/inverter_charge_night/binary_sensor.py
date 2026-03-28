"""Binary sensor platform for Inverter Charge Night."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import InverterChargeNightCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator: InverterChargeNightCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ActiveWindowBinarySensor(coordinator, entry)])


class ActiveWindowBinarySensor(CoordinatorEntity[InverterChargeNightCoordinator], BinarySensorEntity):
    """Binary sensor indicating if we're in the active window."""

    _attr_translation_key = "active"
    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-four"

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_active"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or "Inverter Charge Night",
            manufacturer="Custom Integration",
            model="Inverter Charge Night",
        )

    @property
    def is_on(self) -> bool:
        """Return if the active window is currently active."""
        return bool(self.coordinator.data.get("is_active", False))
