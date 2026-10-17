"""Headless acceptance checks against real HA entities, flows and services.

    python lab/check_real_ha.py

Uses an isolated temporary config and no mocked HA services or registries.
The physical inverter is simulated; the production planner is unmodified.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
from pathlib import Path
import shutil
import socket
import sys
import tempfile

LAB = Path(__file__).resolve().parent


async def main() -> None:
    from windows_support import install
    install()
    from homeassistant import bootstrap, core, loader
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import dt as dt_util

    with tempfile.TemporaryDirectory(prefix="icn-real-ha-") as temporary:
        root = Path(temporary)
        sys.path.insert(0, str(root))
        components = root / "custom_components"
        for domain, source in [
            ("inverter_charge_night", LAB.parent / "custom_components/inverter_charge_night"),
            ("icn_simulator", LAB / "custom_components/icn_simulator"),
        ]:
            shutil.copytree(source, components / domain,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        hass = core.HomeAssistant(str(root))
        hass.config.skip_pip = True
        hass.config.skip_pip_packages = []
        loader.async_setup(hass)
        checks = []

        def check(label, condition, detail=None):
            checks.append({"check": label, "passed": bool(condition), "detail": detail})
            print(f"{'PASS' if condition else 'FAIL'} {label}: {detail}", flush=True)
            if not condition:
                raise AssertionError(label)

        async def service(domain, action, data=None, response=False):
            result = await hass.services.async_call(domain, action, data or {},
                blocking=True, return_response=response)
            await hass.async_block_till_done()
            return result

        def state(entity):
            value = hass.states.get(entity)
            assert value is not None, entity
            return value.state

        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                http_port = listener.getsockname()[1]
            result = await bootstrap.async_from_config_dict({
                "http": {"server_host": "127.0.0.1", "server_port": http_port},
                "homeassistant": {"time_zone": "Europe/Berlin", "latitude": 52.52,
                                  "longitude": 13.405, "elevation": 35},
                "sun": {}, "recorder": {},
                "icn_simulator": {"auto_configure": False},
            }, hass)
            check("real Home Assistant boots", result is not None)
            await hass.async_start()
            await hass.async_block_till_done()
            # The frontend requests this whole catalog after login. A missing
            # dependency in an otherwise unused domain must not stall the UI.
            from homeassistant.helpers.service import async_get_all_descriptions
            descriptions = await async_get_all_descriptions(hass)
            check("frontend can load the complete HA service catalog",
                  "number" in descriptions and "switch" in descriptions)
            lab = hass.data["icn_simulator"]
            lab.plant.speed = 0
            await service("icn_simulator", "load_scenario", {"scenario": "winter_cloudy"})
            lab.plant.speed = 0
            lab.plant.soc = 5
            lab.publish()
            check("real number and switch entities registered",
                  er.async_get(hass).async_get("number.icn_lab_min_soc") is not None
                  and hass.services.has_service("number", "set_value"))

            from custom_components.icn_simulator.defaults import configure_planner, planner_defaults
            data = planner_defaults()
            now = dt_util.now()
            data.update({"start_time": (now - timedelta(hours=1)).strftime("%H:%M"),
                         "end_time": (now + timedelta(hours=2)).strftime("%H:%M")})
            data.pop("active_start_date")
            data.pop("active_end_date")
            # Confirm, close, reconstruct the storage object and resume via the
            # same public flow API used by the frontend. No partial active entry.
            from custom_components.inverter_charge_night.config_flow import _GUIDED_GROUPS
            from custom_components.inverter_charge_night.setup_drafts import get_drafts
            wizard = await hass.config_entries.flow.async_init(
                "inverter_charge_night", context={"source": "user"})
            wizard = await hass.config_entries.flow.async_configure(wizard["flow_id"], {"next_step_id": "basics"})
            wizard = await hass.config_entries.flow.async_configure(wizard["flow_id"],
                {key: data[key] for key in _GUIDED_GROUPS["basics"]})
            wizard = await hass.config_entries.flow.async_configure(wizard["flow_id"], {"next_step_id": "save_draft"})
            check("confirmed setup can close without activating an entry", wizard.get("reason") == "draft_saved"
                  and not hass.config_entries.async_entries("inverter_charge_night"))
            draft_file = root / ".storage" / "inverter_charge_night.setup_drafts"
            check("setup draft is actually persisted to disk", draft_file.is_file())
            hass.data.pop("inverter_charge_night_setup_drafts")
            wizard = await hass.config_entries.flow.async_init(
                "inverter_charge_night", context={"source": "user"})
            check("new dialog offers persisted draft", "resume_draft" in wizard["menu_options"])
            wizard = await hass.config_entries.flow.async_configure(wizard["flow_id"], {"next_step_id": "resume_draft"})
            draft_id = next(iter(wizard["data_schema"].schema.values())).config["options"][0]["value"]
            wizard = await hass.config_entries.flow.async_configure(wizard["flow_id"], {"draft": draft_id})
            wizard = await hass.config_entries.flow.async_configure(wizard["flow_id"], {"next_step_id": "basics"})
            defaults = {key.schema: key.default() for key in wizard["data_schema"].schema
                        if key.default is not __import__("voluptuous").UNDEFINED}
            check("resumed page retains gross capacity", defaults["battery_capacity"] == 35.8)
            hass.config_entries.flow.async_abort(wizard["flow_id"])
            await get_drafts(hass).remove(draft_id)
            entry = await configure_planner(hass, data)
            await hass.async_block_till_done()
            check("guided production setup creates a loaded entry",
                  entry is not None and entry.state.value == "loaded", str(entry.state))
            coordinator = entry.runtime_data
            await coordinator.async_refresh()
            await hass.async_block_till_done()
            check("scheduler starts charging in its real time window",
                  coordinator.is_active and state("switch.icn_lab_grid_charge") == "on")

            cloudy = await service("inverter_charge_night", "plan_target_soc", response=True)
            await service("number", "set_value", {"entity_id": "number.icn_lab_set_forecast_today", "value": 30})
            await service("number", "set_value", {"entity_id": "number.icn_lab_set_forecast_tomorrow", "value": 30})
            sunny = await service("inverter_charge_night", "plan_target_soc", response=True)
            check("sunny forecast requires no more grid charging than cloudy forecast",
                  sunny["target_soc"] <= cloudy["target_soc"],
                  {"cloudy": cloudy["target_soc"], "sunny": sunny["target_soc"]})

            await service("inverter_charge_night", "reset_inverter")
            await service("icn_simulator", "load_scenario", {"scenario": "winter_cloudy"})
            lab.plant.speed = 0
            await service("inverter_charge_night", "charge_to", {"target_soc": 70,
                           "duration": {"hours": 2}})
            check("charge_to reaches a real inverter switch", lab.plant.grid_charge)
            # Small physical steps let the real SOC listener stop at the target.
            for _ in range(240):
                await service("icn_simulator", "step", {"seconds": 300})
                if coordinator.target_reached:
                    break
            check("physical battery reaches requested SOC without overshoot",
                  69 <= lab.plant.soc <= 70.01, lab.plant.soc)
            check("SOC listener stops grid charging", not lab.plant.grid_charge)
            check("AC/DC loss is visible in real energy sensors",
                  lab.plant.grid_charge_energy > lab.plant.stored_energy > 0)

            await service("inverter_charge_night", "reset_inverter")
            await coordinator._on_window_end(dt_util.now())
            await hass.async_block_till_done()
            check("window end restores original minimum SOC and charge limit",
                  lab.plant.min_soc == 8 and lab.plant.charge_limit == 5000,
                  {"min_soc": lab.plant.min_soc, "charge_limit": lab.plant.charge_limit})

            await service("switch", "turn_off", {"entity_id": "switch.icn_lab_forecast_available"})
            check("forecast fault produces unavailable telemetry",
                  state("sensor.icn_lab_forecast_tomorrow") == "unavailable")
            await service("switch", "turn_on", {"entity_id": "switch.icn_lab_forecast_available"})
            await service("inverter_charge_night", "charge_to", {"target_soc": 80,
                           "duration": {"hours": 2}})
            await service("switch", "turn_on", {"entity_id": "switch.icn_lab_backup"})
            await asyncio.sleep(0.3)
            await hass.async_block_till_done()
            check("island event releases grid charging and restores reserve",
                  not lab.plant.grid_charge and lab.plant.min_soc == 8)
            await service("switch", "turn_off", {"entity_id": "switch.icn_lab_backup"})
            await service("switch", "turn_on", {"entity_id": "switch.icn_lab_reject_writes"})
            before = lab.plant.charge_limit
            await service("number", "set_value", {"entity_id": "number.icn_lab_charge_limit", "value": 1000})
            check("acknowledged but ignored write is observable by readback",
                  lab.plant.charge_limit == before and not lab.commands[-1]["accepted"])
            await service("switch", "turn_off", {"entity_id": "switch.icn_lab_reject_writes"})
            await service("switch", "turn_off", {"entity_id": "switch.icn_lab_online"})
            check("inverter disconnect marks real telemetry unavailable",
                  state("sensor.icn_lab_battery_soc") == "unavailable")
            await service("switch", "turn_on", {"entity_id": "switch.icn_lab_online"})
            check("inverter reconnect restores telemetry",
                  state("sensor.icn_lab_battery_soc") != "unavailable")
            check("entry unload cleans up successfully", await hass.config_entries.async_unload(entry.entry_id))
        finally:
            await hass.async_stop()
            reports = LAB / "reports"
            reports.mkdir(exist_ok=True)
            from homeassistant.const import __version__
            (reports / "real-ha.json").write_text(json.dumps({"ha_version": __version__,
                "checks": checks}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(main())
