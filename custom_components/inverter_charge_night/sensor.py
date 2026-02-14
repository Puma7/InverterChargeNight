"""Sensor platform for Inverter Charge Night."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
if TYPE_CHECKING:  # pragma: no cover
    from . import InverterChargeNightConfigEntry, InverterChargeNightCoordinator

PARALLEL_UPDATES = 1


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    """Return shared device info dict."""
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title or "Inverter Charge Night",
        "manufacturer": "Custom Integration",
        "model": "Inverter Charge Night",
    }


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
            ChargingEfficiencySensor(coordinator, entry),
            AutoTestStatusSensor(coordinator, entry),
        ]
    )


class CalculatedSOCSensor(CoordinatorEntity["InverterChargeNightCoordinator"], SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Sensor for calculated SOC."""

    _attr_translation_key = "calculated_soc"
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "%"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_calculated_soc"
        self._attr_device_info = _device_info(entry)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data
        self._attr_native_value = data.get("calculated_soc")
        self._attr_extra_state_attributes = {
            "is_active": data.get("is_active", False),
            "target_reached": data.get("target_reached", False),
            "current_soc": data.get("current_soc"),
        }
        self.async_write_ha_state()


class BestChargePowerSensor(CoordinatorEntity["InverterChargeNightCoordinator"], SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Sensor for best charge power found by auto efficient charge."""

    _attr_translation_key = "best_charge_power"
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "W"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_best_charge_power"
        self._attr_device_info = _device_info(entry)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator._get_auto_efficiency_data()
        best_power = data.get("best_power_w")
        self._attr_native_value = float(best_power) if isinstance(best_power, int) else None
        best_loss = data.get("best_loss")
        history = data.get("history", {})
        self._attr_extra_state_attributes = {
            "best_efficiency_pct": round((1.0 - best_loss) * 100.0, 2) if isinstance(best_loss, (int, float)) else None,
            "tests_completed": len(history),
        }
        self.async_write_ha_state()


class ChargingEfficiencySensor(CoordinatorEntity["InverterChargeNightCoordinator"], SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Sensor showing the efficiency of the last charge session."""

    _attr_translation_key = "charging_efficiency"
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lightning-bolt-circle"

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_charging_efficiency"
        self._attr_device_info = _device_info(entry)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        session_data = self.coordinator._get_session_data()
        last: dict[str, Any] = session_data.get("last_session", {})
        eff = last.get("efficiency_pct")
        self._attr_native_value = float(eff) if eff is not None else None
        self._attr_extra_state_attributes = {
            "energy_sent_wh": last.get("energy_sent_wh"),
            "energy_received_wh": last.get("energy_received_wh"),
            "loss_pct": last.get("loss_pct"),
            "avg_power_sent_w": last.get("avg_power_sent_w"),
            "avg_power_received_w": last.get("avg_power_received_w"),
            "duration_s": last.get("duration_s"),
            "timestamp": last.get("timestamp"),
        }
        self.async_write_ha_state()


class AutoTestStatusSensor(CoordinatorEntity["InverterChargeNightCoordinator"], SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Sensor showing the current auto-efficiency test status."""

    _attr_translation_key = "auto_test_status"
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:test-tube"

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_auto_test_status"
        self._attr_device_info = _device_info(entry)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator._auto_test_active:
            power = self.coordinator._auto_test_power_w
            self._attr_native_value = f"testing_{power}W" if power else "testing"
        elif self.coordinator.auto_efficient_charge:
            self._attr_native_value = "waiting"
        else:
            self._attr_native_value = "idle"
        data = self.coordinator._get_auto_efficiency_data()
        history = data.get("history", {})
        self._attr_extra_state_attributes = {
            "tests_completed": len(history),
            "auto_efficient_charge_enabled": self.coordinator.auto_efficient_charge,
            "current_test_power_w": self.coordinator._auto_test_power_w,
        }
        self.async_write_ha_state()

