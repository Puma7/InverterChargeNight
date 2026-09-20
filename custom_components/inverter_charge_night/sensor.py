"""Sensor platform for Inverter Charge Night."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import InverterChargeNightConfigEntry, InverterChargeNightCoordinator
from .entity import InverterChargeNightEntity
from .const import (
    ATTR_DISCHARGE_BLOCK,
    ATTR_INVERTER_FLOOR_SOC,
    ATTR_SNOW_NIGHTS,
    AUTO_TEST_STATE_FINISHED,
    AUTO_TEST_STATE_IDLE,
    AUTO_TEST_STATE_MEASURING,
    AUTO_TEST_STATE_SETTLING,
    AUTO_TEST_STATE_WAITING,
)

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
            EfficiencySearchSensor(coordinator, entry),
        ]
    )


class CalculatedSOCSensor(InverterChargeNightEntity, SensorEntity):
    """Sensor for calculated SOC."""

    _attr_translation_key = "calculated_soc"
    _attr_native_unit_of_measurement = "%"
    # Deliberately no BATTERY device class: this is the level the planner is
    # aiming for, not the level of a battery. Declared as one it would show up
    # in battery cards and low-battery automations as if it were a reading.
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
            # Plan 009: why the inverter may show a higher min SOC than the
            # charge target while the window is open.
            ATTR_INVERTER_FLOOR_SOC: self.coordinator.inverter_floor_soc(),
            ATTR_DISCHARGE_BLOCK: self.coordinator.discharge_block_state(),
        }


class BestChargePowerSensor(InverterChargeNightEntity, SensorEntity):
    """Sensor for best charge power found by auto efficient charge.

    Disabled by default: it belongs to the efficiency search, which is off
    unless the user turns it on, and reports nothing until that search has
    finished. Whoever runs the search enables it from the device page.
    """

    _attr_translation_key = "best_charge_power"
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

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
    """The AC charge power planned for the remaining window (plan 006, step 6).

    The plan is shown in every mode, because what the window would need is
    worth knowing on its own. Whether it is also ordered from the inverter
    depends on the configuration - the planner only owns the setpoint in
    bridge mode or behind a house connection limit, and only with an AC charge
    limit entity to write to. ``applied`` says which of the two this is, so a
    plan that reaches nothing cannot be read as a command.
    """

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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Say whether this number is reaching the inverter."""
        return {"applied": self.coordinator.planned_power_is_applied}


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


class EfficiencySearchSensor(InverterChargeNightEntity, SensorEntity):
    """What the efficiency search is doing, and what it has found so far.

    The search takes one measurement per night and needs several nights, so
    without this sensor there is no way to tell a search that is working from
    one that is quietly discarding every sample.

    Disabled by default for the same reason as the sensor above, and because
    its attributes carry the whole measurement series: state that the recorder
    would keep for every user, including the ones who never run a search.
    """

    _attr_translation_key = "efficiency_search"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_options = [
        AUTO_TEST_STATE_IDLE,
        AUTO_TEST_STATE_WAITING,
        AUTO_TEST_STATE_SETTLING,
        AUTO_TEST_STATE_MEASURING,
        AUTO_TEST_STATE_FINISHED,
    ]

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "efficiency_search")

    @property
    def native_value(self) -> str:
        """Return the state of the search."""
        return str(self.coordinator.auto_test_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the measurements, the search range and the last result."""
        return self.coordinator.auto_test_attributes()
