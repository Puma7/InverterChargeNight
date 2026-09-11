"""Sensor platform for Inverter Charge Night."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
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
    """Set up the sensor platform."""
    coordinator: InverterChargeNightCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            CalculatedSOCSensor(coordinator, entry),
            BestChargePowerSensor(coordinator, entry),
        ]
    )


class CalculatedSOCSensor(InverterChargeNightEntity, SensorEntity):
    """Sensor for calculated SOC."""

    _attr_translation_key = "calculated_soc"
    _attr_native_unit_of_measurement = "%"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "calculated_soc")

    @property
    def native_value(self) -> float | None:
        """Return the calculated SOC."""
        value = self.coordinator.data.get("calculated_soc")
        return float(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        return {
            "is_active": data.get("is_active", False),
            "target_reached": data.get("target_reached", False),
            "current_soc": data.get("current_soc"),
            "operation_mode": data.get("operation_mode"),
            "skip_next": data.get("skip_next", False),
        }


class BestChargePowerSensor(InverterChargeNightEntity, SensorEntity):
    """Sensor for best charge power found by auto efficient charge."""

    _attr_translation_key = "best_charge_power"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "best_charge_power")

    @property
    def native_value(self) -> float | None:
        """Return the best charge power if available."""
        data = self.coordinator.get_auto_efficiency_data()
        best_power = data.get("best_power_w")
        if isinstance(best_power, int):
            return float(best_power)
        return None
