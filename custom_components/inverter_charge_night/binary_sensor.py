"""Binary sensor platform for Inverter Charge Night."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import InverterChargeNightCoordinator
from .entity import InverterChargeNightEntity
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator: InverterChargeNightCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ActiveWindowBinarySensor(coordinator, entry)])


class ActiveWindowBinarySensor(InverterChargeNightEntity, BinarySensorEntity):
    """Binary sensor indicating if we're in the active window."""

    _attr_translation_key = "active"
    _attr_icon = "mdi:clock-time-four"

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "active")

    @property
    def is_on(self) -> bool:
        """Return if the active window is currently active."""
        return bool(self.coordinator.data.get("is_active", False))
