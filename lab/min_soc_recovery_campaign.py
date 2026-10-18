"""Exercise reserve reload and rejected restores through real HA/KORE I/O."""


async def check_min_soc_recovery(hass, lab, planner, kore_entry, check):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from homeassistant.util import dt as dt_util

    minimum = hass.data[DATA_INSTANCES]["number"].get_entity(planner.data["min_soc_entity"])
    process = hass.data["kostal_kore"][kore_entry.entry_id]["process_coordinator"]

    async def set_floor(value):
        await hass.services.async_call("number", "set_value",
            {"entity_id": planner.data["min_soc_entity"], "value": value}, blocking=True)
        await minimum.coordinator.async_refresh()
        await hass.async_block_till_done()

    # Deliberately different from the configured 8% default: a fallback to the
    # default must not masquerade as successful restoration of the real capture.
    await set_floor(11)
    lab.plant.soc = 32
    lab.plant.pv_power = lab.plant.pv_string1 = lab.plant.pv_string2 = 0
    lab.plant.advance(.001)
    lab.publish()
    await process.async_refresh()
    await hass.services.async_call("inverter_charge_night", "block_discharge",
        {"config_entry_id": planner.entry_id, "duration": {"minutes": 30}}, blocking=True)
    await hass.async_block_till_done()
    await minimum.coordinator.async_refresh()
    co = planner.runtime_data
    check("reserve captures the actual 11 percent floor rather than configured default",
          co.is_active and co.original_min_soc == 11 and lab.plant.min_soc == 32,
          {"original": co.original_min_soc, "physical": lab.plant.min_soc})

    previous = co
    check("active reserve integration reload succeeds", await hass.config_entries.async_reload(planner.entry_id))
    await hass.async_block_till_done()
    await minimum.coordinator.async_refresh()
    co = planner.runtime_data
    check("reload resumes reserve without capturing its raised floor as original",
          co is not previous and co.is_active and co.original_min_soc == 11 and lab.plant.min_soc == 32,
          {"original": co.original_min_soc, "physical": lab.plant.min_soc})

    # A successful HTTP response is deliberately not successful actuation.
    lab.plant.reject_writes = True
    error = None
    try:
        await hass.services.async_call("inverter_charge_night", "allow_discharge",
            {"config_entry_id": planner.entry_id}, blocking=True)
    except HomeAssistantError as err:
        error = err.translation_key
    await hass.async_block_till_done()
    check("ignored restore is reported and retains original floor for recovery",
          error == "reset_failed" and co._pending_reset and co.original_min_soc == 11
          and not co.is_active and lab.plant.min_soc == 32,
          {"error": error, "original": co.original_min_soc, "physical": lab.plant.min_soc})
    check("pending reset stores the actual original floor in HA entry options",
          planner.options["runtime_state"]["original_min_soc"] == 11
          and planner.options["runtime_state"]["pending_reset"])

    previous = co
    check("integration reload during pending reset succeeds", await hass.config_entries.async_reload(planner.entry_id))
    await hass.async_block_till_done()
    co = planner.runtime_data
    check("pending original survives reload while writes are still ignored",
          co is not previous and co._pending_reset and co.original_min_soc == 11
          and not co.is_active and lab.plant.min_soc == 32,
          {"original": co.original_min_soc, "physical": lab.plant.min_soc})

    lab.plant.reject_writes = False
    # Poll through the real KORE client between retries, just as later observed
    # feedback becomes available to ICN's bounded-backoff retry callback.
    for _ in range(3):
        await minimum.coordinator.async_refresh()
        if not co._pending_reset:
            break
        await co._retry_reset()
        await hass.async_block_till_done()
    check("recovered writes restore 11 percent and clear persisted pending reset",
          lab.plant.min_soc == 11 and not co._pending_reset and co.original_min_soc is None
          and not planner.options["runtime_state"]["pending_reset"],
          {"physical": lab.plant.min_soc, "pending": co._pending_reset})
    before = lab.plant.soc
    lab.plant.advance(60)
    lab.publish()
    await process.async_refresh()
    check("battery physically discharges after failed release and reload recovery",
          lab.plant.soc < before and lab.plant.battery_power < 0 and not lab.plant.grid_charge,
          {"before": before, "after": lab.plant.soc, "power_w": lab.plant.battery_power})

    await set_floor(17)
    await co._on_scheduled_window_end(dt_util.now())
    check("idle scheduled end preserves a subsequently chosen manual floor",
          lab.plant.min_soc == 17 and co.original_min_soc is None and not co._pending_reset,
          lab.plant.min_soc)
    await set_floor(8)
