"""Accelerate only the finder's measurement clock; keep real HA/KORE I/O.

The known conversion curve is a test fixture, not a measured inverter curve.
Neither HA timers nor the host clock are accelerated. No real device is used.
"""
from datetime import timedelta
from unittest.mock import patch


async def check_efficiency(hass, lab, planner, kore_entry, check):
    from homeassistant.util import dt as dt_util

    co = planner.runtime_data
    import logging
    logging.getLogger("custom_components.inverter_charge_night").setLevel(logging.INFO)
    process = hass.data["kostal_kore"][kore_entry.entry_id]["process_coordinator"]
    await hass.services.async_call("inverter_charge_night", "reset_inverter", blocking=True)
    lab.plant.soc, lab.plant.min_soc, lab.plant.pv_power = 10, 8, 0
    lab.plant.wallbox1_connected = lab.plant.wallbox2_connected = False
    lab.plant.advance(.001)
    lab.publish()
    await process.async_refresh()
    # Use the public entry update boundary rather than manufacturing an active
    # measurement or writing fabricated efficiency history.
    hass.config_entries.async_update_entry(planner, data={**planner.data,
        "auto_efficient_charge": True, "min_charge_power_w": 1000, "max_charge_power_w": 15000,
        "charge_energy_sent_entity": None, "charge_energy_received_entity": None})
    await hass.async_block_till_done()
    # Reconfiguration deliberately releases an existing window. Start the
    # experiment only once the new measurement configuration has settled.
    # reset_inverter also requests a manual hands-off period. Resume through
    # the same public automation switch a user operates; never bypass it.
    from homeassistant.helpers import entity_registry as er
    enabled = next(entity.entity_id for entity in er.async_get(hass).entities.values()
                   if entity.config_entry_id == planner.entry_id and entity.unique_id.endswith("_enabled"))
    await hass.services.async_call("switch", "turn_off", {"entity_id": enabled}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": enabled}, blocking=True)
    await hass.services.async_call("inverter_charge_night", "charge_to",
        {"target_soc": 100, "duration": {"hours": 12}}, blocking=True)
    # A second service call inside HA's refresh debounce does not itself run
    # another planning cycle. Exercise that real cycle explicitly.
    await co.async_refresh()
    await hass.async_block_till_done()
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    grid_switch = hass.data[DATA_INSTANCES]["switch"].get_entity(planner.data["grid_charge_switch"])
    await grid_switch.coordinator.async_refresh()
    modbus = hass.data["kostal_kore"][kore_entry.entry_id]["modbus_coordinator"]
    check("efficiency campaign starts with device-confirmed grid charging", lab.plant.grid_charge,
          {"active": co.is_active, "target": co.minimum_calculated_soc,
           "pending_reset": co._pending_reset, "hands_off": str(co._hands_off_until),
           "soc": lab.plant.soc, "min_soc": lab.plant.min_soc, "health": co.communication_diagnostics()})
    lab.commands.clear()

    clock = [dt_util.now()]
    actual_hours = 0
    with patch.object(co, "_efficiency_now", side_effect=lambda: clock[0]):
        for iteration in range(120):
            # Independent, smooth reference fixture with a unique optimum at
            # 8 kW. HA sensor values still come from the actual REST client.
            power = lab.plant.charge_limit
            lab.plant.charge_efficiency = .96 - .18 * ((power - 8000) / 7000) ** 2
            lab.plant.advance(120)
            actual_hours += 1 / 30
            clock[0] += timedelta(seconds=120)
            lab.publish()
            await modbus.async_refresh()
            await grid_switch.coordinator.async_refresh()
            await process.async_refresh()
            await hass.async_block_till_done()
            await co._handle_auto_charge()
            if iteration % 15 == 0:
                print(f"Finder sample {iteration}: state={co.auto_test_state}, enabled={co.auto_efficient_charge}, grid={lab.plant.grid_charge}, target_reached={co.target_reached}, SOC={lab.plant.soc:.1f}, results={co.get_auto_efficiency_data()}", flush=True)
            if not co.auto_efficient_charge:
                break
    data = co.get_auto_efficiency_data()
    check("efficiency search finishes using real KORE power sensors", not co.auto_efficient_charge,
          {"hours": round(actual_hours, 2), "state": co.auto_test_state, "data": data})
    check("efficiency search locates the known 8 kW fixture optimum",
          abs(data.get("best_power_w", 0) - 8000) <= 300, data.get("best_power_w"))
    check("recorded conversion loss matches the independent fixture",
          abs(data.get("best_loss", 1) - .04) <= .002, data.get("best_loss"))
    commands = [item["value"] for item in lab.commands if item["key"] == "charge_limit" and item["accepted"]]
    check("search actually writes multiple permitted device setpoints",
          len(set(commands)) >= 3 and all(0 <= value <= 15000 for value in commands), commands)
    await hass.services.async_call("inverter_charge_night", "reset_inverter", blocking=True)
    lab.plant.charge_efficiency = .90
