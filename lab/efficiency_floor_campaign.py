"""The efficiency search over the min SOC floor (21 October 2026).

On the field Kostal `Battery:ManualCharge` is a forced charge: switching it on
lifts the charge limit, and 3.10.8 keeps it off for the rest of the window. The
raised min SOC then charges the battery from the grid by itself, under the
written limit - and that is where the search measures now.

Real HA, real KORE, the emulated Kostal with the forced charge and the float
readback; the conversion curve is the same fixture as efficiency_campaign.py
(optimum at 8 kW). Only the finder's measurement clock is accelerated.
"""
from datetime import timedelta
from unittest.mock import patch


async def check_efficiency_over_the_floor(hass, lab, planner, kore_entry, check):
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import dt as dt_util

    hass.states.async_set("sun.sun", "below_horizon")
    co = planner.runtime_data
    process = hass.data["kostal_kore"][kore_entry.entry_id]["process_coordinator"]
    modbus = hass.data["kostal_kore"][kore_entry.entry_id]["modbus_coordinator"]
    saved_data = dict(planner.data)
    saved_options = dict(planner.options)
    await hass.services.async_call("inverter_charge_night", "reset_inverter", blocking=True)
    lab.plant.soc, lab.plant.min_soc, lab.plant.pv_power = 10, 8, 0
    lab.plant.grid_charge = False
    lab.plant.wallbox1_connected = lab.plant.wallbox2_connected = False
    charge = hass.data[DATA_INSTANCES]["number"].get_entity(planner.data["charge_power_entity"])
    native_ceiling = charge.native_max_value * 2 + .25
    lab.plant.charge_limit = native_ceiling
    lab.emulator.readback_offset_w = 1.5
    lab.emulator.manual_charge_limit_w = native_ceiling
    lab.plant.floor_charges_from_grid = True
    lab.plant.advance(.001)
    lab.publish()
    await process.async_refresh()
    options = dict(planner.options)
    options.pop("auto_efficiency_data", None)  # a search from scratch
    hass.config_entries.async_update_entry(planner, options=options, data={**planner.data,
        "auto_efficient_charge": True, "min_charge_power_w": 1000, "max_charge_power_w": 15000,
        "charge_energy_sent_entity": None, "charge_energy_received_entity": None,
        # The AC side from the actual import, not the per-phase guard value the
        # connection limit reads: that one is the larger of the two under an
        # unbalanced load and would overstate what the charger takes
        "grid_import_entity": "sensor.icn_lab_grid_power"})
    await hass.async_block_till_done()
    co = planner.runtime_data
    enabled = next(entity.entity_id for entity in er.async_get(hass).entities.values()
                   if entity.config_entry_id == planner.entry_id and entity.unique_id.endswith("_enabled"))
    await hass.services.async_call("switch", "turn_off", {"entity_id": enabled}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": enabled}, blocking=True)
    received = []
    audit = lab.audit

    def recording(key, value, accepted=True):
        received.append((key, value, accepted))
        audit(key, value, accepted)

    lab.audit = recording
    grid_switch = hass.data[DATA_INSTANCES]["switch"].get_entity(planner.data["grid_charge_switch"])
    try:
        await hass.services.async_call("inverter_charge_night", "charge_to",
            {"target_soc": 100, "duration": {"hours": 12}}, blocking=True)
        await co.async_refresh()
        await hass.async_block_till_done()
        await grid_switch.coordinator.async_refresh()
        check("floor search: the forced charge is found and the switch kept off",
              co._grid_switch_suspended and not lab.plant.grid_charge,
              {"suspended": co._grid_switch_suspended, "grid": lab.plant.grid_charge,
               "commands": received[:8]})

        clock = [dt_util.now()]
        hours = 0.0
        with patch.object(co, "_efficiency_now", side_effect=lambda: clock[0]):
            for _ in range(480):
                power = lab.plant.charge_limit
                lab.plant.charge_efficiency = .96 - .18 * ((power - 8000) / 7000) ** 2
                lab.plant.advance(30)
                hours += 30 / 3600
                clock[0] += timedelta(seconds=30)
                lab.publish()
                await modbus.async_refresh()
                await grid_switch.coordinator.async_refresh()
                await process.async_refresh()
                await hass.async_block_till_done()
                await co._verify_ac_charge_limit()
                await co._handle_auto_charge()
                if not co.auto_efficient_charge:
                    break
        data = co.get_auto_efficiency_data()
        check("floor search: it finishes without ever switching grid charging on again",
              not co.auto_efficient_charge
              and [v for k, v, _ in received if k == "grid_charge"] in ([True, False], [False], []),
              {"hours": round(hours, 2), "state": co.auto_test_state,
               "switch": [v for k, v, _ in received if k == "grid_charge"], "data": data})
        check("floor search: it locates the 8 kW optimum",
              abs(data.get("best_power_w", 0) - 8000) <= 300, data.get("best_power_w"))
        check("floor search: the loss matches the fixture, with its uncertainty recorded",
              abs(data.get("best_loss", 1) - .04) <= .002 and "uncertainty" in data,
              {"loss": data.get("best_loss"), "uncertainty": data.get("uncertainty")})
        trials = sorted({int(v) for k, v, a in received if k == "charge_limit" and a})
        check("floor search: coarse first, then around the best - few trials",
              3 <= len(data.get("history", {})) <= 14, {"history": data.get("history"), "written": trials})
    finally:
        lab.audit = audit
        lab.emulator.readback_offset_w = 0.0
        lab.emulator.manual_charge_limit_w = None
        lab.plant.floor_charges_from_grid = False
        lab.plant.charge_efficiency = .90
        await hass.services.async_call("inverter_charge_night", "reset_inverter", blocking=True)
        hass.config_entries.async_update_entry(planner, data=saved_data, options=saved_options)
        await hass.async_block_till_done()
