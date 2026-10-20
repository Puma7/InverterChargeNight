"""After the window end at 05:00, nothing more reaches the inverter (20 October 2026).

Three nights through real HA, real KORE and the emulated Kostal, each with the
field's quirks: the inverter reports a native charge ceiling above what the KORE
control accepts, drops any limit written to it back to that native value, and
at 03:10 answers Modbus with nothing for a moment. The original min SOC is 10 %.

* charge:  50 % -> 60 % from the grid
* drain:   50 % -> 20 %, the house runs on the battery down to the floor
* hold:    50 % stays at 50 %

Every command the emulated inverter receives is recorded, REST and Modbus. At
05:00 the window end may restore what the night changed - the min SOC once,
grid charging off once, the charge ceiling at most once. From then on, through
eight simulated hours of polls, verifications and window checks, it must
receive nothing at all.
"""
from datetime import timedelta
from unittest.mock import patch

STEP = timedelta(minutes=15)
ORIGINAL_MIN_SOC = 10.0
SCENARIOS = (("charge", 60.0), ("drain", 20.0), ("hold", 50.0))


async def check_morning_silence(hass, lab, planner, kore_entry, check):
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.util import dt as dt_util

    saved_data = dict(planner.data)
    house_load = lab.plant.house_load
    # Later campaigns start from the plant as it was (min SOC 8 %, normal limits)
    saved_plant = {key: getattr(lab.plant, key)
                   for key in ("soc", "min_soc", "charge_limit", "grid_charge", "ev_load")}
    forecasts = (lab.plant.forecast_today, lab.plant.forecast_tomorrow)
    data = {**saved_data, "start_time": "23:00", "end_time": "05:00",
            "planner_mode": "headroom", "forecast_error_margin": 0,
            "grid_import_entity": None, "main_fuse_a": None}
    data.pop("discharge_block_switch", None)
    data.pop("discharge_limit_entity", None)
    hass.config_entries.async_update_entry(planner, data=data)
    await hass.async_block_till_done()
    process = hass.data["kostal_kore"][kore_entry.entry_id]["process_coordinator"]
    modbus = hass.data["kostal_kore"][kore_entry.entry_id]["modbus_coordinator"]
    minimum = hass.data[DATA_INSTANCES]["number"].get_entity(data["min_soc_entity"])
    grid = hass.data[DATA_INSTANCES]["switch"].get_entity(data["grid_charge_switch"])
    charge = hass.data[DATA_INSTANCES]["number"].get_entity(data["charge_power_entity"])
    native_ceiling = charge.native_max_value * 2 + .25  # the field: 22063 W behind 12540 W
    # The target is set the way a user sets it: through ICN's own override number
    override = next(e.entity_id for e in er.async_get(hass).entities.values()
                    if e.config_entry_id == planner.entry_id and e.domain == "number"
                    and "override" in e.unique_id)
    start = dt_util.now().replace(hour=23, minute=0, second=0, microsecond=0) + timedelta(days=10)
    clock = [start]

    # Every command that reaches the emulated inverter, with the simulated time
    received = []
    audit = lab.audit

    def recording(key, value, accepted=True):
        received.append((clock[0], key, value))
        audit(key, value, accepted)

    lab.audit = recording

    async def feedback():
        lab.publish()
        await process.async_refresh()
        await modbus.async_refresh()
        await minimum.coordinator.async_refresh()
        await grid.coordinator.async_refresh()
        await hass.async_block_till_done()

    def kostal_falls_back():
        # The Kostal drops an external charge limit back to its own value
        lab.plant.charge_limit = native_ceiling

    async def poll(co):
        await feedback()
        await co.async_refresh()
        await co._verify_and_restore_min_soc()
        await co._check_current_window()
        await hass.async_block_till_done()

    try:
        with patch("custom_components.inverter_charge_night.coordinator.dt_util.now",
                   side_effect=lambda: clock[0]):
            for name, target in SCENARIOS:
                plant = lab.plant
                clock[0] = start
                plant.soc = 50.0
                plant.min_soc = ORIGINAL_MIN_SOC
                plant.grid_charge = plant.block_discharge = False
                plant.pv_power = plant.pv_string1 = plant.pv_string2 = 0
                plant.house_load = 2000 if name == "drain" else 600
                plant.ev_load = 0
                kostal_falls_back()
                plant.advance(.001)
                await feedback()
                co = planner.runtime_data
                await hass.services.async_call(
                    "number", "set_value", {"entity_id": override, "value": target}, blocking=True)
                await co._on_window_start(start)
                await co.async_refresh()
                await feedback()
                check(f"{name}: the night captures 10 % and holds the {target:.0f} % floor",
                      co.is_active and co.original_min_soc == ORIGINAL_MIN_SOC and plant.min_soc == target,
                      {"original": co.original_min_soc, "floor": plant.min_soc})

                while clock[0] < start + timedelta(hours=6) - STEP:
                    plant.advance(STEP.total_seconds())
                    clock[0] += STEP
                    kostal_falls_back()
                    if clock[0] == start + timedelta(hours=4, minutes=15):
                        # 03:15: the inverter answers Modbus with nothing for a moment
                        lab.emulator.modbus_online = False
                        await poll(co)
                        lab.emulator.modbus_online = True
                    await poll(co)

                soc_at_end = plant.soc
                expected = {"charge": abs(soc_at_end - 60) < .5,
                            "drain": abs(soc_at_end - 20) < .5,
                            "hold": abs(soc_at_end - 50) < .5}[name]
                check(f"{name}: the battery ends the night where the plan put it",
                      expected, {"soc": soc_at_end, "target": target})

                # 05:00 - the cheap window ends and everything is handed back
                clock[0] = start + timedelta(hours=6)
                at_end = len(received)
                await co._on_window_end(clock[0], scheduled=True)
                await feedback()
                reset_writes = received[at_end:]
                check(f"{name}: 05:00 restores 10 %, grid charging off, nothing pending",
                      plant.min_soc == ORIGINAL_MIN_SOC and not plant.grid_charge
                      and not co.is_active and not co._pending_reset and not co._reset_abandoned
                      and co.original_min_soc is None and co._original_ac_charge_power is None
                      and co._reset_retry_unsub is None,
                      {"min_soc": plant.min_soc, "grid": plant.grid_charge, "pending": co._pending_reset,
                       "abandoned": co._reset_abandoned, "writes": [(k, v) for _, k, v in reset_writes]})
                keys = [key for _, key, _ in reset_writes]
                check(f"{name}: the window end writes each setting at most once",
                      keys.count("min_soc") <= 1 and keys.count("grid_charge") <= 1
                      and keys.count("charge_limit") <= 1 and set(keys) <= {"min_soc", "grid_charge", "charge_limit"},
                      [(k, v) for _, k, v in reset_writes])

                # 05:00 - 13:00: polls, verifications, window checks, the Kostal
                # dropping limits and another short Modbus silence at 06:05
                after = len(received)
                while clock[0] < start + timedelta(hours=14):
                    plant.advance(STEP.total_seconds())
                    clock[0] += STEP
                    kostal_falls_back()
                    if clock[0] == start + timedelta(hours=7, minutes=0):
                        lab.emulator.modbus_online = False
                        await poll(co)
                        lab.emulator.modbus_online = True
                    await poll(co)
                late = [(t.strftime("%H:%M"), k, v) for t, k, v in received[after:]]
                check(f"{name}: from 05:00 to 13:00 the inverter receives no command at all",
                      not late, late)
                issues = [i.issue_id for i in ir.async_get(hass).issues.values()
                          if i.domain == "inverter_charge_night"
                          and i.issue_id.startswith(("reset_incomplete", "charge_limit_not_held"))]
                check(f"{name}: no repair issue is left behind", not issues, issues)
                start += timedelta(days=1)
    finally:
        lab.audit = audit
        lab.emulator.modbus_online = True
        hass.config_entries.async_update_entry(planner, data=saved_data)
        lab.plant.forecast_today, lab.plant.forecast_tomorrow = forecasts
        lab.plant.house_load = house_load
        for key, value in saved_plant.items():
            setattr(lab.plant, key, value)
        await feedback()
