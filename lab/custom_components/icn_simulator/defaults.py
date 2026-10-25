"""Lab fixture passed through the integration's real configuration wizard."""


def planner_defaults(capacity=35.8):
    return {
        "name": "ICN Lab", "operation_mode": "night_charge",
        "min_soc_entity": "number.icn_lab_min_soc",
        "grid_charge_switch": "switch.icn_lab_grid_charge",
        "pv_forecast_entity": "sensor.icn_lab_forecast_tomorrow",
        "pv_forecast_today_entity": "sensor.icn_lab_forecast_today",
        "battery_soc_entity": "sensor.icn_lab_battery_soc",
        "battery_capacity": capacity, "start_time": "23:00", "end_time": "05:00",
        "user_min_soc": 8.0, "user_max_soc": 95.0, "default_min_soc": 8.0,
        "forecast_error_margin": 10.0, "planner_mode": "bridge",
        "min_charge_power_w": 500, "max_charge_power_w": 5000,
        "charge_power_entity": "number.icn_lab_charge_limit",
        "discharge_limit_entity": "number.icn_lab_discharge_limit",
        "charge_power_sent_entity": "sensor.icn_lab_charge_ac_power",
        "charge_power_received_entity": "sensor.icn_lab_battery_power",
        "charge_energy_sent_entity": "sensor.icn_lab_grid_charge_energy",
        "charge_energy_received_entity": "sensor.icn_lab_stored_energy",
        "auto_efficient_charge": False,
        "grid_import_entity": "sensor.icn_lab_grid_guard_power",
        "main_fuse_a": 63.0, "grid_phases": 3, "grid_voltage_v": 230.0,
        "grid_continuous_pct": 80.0, "grid_headroom_w": 500,
        "discharge_block_switch": "switch.icn_lab_block_discharge",
        "discharge_block_mode": "auto", "update_interval": 60, "command_delay": 0.1,
        "active_start_date": "2026-10-01", "active_end_date": "2027-03-31",
        "active_range_yearly": True, "backup_mode_entity": "binary_sensor.icn_lab_charge_inhibit",
        "house_load_entity": "sensor.icn_lab_house_energy",
        "avg_house_load_kw": 0.6, "pv_crossover_delay_min": 90,
        "bridge_reserve_kwh": 0.5, "charge_efficiency": 0.90,
        "discharge_efficiency": 0.95,
        "night_price_ct": 14.0, "day_price_ct": 30.0, "feed_in_price_ct": 8.0,
    }


async def configure_planner(hass, data):
    """Use the production guided pages through Home Assistant's public flow API."""
    from custom_components.inverter_charge_night.config_flow import _GUIDED_GROUPS
    if any(entry.data.get("min_soc_entity") == data["min_soc_entity"]
           for entry in hass.config_entries.async_entries("inverter_charge_night")):
        return None
    result = await hass.config_entries.flow.async_init(
        "inverter_charge_night", context={"source": "user"})

    async def choose(step):
        nonlocal result
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": step})

    for group, keys in _GUIDED_GROUPS.items():
        if group == "grid" and "grid_max_continuous_w" not in data:
            continue
        if group == "grid_fuse" and ("main_fuse_a" not in data or "grid_max_continuous_w" in data):
            continue
        if group == "pv_shortfall" and not data.get("auto_pv_shortfall"):
            continue
        if group == "efficiency" and not (
                data.get("charge_power_entity") and data.get("charge_power_received_entity")
                and (data.get("charge_power_sent_entity") or data.get("charge_power_house_entity"))):
            continue
        if group not in ("basics", "schedule"):
            await choose("features")
            if group in ("planning", "reserve", "evening", "season", "pv_shortfall", "prices"):
                await choose("planning_features")
            elif group in ("meters", "backup", "hardware", "grid_fuse", "runtime"):
                await choose("advanced_features")
        await choose(group)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {key: data[key] for key in keys if key in data})
        if result.get("errors") or result.get("step_id") != "setup":
            raise RuntimeError(f"Lab guided setup failed at {group}: {result}")
    if not data.get("auto_efficient_charge"):
        await choose("features")
        await choose("advanced_features")
        await choose("disable_features")
        await choose("efficiency_off")
        await choose("setup")
    await choose("summary")
    await choose("finish")
    if result["type"] != "create_entry":
        raise RuntimeError(f"Lab setup did not create an entry: {result}")
    return result["result"]
