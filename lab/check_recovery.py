"""Independent HA processes at controlled wall times; actual config persistence.

The clock is controlled only in these disposable processes. Midnight callbacks
are invoked explicitly after the clock shift; this does not accelerate the UI.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

LAB = Path(__file__).resolve().parent
TIMES = {"start": "2026-12-01T23:59:40+01:00", "resume": "2026-12-02T01:00:00+01:00",
         "late": "2026-12-02T09:00:00+01:00", "island": "2026-12-02T02:00:00+01:00"}


async def stage(root, name, clock, crash=False):
    from windows_support import install
    install()
    from homeassistant import bootstrap, core, loader
    from homeassistant.util import dt as dt_util
    from homeassistant.helpers.storage import Store
    from dataclasses import asdict
    import time

    sys.path.insert(0, str(root))
    hass = core.HomeAssistant(str(root))
    hass.config.skip_pip = True
    hass.config.skip_pip_packages = []
    loader.async_setup(hass)
    checks = []

    def check(label, condition, detail=None):
        checks.append({"check": f"{name}: {label}", "passed": bool(condition), "detail": detail})
        print(f"{'PASS' if condition else 'FAIL'} {name}: {label}: {detail}", flush=True)
        if not condition:
            raise AssertionError(label)

    try:
        print("Bootstrapping " + name, flush=True)
        await bootstrap.async_from_config_dict({"homeassistant": {"time_zone": "Europe/Berlin"},
            "http": {"server_host": "127.0.0.1", "server_port": 18125}, "sun": {},
            "icn_simulator": {"auto_configure": False, "persist_plant": True}}, hass)
        print("Starting HA " + name, flush=True)
        await hass.async_start()
        print("Waiting for HA tasks " + name, flush=True)
        await hass.async_block_till_done()
        p = hass.data["icn_simulator"].plant
        lab = hass.data["icn_simulator"]
        p.speed = 0
        if name == "start":
            p.soc = 20
            lab.publish()
            from custom_components.icn_simulator.defaults import planner_defaults, configure_planner
            data = planner_defaults()
            data["user_min_soc"] = 70
            entry = await configure_planner(hass, data)
        else:
            entry = hass.config_entries.async_entries("inverter_charge_night")[0]
            for _ in range(150):
                if entry.state.value == "loaded":
                    break
                await asyncio.sleep(0.1)
            if entry.state.value != "loaded":
                await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        await coordinator._check_current_window()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        p.advance(0.001)
        if name == "start":
            target = coordinator.initial_calculated_soc
            (root / "expected-target.json").write_text(json.dumps(target))
            check("night window drives actual inverter controls", p.grid_charge and p.min_soc >= 70,
                {"now": dt_util.now().isoformat(), "active": coordinator.is_active,
                 "target": coordinator.calculated_soc, "commands": list(lab.commands)})
            original = coordinator.original_min_soc
            clock.shift(timedelta(seconds=30))
            await coordinator._on_date_boundary(dt_util.now())
            await coordinator.async_refresh()
            check("midnight preserves target and original reserve",
                p.min_soc == target and coordinator.original_min_soc == original and coordinator.is_active)
        elif name == "resume":
            check("restart inside window preserves captured reserve", coordinator.original_min_soc == 8,
                coordinator.original_min_soc)
            target = json.loads((root / "expected-target.json").read_text())
            check("restart rechecks SOC and resumes current window", p.grid_charge and p.min_soc == target and p.soc < target,
                {"soc": p.soc, "min_soc": p.min_soc})
        elif name == "late":
            target = json.loads((root / "expected-target.json").read_text())
            check("eight hours downtime replay respects inverter target", p.soc <= target + 0.01, p.soc)
            check("restart after window end releases stale controls",
                not p.grid_charge and p.min_soc == 8 and not coordinator.is_active,
                {"grid_charge": p.grid_charge, "min_soc": p.min_soc, "soc": p.soc})
        elif name == "island":
            check("startup in island does not issue grid-charge activation",
                p.grid_power == 0 and not any(c["key"] == "grid_charge" and c["value"] for c in lab.commands),
                list(lab.commands))
        if crash:
            await Store(hass, 1, "icn_lab_plant").async_save({"timestamp": time.time(), "plant": asdict(p)})
            await asyncio.sleep(6)  # Allow HA's config-entry persistence to flush.
            (root / f"result-{name}.json").write_text(json.dumps(checks, indent=2) + "\n")
            import os
            os._exit(0)  # Deliberate crash: no HA shutdown or integration unload.
    finally:
        await hass.async_stop()
        (root / f"result-{name}.json").write_text(json.dumps(checks, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=TIMES)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--crash", action="store_true")
    args = parser.parse_args()
    if args.stage:
        import time_machine
        with time_machine.travel(datetime.fromisoformat(TIMES[args.stage]), tick=True) as clock:
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
                runner.run(stage(args.root, args.stage, clock, args.crash))
        return
    results = []
    (LAB / "reports").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="icn-recovery-") as temporary:
        root = Path(temporary) / "normal"
        for domain, source in [("inverter_charge_night", LAB.parent / "custom_components/inverter_charge_night"),
                               ("icn_simulator", LAB / "custom_components/icn_simulator")]:
            shutil.copytree(source, root / "custom_components" / domain,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("start", "resume", "late", "island"):
            stage_root = Path(temporary) / "island" if name == "island" else root
            with (LAB / f"reports/recovery-{name}.log").open("w", encoding="utf-8") as log:
                completed = subprocess.run([sys.executable, "-X", "utf8", __file__, "--stage", name,
                    "--root", str(stage_root)] + (["--crash"] if name == "resume" else []),
                    stdout=log, stderr=subprocess.STDOUT, timeout=55)
            result = stage_root / f"result-{name}.json"
            if result.exists():
                for check in json.loads(result.read_text()):
                    results.append(check)
                    print(f"{'PASS' if check['passed'] else 'FAIL'} {check['check']}", flush=True)
            if completed.returncode:
                raise RuntimeError(f"Recovery stage {name} failed; see lab/reports/recovery-{name}.log")
            if name == "resume":
                island_root = Path(temporary) / "island"
                shutil.copytree(root, island_root)
                path = island_root / ".storage/icn_lab_plant"
                physical = json.loads(path.read_text(encoding="utf-8"))
                physical["data"]["plant"]["transfer_island"] = True
                path.write_text(json.dumps(physical), encoding="utf-8")
    (LAB / "reports/recovery.json").write_text(json.dumps({"checks": results}, indent=2) + "\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("custom_components.inverter_charge_night").setLevel(logging.DEBUG)
    main()
