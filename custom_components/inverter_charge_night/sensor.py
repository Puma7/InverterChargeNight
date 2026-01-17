"""Sensor platform for Inverter Charge Night."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_CALCULATED_SOC, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            CalculatedSOCSensor(coordinator, entry),
            BestChargePowerSensor(coordinator, entry),
        ]
    )


class CalculatedSOCSensor(CoordinatorEntity, SensorEntity):
    """Sensor for calculated SOC."""

    _attr_translation_key = "calculated_soc"
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "%"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_calculated_soc"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title or "Inverter Charge Night",
            "manufacturer": "Custom Integration",
            "model": "Inverter Charge Night",
        }

    @property
    def native_value(self) -> float | None:
        """Return the calculated SOC."""
        data = self.coordinator.data
        return data.get("calculated_soc")

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        data = self.coordinator.data
        return {
            "is_active": data.get("is_active", False),
            "target_reached": data.get("target_reached", False),
            "current_soc": data.get("current_soc"),
        }


class BestChargePowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for best charge power found by auto efficient charge."""

    _attr_translation_key = "best_charge_power"
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_best_charge_power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title or "Inverter Charge Night",
            "manufacturer": "Custom Integration",
            "model": "Inverter Charge Night",
        }

    @property
    def native_value(self) -> float | None:
        """Return the best charge power if available."""
        data = self.coordinator._get_auto_efficiency_data()
        best_power = data.get("best_power_w")
        if isinstance(best_power, int):
            return float(best_power)
        return None

