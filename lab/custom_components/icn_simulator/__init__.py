"""Local-only virtual inverter for a full Home Assistant development instance."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, fields
from datetime import timedelta
import logging
import time

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP, EVENT_HOMEASSISTANT_CLOSE
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .defaults import configure_planner, planner_defaults
from .entity import DOMAIN
from .model import Plant, SCENARIOS

LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): vol.Schema({
    vol.Optional("auto_configure", default=True): cv.boolean,
    vol.Optional("capacity_kwh", default=35.8): vol.All(vol.Coerce(float), vol.Range(min=1, max=200)),
    vol.Optional("kostal_emulator", default=False): cv.boolean,
    vol.Optional("api_port", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
    vol.Optional("persist_plant", default=False): cv.boolean,
    vol.Optional("auto_configure_kore", default=False): cv.boolean,
})}, extra=vol.ALLOW_EXTRA)


class Laboratory:
    def __init__(self, hass, capacity):
        self.hass = hass
        self.plant = Plant(capacity=capacity)
        self.listeners = set()
        self.commands = deque(maxlen=30)
        self.scenario = "manual"
        self.last_tick = time.monotonic()

    @callback
    def publish(self):
        for listener in tuple(self.listeners):
            listener()

    def audit(self, key, value, accepted=True):
        self.commands.append({"time": dt_util.utcnow().isoformat(),
                              "key": key, "value": value, "accepted": accepted})

    @callback
    def tick(self, now):
        current = time.monotonic()
        seconds = min(current - self.last_tick, 5) * self.plant.speed
        self.last_tick = current
        if seconds > 0:
            self.plant.advance(seconds)
            self.publish()


async def async_setup(hass, config):
    settings = config[DOMAIN]
    lab = hass.data[DOMAIN] = Laboratory(hass, settings["capacity_kwh"])
    store = Store(hass, 1, "icn_lab_plant") if settings["persist_plant"] else None
    if store is not None:
        saved = await store.async_load()
        if saved:
            import math
            for field in fields(lab.plant):
                value = saved.get("plant", {}).get(field.name)
                original = getattr(lab.plant, field.name)
                if type(value) is type(original) and (isinstance(value, bool) or math.isfinite(value)):
                    setattr(lab.plant, field.name, value)
            # The inverter keeps its last setpoints while HA is down. This is a
            # constant-input replay, not a prediction of household behaviour.
            downtime = max(0, min(86400, time.time() - saved.get("timestamp", time.time())))
            if downtime:
                lab.plant.advance(downtime)
            lab.scenario = "restored_constant_inputs"
            LOGGER.info("Restored physical plant; replayed %.1fs downtime", downtime)
    for platform in ("number", "switch", "sensor", "binary_sensor"):
        await discovery.async_load_platform(hass, platform, DOMAIN, {}, config)

    async def refresh_planner():
        for entry in hass.config_entries.async_entries("inverter_charge_night"):
            if entry.title == "ICN Lab":
                coordinator = getattr(entry, "runtime_data", None)
                if coordinator is not None:
                    await coordinator.async_refresh()

    async def load_scenario(call):
        # Restore a running controller before replacing plant state.
        if lab.plant.online and not lab.plant.charge_inhibit and hass.services.has_service("inverter_charge_night", "reset_inverter"):
            for entry in hass.config_entries.async_entries("inverter_charge_night"):
                if entry.title == "ICN Lab":
                    await hass.services.async_call("inverter_charge_night", "reset_inverter",
                        {"config_entry_id": entry.entry_id}, blocking=True)
        capacity = lab.plant.capacity
        lab.plant.load_scenario(call.data["scenario"])
        lab.plant.capacity = capacity
        lab.plant.advance(0.001)
        lab.scenario = call.data["scenario"]
        lab.commands.clear()
        lab.publish()
        await refresh_planner()

    async def step(call):
        lab.plant.advance(call.data["seconds"])
        lab.publish()
        await refresh_planner()

    async def refresh(call):
        await refresh_planner()

    hass.services.async_register(DOMAIN, "load_scenario", load_scenario,
        schema=vol.Schema({vol.Required("scenario"): vol.In(SCENARIOS)}))
    hass.services.async_register(DOMAIN, "step", step,
        schema=vol.Schema({vol.Required("seconds"): vol.All(vol.Coerce(float), vol.Range(min=0.01, max=86400))}))
    hass.services.async_register(DOMAIN, "refresh", refresh, schema=vol.Schema({}))

    cancel = async_track_time_interval(hass, lab.tick, timedelta(seconds=1))
    async def save(now=None):
        if store is not None:
            await store.async_save({"timestamp": time.time(), "plant": asdict(lab.plant)})
    cancel_save = async_track_time_interval(hass, save, timedelta(seconds=5)) if store else lambda: None

    async def stop(event):
        cancel()
        cancel_save()

    async def close(event):
        emulator = getattr(lab, "emulator", None)
        if emulator is not None:
            await emulator.stop()
        if getattr(lab, "device_api", None) is not None:
            await lab.device_api.stop()
        await save()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, close)

    if settings["kostal_emulator"]:
        from .kostal import KostalEmulator
        lab.emulator = KostalEmulator(lab)
        await lab.emulator.start()

    if settings["api_port"]:
        from .evcc import DeviceAPI
        lab.device_api = DeviceAPI(lab, settings["api_port"])
        await lab.device_api.start()

    async def started(event):
        if settings["auto_configure"]:
            defaults = planner_defaults(lab.plant.capacity)
            user_controller = False
            if settings["auto_configure_kore"]:
                from .fixtures import configure_kore
                _, mapping = await configure_kore(hass)
                user_controller = any(entry.title != "ICN Lab" and
                    entry.data.get("min_soc_entity") == mapping["min_soc_entity"]
                    for entry in hass.config_entries.async_entries("inverter_charge_night"))
                if not user_controller:
                    defaults.update(mapping)
            # Migrate only entries owned by this lab fixture, through HA's API.
            for entry in hass.config_entries.async_entries("inverter_charge_night"):
                if entry.title == "ICN Lab":
                    if user_controller and getattr(entry, "runtime_data", None) is not None:
                        await entry.runtime_data.async_disable()
                    data = dict(entry.data)
                    for key in ("grid_import_entity", "backup_mode_entity", "min_soc_entity", "grid_charge_switch",
                                "battery_soc_entity", "charge_power_entity", "discharge_limit_entity",
                                "charge_power_sent_entity", "charge_power_received_entity"):
                        data[key] = defaults[key]
                    if data != entry.data:
                        hass.config_entries.async_update_entry(entry, data=data, unique_id=defaults["min_soc_entity"])
            await configure_planner(hass, defaults)
            LOGGER.info("ICN laboratory configured; open /icn-lab")

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, started)
    return True
