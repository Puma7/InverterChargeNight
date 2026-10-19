"""Scheduled nights through real HA/KORE services and the physical battery model."""
from datetime import timedelta
from unittest.mock import patch


async def check_night_floor(hass, lab, planner, kore_entry, check):
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import dt as dt_util

    saved_data = dict(planner.data)
    house_load = lab.plant.house_load
    # The fixed simulated night must not make a real-time grid meter stale.
    # Connection-budget behavior has its own coupled and unit campaigns.
    limit = next(e.entity_id for e in er.async_get(hass).entities.values()
                 if e.config_entry_id == kore_entry.entry_id
                 and e.unique_id.endswith("_modbus_bat_max_discharge_limit"))
    forecasts = (lab.plant.forecast_today, lab.plant.forecast_tomorrow)
    lab.plant.forecast_today = lab.plant.forecast_tomorrow = lab.plant.capacity * .7
    data = {**saved_data, "start_time": "23:00", "end_time": "05:00",
            "planner_mode": "headroom", "forecast_error_margin": 0,
            "grid_import_entity": None, "main_fuse_a": None,
            "discharge_limit_entity": limit,
            "discharge_block_switch": "switch.icn_lab_block_discharge"}
    hass.config_entries.async_update_entry(planner, data=data)
    await hass.async_block_till_done()
    process = hass.data["kostal_kore"][kore_entry.entry_id]["process_coordinator"]
    modbus = hass.data["kostal_kore"][kore_entry.entry_id]["modbus_coordinator"]
    minimum = hass.data[DATA_INSTANCES]["number"].get_entity(data["min_soc_entity"])
    grid = hass.data[DATA_INSTANCES]["switch"].get_entity(data["grid_charge_switch"])
    start = dt_util.now().replace(hour=23, minute=0, second=0, microsecond=0)
    clock = [start + timedelta(minutes=1)]

    async def feedback():
        lab.publish()
        await process.async_refresh()
        await modbus.async_refresh()
        await minimum.coordinator.async_refresh()
        await grid.coordinator.async_refresh()
        await hass.async_block_till_done()

    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now",
               side_effect=lambda: clock[0]):
        for initial in (100, 35, 15):
            clock[0] = start + timedelta(minutes=1)
            plant = lab.plant
            plant.soc = initial
            plant.min_soc = 8
            plant.grid_charge = plant.block_discharge = False
            plant.discharge_limit = 5000
            plant.pv_power = plant.pv_string1 = plant.pv_string2 = 0
            plant.house_load = 2000
            plant.ev_load = 0
            plant.advance(.001)
            await feedback()
            co = planner.runtime_data
            await co._on_window_start(start)
            await co.async_refresh()
            await feedback()
            check(f"night {initial}% captures original 8% and sets only target 30%",
                  co.is_active and co.original_min_soc == 8 and plant.min_soc == 30,
                  {"original": co.original_min_soc, "floor": plant.min_soc})
            check(f"night {initial}% leaves configured block switch and discharge limit open",
                  not plant.block_discharge and plant.discharge_limit == 5000)
            before = (plant.house_battery_energy, plant.house_grid_energy, plant.grid_charge_energy)
            plant.advance(6 * 3600)
            clock[0] = start + timedelta(hours=5, minutes=59)
            await feedback()
            await co.async_refresh()
            await feedback()
            if initial == 100:
                check("full battery supplies the complete night without grid charging",
                      60 < plant.soc < 70 and plant.house_battery_energy - before[0] > 11.9
                      and plant.house_grid_energy - before[1] < .001
                      and plant.grid_charge_energy == before[2],
                      {"soc": plant.soc, "house_battery_kwh": plant.house_battery_energy - before[0]})
                previous = co
                check("scheduled night reload succeeds", await hass.config_entries.async_reload(planner.entry_id))
                await feedback()
                co = planner.runtime_data
                check("reload keeps target floor and original without holding remaining energy",
                      co is not previous and co.is_active and co.original_min_soc == 8
                      and plant.min_soc == 30 and co._window_floor_soc is None,
                      {"soc": plant.soc, "floor": plant.min_soc, "original": co.original_min_soc})
            elif initial == 35:
                check("part-full battery supplies the house until 30%, then uses grid",
                      abs(plant.soc - 30) < .001 and plant.house_battery_energy - before[0] > 1.6
                      and plant.house_grid_energy - before[1] > 10
                      and plant.grid_charge_energy == before[2],
                      {"soc": plant.soc, "house_battery_kwh": plant.house_battery_energy - before[0]})
            else:
                check("low battery charges only to the planned 30%",
                      abs(plant.soc - 30) < .001 and plant.grid_charge_energy > before[2]
                      and not plant.grid_charge,
                      {"soc": plant.soc, "grid_charge": plant.grid_charge})
            clock[0] = start + timedelta(hours=6)
            await co._on_window_end(clock[0], scheduled=True)
            await feedback()
            if co._pending_reset:
                await co._retry_reset()
                await feedback()
            check(f"night {initial}% restores 8% at 05:00 without a residual block",
                  plant.min_soc == 8 and not plant.block_discharge and plant.discharge_limit == 5000
                  and not co.is_active and not co._pending_reset and co.original_min_soc is None)
            soc = plant.soc
            plant.advance(60)
            check(f"night {initial}% battery supplies the house after morning release",
                  plant.soc < soc and plant.battery_power < 0)
            # Completed windows intentionally cannot reopen after a clock rollback.
            start += timedelta(days=1)
    hass.config_entries.async_update_entry(planner, data=saved_data)
    lab.plant.forecast_today, lab.plant.forecast_tomorrow = forecasts
    lab.plant.house_load = house_load
    await hass.async_block_till_done()
