"""Loopback device transport for the unmodified evcc HTTP plugins."""
from __future__ import annotations

import math
from aiohttp import web


class DeviceAPI:
    def __init__(self, lab, port):
        self.lab = lab
        self.port = port
        self.runner = None

    async def start(self):
        app = web.Application(client_max_size=4096)
        app.router.add_get("/api/state", self.state)
        app.router.add_post("/api/wallbox/{box}/{key}/{value}", self.write)
        app.router.add_post("/api/scenario/{name}", self.scenario)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    async def state(self, request):
        p = self.lab.plant
        if not p.meter_online:
            raise web.HTTPServiceUnavailable(text="Simulated grid meter disconnected")
        wallboxes = []
        for box in (1, 2):
            power = p.wallbox_power(box)
            connected = getattr(p, f"wallbox{box}_connected")
            wallboxes.append({"status": "A" if not connected else "C" if power > 0 else "B",
                "enabled": getattr(p, f"wallbox{box}_enabled"), "power": power,
                "current": power / max(sum(p.voltages), 1),
                "limit": getattr(p, f"wallbox{box}_current")})
        return web.json_response({"grid": {"power": p.grid_power, "currents": p.phase_currents,
            "voltages": p.voltages, "connected": p.grid_connected},
            "pv": {"power": p.effective_pv},
            "battery": {"power": -p.battery_power, "soc": p.soc,
                "charge_ac_power": p.charge_ac_power, "grid_charge": p.grid_charge},
            "house": {"power": p.total_house_load}, "wallboxes": wallboxes,
            "overcurrent": p.overcurrent, "unserved": p.unserved_power,
            "scenario": self.lab.scenario, "commands": list(self.lab.commands)})

    async def write(self, request):
        box, key, value = (request.match_info[x] for x in ("box", "key", "value"))
        if box not in ("1", "2") or key not in ("enabled", "current"):
            raise web.HTTPNotFound()
        if key == "enabled":
            if value not in ("true", "false"):
                raise web.HTTPBadRequest()
            value = value == "true"
        else:
            try:
                value = float(value)
            except ValueError:
                raise web.HTTPBadRequest() from None
            if not math.isfinite(value) or not 0 <= value <= 32:
                raise web.HTTPBadRequest()
        field = f"wallbox{box}_{key}"
        setattr(self.lab.plant, field, value)
        self.lab.audit("evcc." + field, value)
        self.lab.plant.advance(0.001)
        self.lab.publish()
        return web.Response(text="ok")

    async def scenario(self, request):
        from .model import SCENARIOS
        name = request.match_info["name"]
        if name not in SCENARIOS:
            raise web.HTTPBadRequest()
        await self.lab.hass.services.async_call("icn_simulator", "load_scenario",
            {"scenario": name}, blocking=True)
        return web.Response(text="ok")
