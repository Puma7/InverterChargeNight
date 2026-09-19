"""Conformance checks for the integration's metadata files.

These mirror what hassfest and HACS validate, so a bad manifest fails here
instead of in a release.
"""
from __future__ import annotations

import json
import pathlib
import re

PKG = pathlib.Path(__file__).parent.parent / "custom_components" / "inverter_charge_night"
ROOT = PKG.parent.parent

# Keys Home Assistant accepts in a custom integration manifest.
ALLOWED_MANIFEST_KEYS = {
    "domain", "name", "version", "documentation", "issue_tracker", "dependencies",
    "after_dependencies", "codeowners", "requirements", "iot_class", "config_flow",
    "integration_type", "quality_scale", "single_config_entry", "loggers", "dhcp",
    "ssdp", "zeroconf", "homekit", "mqtt", "usb", "bluetooth", "import_executor",
}
REQUIRED_MANIFEST_KEYS = {"domain", "name", "documentation", "codeowners", "iot_class", "version"}
IOT_CLASSES = {
    "local_polling", "local_push", "cloud_polling", "cloud_push", "calculated", "assumed_state",
}


def _manifest() -> dict:
    return json.loads((PKG / "manifest.json").read_text())


def test_manifest_has_only_known_keys():
    """An unknown key is silently ignored by HA and rejected by hassfest."""
    assert set(_manifest()) <= ALLOWED_MANIFEST_KEYS


def test_manifest_has_the_required_keys():
    assert REQUIRED_MANIFEST_KEYS <= set(_manifest())


def test_manifest_version_is_semantic():
    assert re.match(r"^\d+\.\d+\.\d+", _manifest()["version"])


def test_manifest_iot_class_is_valid():
    assert _manifest()["iot_class"] in IOT_CLASSES


def test_manifest_urls_are_not_placeholders():
    manifest = _manifest()
    for key in ("documentation", "issue_tracker"):
        assert "yourusername" not in manifest.get(key, "")


def test_hacs_json_declares_a_name_and_a_minimum_version():
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"]
    assert re.match(r"^\d{4}\.\d+\.\d+$", hacs["homeassistant"])


def test_every_entity_translation_key_has_a_name():
    """An entity whose translation key is missing renders as the raw key."""
    strings = json.loads((PKG / "strings.json").read_text())
    declared = {key for section in strings["entity"].values() for key in section}
    used = set()
    for module in PKG.glob("*.py"):
        used |= set(re.findall(r'_attr_translation_key = "([^"]+)"', module.read_text()))
    assert used, "no entity translation keys found"
    assert used <= declared
