"""Grid charging switched on once a night, not every 15 minutes (21 October 2026).

The night of 20/21 October through real HA, real KORE and the emulated Kostal,
with the two quirks the field log showed:

* the charge limit reads back as a float a little below what was written
  (1000 W -> 998.5 W), so KORE reports every write as unconfirmed;
* Battery:ManualCharge is a forced charge: switching it on lifts the limit to
  the inverter's own value, and a limit written while it is on is ignored.

The Kostal also charges from the grid up to a raised min SOC by itself, under
its charge limit. Before 3.10.8 every poll switched ManualCharge on, saw the
limit fail and switched it off again. Now: on at most once, the limit holds
otherwise, the floor charges the battery to its target, and nothing reaches
the inverter after the window end.
"""
import asyncio
from datetime import timedelta

STEP = timedelta(minutes=15)


async def check_forced_charge(hass, lab, planner, kore_entry, check):
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.util import dt as dt_util

    saved_data = dict(planner.data)
    saved_plant = {key: getattr(lab.plant, key) for key in (
        "soc", "min_soc", "charge_limit", "grid_charge", "ev_load", "house_load",
        "floor_charges_from_grid")}
    process = hass.data["kostal_kore"][kore_entry.entry_id]["process_coordinator"]
    modbus = hass.data["kostal_kore"][kore_entry.entry_id]["modbus_coordinator"]
    # Real time, not a patched clock: the house connection limit is configured
    # as in the field, and its meter must stay fresh. The physics run in
    # 15-minute steps; Home Assistant sees each step as a poll.
    now = dt_util.now()
    data = {**saved_data, "start_time": (now - timedelta(minutes=1)).strftime("%H:%M"),
            "end_time": (now + timedelta(hours=6)).strftime("%H:%M"),
            "planner_mode": "headroom", "forecast_error_margin": 0,
            # On in the field: its trial write right after switching on was
            # what found the lifted limit every 15 minutes
            "auto_efficient_charge": True}
    hass.config_entries.async_update_entry(planner, data=data)
    await hass.async_block_till_done()
    minimum = hass.data[DATA_INSTANCES]["number"].get_entity(data["min_soc_entity"])
    grid = hass.data[DATA_INSTANCES]["switch"].get_entity(data["grid_charge_switch"])
    charge = hass.data[DATA_INSTANCES]["number"].get_entity(data["charge_power_entity"])
    native_ceiling = charge.native_max_value * 2 + .25  # the field: 22.3 kW behind 12.54 kW
    override = next(e.entity_id for e in er.async_get(hass).entities.values()
                    if e.config_entry_id == planner.entry_id and e.domain == "number"
                    and "override" in e.unique_id)
    clock = [now]  # simulated time, for the record only
    received = []
    audit = lab.audit

    def recording(key, value, accepted=True):
        received.append((clock[0], key, value, accepted))
        audit(key, value, accepted)

    async def feedback():
        lab.publish()
        await process.async_refresh()
        await modbus.async_refresh()
        await minimum.coordinator.async_refresh()
        await grid.coordinator.async_refresh()
        await hass.async_block_till_done()

    lab.audit = recording
    lab.emulator.readback_offset_w = 1.5
    lab.emulator.manual_charge_limit_w = native_ceiling
    # The third quirk, in real seconds: a limit on 1038 lapses back to the
    # Kostal's own value 3 s after its last write; the keepalive runs every 1 s
    import custom_components.inverter_charge_night.coordinator as icn
    keepalive_s = icn.CHARGE_LIMIT_KEEPALIVE_S
    icn.CHARGE_LIMIT_KEEPALIVE_S = 1
    lab.emulator.native_charge_limit_w = native_ceiling
    lab.emulator.limit_fallback_s = 3.0
    try:
        if True:
            plant = lab.plant
            plant.soc, plant.min_soc, plant.grid_charge = 33.0, 8.0, False
            plant.charge_limit, plant.house_load, plant.ev_load = native_ceiling, 500, 0
            plant.pv_power = plant.pv_string1 = plant.pv_string2 = 0
            plant.floor_charges_from_grid = True
            plant.advance(.001)
            await feedback()
            co = planner.runtime_data
            # The morning campaign ran its windows days ahead on a patched clock;
            # their scheduled end would read as a clock rollback here
            co._last_scheduled_end = None
            await hass.services.async_call(
                "number", "set_value", {"entity_id": override, "value": 44}, blocking=True)
            await co._on_window_start(dt_util.now())
            await co.async_refresh()
            await feedback()
            check("forced charge: the night starts with the 8 % original and a 44 % target",
                  co.is_active and co.original_min_soc == 8.0 and plant.min_soc >= 44,
                  {"active": co.is_active, "original": co.original_min_soc, "floor": plant.min_soc,
                   "commands": [(k, v) for _, k, v, _ in received]})
            on_off = [(k, v) for t, k, v, _ in received if k == "grid_charge"]
            check("forced charge: with the switch off again the planned limit holds at once",
                  co._planned_setpoint_written_w is not None
                  and abs(plant.charge_limit - co._planned_setpoint_written_w) < 1,
                  {"plant_limit": plant.charge_limit, "written": co._planned_setpoint_written_w})
            check("forced charge: the lifted limit switches grid charging off again in the same poll",
                  on_off in ([], [("grid_charge", True), ("grid_charge", False)])
                  and not plant.grid_charge, {"commands": on_off,
                  "limit_state": hass.states.get(data["charge_power_entity"]).state,
                  "switch_state": hass.states.get(data["grid_charge_switch"]).state,
                  "written": co._planned_setpoint_written_w, "plant_limit": plant.charge_limit,
                  "suspended": co._grid_switch_suspended})

            peak_w = 0.0
            bought_before = plant.grid_charge_energy
            while clock[0] < now + timedelta(hours=4):
                await asyncio.sleep(1.5)  # real time for the lapse and the keepalive
                lab.emulator.apply_limit_fallback()
                plant.advance(STEP.total_seconds())
                peak_w = max(peak_w, plant.charge_ac_power)
                clock[0] += STEP
                await feedback()
                await co.async_refresh()
                await co._verify_and_restore_min_soc()
                await co._verify_ac_charge_limit()
                await co._check_current_window()
                await hass.async_block_till_done()

            night = list(received)
            switched_on = [r for r in night if r[1] == "grid_charge" and r[2]]
            check("forced charge: grid charging is switched on at most once in the night",
                  len(switched_on) <= 1, [(t.strftime("%H:%M"), k, v) for t, k, v, _ in night])
            check("forced charge: the switch stays off for the rest of the window",
                  not plant.grid_charge and co._grid_switch_suspended is (len(switched_on) == 1),
                  {"grid_charge": plant.grid_charge, "suspended": co._grid_switch_suspended})
            limits = [r for r in night if r[1] == "charge_limit"]
            # The keepalive writes about once a real second while the battery charges
            kept = [r for r in limits if r[0] > night[0][0]]
            check("forced charge: the limit is kept alive while the battery charges",
                  len(kept) >= 10, len(kept))
            first_limit = next(i for i, r in enumerate(night) if r[1] == "charge_limit")
            first_floor = next(i for i, r in enumerate(night) if r[1] == "min_soc" and r[2] >= 44)
            check("forced charge: the limit goes on before the floor that makes the Kostal charge",
                  first_limit < first_floor, [(k, v) for _, k, v, _ in night[:4]])
            # The plan runs on the real clock, which barely moves: it keeps
            # spreading the rest over six hours, so the night is not finished
            # Energy, not a peak: the model's last 5-s step may end at the floor
            bought = plant.grid_charge_energy - bought_before
            planned_w = max((v for _, k, v, accepted in received if k == "charge_limit" and accepted),
                            default=0.0)
            allowed = planned_w * 4 / 1000 + .1  # four hours at the highest limit written
            check("forced charge: the raised floor keeps charging, under the planned limit",
                  plant.soc > 38 and peak_w <= 731 + 100 and bought <= allowed,
                  {"soc": plant.soc, "peak_w": peak_w, "bought_kwh": round(bought, 2),
                   "allowed_kwh": round(allowed, 2)})
            issues = [i.issue_id for i in ir.async_get(hass).issues.values()
                      if i.domain == "inverter_charge_night" and i.issue_id.startswith("charge_limit_not_held")]
            check("forced charge: a float readback is no reason for a repair issue", not issues, issues)

            # Ended at its scheduled end, so the real clock does not reopen it
            from custom_components.inverter_charge_night.util import scheduled_window_bounds
            bounds = scheduled_window_bounds(dt_util.now(), *co._window_times())
            await co._on_window_end(bounds[1], scheduled=True)
            await feedback()
            # The restore reads back as a float too: KORE calls it unconfirmed,
            # and the first retry, 60 s later, finds it back without writing -
            # as in the field at 04:59 on 21 October. The keepalive (1 s here)
            # has stopped with the window: 65 s in which it would have written
            await asyncio.sleep(65)
            await feedback()
            ended = len(received)
            for _ in range(16):
                plant.advance(STEP.total_seconds())
                clock[0] += STEP
                await feedback()
                await co.async_refresh()
                await co._verify_and_restore_min_soc()
                await co._check_current_window()
                await hass.async_block_till_done()
            late = [(t.strftime("%H:%M"), k, v) for t, k, v, _ in received[ended:]]
            check("forced charge: after the window end the inverter receives nothing",
                  not late and plant.min_soc == 8.0 and not co._pending_reset, late)
    finally:
        lab.audit = audit
        icn.CHARGE_LIMIT_KEEPALIVE_S = keepalive_s
        lab.emulator.limit_fallback_s = None
        lab.emulator.readback_offset_w = 0.0
        lab.emulator.manual_charge_limit_w = None
        hass.config_entries.async_update_entry(planner, data=saved_data)
        for key, value in saved_plant.items():
            setattr(lab.plant, key, value)
        await feedback()
