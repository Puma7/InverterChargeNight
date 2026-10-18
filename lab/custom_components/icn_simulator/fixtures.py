"""Configure the real KORE integration against the loopback-only emulator."""
import asyncio
from homeassistant.helpers import entity_registry as er

from .endpoints import inverter_host


async def configure_kore(hass, proxy_port=None, proxy_client_timeout_s=0, enable_g3=False):
    host = inverter_host()
    entries = [e for e in hass.config_entries.async_entries("kostal_kore") if e.data.get("host") == host]
    if entries:
        entry = entries[0]
        if entry.state.value != "loaded":
            await hass.config_entries.async_setup(entry.entry_id)
    else:
        result = await hass.config_entries.flow.async_init("kostal_kore", context={"source": "user"},
            data={"host": host, "password": "lab-only", "service_code": "123456"})
        if result.get("step_id") != "setup_options":
            raise RuntimeError(f"KORE loopback login failed: {result.get('errors')}")
        result = await hass.config_entries.flow.async_configure(result["flow_id"],
            {"modbus_enabled": True, "modbus_port": 1502, "modbus_unit_id": 71})
        if result.get("step_id") != "setup_modbus_test":
            raise RuntimeError(f"KORE loopback Modbus test failed: {result.get('errors')}")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        if result["type"] != "create_entry":
            raise RuntimeError("KORE loopback setup failed")
        entry = result["result"]
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entities = [e for e in registry.entities.values() if e.config_entry_id == entry.entry_id]
    mapping = {}
    changed = False
    if enable_g3:
        g3 = next(e for e in entities if e.unique_id.endswith("_modbus_g3_max_charge"))
        mapping["g3_charge_entity"] = g3.entity_id
        if g3.disabled_by:
            registry.async_update_entity(g3.entity_id, disabled_by=None)
            changed = True
    for key, suffix in {
        "min_soc_entity": ("_devices:local_Battery:MinSocRel", "_devices:local_Battery:MinSoc"),
        "grid_charge_switch": "_devices:local_Battery:ManualCharge",
        "battery_soc_entity": "_devices:local:battery_SoC",
        "charge_power_entity": "_modbus_bat_max_charge_limit",
        "discharge_limit_entity": "_modbus_bat_max_discharge_limit",
    }.items():
        entity = next(e for e in entities if e.unique_id.endswith(suffix))
        mapping[key] = entity.entity_id
        if entity.disabled_by:
            registry.async_update_entity(entity.entity_id, disabled_by=None)
            changed = True
    if proxy_port is not None:
        options = {**entry.options, "modbus_proxy_enabled": True,
                   "modbus_proxy_client_timeout_s": proxy_client_timeout_s,
                   "modbus_proxy_port": proxy_port, "modbus_proxy_bind": "127.0.0.1"}
        hass.config_entries.async_update_entry(entry, options=options)
        changed = True
    if changed:
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    coordinators = hass.data["kostal_kore"][entry.entry_id]
    await coordinators["process_coordinator"].async_refresh()
    for _ in range(6):
        await coordinators["modbus_coordinator"].async_refresh()
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    for key in ("min_soc_entity", "grid_charge_switch"):
        entity_id = mapping[key]
        entity = hass.data[DATA_INSTANCES][entity_id.split(".")[0]].get_entity(entity_id)
        await entity.coordinator.async_refresh()
    await asyncio.sleep(0.6)
    await hass.async_block_till_done()
    return entry, mapping
