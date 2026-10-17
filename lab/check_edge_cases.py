"""Accelerated nights and injected faults in isolated, actual HA processes.

UTC is the independent calendar oracle. Wall-clock jumps never advance the
plant by negative time. Production polling/date callbacks are invoked explicitly;
this does not claim to accelerate Home Assistant's event-loop timers or evcc.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

LAB = Path(__file__).resolve().parent
CASES = {
    "spring": ("2026-03-28T21:50Z", "2026-03-29T03:20Z", "2026-03-28T22:00Z", "2026-03-29T03:00Z"),
    "fall": ("2026-10-24T20:50Z", "2026-10-25T04:20Z", "2026-10-24T21:00Z", "2026-10-25T04:00Z"),
    "fold_end": ("2026-10-24T20:50Z", "2026-10-25T02:20Z", "2026-10-24T21:00Z", "2026-10-25T00:30Z"),
    "gap_start": ("2026-03-29T00:50Z", "2026-03-29T03:20Z", "2026-03-29T01:00Z", "2026-03-29T03:00Z"),
    "gap_end": ("2026-03-28T21:50Z", "2026-03-29T01:20Z", "2026-03-28T22:00Z", "2026-03-29T01:00Z"),
    "leap_day": ("2028-02-28T21:50Z", "2028-02-29T04:20Z", "2028-02-28T22:00Z", "2028-02-29T04:00Z"),
    "common_century": ("2100-02-28T21:50Z", "2100-03-01T04:20Z", "2100-02-28T22:00Z", "2100-03-01T04:00Z"),
    "new_year": ("2026-12-31T21:50Z", "2027-01-01T04:20Z", "2026-12-31T22:00Z", "2027-01-01T04:00Z"),
    "ntp_backward": ("2027-01-14T21:50Z", "2027-01-15T05:20Z", "2027-01-14T22:00Z", "2027-01-15T04:00Z"),
    "ntp_forward": ("2027-01-14T21:50Z", "2027-01-15T04:20Z", "2027-01-14T22:00Z", "2027-01-15T04:00Z"),
    "ntp_reopen": ("2027-01-14T21:50Z", "2027-01-15T05:20Z", "2027-01-14T22:00Z", "2027-01-15T04:00Z"),
    "leap_repeat": ("2026-12-31T23:59:57Z", "2027-01-01T00:00:03Z", "2026-12-31T22:00Z", "2027-01-01T04:00Z"),
    "negative_leap": ("2026-12-31T23:59:57Z", "2027-01-01T00:00:03Z", "2026-12-31T22:00Z", "2027-01-01T04:00Z"),
    "telemetry_faults": ("2027-01-14T21:50Z", "2027-01-15T04:20Z", "2027-01-14T22:00Z", "2027-01-15T04:00Z"),
    "selective_rejection": ("2027-01-14T21:50Z", "2027-01-15T04:20Z", "2027-01-14T22:00Z", "2027-01-15T04:00Z"),
    "ad_hoc_fold": ("2026-10-25T00:30Z", "2026-10-25T02:50Z", "2026-10-25T00:30Z", "2026-10-25T02:30Z"),
}


def moments(name):
    first, last, _, _ = map(datetime.fromisoformat, CASES[name])
    step = 1 if name in ("leap_repeat", "negative_leap") else 600
    rows = []
    for index in range(int((last - first).total_seconds() // step) + 1):
        stamp = first + timedelta(seconds=index * step)
        if name == "ntp_backward" and index >= 18:
            stamp -= timedelta(hours=1)
        if name == "ntp_forward" and index >= 18:
            stamp += timedelta(hours=8)
        if name == "ntp_reopen" and index >= 40:
            stamp -= timedelta(hours=2)
        if name == "leap_repeat" and index == 3:
            stamp -= timedelta(seconds=1)  # Synthetic repeated POSIX second.
        if name == "negative_leap" and index >= 3:
            stamp += timedelta(seconds=1)  # Synthetic skipped second.
        rows.append((stamp, step if index else 0))
    return rows


async def run_case(root, name, clock):
    from windows_support import install
    install()
    from homeassistant import bootstrap, core, loader
    from homeassistant.util import dt as dt_util

    sys.path.insert(0, str(root))
    hass = core.HomeAssistant(str(root))
    hass.config.skip_pip = True
    hass.config.skip_pip_packages = []
    loader.async_setup(hass)
    checks = []
    trace = []
    result = {"case": name, "checks": checks, "trace": trace}

    def check(label, condition, detail=None):
        checks.append({"check": label, "passed": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(f"{name}: {label}: {detail}")

    try:
        await bootstrap.async_from_config_dict({
            "homeassistant": {"time_zone": "Europe/Berlin", "latitude": 52.52, "longitude": 13.405},
            "http": {"server_host": "127.0.0.1", "server_port": 18128}, "sun": {},
            "icn_simulator": {"auto_configure": False},
        }, hass)
        await hass.async_start()
        await hass.async_block_till_done()
        # HA 2026.10 treats a new HTTP configuration as a trial until confirmed.
        # Confirm our reachable disposable listener through HA's own store API;
        # otherwise its legitimate auto-revert requests a restart after 5 min.
        from homeassistant.components.http.config import DATA_STORE
        http_store = hass.data[DATA_STORE]
        if http_store.pending is not None:
            await http_store.async_promote_pending()
        check("HTTP integration started", "http" in hass.config.components)
        lab = hass.data["icn_simulator"]
        p = lab.plant
        p.load_scenario("winter_cloudy")
        p.speed = 0
        lab.publish()
        await hass.async_block_till_done()
        from custom_components.icn_simulator.defaults import configure_planner, planner_defaults
        data = planner_defaults()
        data.update(user_min_soc=70, command_delay=0)
        if name in ("fold_end", "gap_end"):
            data["end_time"] = "02:30"
        if name == "gap_start":
            data["start_time"] = "02:30"
        if name == "ad_hoc_fold":
            data.update(start_time="10:00", end_time="11:00")
        entry = await configure_planner(hass, data)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        if name == "ad_hoc_fold":
            await hass.services.async_call("inverter_charge_night", "charge_to", {
                "config_entry_id": entry.entry_id, "target_soc": 70, "duration": {"hours": 2},
            }, blocking=True)
        first, last, begins, deadline = map(datetime.fromisoformat, CASES[name])
        if name == "ad_hoc_fold":
            check("service duration is exactly two elapsed hours",
                  coordinator._adhoc_until.astimezone(UTC) == deadline)
        started = int(coordinator.is_active)
        original_start = coordinator._on_window_start

        async def counted_start(now):
            nonlocal started
            started += 1
            await original_start(now)

        coordinator._on_window_start = counted_start
        previous_date = dt_util.now().date()
        refused_limit = False
        for index, (instant, physical_seconds) in enumerate(moments(name)):
            clock.move_to(instant)
            # These failures alter telemetry/acknowledgement, never the true SoC.
            if name == "telemetry_faults":
                p.meter_online = not 8 <= index < 10
                p.soc_sensor_online = not 14 <= index < 16
                p.backup_sensor_online = not 20 <= index < 22
            if name == "selective_rejection":
                p.reject_charge_limit = 8 <= index < 12
                p.house_load = 35000 if p.reject_charge_limit else 600
            p.advance(0.001)
            lab.publish()
            await hass.async_block_till_done()
            if dt_util.now().date() != previous_date:
                await coordinator._on_date_boundary(dt_util.now())
                previous_date = dt_util.now().date()
            # Real production entry points, with actual HA service/registry state.
            # Two samples also exercise recovery after a paused plan was replaced.
            for _ in range(2):
                await coordinator._check_current_window()
                await coordinator.async_refresh()
                await hass.async_block_till_done()
            check(f"sample {index}: Home Assistant remains running", hass.state is core.CoreState.running, str(hass.state))
            refused_limit |= any(c["key"] == "charge_limit" and not c["accepted"] for c in lab.commands)
            allowed_time = begins <= instant < deadline
            if not allowed_time:
                check(f"sample {index}: grid charging is off outside the deadline", not p.grid_charge)
                check(f"sample {index}: controller is inactive outside the deadline", not coordinator.is_active)
            if name == "ntp_reopen" and index >= 40:
                check(f"sample {index}: clock rollback cannot reopen a completed night",
                      not coordinator.is_active and not p.grid_charge)
            if name == "telemetry_faults" and not (p.meter_online and p.soc_sensor_online and p.backup_sensor_online):
                check(f"sample {index}: telemetry loss pauses the actual switch", not p.grid_charge)
            if name == "selective_rejection" and p.reject_charge_limit:
                check(f"sample {index}: rejected cap cannot keep charging enabled", not p.grid_charge)
            if physical_seconds:
                p.advance(physical_seconds)
            lab.publish()
            await hass.async_block_till_done()
            check(f"sample {index}: physical SoC stays inside the configured ceiling", 0 <= p.soc <= 95.00001, p.soc)
            check(f"sample {index}: AC charging stays within the configured power ceiling", 0 <= p.charge_ac_power <= 5000.0001, p.charge_ac_power)
            check(f"sample {index}: telemetry remains finite", all(math.isfinite(v) for v in (p.soc, p.grid_power, p.charge_ac_power)))
            trace.append({"utc": instant.isoformat(), "local": dt_util.now().isoformat(),
                          "physical_seconds": physical_seconds, "soc": round(p.soc, 4),
                          "grid_charge": p.grid_charge, "charge_w": p.charge_ac_power,
                          "active": coordinator.is_active, "target": coordinator.initial_calculated_soc})
        check("the scenario started its requested window", started >= 1, started)
        if name == "ntp_reopen":
            check("clock rollback does not start a second window", started == 1, started)
        if name in ("spring", "fall", "fold_end", "gap_start", "gap_end", "leap_day", "common_century", "new_year", "leap_repeat", "negative_leap", "ad_hoc_fold"):
            check("repeated wall times do not start a second window", started == 1, started)
        if name == "selective_rejection":
            check("the device actually refused a charge-limit write",
                  refused_limit)
        if moments(name)[-1][0] >= deadline:
            check("the original reserve is restored", p.min_soc == 8, p.min_soc)
            check("the actual grid-charge switch is off after the window", not p.grid_charge)
        result.update(passed=True, starts=started, final_soc=p.soc,
                      physical_hours=sum(seconds for _, seconds in moments(name)) / 3600)
    finally:
        await hass.async_stop()
        (root / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--only", nargs="+", choices=CASES)
    args = parser.parse_args()
    if args.case:
        import time_machine
        with time_machine.travel(moments(args.case)[0][0], tick=False) as clock:
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
                runner.run(run_case(args.root, args.case, clock))
        return
    reports = LAB / "reports"
    reports.mkdir(exist_ok=True)
    results = []
    failures = []
    with tempfile.TemporaryDirectory(prefix="icn-edge-") as temporary:
        for name in args.only or CASES:
            root = Path(temporary) / name
            for domain, source in [("inverter_charge_night", LAB.parent / "custom_components/inverter_charge_night"),
                                   ("icn_simulator", LAB / "custom_components/icn_simulator")]:
                shutil.copytree(source, root / "custom_components" / domain,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            with (reports / f"edge-{name}.log").open("w", encoding="utf-8") as log:
                try:
                    completed = subprocess.run([sys.executable, "-X", "utf8", __file__, "--case", name, "--root", str(root)],
                                               stdout=log, stderr=subprocess.STDOUT, timeout=90)
                    exited_cleanly = completed.returncode == 0
                except subprocess.TimeoutExpired:
                    log.write("\nFAIL: isolated HA case exceeded its 90-second deadline\n")
                    exited_cleanly = False
            result_path = root / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {"case": name, "checks": []}
            results.append(result)
            passed = exited_cleanly and result.get("passed") is True
            if not passed:
                failures.append(name)
            print(f"{'PASS' if passed else 'FAIL'} {name}: {len(result['checks'])} checks", flush=True)
    summary = {"source": "actual HA, generic HA inverter adapter, explicitly invoked polling/date callbacks",
               "cases": results, "failures": failures,
               "checks": sum(len(result["checks"]) for result in results)}
    (reports / "edge-cases.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if failures:
        raise SystemExit("Failed: " + ", ".join(failures) + "; see lab/reports/edge-<case>.log")


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    main()
