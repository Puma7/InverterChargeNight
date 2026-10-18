"""Parallel laboratories must preserve identity and isolate evcc servers."""
import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
import yaml

from lab.evcc_config import configuration, seed


@pytest.mark.parametrize("ui_port", [7070, 7071, 65535])
def test_seed_isolates_protocol_ports_without_replacing_identity(tmp_path, ui_port):
    config = tmp_path / "evcc.yaml"
    config.write_text(f"network:\n  port: {ui_port}\n", encoding="utf-8")
    original = {"shipid": "existing-id", "certificate": {"private": "fake-private", "public": "fake-public"},
                "secret": "existing-pairing", "port": 4712}
    saved = {}

    def run(args, **kwargs):
        if args[1:3] == ["settings", "get"]:
            key = args[-1].strip("^$")
            value = original if key == "eebus" else {"port": 8887}
            return CompletedProcess(args, 0, stdout=key + ": " + json.dumps(value), stderr="")
        if args[-2] in ("eebus", "ocpp"):
            saved[args[-2]] = json.loads(args[-1])
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("lab.evcc_config.subprocess.run", side_effect=run):
        seed(Path("evcc"), tmp_path / "evcc.db", config)
    assert saved["eebus"] == {**original, "port": (ui_port + 9999) % 65535 + 1}
    assert saved["ocpp"]["port"] == (ui_port + 19999) % 65535 + 1
    assert len({ui_port, saved["eebus"]["port"], saved["ocpp"]["port"]}) == 3


def test_seed_failure_does_not_expose_certificate_in_exception(tmp_path):
    config = tmp_path / "evcc.yaml"
    config.write_text("network:\n  port: 7071\n", encoding="utf-8")

    def run(args, **kwargs):
        if args[1:3] == ["settings", "get"]:
            return CompletedProcess(args, 0, stdout='eebus: {"certificate":{"private":"fake-private"}}', stderr="")
        return CompletedProcess(args, 1 if args[-2] == "eebus" else 0, stdout="", stderr="")

    with patch("lab.evcc_config.subprocess.run", side_effect=run), \
         pytest.raises(RuntimeError, match="evcc eebus settings write failed") as error:
        seed(Path("evcc"), tmp_path / "evcc.db", config)
    assert "fake-private" not in str(error.value)


@pytest.mark.parametrize("ui_port,device_port,proxy_port", [(8080, 18080, 15502), (5502, 18080, 15502)])
def test_protocol_ports_avoid_configured_device_and_proxy_endpoints(tmp_path, ui_port, device_port, proxy_port):
    config = tmp_path / "evcc.yaml"
    config.write_text(yaml.safe_dump(configuration(device_port, ui_port, proxy_port)), encoding="utf-8")
    saved = {}

    def run(args, **kwargs):
        if args[1:3] == ["settings", "get"]:
            return CompletedProcess(args, 0, stdout="", stderr="")
        if args[-2] in ("eebus", "ocpp"):
            saved[args[-2]] = json.loads(args[-1])
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("lab.evcc_config.subprocess.run", side_effect=run):
        seed(Path("evcc"), tmp_path / "evcc.db", config)
    ports = {ui_port, device_port, proxy_port, saved["eebus"]["port"], saved["ocpp"]["port"]}
    assert len(ports) == 5
