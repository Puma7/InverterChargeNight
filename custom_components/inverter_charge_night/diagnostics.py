"""Diagnostics support for Inverter Charge Night."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import InverterChargeNightConfigEntry, InverterChargeNightCoordinator

REDACT_KEYS = {
    "pv_forecast_entity",
    "battery_soc_entity",
    "kostal_min_soc_entity",
    "kostal_grid_charge_switch",
    "charge_power_entity",
    "charge_power_sent_entity",
    "charge_power_received_entity",
    "absolute_max_charge_power_entity",
    "backup_mode_entity",
    "pv_forecast_today_entity",
    "force_discharge_switch",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: InverterChargeNightConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: InverterChargeNightCoordinator | None = getattr(entry, "runtime_data", None)
    data: dict[str, Any] = {
        "entry": async_redact_data(dict(entry.data), REDACT_KEYS),
        "options": async_redact_data(dict(entry.options), REDACT_KEYS),
    }
    if coordinator:
        auto_data = coordinator.get_auto_efficiency_data()
        auto_test_duration_s: int | None = None
        if coordinator._auto_test_active and coordinator._auto_test_start:
            auto_test_duration_s = int(
                (dt_util.now() - coordinator._auto_test_start).total_seconds()
            )
        data["state"] = {
            "is_active": coordinator.is_active,
            "is_enabled": coordinator.is_enabled,
            "operation_mode": coordinator.operation_mode,
            "skip_next": coordinator.skip_next,
            "target_reached": coordinator.target_reached,
            "calculated_soc": coordinator.calculated_soc,
            "initial_calculated_soc": coordinator.initial_calculated_soc,
            "minimum_calculated_soc": coordinator.minimum_calculated_soc,
            "override_soc": coordinator.override_soc,
            "auto_efficient_charge": coordinator.auto_efficient_charge,
            "auto_efficiency_best_power_w": auto_data.get("best_power_w"),
            "auto_efficiency_best_loss": auto_data.get("best_loss"),
            "auto_efficiency_history": auto_data.get("history"),
            "auto_test_active": coordinator._auto_test_active,
            "auto_test_power_w": coordinator._auto_test_power_w,
            "auto_test_duration_s": auto_test_duration_s,
            "auto_test_energy_sent_wh": coordinator._auto_energy_sent_wh,
            "auto_test_energy_received_wh": coordinator._auto_energy_received_wh,
        }
    return data
