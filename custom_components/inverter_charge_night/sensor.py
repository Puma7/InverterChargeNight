"""Sensor platform for Inverter Charge Night."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import InverterChargeNightConfigEntry, InverterChargeNightCoordinator
from .entity import InverterChargeNightEntity
from .const import ATTR_SNOW_NIGHTS

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InverterChargeNightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            CalculatedSOCSensor(coordinator, entry),
            BestChargePowerSensor(coordinator, entry),
            PlannedChargePowerSensor(coordinator, entry),
            GridChargeHeadroomSensor(coordinator, entry),
        ]
    )


class CalculatedSOCSensor(InverterChargeNightEntity, SensorEntity):
    """Sensor for calculated SOC."""

    _attr_translation_key = "calculated_soc"
    _attr_native_unit_of_measurement = "%"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT

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
            # Planner v2 (plan 006): only present while a bridge plan exists
            "plan_reason": data.get("plan_reason"),
            "bridge_kwh": data.get("bridge_kwh"),
            "surplus_kwh": data.get("surplus_kwh"),
            "lower_bound_soc": data.get("lower_bound_soc"),
            "upper_bound_soc": data.get("upper_bound_soc"),
            "pv_crossover": data.get("pv_crossover"),
            ATTR_SNOW_NIGHTS: self.coordinator.snow_nights,
        }


class BestChargePowerSensor(InverterChargeNightEntity, SensorEntity):
    """Sensor for best charge power found by auto efficient charge."""

    _attr_translation_key = "best_charge_power"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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


class PlannedChargePowerSensor(InverterChargeNightEntity, SensorEntity):
    """The AC charge power planned for the remaining window (plan 006, step 6)."""

    _attr_translation_key = "planned_charge_power"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "planned_charge_power")

    @property
    def native_value(self) -> float | None:
        """Return the planned setpoint, or None outside a night charge window."""
        value = self.coordinator.planned_charge_power_w
        return float(value) if value is not None else None


class GridChargeHeadroomSensor(InverterChargeNightEntity, SensorEntity):
    """What the house connection still allows the battery (plan 008).

    ``None`` outside a window or without a configured connection limit: the
    integration is not holding the battery back then, and a number would
    suggest a limit that is not being applied.
    """

    _attr_translation_key = "grid_charge_headroom"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "grid_charge_headroom")

    @property
    def native_value(self) -> float | None:
        """Return the power the connection still has room for."""
        value = self.coordinator.grid_charge_headroom_w
        return float(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show whether the house connection limit is currently braking."""
        return self.coordinator.grid_limit_attributes()
