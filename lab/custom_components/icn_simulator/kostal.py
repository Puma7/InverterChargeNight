"""Small local Plenticore REST + Modbus emulator for the KORE control path.

Implements real SCRAM-SHA256 / AES-GCM session negotiation. Unsupported
settings and Modbus addresses fail explicitly. This is not firmware emulation.
Only loopback listeners are permitted; credentials below are public lab data.
"""
from __future__ import annotations

import asyncio
from base64 import b64decode, b64encode
import hashlib
import hmac
import secrets
import struct
import time

from aiohttp import web
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .endpoints import inverter_host

PASSWORD = "lab-only"
SERVICE_CODE = "123456"


def b64(value):
    return b64encode(value).decode("ascii")


class KostalEmulator:
    def __init__(self, lab):
        self.lab = lab
        self.transactions = {}
        self.sessions = {}
        self.runner = None
        self.server = None
        self.clients = set()
        # Independent virtual links; no host network settings are changed.
        self.rest_online = True
        self.modbus_online = True
        self.modbus_delay_s = 0
        # Field quirks of 20/21 October 2026: the charge limit reads back as a
        # float a little below what was written, and Battery:ManualCharge is a
        # forced charge that lifts the limit to the inverter's own value
        self.readback_offset_w = 0.0
        self.manual_charge_limit_w: float | None = None
        # ...and a limit on 1038 lapses back to the inverter's own value unless
        # it is written again within this many seconds (23:01 -> 8 kW at 23:12)
        self.limit_fallback_s: float | None = None
        self.native_charge_limit_w = 0.0
        self._limit_written_at = 0.0

    async def start(self, http_port=80, modbus_port=1502):
        host = inverter_host()
        application = web.Application()
        application.router.add_route("*", "/api/v1/{path:.*}", self.rest)
        self.runner = web.AppRunner(application)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, host, http_port).start()
            self.server = await asyncio.start_server(self.modbus, host, modbus_port)
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        if self.server:
            self.server.close()
        # Server.wait_closed() also waits for active connections. Close those
        # first; a connected KORE client would otherwise deadlock HA shutdown.
        clients = tuple(self.clients)
        for client in clients:
            client.close()
        await asyncio.gather(*(client.wait_closed() for client in clients), return_exceptions=True)
        if self.server:
            await self.server.wait_closed()
        if self.runner:
            await self.runner.cleanup()

    def settings(self):
        p = self.lab.plant
        return {
            "scb:network": {"Network:Hostname": "KOSTAL Lab"},
            "devices:local": {
                "Properties:SerialNo": "ICN-LAB-0001", "Branding:ProductName1": "PLENTICORE",
                "Branding:ProductName2": "L G3 Lab", "Properties:VersionIOC": "01.00.00",
                "Properties:VersionMC": "03.07.00", "Properties:StringCnt": "2",
                "Properties:String1Features": "0", "Properties:String2Features": "0",
                "Battery:SmartBatteryControl:Enable": "0", "Battery:TimeControl:Enable": "0",
                "Battery:MinSocRel": str(p.min_soc), "Battery:ManualCharge": str(int(p.grid_charge)),
                "Battery:MinSoc": str(p.min_soc), "Battery:MinHomeConsumption": "50", "Battery:MinHomeComsumption": "50",
                "EnergyMgmt:BatCtrl:DisabelDischarge": str(int(p.block_discharge)),
            },
        }

    def process(self):
        p = self.lab.plant
        energy = {"EnergyHome": p.house_energy,
                  "EnergyHomeBat": p.house_battery_energy,
                  "EnergyHomeGrid": p.house_grid_energy,
                  "EnergyHomePv": p.house_pv_energy, "EnergyChargePv": p.pv_battery_energy,
                  "EnergyChargeGrid": p.grid_charge_energy, "EnergyDischargeGrid": 0.0,
                  "EnergyDischarge": p.discharged_energy, "Yield": p.pv_energy}
        result = {
            "devices:local": {"Inverter:State": (3, ""), "Dc_P": (p.effective_pv, "W"),
                "Ac_P": (p.effective_pv - (p.battery_power / p.charge_efficiency if p.battery_power >= 0 else p.battery_power * p.discharge_efficiency), "W"), # Like the real Kostal: its own split of the battery's DC power
                # by source, not a measurement of the charger's AC input. The
                # lab used to publish the AC side here, and so never saw the
                # ratio of -1 the inverter showed on 24/25 October 2026.
                "Grid2Bat_P": (p.battery_power if p.charge_ac_power > 0 else 0.0, "W"),
                "Home_P": (p.total_house_load, "W"), "HomeGrid_P": (max(0, p.grid_power - p.charge_ac_power), "W"),
                "HomeBat_P": (max(0, -p.battery_power) * p.discharge_efficiency, "W")},
            "devices:local:battery": {"SoC": (p.soc, "%"), "P": (p.battery_power, "W"),
                "U": (400, "V"), "I": (p.battery_power / 400, "A"),
                "Temperature": (22, "°C"), "Cycles": (100, ""),
                "WorkCapacity": (p.capacity * 1000, "Wh")},
            "devices:local:pv1": {"P": (p.pv_string_power(1), "W")},
            "devices:local:pv2": {"P": (p.pv_string_power(2), "W")},
            "devices:local:powermeter": {"P": (p.grid_power, "W"),
                "L1_I": (p.grid_power / 690, "A"), "L2_I": (p.grid_power / 690, "A"),
                "L3_I": (p.grid_power / 690, "A")},
            "scb:statistic:EnergyFlow": {
                f"Statistic:{key}:{period}": (value * 1000, "Wh")
                for key, value in energy.items() for period in ("Day", "Month", "Year", "Total")},
        }
        # KORE polls standard groups in a batch, including nominal diagnostics.
        # Report explicit simulated values for that group; unknown IDs still fail.
        home_pv = min(p.total_house_load, p.effective_pv)
        home_battery = min(max(0, p.total_house_load - home_pv), max(0, -p.battery_power) * p.discharge_efficiency)
        result["devices:local"].update({
            "Grid_P": (p.grid_power, "W"), "HomeOwn_P": (home_pv + home_battery, "W"),
            "HomePv_P": (home_pv, "W"), "PV2Bat_P": (max(0, p.battery_power) if p.charge_ac_power == 0 else 0, "W"),
            "EM_State": (0, ""), "Properties:String1Features": (0, ""), "Properties:String2Features": (0, ""),
        })
        result["devices:local:battery"].update({
            "SoH": (100, "%"), "FullChargeCap_E": (p.capacity * 1000 / 400, "Ah"),
            "BatManufacturer": (0, ""), "BatModel": (0, ""), "BatSerialNo": (1001, ""),
            "BatVersionFW": (1, ""), "BatModuleCnt": (10, ""),
        })
        ac_output = p.effective_pv - (p.battery_power / p.charge_efficiency if p.battery_power >= 0 else p.battery_power * p.discharge_efficiency)
        result["devices:local:ac"] = {"P": (ac_output, "W"), "Frequency": (50, "Hz"), "CosPhi": (1, "")}
        for phase in (1, 2, 3):
            result["devices:local:ac"].update({f"L{phase}_P": (ac_output / 3, "W"),
                f"L{phase}_U": (230, "V"), f"L{phase}_I": (abs(ac_output) / 690, "A")})
            result["devices:local:powermeter"].update({f"L{phase}_P": (p.phase_powers[phase - 1], "W"),
                f"L{phase}_U": (p.voltages[phase - 1], "V"), f"L{phase}_I": (p.phase_currents[phase - 1], "A")})
        result["devices:local:powermeter"]["Frequency"] = (50, "Hz")
        for string in (1, 2):
            result[f"devices:local:pv{string}"].update({"U": (400, "V"), "I": (p.pv_string_power(string) / 400, "A")})
        result["scb:event"] = {"Event:ActiveWarningCnt": (0, ""), "Event:ActiveAckCnt": (0, "")}
        result["scb:system"] = {"System:State": (1, "")}
        result["scb:update"] = {"Update:Status": (0, ""), "Update:Progress": (0, "%"), "Update:Version": (30700, "")}
        return result

    async def rest(self, request):
        if not self.lab.plant.online or not self.rest_online:
            return web.json_response({"message": "Simulated inverter offline"}, status=503)
        path = request.match_info["path"]
        if path == "info/version":
            return web.json_response({"api_version": "0.2.0", "hostname": "KOSTAL Lab",
                                      "name": "PLENTICORE Lab", "sw_version": "03.07.00"})
        if path.startswith("auth/"):
            return await self.auth(path, request)
        if request.headers.get("authorization") not in self.sessions:
            return web.json_response({"message": "Login required"}, status=401)
        if path == "events/latest":
            return web.json_response([])
        if path == "modules":
            return web.json_response([{"id": mid, "type": "device"} for mid in self.process()])
        if path == "processdata" and request.method == "GET":
            return web.json_response([{"moduleid": mid, "processdataids": list(values)}
                                      for mid, values in self.process().items()])
        if path == "settings" and request.method == "GET":
            return web.json_response([{"moduleid": mid, "settings": [
                {"id": key, "type": "string" if key.startswith(("Properties:", "Branding:", "Network:")) else "float",
                 "access": "readwrite" if key.startswith(("Battery:", "EnergyMgmt:")) else "readonly",
                 "min": "0", "max": "100", "default": value,
                 "unit": "%" if key == "Battery:MinSocRel" else ""}
                for key, value in values.items()]} for mid, values in self.settings().items()])
        if path == "settings" and request.method == "PUT":
            payload = await request.json()
            controls = {"Battery:MinSocRel": ("min_soc", float),
                        "Battery:MinSoc": ("min_soc", float),
                        "Battery:ManualCharge": ("grid_charge", lambda value: bool(int(value))),
                        "EnergyMgmt:BatCtrl:DisabelDischarge": ("block_discharge", lambda value: bool(int(value)))}
            updates = []
            for module in payload:
                for setting in module["settings"]:
                    if module["moduleid"] != "devices:local" or setting["id"] not in controls:
                        return web.json_response({"message": "Unsupported setting"}, status=404)
                    key, convert = controls[setting["id"]]
                    value = convert(setting["value"])
                    if key == "min_soc" and not 0 <= value <= 100:
                        return web.json_response({"message": "Invalid SoC"}, status=400)
                    updates.append((key, value))
            for key, value in updates:
                self.lab.audit(key, value, accepted=not self.lab.plant.reject_writes)
                if not self.lab.plant.reject_writes:
                    setattr(self.lab.plant, key, value)
                    if key == "grid_charge" and value and self.manual_charge_limit_w is not None:
                        self.lab.plant.charge_limit = self.manual_charge_limit_w
            self.lab.publish()
            return web.json_response([])
        for kind, catalog in (("settings", self.settings()), ("processdata", self.process())):
            if path == kind and request.method == "POST":
                selection = await request.json()
            elif path.startswith(kind + "/") and request.method == "GET":
                parts = path.split("/", 2)
                ids_key = "settingids" if kind == "settings" else "processdataids"
                selection = [{"moduleid": parts[1], ids_key: parts[2].split(",") if len(parts) > 2 else list(catalog.get(parts[1], {}))}]
            else:
                continue
            result = []
            for module in selection:
                mid = module["moduleid"]
                ids = module.get("settingids", module.get("processdataids", []))
                if mid not in catalog or any(key not in catalog[mid] for key in ids):
                    return web.json_response({"message": "Unsupported module or data ID"}, status=404)
                values = [{"id": key, "value": str(catalog[mid][key])} if kind == "settings" else
                          {"id": key, "value": catalog[mid][key][0], "unit": catalog[mid][key][1]}
                          for key in ids]
                result.append({"moduleid": mid, kind: values})
            if kind == "settings" and path.startswith("settings/"):
                return web.json_response(result[0]["settings"])
            return web.json_response(result)
        return web.json_response({"message": "Unsupported lab endpoint"}, status=404)

    async def auth(self, path, request):
        if path == "auth/me":
            role = self.sessions.get(request.headers.get("authorization"))
            return web.json_response({"locked": False, "active": bool(role), "authenticated": bool(role),
                                      "anonymous": not bool(role), "permissions": ["read", "write"],
                                      "role": "INSTALLER" if role == "master" else "USER"})
        if path == "auth/logout":
            self.sessions.pop(request.headers.get("authorization"), None)
            return web.json_response({})
        data = await request.json()
        if path == "auth/start":
            nonce = b64(b64decode(data["nonce"]) + secrets.token_bytes(12))
            salt = secrets.token_bytes(16)
            transaction = b64(secrets.token_bytes(16))
            rounds = 4096
            message = f"n={data['username']},r={data['nonce']},r={nonce},s={b64(salt)},i={rounds},c=biws,r={nonce}".encode()
            salted = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt, rounds)
            client_key = hmac.digest(salted, b"Client Key", "sha256")
            stored = hashlib.sha256(client_key).digest()
            signature = hmac.digest(stored, message, "sha256")
            protocol_key = hmac.digest(stored, b"Session Key" + message + client_key, "sha256")
            self.transactions[transaction] = {"user": data["username"], "proof": bytes(a ^ b for a, b in zip(client_key, signature)),
                "signature": hmac.digest(hmac.digest(salted, b"Server Key", "sha256"), message, "sha256"),
                "key": protocol_key, "token": secrets.token_hex(16)}
            return web.json_response({"nonce": nonce, "salt": b64(salt), "rounds": rounds, "transactionId": transaction})
        transaction = self.transactions.get(data.get("transactionId"))
        if not transaction:
            return web.json_response({"message": "Unknown transaction"}, status=400)
        if path == "auth/finish":
            if not hmac.compare_digest(transaction["proof"], b64decode(data["proof"])):
                return web.json_response({"message": "Invalid password"}, status=400)
            transaction["verified"] = True
            return web.json_response({"token": transaction["token"], "signature": b64(transaction["signature"])})
        if path == "auth/create_session" and transaction.get("verified"):
            token = AESGCM(transaction["key"]).decrypt(b64decode(data["iv"]),
                b64decode(data["payload"]) + b64decode(data["tag"]), None).decode()
            expected = transaction["token"] + (":" + SERVICE_CODE if transaction["user"] == "master" else "")
            if not hmac.compare_digest(token, expected):
                return web.json_response({"message": "Invalid service code"}, status=400)
            session = secrets.token_hex(16)
            self.sessions["Session " + session] = transaction["user"]
            self.transactions.pop(data["transactionId"])
            return web.json_response({"sessionId": session})
        return web.json_response({"message": "Invalid authentication request"}, status=400)

    def apply_limit_fallback(self):
        if (self.limit_fallback_s is not None
                and time.monotonic() - self._limit_written_at > self.limit_fallback_s):
            self.lab.plant.charge_limit = self.native_charge_limit_w

    def registers(self):
        self.apply_limit_fallback()
        p = self.lab.plant
        values = {}

        def put(address, fmt, value):
            raw = struct.pack(">" + fmt, value)
            values.update({address + i // 2: struct.unpack(">H", raw[i:i + 2])[0] for i in range(0, len(raw), 2)})

        for address, value in {2: 1, 4: 71, 5: 1, 30: 1, 32: 3, 34: 2, 514: int(p.soc),
                               531: 20000, 588: 2, 1080: 2 << 8, 1082: 1}.items():
            put(address, "H", value)
        for address, value in {54: 20000, 56: 3, 512: int(p.capacity * 1000 / 400), 1070: 1001, 1288: 60}.items():
            put(address, "I", value)
        for address, value in {100: p.effective_pv, 106: p.effective_pv - p.battery_power,
            108: p.total_house_load, 110: max(0, p.grid_power), 114: max(0, -p.battery_power),
            150: 50, 158: 230, 164: 230, 170: 230, 178: p.pv_power, 190: p.battery_power / 400,
            194: 100, 200: p.battery_power / 400, 210: p.soc, 214: 22, 216: 400,
            252: p.grid_power, 260: p.pv_string_power(1), 270: p.pv_string_power(2),
            1034: 0, 1038: p.charge_limit - self.readback_offset_w if p.charge_limit else 0.0,
            1040: p.discharge_limit,
            1042: p.min_soc, 1044: 100.0,
            1046: p.stored_energy * 1000, 1048: p.discharged_energy * 1000,
            1068: p.capacity * 1000, 1076: 10000, 1078: 10000,
            1280: p.charge_limit, 1282: p.discharge_limit, 1284: 5000, 1286: 5000}.items():
            put(address, "f", value)
        for index in range(3):
            for address, value in {222 + 10 * index: p.phase_currents[index],
                                   224 + 10 * index: p.phase_powers[index],
                                   230 + 10 * index: p.voltages[index],
                                   158 + 6 * index: p.voltages[index]}.items():
                put(address, "f", value)
        for address, count, text in [(6, 8, "LAB"), (14, 8, "ICN-LAB-0001"),
            (38, 8, "03.07.00"), (46, 8, "01.00.00"), (58, 13, "03.07.00"),
            (768, 32, "PLENTICORE L G3 Lab"), (800, 32, "20 kW")]:
            raw = text.encode().ljust(count * 2, b"\0")[:count * 2]
            for i in range(count):
                values[address + i] = struct.unpack(">H", raw[i * 2:i * 2 + 2])[0]
        return values

    async def modbus(self, reader, writer):
        self.clients.add(writer)
        try:
            while True:
                header = await reader.readexactly(7)
                transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                if protocol != 0 or not 2 <= length <= 254:
                    break
                packet = await reader.readexactly(length - 1)
                if self.modbus_delay_s:
                    await asyncio.sleep(self.modbus_delay_s)
                function = packet[0]
                response = bytes([function | 0x80, 2])
                if unit != 71 or not self.lab.plant.online or not self.modbus_online:
                    response = bytes([function | 0x80, 11])
                elif function == 3 and len(packet) == 5:
                    address, count = struct.unpack(">HH", packet[1:])
                    registers = self.registers()
                    if 1 <= count <= 125 and all(address + i in registers for i in range(count)):
                        response = bytes([3, count * 2]) + struct.pack(">" + "H" * count,
                            *(registers[address + i] for i in range(count)))
                elif function == 16 and len(packet) >= 6:
                    address, count, size = struct.unpack(">HHB", packet[1:6])
                    if address == 1034 and count == 2 and size == 4 and len(packet) == 10 and struct.unpack(">f", packet[6:])[0] == 0:
                        # KORE releases its inactive SoC controller on unload.
                        response = struct.pack(">BHH", 16, address, count)
                    key = {1038: "charge_limit", 1040: "discharge_limit",
                           1280: "charge_limit", 1282: "discharge_limit", 1042: "min_soc"}.get(address)
                    expected_count = 1 if address == 1042 else 2
                    if key and count == expected_count and size == count * 2 and len(packet) == 6 + size:
                        value = struct.unpack(">H" if count == 1 else ">f", packet[6:])[0]
                        maximum = 100 if key == "min_soc" else 20000
                        if 0 <= value <= maximum:
                            refused = self.lab.plant.reject_writes or (key == "charge_limit" and (
                                self.lab.plant.reject_charge_limit
                                # A forced charge acknowledges a limit and ignores it
                                or (self.manual_charge_limit_w is not None and self.lab.plant.grid_charge)))
                            self.lab.audit(key, value, accepted=not refused)
                            if not refused:
                                setattr(self.lab.plant, key, float(value))
                                if key == "charge_limit":
                                    self._limit_written_at = time.monotonic()
                            self.lab.publish()
                            response = struct.pack(">BHH", 16, address, count)
                        else:
                            response = bytes([0x90, 3])
                writer.write(struct.pack(">HHHB", transaction, 0, len(response) + 1, unit) + response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()
            await writer.wait_closed()
