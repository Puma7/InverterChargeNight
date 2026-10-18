"""Real HA + official evcc -> KORE Modbus proxy -> virtual inverter.

Only disposable loopback links are interrupted. Physical last setpoints remain
in the plant during a transport failure: a lost packet is not a device stop.
Ports 80/1502 must be free (stop the interactive lab first).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

from evcc_config import configuration, seed

LAB = Path(__file__).resolve().parent


async def main():
    from windows_support import install
    install()
    from homeassistant import bootstrap, core, loader
    from homeassistant.util import dt as dt_util
    from aiohttp import ClientSession
    import yaml

    checks = []

    def check(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), "detail": detail})
        print(f"{'PASS' if condition else 'FAIL'} {name}: {detail}", flush=True)
        if not condition:
            raise AssertionError(name)

    async def until(predicate, timeout=35):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(.1)
        return False

    async def read_proxy():
        reader, writer = await asyncio.open_connection("127.0.0.1", 15502)
        try:
            pdu = struct.pack(">BHH", 3, 252, 2)
            writer.write(struct.pack(">HHHB", 42, 0, len(pdu) + 1, 71) + pdu)
            await writer.drain()
            header = await asyncio.wait_for(reader.readexactly(7), 15)
            length = struct.unpack(">HHHB", header)[2]
            return await asyncio.wait_for(reader.readexactly(length - 1), 15)
        finally:
            writer.close()
            await writer.wait_closed()

    binary = LAB / "runtime/evcc" / ("evcc.exe" if sys.platform == "win32" else "evcc")
    process = None
    (LAB / "reports").mkdir(exist_ok=True)
    log_path = LAB / "reports/communication-evcc.log"
    with tempfile.TemporaryDirectory(prefix="icn-communication-") as temporary:
        root = Path(temporary)
        sys.path.insert(0, str(root))
        for domain, source in [
            ("inverter_charge_night", LAB.parent / "custom_components/inverter_charge_night"),
            ("icn_simulator", LAB / "custom_components/icn_simulator"),
            ("kostal_kore", Path(os.environ.get("KOSTAL_REPO_PATH", str(LAB.parent.parent / "KostalKore"))) / "custom_components/kostal_kore"),
        ]:
            shutil.copytree(source, root / "custom_components" / domain,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        config = root / "evcc.yaml"
        config.write_text(yaml.safe_dump(configuration(18082, 7074, proxy_port=15502)), encoding="utf-8")
        seed(binary, root / "evcc.db", config)
        hass = core.HomeAssistant(str(root))
        hass.config.skip_pip = True
        hass.config.skip_pip_packages = []
        loader.async_setup(hass)
        log = log_path.open("w", encoding="utf-8")
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                http_port = listener.getsockname()[1]
            await bootstrap.async_from_config_dict({
                "homeassistant": {"time_zone": "Europe/Berlin"}, "sun": {},
                "http": {"server_host": "127.0.0.1", "server_port": http_port},
                "icn_simulator": {"auto_configure": False, "api_port": 18082, "kostal_emulator": True},
            }, hass)
            await hass.async_start()
            await hass.async_block_till_done()
            lab = hass.data["icn_simulator"]
            print("HA ready; configuring KORE and proxy", flush=True)
            p = lab.plant
            p.speed = 0
            p.soc, p.house_load, p.pv_power = 20, 600, 0
            from custom_components.icn_simulator.fixtures import configure_kore
            from custom_components.icn_simulator.defaults import configure_planner, planner_defaults
            kore, mapping = await configure_kore(hass, proxy_port=15502, proxy_client_timeout_s=10, enable_g3=True)
            g3_id = mapping.pop("g3_charge_entity")
            store = hass.data["kostal_kore"][kore.entry_id]
            modbus, rest = store["modbus_coordinator"], store["process_coordinator"]
            proxy = store["modbus_proxy"]
            data = planner_defaults()
            now = dt_util.now()
            data.update(mapping, start_time=(now + timedelta(hours=6)).strftime("%H:%M"),
                        end_time=(now + timedelta(hours=12)).strftime("%H:%M"))
            icn_entry = await configure_planner(hass, data)
            icn = icn_entry.runtime_data
            check("both integrations loaded, actual proxy listening", kore.state.value == "loaded" and proxy.running)
            check("healthy proxy returns actual 600 W", (reply := await read_proxy())[:2] == b"\x03\x04"
                  and abs(struct.unpack(">f", reply[2:])[0] - 600) < 1, reply.hex())
            process = await asyncio.to_thread(subprocess.Popen,
                [str(binary), "--config", str(config), "--database", str(root / "evcc.db")],
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            check("official evcc reads KORE proxy", await until(lambda: proxy.communication_diagnostics()["read_requests"] > 10))
            # Read-only HA response actions are the same surface a future
            # authenticated connector can use, without device writes.
            for domain in ("kostal_kore", "inverter_charge_night"):
                response = await hass.services.async_call(domain, "communication_diagnostics", {},
                                                         blocking=True, return_response=True)
                check(domain + " exposes JSON-safe local diagnosis", isinstance(response, dict) and bool(json.dumps(response)))
            opened = await icn.async_open_adhoc_window(target_soc=70, until=now + timedelta(minutes=30),
                                                      reason="communication acceptance", allow_grid_charge=True)
            check("real ICN can control real KORE-backed charging", opened and p.grid_charge,
                  {"grid_charge": p.grid_charge, "limit": p.charge_limit})

            # Modbus only: the independent REST OFF path must still run.
            lab.emulator.modbus_online = False
            await modbus.async_refresh()
            check("failed poll invalidates proxy cache", not modbus.proxy_cache_data)
            check("upstream loss is an exception, never old power", await read_proxy() == b"\x83\x04")
            await icn.async_refresh()
            check("REST stop survives Modbus failure", await until(lambda: not p.grid_charge))
            check("ICN reports communication incident", icn.communication_diagnostics()["incidents"] >= 1)
            check("evcc receives gateway failure", await until(lambda: "exception" in log_path.read_text(encoding="utf-8").lower()))

            lab.emulator.modbus_online = True
            for _ in range(6):
                await modbus.async_refresh()
            check("fresh data accepted after reconnect", (await read_proxy())[:2] == b"\x03\x04")
            await icn.async_refresh()
            # Essential REST loss must not look healthy just because a display
            # cache is retained. It makes the battery sensor unavailable.
            lab.emulator.rest_online = False
            await rest.async_refresh()
            from homeassistant.helpers.entity_component import DATA_INSTANCES
            switch_id = mapping["grid_charge_switch"]
            switch = hass.data[DATA_INSTANCES]["switch"].get_entity(switch_id)
            await switch.coordinator.async_refresh()
            await hass.async_block_till_done()
            check("REST cache does not conceal broken transport", rest.communication_stale is True
                  and hass.states.get(mapping["battery_soc_entity"]).state == "unavailable")
            await icn.async_refresh()
            check("Modbus zero survives REST failure", p.charge_limit == 0, p.charge_limit)

            # Both links down: state is deliberately retained at the device.
            lab.emulator.modbus_online = False
            p.grid_charge, p.charge_limit = True, 4000
            await modbus.async_refresh()
            await icn.async_refresh()
            check("lost links never fabricate a physical stop", p.grid_charge and p.charge_limit == 4000)
            health = icn.communication_diagnostics()
            check("unconfirmed stop is explicit", health["stop_confirmed"] is False, health["faults"])
            original = icn._original_ac_charge_power
            check("cleanup cannot lift a limit without stop feedback", not await icn._reset_settings()
                  and icn._original_ac_charge_power == original)

            lab.emulator.rest_online = lab.emulator.modbus_online = True
            await rest.async_refresh()
            for _ in range(6):
                await modbus.async_refresh()
            await switch.coordinator.async_refresh()
            await icn.async_refresh()
            check("restored links provide current observations", not rest.communication_stale and bool(modbus.proxy_cache_data))
            await icn.async_allow_discharge()
            check("stop/reset eventually completes after recovery", not p.grid_charge and not icn._pending_reset)
            await proxy.stop()
            await proxy.start()
            check("proxy restart accepts a new client", (await read_proxy())[:2] == b"\x03\x04")
            # A partial TCP request may disappear; it must not poison others.
            _, partial = await asyncio.open_connection("127.0.0.1", 15502)
            partial.write(b"\x00\x01\x00")
            await partial.drain()
            partial.close()
            await partial.wait_closed()
            check("partial client disconnect does not break service", (await read_proxy())[:2] == b"\x03\x04")
            snapshot = await hass.services.async_call("kostal_kore", "communication_diagnostics", {},
                                                     blocking=True, return_response=True)
            check("recovery receipt and proxy error counters retained", proxy.communication_diagnostics()["failed_requests"] > 0
                  and any(v.get("modbus", {}).get("incidents", 0) for v in snapshot.values()))
            process.terminate()
            await asyncio.to_thread(process.wait, 10)
            process = None
            check("expected evcc traffic loss is visible without a broken inverter",
                  await until(lambda: proxy.communication.snapshot()["state"] == "interrupted", timeout=15)
                  and modbus.client.connected)
            await read_proxy()
            check("new traffic clears watchdog with retained recovery history",
                  proxy.communication.snapshot()["state"] == "healthy")
            await hass.services.async_call("number", "set_value", {"entity_id": g3_id, "value": 3500}, blocking=True)
            g3 = hass.data[DATA_INSTANCES]["number"].get_entity(g3_id)
            check("real G3 cyclic limit has current readback before keepalive",
                  p.charge_limit == 3500 and g3._keepalive_value == 3500)
            old_task = g3._keepalive_task
            lab.emulator.modbus_online = False
            try:
                await hass.services.async_call("number", "set_value", {"entity_id": g3_id, "value": 0}, blocking=True)
            except Exception:
                replacement_failed = True
            else:
                replacement_failed = False
            await asyncio.sleep(.1)
            check("failed G3 zero replacement cancels the previous live keepalive",
                  replacement_failed and g3._keepalive_value is None and old_task.done())
            lab.emulator.modbus_online = True
            await modbus.async_refresh()
            check("reconnection does not resurrect the old G3 keepalive", g3._keepalive_task is None)
            await hass.services.async_call("number", "set_value", {"entity_id": g3_id, "value": 0}, blocking=True)
            check("fresh G3 zero command is read back and can own a new keepalive",
                  p.charge_limit == 0 and g3._keepalive_value == 0)
        finally:
            if process is not None:
                process.terminate()
                await asyncio.to_thread(process.wait, 10)
            for domain in ("inverter_charge_night", "kostal_kore"):
                for entry in hass.config_entries.async_entries(domain):
                    if entry.state.value == "loaded":
                        await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_stop()
            log.close()
            (LAB / "reports/communication.json").write_text(json.dumps({"checks": checks}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(main())
