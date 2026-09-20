"""End-to-end smoke test: load the integration in a real Home Assistant instance.

Run it with a Home Assistant of your choice installed:

    python scripts/smoke_real_ha.py

Unlike the unit suite, nothing about Home Assistant is mocked here: a real core
is bootstrapped, the config entry is set up, and the integration drives a real
window. Only the inverter's own services are stood in for, the way a second
integration would provide them. Exits non-zero when a check fails.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


async def main() -> int:
    from homeassistant import config_entries, core, loader
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import dt as dt_util

    config_dir = tempfile.mkdtemp(prefix="ha-smoke-")
    os.makedirs(os.path.join(config_dir, "custom_components"), exist_ok=True)
    os.symlink(
        os.path.join(REPO, "custom_components", "inverter_charge_night"),
        os.path.join(config_dir, "custom_components", "inverter_charge_night"),
    )

    from homeassistant import bootstrap

    hass = core.HomeAssistant(config_dir)
    hass.config.config_dir = config_dir
    hass.config.latitude = 52.1
    hass.config.longitude = 11.6
    hass.config.skip_pip = True
    hass.config.skip_pip_packages = []
    loader.async_setup(hass)
    await bootstrap.async_from_config_dict({"homeassistant": {}}, hass)
    await hass.async_block_till_done()

    problems: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
        if not ok:
            problems.append(label)

    # The integration must be discoverable as a custom integration
    integration = await loader.async_get_integration(hass, "inverter_charge_night")
    check("manifest loads", integration.domain == "inverter_charge_night",
          f"version={integration.manifest.get('version')}")
    check("manifest declares config_flow", bool(integration.manifest.get("config_flow")))

    # Real states for every entity the integration talks to
    hass.states.async_set("number.inv_min_soc", "8", {"unit_of_measurement": "%"})
    hass.states.async_set("switch.inv_grid_charge", "off")
    hass.states.async_set("sensor.pv_forecast_tomorrow", "5.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.battery_soc", "42", {"unit_of_measurement": "%"})
    hass.states.async_set("number.inv_ac_charge_limit", "5000", {"unit_of_measurement": "W"})
    now = dt_util.now()
    hass.states.async_set(
        "sun.sun", "below_horizon",
        {"next_rising": (now + timedelta(hours=6)).isoformat(),
         "next_setting": (now + timedelta(hours=16)).isoformat()},
    )

    # Stand in for the inverter integration: real HA services that write the state,
    # so the smoke test exercises the real service-call path.
    calls: list[tuple[str, str, dict]] = []

    from homeassistant.const import EVENT_CALL_SERVICE

    @core.callback
    def _record(event: core.Event) -> None:
        d = event.data.get("service_data", {})
        calls.append((event.data["domain"], event.data["service"], dict(d)))
        eids = d.get("entity_id")
        if not eids:
            return
        for eid in ([eids] if isinstance(eids, str) else eids):
            old = hass.states.get(eid)
            if event.data["service"] == "set_value":
                hass.states.async_set(eid, str(d["value"]), old.attributes if old else {})
            elif event.data["service"] in ("turn_on", "turn_off"):
                hass.states.async_set(eid, "on" if event.data["service"] == "turn_on" else "off",
                                      old.attributes if old else {})

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)

    async def _set_value(call: core.ServiceCall) -> None:
        for eid in ([call.data["entity_id"]] if isinstance(call.data["entity_id"], str)
                    else call.data["entity_id"]):
            old = hass.states.get(eid)
            hass.states.async_set(eid, str(call.data["value"]), old.attributes if old else {})

    async def _switch(call: core.ServiceCall) -> None:
        state = "on" if call.service == "turn_on" else "off"
        for eid in ([call.data["entity_id"]] if isinstance(call.data["entity_id"], str)
                    else call.data["entity_id"]):
            old = hass.states.get(eid)
            hass.states.async_set(eid, state, old.attributes if old else {})

    hass.services.async_register("number", "set_value", _set_value)
    hass.services.async_register("switch", "turn_on", _switch)
    hass.services.async_register("switch", "turn_off", _switch)

    data = {
        "name": "Smoke",
        "operation_mode": "night_charge",
        "min_soc_entity": "number.inv_min_soc",
        "grid_charge_switch": "switch.inv_grid_charge",
        "pv_forecast_entity": "sensor.pv_forecast_tomorrow",
        "battery_soc_entity": "sensor.battery_soc",
        "battery_capacity": 10.0,
        # A window that contains the current time, so the polling safety check
        # (which ends a window whose end time has passed) does not fire.
        "start_time": (now - timedelta(hours=1)).strftime("%H:%M"),
        "end_time": (now + timedelta(hours=1)).strftime("%H:%M"),
        "user_min_soc": 8.0,
        "user_max_soc": 100.0,
        "forecast_error_margin": 10.0,
        "default_min_soc": 8.0,
        "update_interval": 900,
        "command_delay": 0.1,
        "min_charge_power_w": 1000,
        "max_charge_power_w": 10000,
        "charge_power_entity": "number.inv_ac_charge_limit",
        "auto_efficient_charge": False,
    }

    import inspect
    kwargs = dict(
        version=1, minor_version=1, domain="inverter_charge_night", title="Smoke",
        data=data, source="user", options={}, unique_id="number.inv_min_soc",
        entry_id="smoke1", discovery_keys={}, subentries_data=(),
    )
    accepted = set(inspect.signature(config_entries.ConfigEntry.__init__).parameters)
    entry = config_entries.ConfigEntry(**{k: v for k, v in kwargs.items() if k in accepted})
    hass.config_entries._entries[entry.entry_id] = entry  # noqa: SLF001

    ok_setup = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check("config entry sets up", ok_setup, f"state={entry.state}")

    coordinator = getattr(entry, "runtime_data", None)
    check("runtime_data holds the coordinator", coordinator is not None)

    registry = er.async_get(hass)
    ents = [e.entity_id for e in registry.entities.values() if e.config_entry_id == entry.entry_id]
    expected_domains = {"sensor", "switch", "number", "binary_sensor", "select"}
    got_domains = {e.split(".")[0] for e in ents}
    check("all five platforms created entities", expected_domains <= got_domains,
          f"{len(ents)} entities: {sorted(ents)}")

    if coordinator is not None:
        # The window contains the current time, so setup itself must have started it
        # and driven the inverter. No manual calls: this is the real path.
        await coordinator.async_refresh()  # async_request_refresh is debounced
        # The control path sleeps command_delay between the two writes, and that
        # timer is not covered by async_block_till_done on every HA version.
        await asyncio.sleep(0.5)
        await hass.async_block_till_done()
        check("window is active after setup", coordinator.is_active is True)
        check("a target was computed", coordinator.initial_calculated_soc is not None,
              f"target={coordinator.initial_calculated_soc}")
        min_soc = hass.states.get("number.inv_min_soc")
        check("inverter min SOC was written",
              min_soc is not None and float(min_soc.state) > 8.0, f"min_soc={min_soc.state}")
        grid = hass.states.get("switch.inv_grid_charge")
        check("grid charging was turned on", grid is not None and grid.state == "on",
              f"grid_charge={grid.state}")

        # Reaching the target must stop grid charging
        hass.states.async_set("sensor.battery_soc", "99", {"unit_of_measurement": "%"})
        await hass.async_block_till_done()
        await coordinator.async_refresh()
        await asyncio.sleep(0.3)
        await hass.async_block_till_done()
        grid = hass.states.get("switch.inv_grid_charge")
        check("grid charging stops at the target", grid is not None and grid.state == "off",
              f"grid_charge={grid.state}")

        # The actions are registered from async_setup, so they exist for every
        # entry and survive one being unloaded.
        check("both actions are registered",
              hass.services.has_service("inverter_charge_night", "plan_target_soc")
              and hass.services.has_service("inverter_charge_night", "reset_inverter"))

        # A response action: it must answer with a plan and write nothing.
        before = hass.states.get("number.inv_min_soc").state
        answer = await hass.services.async_call(
            "inverter_charge_night", "plan_target_soc", {},
            blocking=True, return_response=True,
        )
        check("plan_target_soc answers with a target",
              isinstance(answer, dict) and isinstance(answer.get("target_soc"), float),
              f"answer={answer}")
        check("plan_target_soc writes nothing",
              hass.states.get("number.inv_min_soc").state == before,
              f"min_soc={hass.states.get('number.inv_min_soc').state} (was {before})")

        # The other direction: an action that reaches the inverter. The floor is
        # raised at this point, so a restore to 8 % can only come from the call.
        check("the floor is raised before the reset action", float(before) > 8.0,
              f"min_soc={before}")
        await hass.services.async_call(
            "inverter_charge_night", "reset_inverter", {}, blocking=True,
        )
        await hass.async_block_till_done()
        min_soc = hass.states.get("number.inv_min_soc")
        check("reset_inverter writes the floor back to the inverter",
              min_soc is not None and float(min_soc.state) == 8.0, f"min_soc={min_soc.state}")

        # Bad input is refused with a translated message, not a raw key.
        from homeassistant.exceptions import ServiceValidationError

        try:
            await hass.services.async_call(
                "inverter_charge_night", "reset_inverter",
                {"config_entry_id": "does_not_exist"}, blocking=True,
            )
        except ServiceValidationError as err:
            message = str(err)
        else:
            message = ""
        check("an unknown entry id is refused with a translated message",
              "does_not_exist" in message and "entry_not_found" not in message,
              f"message={message!r}")

        # The window end must restore the original floor
        await coordinator._on_window_end(dt_util.now())  # noqa: SLF001
        await hass.async_block_till_done()
        min_soc = hass.states.get("number.inv_min_soc")
        check("inverter min SOC was restored",
              min_soc is not None and float(min_soc.state) == 8.0, f"min_soc={min_soc.state}")
        check("window is inactive after end", coordinator.is_active is False)

        from custom_components.inverter_charge_night.diagnostics import (
            async_get_config_entry_diagnostics,
        )
        diag = await async_get_config_entry_diagnostics(hass, entry)
        check("diagnostics render", "state" in diag and "entry" in diag)
        check("diagnostics redact entity ids", "number.inv_min_soc" not in str(diag.get("entry", {})))

    ok_unload = await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    check("config entry unloads", ok_unload, f"state={entry.state}")

    # An entry written before 3.0.2 carries the two inverter entities under
    # their kostal_ names. It has to come up as if it had always used the new
    # ones - the migration runs inside async_setup_entry, so this is the only
    # place it can be seen end to end.
    legacy_data = {k: v for k, v in data.items()
                   if k not in ("min_soc_entity", "grid_charge_switch")}
    legacy_data["kostal_min_soc_entity"] = "number.inv_min_soc"
    legacy_data["kostal_grid_charge_switch"] = "switch.inv_grid_charge"
    legacy_kwargs = dict(kwargs) | {"data": legacy_data, "entry_id": "smoke3",
                                    "title": "Smoke legacy", "unique_id": "number.inv_min_soc_3"}
    legacy_entry = config_entries.ConfigEntry(
        **{k: v for k, v in legacy_kwargs.items() if k in accepted}
    )
    hass.config_entries._entries[legacy_entry.entry_id] = legacy_entry  # noqa: SLF001
    ok_legacy = await hass.config_entries.async_setup(legacy_entry.entry_id)
    await hass.async_block_till_done()
    check("an entry from before the rename still sets up", ok_legacy, f"state={legacy_entry.state}")
    check("its inverter entities moved to the new keys",
          legacy_entry.data.get("min_soc_entity") == "number.inv_min_soc"
          and legacy_entry.data.get("grid_charge_switch") == "switch.inv_grid_charge"
          and "kostal_min_soc_entity" not in legacy_entry.data,
          f"keys={sorted(k for k in legacy_entry.data if 'min_soc' in k or 'grid_charge' in k)}")
    await hass.config_entries.async_unload(legacy_entry.entry_id)
    await hass.async_block_till_done()

    # A required entity that Home Assistant does not know keeps the entry in
    # SETUP_RETRY. The message is a translation key, so this also proves that a
    # real HA resolves it instead of showing the key to the user.
    retry_data = dict(data) | {"battery_soc_entity": "sensor.does_not_exist"}
    retry_kwargs = dict(kwargs) | {"data": retry_data, "entry_id": "smoke2",
                                   "title": "Smoke retry", "unique_id": "number.inv_min_soc_2"}
    retry_entry = config_entries.ConfigEntry(
        **{k: v for k, v in retry_kwargs.items() if k in accepted}
    )
    hass.config_entries._entries[retry_entry.entry_id] = retry_entry  # noqa: SLF001
    await hass.config_entries.async_setup(retry_entry.entry_id)
    await hass.async_block_till_done()
    check("a missing entity keeps the entry retrying",
          retry_entry.state is config_entries.ConfigEntryState.SETUP_RETRY,
          f"state={retry_entry.state}")
    check("the not-ready message is translated, not a raw key",
          "sensor.does_not_exist" in (retry_entry.reason or "")
          and "entity_not_available" not in (retry_entry.reason or ""),
          f"reason={retry_entry.reason!r}")

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    await hass.async_stop()

    print()
    if problems:
        print("PROBLEMS:", ", ".join(problems))
        return 1
    print("all smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
