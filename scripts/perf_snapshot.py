"""Performance snapshot for Inverter Charge Night."""
from __future__ import annotations

import asyncio
import os
import sys
import time
import tracemalloc
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.getcwd())

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_UPDATE_INTERVAL,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.async_create_task = MagicMock()
    hass.async_create_background_task = MagicMock()
    return hass


def _make_state(value: str) -> MagicMock:
    state = MagicMock()
    state.state = value
    state.attributes = {}
    return state


async def _run_once() -> None:
    hass = _make_hass()
    entry = MagicMock()
    entry.entry_id = "perf_entry"
    entry.title = "Perf"
    entry.data = {
        CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
        CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
        CONF_PV_FORECAST_ENTITY: "sensor.pv",
        CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        CONF_BATTERY_CAPACITY: 10.0,
        CONF_START_TIME: "00:00",
        CONF_END_TIME: "05:59",
        CONF_USER_MIN_SOC: 8.0,
        CONF_USER_MAX_SOC: 100.0,
        CONF_FORECAST_ERROR_MARGIN: 10.0,
        CONF_DEFAULT_MIN_SOC: 8.0,
        CONF_UPDATE_INTERVAL: 900,
        CONF_COMMAND_DELAY: 0.1,
        CONF_MIN_CHARGE_POWER_W: 1000,
        CONF_MAX_CHARGE_POWER_W: 2000,
    }
    entry.options = {}

    def _get_state(entity_id: str):
        if entity_id == "sensor.pv":
            return _make_state("5")
        if entity_id == "sensor.soc":
            return _make_state("10")
        if entity_id == "number.min_soc":
            return _make_state("8")
        if entity_id == "switch.grid":
            return _make_state("off")
        return _make_state("unknown")

    hass.states.get.side_effect = _get_state

    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.initial_calculated_soc = 50.0
    coordinator.minimum_calculated_soc = 50.0
    await coordinator._async_update_data()


def main() -> None:
    tracemalloc.start()
    start = time.perf_counter()
    asyncio.run(_run_once())
    elapsed = time.perf_counter() - start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("perf_snapshot")
    print(f"elapsed_s={elapsed:.6f}")
    print(f"mem_current_kib={current / 1024:.2f}")
    print(f"mem_peak_kib={peak / 1024:.2f}")


if __name__ == "__main__":
    main()
