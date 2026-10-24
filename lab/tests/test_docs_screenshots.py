"""The screenshot script maps the dashboard examples' ids onto the lab's entities."""
import re
import sys
import types
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[1]
REPO = LAB.parent


@pytest.fixture(scope="module")
def shots():
    if "playwright.async_api" not in sys.modules:  # only its names are needed here
        api = types.ModuleType("playwright.async_api")
        api.Page, api.async_playwright = object, None
        sys.modules.setdefault("playwright", types.ModuleType("playwright"))
        sys.modules["playwright.async_api"] = api
    sys.path.insert(0, str(LAB))
    try:
        import docs_screenshots
    finally:
        sys.path.remove(str(LAB))
    return docs_screenshots


def test_every_id_in_the_examples_is_known(shots):
    """Codex on #43: the German example has German ids. In a lab set up in
    English they exist under English ids, so the script maps both by the
    entity's translation key instead of trusting the screenshot language."""
    known = shots.example_entity_ids()
    for name in ("dashboard.yaml", "dashboard.de.yaml"):
        text = (REPO / "docs/examples" / name).read_text("utf-8")
        used = set(re.findall(r"\b(?:sensor|switch|number|select|binary_sensor)\.inverter_charge_night_\w+", text))
        assert used and used <= set(known), (name, sorted(used - set(known)))


def test_english_and_german_ids_meet_on_one_key(shots):
    known = shots.example_entity_ids()
    assert known["sensor.inverter_charge_night_target_soc"] == ("sensor", "calculated_soc")
    assert known["sensor.inverter_charge_night_zielladestand"] == ("sensor", "calculated_soc")
    assert known["switch.inverter_charge_night_nachstes_fenster_uberspringen_24_h"] == ("switch", "skip_next")
    assert shots.slug("Schnee-Nächte") == "schnee_nachte"
