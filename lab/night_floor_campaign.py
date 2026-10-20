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

    # Legacy cleanup must survive a command whose new HA feedback is delayed.
    # The real REST/Modbus services still actuate the isolated inverter.
    co = planner.runtime_data
    block = hass.data[DATA_INSTANCES]["switch"].get_entity(data["discharge_block_switch"])
    limit_entity = hass.data[DATA_INSTANCES]["number"].get_entity(limit)
    lab.plant.block_discharge, lab.plant.discharge_limit = True, 0
    await feedback()
    co._original_discharge_block, co._original_discharge_limit = False, 5000
    # HA's forced entity update also uses the private publication boundary.
    # Released once, at a window start (3.10.6); verifications no longer write it.
    with patch.object(block, "_async_write_ha_state"), patch.object(limit_entity, "_async_write_ha_state"):
        await co._apply_discharge_block(announce=True)
    check("legacy release actuates real switch and Modbus limit without discarding delayed captures",
          not lab.plant.block_discharge and lab.plant.discharge_limit == 5000
          and co._original_discharge_block is False and co._original_discharge_limit == 5000,
          {"physical_block": lab.plant.block_discharge, "physical_limit": lab.plant.discharge_limit,
           "capture_block": co._original_discharge_block, "capture_limit": co._original_discharge_limit})
    block.async_write_ha_state()
    limit_entity.async_write_ha_state()
    await feedback()
    # A verification is not a window start: it neither writes nor clears.
    writes = []

    def recording(event):
        if event.data.get("service") in ("turn_on", "turn_off", "set_value"):
            writes.append((event.data.get("domain"), event.data.get("service")))

    unsubscribe = hass.bus.async_listen("call_service", recording)
    try:
        await co._apply_discharge_block()
        await hass.async_block_till_done()
    finally:
        unsubscribe()
    check("a verification leaves the legacy release to the window start or end",
          not writes and co._original_discharge_block is False and co._original_discharge_limit == 5000,
          {"writes": writes, "capture_block": co._original_discharge_block,
           "capture_limit": co._original_discharge_limit})
    await co._apply_discharge_block(announce=True)
    check("legacy release clears both captures after fresh matching HA feedback",
          co._original_discharge_block is None and co._original_discharge_limit is None)

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

    # Native battery ceilings can be larger than KORE's accepted HA write range
    # (the field Kostal read 22063 W through a 12540 W control). Exercise the
    # real range validator and Modbus readback on recovery (from #35).
    co = planner.runtime_data
    charge_id = data["charge_power_entity"]
    charge_number = hass.data[DATA_INSTANCES]["number"].get_entity(charge_id)
    maximum = charge_number.native_max_value
    previous_limit = lab.plant.charge_limit
    native_limit = maximum * 2 + .25
    lab.plant.charge_limit = native_limit
    modbus._slow_tick = 5
    await feedback()
    check("native charge ceiling above HA maximum is observed before capture",
          float(hass.states.get(charge_id).state) == native_limit,
          {"observed_w": hass.states.get(charge_id).state, "native_w": native_limit})
    check("protective zero captures the native ceiling through real KORE services",
          await co._set_ac_charge_limit_w(0)
          and co._original_ac_charge_power == native_limit and lab.plant.charge_limit == 0)
    co.original_min_soc = 8
    check("bounded ceiling recovery completes the whole reset without pending retry",
          await co._reset_settings() and not co._pending_reset
          and co._original_ac_charge_power is None and lab.plant.charge_limit == maximum
          and lab.plant.min_soc == 8 and not lab.plant.grid_charge,
          {"captured_w": native_limit, "restored_w": lab.plant.charge_limit, "maximum_w": maximum})
    lab.plant.charge_limit = previous_limit
    await feedback()
    hass.config_entries.async_update_entry(planner, data=saved_data)
    lab.plant.forecast_today, lab.plant.forecast_tomorrow = forecasts
    lab.plant.house_load = house_load
    await hass.async_block_till_done()
