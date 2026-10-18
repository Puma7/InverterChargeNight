"""Exercise the emulator using unmodified pykoplenti and KostalKore clients.

Requires ../KostalKore checkout and ports 127.0.0.1:80 and :1502 free.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from ipaddress import IPv4Address
import logging
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile

LAB = Path(__file__).resolve().parent


async def main():
    host = os.environ.get("ICN_LAB_INVERTER_HOST", "127.0.0.1")
    if not IPv4Address(host).is_loopback:
        raise ValueError("ICN_LAB_INVERTER_HOST must be an IPv4 loopback address")
    # Refuse before any client connects. A failed emulator bind must never
    # leave this campaign writing test values to a different running lab.
    for port in (80, 1502):
        with socket.socket() as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind((host, port))
            except OSError as err:
                raise RuntimeError(f"Lab port {port} is occupied; stop the running lab first") from err
    from windows_support import install
    install()
    from homeassistant import bootstrap, core, loader
    from homeassistant.helpers import entity_registry as er
    from aiohttp import ClientSession
    from pykoplenti import ApiClient, AuthenticationException

    checks = []

    def check(label, condition, detail=None):
        checks.append({"check": label, "passed": bool(condition), "detail": detail})
        print(f"{'PASS' if condition else 'FAIL'} {label}: {detail}", flush=True)
        if not condition:
            raise AssertionError(label)

    with tempfile.TemporaryDirectory(prefix="icn-kostal-") as temporary:
        root = Path(temporary)
        sys.path.insert(0, str(root))
        for domain, source in [
            ("inverter_charge_night", LAB.parent / "custom_components/inverter_charge_night"),
            ("icn_simulator", LAB / "custom_components/icn_simulator"),
            ("kostal_kore", Path(os.environ.get("KOSTAL_REPO_PATH", str(LAB.parent.parent / "KostalKore"))) / "custom_components/kostal_kore"),
        ]:
            shutil.copytree(source, root / "custom_components" / domain,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        hass = core.HomeAssistant(str(root))
        hass.config.skip_pip = True
        hass.config.skip_pip_packages = []
        loader.async_setup(hass)
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                http_port = listener.getsockname()[1]
            await bootstrap.async_from_config_dict({"homeassistant": {"time_zone": "Europe/Berlin", "latitude": 52.52, "longitude": 13.405},
                "http": {"server_host": "127.0.0.1", "server_port": http_port}, "sun": {},
                "icn_simulator": {"auto_configure": False, "kostal_emulator": True}}, hass)
            if "icn_simulator" not in hass.config.components:
                raise RuntimeError("Own simulator did not start; refusing all inverter client calls")
            await hass.async_start()
            await hass.async_block_till_done()
            check("isolated HA HTTP starts without using the interactive lab port", "http" in hass.config.components, http_port)
            lab = hass.data["icn_simulator"]
            lab.plant.speed = 0
            async with ClientSession() as session:
                async with ApiClient(session, host) as client:
                    await client.login("lab-only", service_code="123456")
                    me = await client.get_me()
                    check("real SCRAM/AES client logs in as installer", me.role == "INSTALLER")
                    await client.set_setting_values("devices:local", {"Battery:MinSocRel": "65", "Battery:ManualCharge": "1"})
                    check("REST writes reach the physical battery model", lab.plant.min_soc == 65 and lab.plant.grid_charge)
                    lab.plant.advance(60)
                    values = await client.get_process_data_values("devices:local:battery", "SoC")
                    check("real REST client reads physical SoC", values["devices:local:battery"]["SoC"].value > 25)
                bad = ApiClient(session, host)
                try:
                    await bad.login("wrong")
                except AuthenticationException:
                    check("invalid password is refused", True)
                else:
                    check("invalid password is refused", False)

            from custom_components.kostal_kore.modbus_client import KostalModbusClient
            from custom_components.kostal_kore.modbus_registers import REG_BAT_MAX_CHARGE_LIMIT, REG_BATTERY_STATE_OF_CHARGE
            client = KostalModbusClient(host, port=1502, unit_id=71)
            try:
                await client.connect()
                await client.detect_endianness()
                await client.write_register(REG_BAT_MAX_CHARGE_LIMIT, 1800)
                actual = await client.read_register(REG_BAT_MAX_CHARGE_LIMIT)
                check("KORE writes and reads back a real Modbus charge limit", actual == 1800 and lab.plant.charge_limit == 1800)
                soc = await client.read_register(REG_BATTERY_STATE_OF_CHARGE)
                check("KORE reads real Modbus battery telemetry", abs(soc - lab.plant.soc) < 0.001, soc)
            finally:
                await client.disconnect()

            result = await hass.config_entries.flow.async_init("kostal_kore", context={"source": "user"},
                data={"host": host, "password": "lab-only", "service_code": "123456"})
            check("KORE setup authenticates against emulator", result.get("step_id") == "setup_options", result.get("errors"))
            result = await hass.config_entries.flow.async_configure(result["flow_id"],
                {"modbus_enabled": True, "modbus_port": 1502, "modbus_unit_id": 71})
            check("KORE built-in Modbus connection test passes", result.get("step_id") == "setup_modbus_test" and not result.get("errors"), result.get("errors"))
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
            check("KORE wizard creates an entry", result["type"] == "create_entry")
            entry = result["result"]
            await hass.async_block_till_done()
            check("unmodified KORE loads in real Home Assistant", entry.state.value == "loaded", str(entry.state))
            entities = [item for item in er.async_get(hass).entities.values() if item.config_entry_id == entry.entry_id]
            check("KORE creates actual HA entities", len(entities) > 10, len(entities))
            modbus = hass.data["kostal_kore"][entry.entry_id]["modbus_coordinator"]
            for _ in range(1):
                await modbus.async_refresh()
            check("readable charge limit is polled into KORE coordinator",
                  modbus.data.get(REG_BAT_MAX_CHARGE_LIMIT.name) == lab.plant.charge_limit,
                  modbus.data.get(REG_BAT_MAX_CHARGE_LIMIT.name))
            registry = er.async_get(hass)
            mapping = {}
            suffixes = {"min_soc_entity": ("_devices:local_Battery:MinSocRel", "_devices:local_Battery:MinSoc"),
                        "grid_charge_switch": "_devices:local_Battery:ManualCharge",
                        "battery_soc_entity": "_devices:local:battery_SoC",
                        "charge_power_entity": "_modbus_bat_max_charge_limit",
                        "charge_power_sent_entity": "_devices:local_Grid2Bat_P",
                        "charge_power_received_entity": "_devices:local:battery_P",
                        "discharge_limit_entity": "_modbus_bat_max_discharge_limit"}
            for key, suffix in suffixes.items():
                item = next((item for item in entities if item.unique_id.endswith(suffix)), None)
                check(f"KORE exposes {key}", item is not None)
                mapping[key] = item.entity_id
                if item.disabled_by:
                    registry.async_update_entity(item.entity_id, disabled_by=None)
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            process_coordinator = hass.data["kostal_kore"][entry.entry_id]["process_coordinator"]
            await process_coordinator.async_refresh()
            modbus = hass.data["kostal_kore"][entry.entry_id]["modbus_coordinator"]
            for _ in range(1):
                await modbus.async_refresh()
            from homeassistant.helpers.entity_component import DATA_INSTANCES
            for key in ("min_soc_entity", "grid_charge_switch"):
                entity_id = mapping[key]
                entity = hass.data[DATA_INSTANCES][entity_id.split(".")[0]].get_entity(entity_id)
                await entity.coordinator.async_refresh()
            await asyncio.sleep(0.6)  # KORE registers its first REST reads after 0.5s.
            await hass.async_block_till_done()
            from custom_components.icn_simulator.kore_readiness import wait_for_kore_feedback
            feedback = await wait_for_kore_feedback(hass, mapping)
            check("all KORE control and measurement bindings have observed feedback", True, feedback)
            check("KORE battery sensor has live telemetry",
                  hass.states.get(mapping["battery_soc_entity"]).state not in ("unknown", "unavailable"),
                  {key: hass.states.get(entity_id).state if hass.states.get(entity_id) else None for key, entity_id in mapping.items()})
            # The REST smoke check above deliberately set 65%. Establish the
            # actual pre-night floor before ICN captures any inverter settings.
            await hass.services.async_call("number", "set_value",
                {"entity_id": mapping["min_soc_entity"], "value": 8}, blocking=True)
            minimum = hass.data[DATA_INSTANCES]["number"].get_entity(mapping["min_soc_entity"])
            await minimum.coordinator.async_refresh()
            await hass.services.async_call("switch", "turn_off",
                {"entity_id": mapping["grid_charge_switch"]}, blocking=True)
            await hass.async_block_till_done()
            from custom_components.icn_simulator.defaults import configure_planner, planner_defaults
            defaults = planner_defaults()
            defaults.update(mapping)
            # Exercise the same min-SOC based hold as the persistent ICN2 lab.
            defaults.pop("discharge_block_switch", None)
            defaults.pop("discharge_limit_entity", None)
            # This campaign tests ad-hoc charging, which correctly refuses to
            # interrupt an already-running scheduled night window.
            from homeassistant.util import dt as dt_util
            now = dt_util.now()
            defaults.update(start_time=(now + timedelta(hours=6)).strftime("%H:%M"),
                            end_time=(now + timedelta(hours=12)).strftime("%H:%M"))
            planner = await configure_planner(hass, defaults)
            await hass.async_block_till_done()
            check("ICN configures against real KORE entities", planner.state.value == "loaded", mapping)
            await hass.services.async_call("inverter_charge_night", "charge_to",
                {"target_soc": 70, "duration": {"hours": 2}}, blocking=True)
            await hass.async_block_till_done()
            check("ICN charge command passes through KORE REST to simulated inverter",
                  lab.plant.grid_charge and lab.plant.min_soc >= 70,
                  {"grid_charge": lab.plant.grid_charge, "min_soc": lab.plant.min_soc})
            from window_handoff_campaign import check_window_handoff
            await check_window_handoff(hass, lab, planner, entry, check)
            from min_soc_recovery_campaign import check_min_soc_recovery
            await asyncio.wait_for(check_min_soc_recovery(hass, lab, planner, entry, check), 180)
            from efficiency_campaign import check_efficiency
            await asyncio.wait_for(check_efficiency(hass, lab, planner, entry, check), 180)
            await hass.config_entries.async_unload(planner.entry_id)
            check("KORE unloads cleanly", await hass.config_entries.async_unload(entry.entry_id))
        finally:
            for domain in ("inverter_charge_night", "kostal_kore"):
                for active in hass.config_entries.async_entries(domain):
                    if active.state.value == "loaded":
                        await hass.config_entries.async_unload(active.entry_id)
            await hass.async_stop()
            (LAB / "reports").mkdir(exist_ok=True)
            (LAB / "reports/kostal.json").write_text(json.dumps({"checks": checks}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(main())
