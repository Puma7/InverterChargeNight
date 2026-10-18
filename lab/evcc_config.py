"""Generate evcc configuration against only the local simulated devices."""
from pathlib import Path
import json
import math
import subprocess
from urllib.parse import urlsplit
import yaml


def configuration(device_port=18080, ui_port=7070, proxy_port=None, capacity_kwh=35.8):
    if not math.isfinite(capacity_kwh) or capacity_kwh <= 0:
        raise ValueError("Battery capacity must be finite and positive")
    uri = f"http://127.0.0.1:{device_port}"

    def get(path):
        return {"source": "http", "uri": uri + "/api/state", "jq": path, "timeout": "2s"}

    meters = [{"name": "grid", "type": "custom", "power": get(".grid.power"),
        "currents": [get(f".grid.currents[{i}]") for i in range(3)],
        "voltages": [get(f".grid.voltages[{i}]") for i in range(3)]},
        {"name": "pv", "type": "custom", "power": get(".pv.power")},
        {"name": "battery", "type": "custom", "power": get(".battery.power"),
         "soc": get(".battery.soc"), "capacity": capacity_kwh}]
    if proxy_port is not None:
        def register(address, decode="float32"):
            return {"source": "modbus", "uri": f"127.0.0.1:{proxy_port}", "id": 71,
                    "timeout": "2s", "register": {"address": address, "type": "holding", "decode": decode}}
        meters[0].update(power=register(252),
            currents=[register(222 + 10 * i) for i in range(3)],
            voltages=[register(230 + 10 * i) for i in range(3)])
        meters[2]["soc"] = register(514, "uint16")
    chargers, loadpoints = [], []
    for i in range(2):
        box = i + 1
        chargers.append({"name": f"wallbox{box}", "type": "custom",
            "status": get(f".wallboxes[{i}].status"),
            "enabled": get(f".wallboxes[{i}].enabled"),
            "enable": {"source": "http", "method": "POST",
                "uri": uri + f"/api/wallbox/{box}/enabled/" + "{{.enable}}"},
            "maxcurrent": {"source": "http", "method": "POST",
                "uri": uri + f"/api/wallbox/{box}/current/" + "{{.maxcurrent}}"},
            "power": get(f".wallboxes[{i}].power"),
            "currents": [get(f".wallboxes[{i}].current") for _ in range(3)]})
        loadpoints.append({"title": f"Labor Wallbox {box} (22 kW)", "charger": f"wallbox{box}",
            "mode": "now", "circuit": "main"})
    return {"network": {"host": "127.0.0.1", "port": ui_port}, "interval": "1s",
        "site": {"title": "ICN / KORE / evcc Labor", "meters": {"grid": "grid", "pv": ["pv"], "battery": ["battery"]}},
        "meters": meters, "chargers": chargers, "loadpoints": loadpoints,
        "circuits": [{"name": "main", "title": "3 x 63 A, 80 % Dauerlast", "meter": "grid",
            "maxcurrent": 50.4, "maxpower": 34776}],
        "tariffs": {"currency": "EUR", "grid": {"type": "fixed", "price": 0.30,
            "zones": [{"hours": "23-0", "price": 0.14}, {"hours": "0-5", "price": 0.14}]},
            "feedin": {"type": "fixed", "price": 0.08}}}


def write(path: Path, device_port=18080, ui_port=7070, capacity_kwh=None):
    if capacity_kwh is None:
        ha_config = path.parent.parent / "configuration.yaml"
        config = yaml.safe_load(ha_config.read_text(encoding="utf-8")) if ha_config.exists() else {}
        capacity_kwh = float((config or {}).get("icn_simulator", {}).get("capacity_kwh", 35.8))
    path.write_text(yaml.safe_dump(configuration(device_port, ui_port, capacity_kwh=capacity_kwh), sort_keys=False, allow_unicode=True), encoding="utf-8")


def _endpoint_ports(settings):
    """Collect device/proxy listeners referenced by the generated configuration."""
    ports = set()
    if isinstance(settings, dict):
        uri = settings.get("uri")
        if isinstance(uri, str):
            endpoint = urlsplit(uri if "://" in uri else "//" + uri)
            if endpoint.port is not None:
                ports.add(endpoint.port)
        for value in settings.values():
            ports.update(_endpoint_ports(value))
    elif isinstance(settings, list):
        for value in settings:
            ports.update(_endpoint_ports(value))
    return ports


def seed(binary: Path, database: Path, config: Path):
    settings = yaml.safe_load(config.read_text(encoding="utf-8"))
    ui_port = int(settings["network"]["port"])
    if not 1 <= ui_port <= 65535:
        raise ValueError("Invalid evcc UI port")
    reserved = {ui_port, *_endpoint_ports(settings)}
    # Each nearby UI endpoint gets distinct protocol servers too. Merge the
    # existing database objects so certificates and pairing identities survive.
    for key, offset in (("eebus", 10000), ("ocpp", 20000)):
        common = ["--config", str(config), "--database", str(database)]
        read = subprocess.run([str(binary), "settings", "get", *common, f"^{key}$"],
                              capture_output=True, text=True)
        if read.returncode:
            raise RuntimeError(f"evcc {key} settings read failed")
        value = next((line.partition(":")[2].strip() for line in read.stdout.splitlines()
                      if line.startswith(key + ":")), "{}")
        current = json.loads(value)
        if not isinstance(current, dict):
            raise ValueError(f"Invalid evcc {key} settings object")
        protocol_port = (ui_port + offset - 1) % 65535 + 1
        while protocol_port in reserved:
            protocol_port = protocol_port % 65535 + 1
        reserved.add(protocol_port)
        current["port"] = protocol_port
        # Do not let CalledProcessError expose certificate material in argv.
        write_result = subprocess.run([str(binary), "settings", "set", "--force", *common,
                                       key, json.dumps(current)], capture_output=True)
        if write_result.returncode:
            raise RuntimeError(f"evcc {key} settings write failed")
    for box in (1, 2):
        subprocess.run([str(binary), "settings", "set", "--force", "--config", str(config),
            "--database", str(database), f"lp{box}.maxCurrent", "32"], check=True, capture_output=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-port", type=int, default=18080)
    parser.add_argument("--ui-port", type=int, default=7070)
    parser.add_argument("--seed-only", action="store_true", help="Preserve YAML and seed this lab's evcc settings")
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in (args.device_port, args.ui_port)):
        parser.error("Ports must be between 1 and 65535")
    target = Path(__file__).resolve().parent / "runtime/evcc/evcc.yaml"
    if args.seed_only:
        seed(target.parent / "evcc.exe", target.parent / "evcc.db", target)
        raise SystemExit(0)
    target.parent.mkdir(parents=True, exist_ok=True)
    write(target, device_port=args.device_port, ui_port=args.ui_port)
    target.with_name("endpoint.json").write_text(json.dumps({"url": f"http://127.0.0.1:{args.ui_port}"}) + "\n", encoding="utf-8")
    print(target)
