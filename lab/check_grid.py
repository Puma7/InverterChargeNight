"""Run real HA/ICN and the official evcc binary on a shared physical bus."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from evcc_config import seed, write

LAB = Path(__file__).resolve().parent


async def main():
    from windows_support import install
    install()
    from homeassistant import bootstrap, core, loader
    from homeassistant.util import dt as dt_util
    from aiohttp import ClientSession

    binary = LAB / "runtime/evcc" / ("evcc.exe" if sys.platform == "win32" else "evcc")
    if not binary.exists():
        raise RuntimeError("Run python lab/install_evcc.py first")
    checks, samples = [], []

    def check(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), "detail": detail})
        print(f"{'PASS' if condition else 'FAIL'} {name}: {detail}", flush=True)
        if not condition:
            raise AssertionError(name)

    async def until(predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.1)
        return False

    with tempfile.TemporaryDirectory(prefix="icn-grid-") as temporary:
        root = Path(temporary)
        sys.path.insert(0, str(root))
        for domain, source in [("inverter_charge_night", LAB.parent / "custom_components/inverter_charge_night"),
                               ("icn_simulator", LAB / "custom_components/icn_simulator"),
                               ("kostal_kore", Path(os.environ.get("KOSTAL_REPO_PATH", str(LAB.parent.parent / "KostalKore"))) / "custom_components/kostal_kore")]:
            shutil.copytree(source, root / "custom_components" / domain,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        config = root / "evcc.yaml"
        write(config, device_port=18081, ui_port=7073)
        seed(binary, root / "evcc.db", config)
        hass = core.HomeAssistant(str(root))
        hass.config.skip_pip = True
        hass.config.skip_pip_packages = []
        loader.async_setup(hass)
        process = None
        log = (LAB / "reports/grid-evcc.log").open("w", encoding="utf-8")
        entry = None
        try:
            await bootstrap.async_from_config_dict({"homeassistant": {"time_zone": "Europe/Berlin"},
                "http": {"server_host": "127.0.0.1", "server_port": 18126},
                "sun": {}, "icn_simulator": {"auto_configure": False, "api_port": 18081, "kostal_emulator": True}}, hass)
            await hass.async_start()
            await hass.async_block_till_done()
            lab = hass.data["icn_simulator"]
            p = lab.plant
            from custom_components.icn_simulator.defaults import configure_planner, planner_defaults
            data = planner_defaults()
            from custom_components.icn_simulator.fixtures import configure_kore
            kore_entry, mapping = await configure_kore(hass)
            data.update(mapping)
            now = dt_util.now()
            data.update(start_time=(now + timedelta(hours=6)).strftime("%H:%M"),
                        end_time=(now + timedelta(hours=12)).strftime("%H:%M"),
                        active_start_date=(now - timedelta(days=1)).date().isoformat(),
                        active_end_date=(now + timedelta(days=1)).date().isoformat())
            p.soc = 20
            lab.publish()
            entry = await configure_planner(hass, data)
            await hass.async_block_till_done()
            check("real ICN configured against phase-conservative grid meter", entry.state.value == "loaded")
            process = await asyncio.to_thread(subprocess.Popen, [str(binary), "--config", str(config),
                "--database", str(root / "evcc.db")], stdout=log, stderr=subprocess.STDOUT)
            async with ClientSession() as session:
                async def evcc_state():
                    async with session.get("http://127.0.0.1:7073/api/state") as response:
                        response.raise_for_status()
                        return await response.json()
                for _ in range(100):
                    try:
                        state = await evcc_state()
                        break
                    except Exception:
                        await asyncio.sleep(0.1)
                else:
                    raise AssertionError("evcc did not start")
                check("official evcc has two 32 A loadpoints and shared circuit",
                      len(state["loadpoints"]) == 2 and
                      all(lp["maxCurrent"] == 32 for lp in state["loadpoints"]) and
                      state["circuits"]["main"]["maxCurrent"] == 50.4)
                await hass.services.async_call("inverter_charge_night", "charge_to",
                    {"target_soc": 70, "duration": {"hours": 2}}, blocking=True)
                check("battery draws power before EV peak", await until(lambda: p.charge_ac_power > 0), p.charge_ac_power)
                # Wait for the production write debounce, with the actual clock.
                await asyncio.sleep(11)
                for box in (1, 2):
                    setattr(p, f"wallbox{box}_connected", True)
                    setattr(p, f"wallbox{box}_enabled", True)
                    setattr(p, f"wallbox{box}_current", 32.0)
                p.advance(0.001)
                peak = p.grid_power
                lab.publish()
                check("two EV demands exceed the physical 63 A connection", peak > 43470, peak)
                paused = False
                for _ in range(200):
                    sample = {"time": time.monotonic(), "grid": p.grid_power,
                        "currents": p.phase_currents, "battery_ac": p.charge_ac_power,
                        "ev1": p.wallbox_power(1), "ev2": p.wallbox_power(2)}
                    samples.append(sample)
                    paused |= p.charge_limit == 0 or (not p.grid_charge)
                    if paused and max(p.phase_currents) <= 50.4 and min(p.wallbox_power(1), p.wallbox_power(2)) > 0:
                        break
                    await asyncio.sleep(0.1)
                coordinator = entry.runtime_data
                check("ICN pauses battery charging at the EV peak", paused,
                    {"planned": coordinator.planned_charge_power_w,
                     "written": coordinator._planned_setpoint_written_w,
                     "active": coordinator.is_active, "grid": coordinator.grid_limit_attributes(),
                     "grid_entity": coordinator.config.get("grid_import_entity"), "commands": list(lab.commands)})
                check("evcc reduces two EVs below shared continuous phase budget",
                    max(p.phase_currents) <= 50.4 and min(p.wallbox_power(1), p.wallbox_power(2)) > 0,
                    {"currents": p.phase_currents, "ev1": p.wallbox_power(1), "ev2": p.wallbox_power(2)})
                p.heat_pump, p.cooker, p.oven = 6000, 7000, 3000
                p.advance(0.001)
                lab.publish()
                check("evcc responds to cooking and heat-pump load step",
                    await until(lambda: max(p.phase_currents) <= 50.4), list(p.phase_currents))
                # A device may acknowledge Modbus without applying the ceiling.
                # Keep REST grid-charge stop functional as an independent path.
                p.reject_charge_limit = True
                p.charge_limit = 5000
                p.grid_charge = True
                p.house_load = 35000
                p.advance(0.001)
                lab.publish()
                # KORE rotates register groups; one entity update is not a
                # guarantee that this register has been polled. Read all groups
                # through the actual coordinator, then the independent REST path.
                modbus = hass.data["kostal_kore"][kore_entry.entry_id]["modbus_coordinator"]
                for _ in range(6):
                    await modbus.async_refresh()
                from homeassistant.helpers.entity_component import DATA_INSTANCES
                switch = hass.data[DATA_INSTANCES]["switch"].get_entity(data["grid_charge_switch"])
                await switch.coordinator.async_refresh()
                await hass.async_block_till_done()
                check("the actual KORE entities observe the externally changed controls",
                    float(hass.states.get(data["charge_power_entity"]).state) == 5000 and
                    hass.states.get(data["grid_charge_switch"]).state == "on")
                await coordinator._verify_ac_charge_limit()
                check("KORE readback detects an acknowledged but ignored protective cap",
                    any(c["key"] == "charge_limit" and not c["accepted"] for c in lab.commands),
                    list(lab.commands))
                check("independent REST grid-charge stop survives ignored Modbus cap",
                    await until(lambda: not p.grid_charge) and p.charge_limit == 5000,
                    {"grid_charge": p.grid_charge, "cap": p.charge_limit})
                p.reject_charge_limit = False
                p.house_load = 600
                p.transfer_island = True
                p.advance(0.001)
                lab.publish()
                check("manual transfer is seen even without inverter backup bit",
                    not p.backup and p.islanded and p.grid_power == 0 and p.ev_power == 0)
                check("ICN releases controls after independent island event",
                    await until(lambda: not p.grid_charge and p.min_soc == 8))
                check("island overload is visible as unserved power", p.unserved_power > 0, p.unserved_power)
                p.transfer_island = False
                p.meter_online = False
                lab.publish()
                async with session.get("http://127.0.0.1:18081/api/state") as response:
                    check("meter disconnect returns an error, never plausible zero", response.status == 503)
                p.meter_online = True
                for scenario in ("overvoltage", "undervoltage", "phase_loss", "pv_snow", "pv_string_failure"):
                    await hass.services.async_call("icn_simulator", "load_scenario", {"scenario": scenario}, blocking=True)
                    p.speed = 0
                    if "voltage" in scenario or scenario == "phase_loss":
                        check(scenario + " inhibits grid charging", p.charge_inhibit and p.charge_ac_power == 0)
                    else:
                        check(scenario + " changes generation independently of forecast",
                            p.forecast_tomorrow == 20 and p.effective_pv == (0 if scenario == "pv_snow" else 6000))
        finally:
            if process is not None:
                process.terminate()
                await asyncio.to_thread(process.wait, 10)
            if entry is not None and entry.state.value == "loaded":
                await hass.config_entries.async_unload(entry.entry_id)
            for active in hass.config_entries.async_entries("kostal_kore"):
                if active.state.value == "loaded":
                    await hass.config_entries.async_unload(active.entry_id)
            await hass.async_stop()
            log.close()
            (LAB / "reports/grid.json").write_text(json.dumps({"checks": checks, "samples": samples}, indent=2) + "\n")


if __name__ == "__main__":
    (LAB / "reports").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("custom_components.inverter_charge_night").setLevel(logging.DEBUG)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(main())
