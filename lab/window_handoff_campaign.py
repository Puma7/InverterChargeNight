"""Real HA/KostalKore restore, immediate reserve and final discharge release."""
from unittest.mock import patch


async def check_window_handoff(hass, lab, planner, kore_entry, check):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.util import dt as dt_util

    co = planner.runtime_data
    process = hass.data["kostal_kore"][kore_entry.entry_id]["process_coordinator"]
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    minimum = hass.data[DATA_INSTANCES]["number"].get_entity(planner.data["min_soc_entity"])
    grid_switch = hass.data[DATA_INSTANCES]["switch"].get_entity(planner.data["grid_charge_switch"])
    # A night that already reached its target has grid charging off. This
    # eliminates an unrelated switch command that could hide a queued reset.
    await hass.services.async_call("switch", "turn_off",
        {"entity_id": planner.data["grid_charge_switch"]}, blocking=True)
    await grid_switch.coordinator.async_refresh()
    await co._on_window_end(dt_util.now(), scheduled=True)
    check("completed window restores min SOC without losing an unconfirmed original",
          lab.plant.min_soc == 8 and co.original_min_soc in (None, 8),
          {"physical_min_soc": lab.plant.min_soc,
           "reported_min_soc": hass.states.get(planner.data["min_soc_entity"]).state})

    lab.plant.soc = 16
    lab.plant.pv_power = lab.plant.pv_string1 = lab.plant.pv_string2 = 0
    lab.plant.block_discharge = False
    lab.plant.advance(.001)
    lab.publish()
    await process.async_refresh()
    await hass.services.async_call("inverter_charge_night", "block_discharge",
        {"config_entry_id": planner.entry_id, "duration": {"minutes": 5}}, blocking=True)
    await hass.async_block_till_done()
    check("immediate daytime reserve captures pre-night floor, not the night target",
          co.is_active and co.original_min_soc == 8 and lab.plant.min_soc == 16,
          {"original_min_soc": co.original_min_soc, "physical_min_soc": lab.plant.min_soc})

    await minimum.coordinator.async_refresh()
    await hass.async_block_till_done()
    check("raised reserve floor is observed before delayed release",
          float(hass.states.get(planner.data["min_soc_entity"]).state) == 16)
    try:
        # Hold back only this entity's HA publication to reproduce delayed
        # feedback deterministically; KORE still writes the actual inverter.
        with patch.object(minimum, "async_write_ha_state"):
            await hass.services.async_call("inverter_charge_night", "allow_discharge",
                {"config_entry_id": planner.entry_id}, blocking=True)
    except HomeAssistantError as err:
        check("delayed release feedback reports a pending reset, not a missing hold",
              err.translation_key == "reset_failed" and co._pending_reset and not co.is_active,
              {"translation_key": err.translation_key, "pending_reset": co._pending_reset})
    else:
        check("delayed release must report its unconfirmed reset", False)
    await hass.async_block_till_done()
    # KORE can debounce feedback past the service handler. Complete a real
    # poll, then invoke the same retry that its scheduled backoff would call.
    await minimum.coordinator.async_refresh()
    await grid_switch.coordinator.async_refresh()
    if co._pending_reset:
        await co._retry_reset()
    check("reserve release restores 8 percent and clears pending reset",
          not co.is_active and lab.plant.min_soc == 8 and not co._pending_reset,
          {"physical_min_soc": lab.plant.min_soc, "active": co.is_active})
    before = lab.plant.soc
    lab.plant.advance(60)
    lab.publish()
    await process.async_refresh()
    check("battery supplies the house again after reserve release",
          lab.plant.soc < before and lab.plant.battery_power < 0 and not lab.plant.grid_charge,
          {"before_soc": before, "after_soc": lab.plant.soc,
           "battery_power_w": lab.plant.battery_power})
