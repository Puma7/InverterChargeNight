"""Regenerate the documentation screenshots from a running lab Home Assistant.

    python lab/docs_screenshots.py --url http://127.0.0.1:8124 --token-file token.txt

Needs Python packages ``playwright`` (with its Chromium) and ``pyyaml``; Pillow is
used to shrink the PNGs when it is installed. The token is a long-lived access
token of an admin user (Profile -> Security), passed with ``--token-file`` or
the ``HA_TOKEN`` environment variable. Home Assistant must have one Inverter
Charge Night entry (the lab sets it up); a running charge window gives a target
SOC instead of "unknown".

Side effects on that instance only: the user's language and the server
language are switched per screenshot language (afterwards the user gets the
first one, the server its original one; Inverter Charge Night is reloaded so
its entity names follow), the storage
dashboard ``icn-docs`` is created or overwritten, and the setup and options
flows are opened and closed again without saving. The reserve form is filled
with the recommended values for the picture but not submitted.

Output: docs/images/screenshots/<lang>/<name>.png
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
from pathlib import Path

import yaml
from playwright.async_api import Page, async_playwright

REPO = Path(__file__).resolve().parent.parent
DOMAIN = "inverter_charge_night"
TARGET = "sensor.inverter_charge_night_target_soc"
DASHBOARD = "icn-docs"
LOCALES = {"en": "en-US", "de": "de-DE"}
# Recommended reserve settings shown on reserve.png (form only, not saved).
RESERVE = {"safety_buffer_kwh": 4.5, "load_safety_level": "p75", "bridge_model": "modelled"}
MDI_COG = "M12,8A4,4"  # mdiCogOutline
MDI_DOTS = "M12,16A2"
MDI_CLOSE = "M19,6.41L17.59"

# Shadow-DOM helpers; HA's UI is web components all the way down.
HELPERS = """
// __deep('a b'): every b anywhere below an a, crossing shadow roots.
window.__deep = (sel, root = document) => sel.split(' ').reduce((roots, part) => {
  const out = [];
  const walk = (r) => { out.push(...r.querySelectorAll(part)); if (r.shadowRoot) walk(r.shadowRoot);
    for (const el of r.querySelectorAll('*')) if (el.shadowRoot) walk(el.shadowRoot); };
  roots.forEach(walk); return [...new Set(out)];
}, [root]);
window.__shown = (els) => els.filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
window.__dialog = () => __shown(__deep('wa-dialog[open]').map((w) => w.shadowRoot.querySelector('dialog'))).pop();
window.__iconButton = (prefix, root = document) =>
  __shown(__deep('ha-icon-button', root)).find((b) => (b.path || '').startsWith(prefix));
window.__hass = () => document.querySelector('home-assistant').hass;
"""


def optimise(png: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        path.write_bytes(png)
        return
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
        path, optimize=True)


class Shooter:
    def __init__(self, page: Page, base: str, lang: str, out: Path) -> None:
        self.page, self.base, self.lang, self.out = page, base, lang, out
        icn = json.loads((REPO / f"custom_components/{DOMAIN}/translations/{lang}.json").read_text("utf-8"))
        self.menu = {step: icn["options"]["step"][step]["menu_options"]
                     for step in ("setup", "features", "planning_features")}

    async def goto(self, path: str) -> None:
        await self.page.goto(self.base + path)
        await self.page.wait_for_function("() => window.__hass && __hass()?.connected && __hass().localize('ui.common.ok')")
        await self.page.wait_for_timeout(1500)

    async def ws(self, message: dict) -> object:
        return await self.page.evaluate("(m) => __hass().callWS(m)", message)

    async def settle(self, selector: str | None = None) -> None:
        """Wait for the selector (default: an open dialog), animations and lazy content; drop focus/hover."""
        if selector is None:
            await self.page.wait_for_function("() => !!__dialog()")
        else:
            await self.page.wait_for_function("(s) => __shown(__deep(s)).length > 0", arg=selector)
        await self.page.wait_for_timeout(1200)
        await self.page.mouse.move(0, 0)
        await self.page.evaluate("""() => { let a = document.activeElement;
            while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
            if (a && a.blur) a.blur(); }""")
        await self.page.wait_for_timeout(300)

    async def height(self, height: int) -> None:
        await self.page.set_viewport_size({"width": 1280, "height": height})
        await self.page.wait_for_timeout(500)

    async def shot(self, name: str, dialog: bool = True) -> None:
        if not dialog:  # whole page down to its lowest card
            bottom = await self.page.evaluate(
                "() => Math.max(...__shown(__deep('ha-card')).map((c) => c.getBoundingClientRect().bottom))")
            clip = {"x": 0, "y": 0, "width": 1280,
                    "height": min(bottom + 24, self.page.viewport_size["height"])}
        else:
            box = await self.page.evaluate("() => { const r = __dialog().getBoundingClientRect();"
                                           " return {x: r.x, y: r.y, width: r.width, height: r.height}; }")
            margin = 0  # the backdrop around the dialog adds nothing
            clip = {"x": box["x"] - margin, "y": box["y"] - margin,
                    "width": box["width"] + 2 * margin, "height": box["height"] + 2 * margin}
        optimise(await self.page.screenshot(clip=clip), self.out / name)
        print(f"wrote {self.out / name}")

    async def click_text(self, text: str) -> None:
        await self.page.get_by_text(text, exact=True).click()

    async def close_dialog(self) -> None:
        await self.page.evaluate(f"""() => {{ const d = __dialog().getBoundingClientRect();
            __shown(__deep('ha-icon-button')).find((b) => {{ const r = b.getBoundingClientRect();
              return (b.path || '').startsWith('{MDI_CLOSE}') && r.x >= d.x && r.y >= d.y && r.right <= d.right; }}).click(); }}""")
        await self.page.wait_for_timeout(800)

    async def set_language(self) -> None:
        await self.ws({"type": "frontend/set_user_data", "key": "language", "value": {
            "language": self.lang, "number_format": "language", "time_format": "language",
            "date_format": "language", "time_zone": "server", "first_weekday": "language"}})
        await self.page.evaluate("(l) => localStorage.setItem('selectedLanguage', JSON.stringify(l))", self.lang)

    async def set_core_language(self) -> None:
        """Entity and device names come from the server language; a reload applies it."""
        await self.ws({"type": "config/core/update", "language": self.lang})
        for entry in await self.ws({"type": "config_entries/get", "domain": DOMAIN}):
            await self.page.evaluate("(id) => __hass().callApi('POST', `config/config_entries/entry/${id}/reload`)",
                                     entry["entry_id"])
        await self.page.wait_for_timeout(5000)

    async def setup_start(self) -> None:
        await self.goto(f"/config/integrations/dashboard/add?domain={DOMAIN}")
        # "Do you want to set up ...?" confirmation: its last button confirms.
        await self.page.wait_for_function("() => __shown(__deep('dialog-box ha-button')).length > 0")
        await self.page.evaluate("() => __shown(__deep('dialog-box ha-button')).pop().click()")
        await self.settle("step-flow-menu")
        await self.shot("setup-start.png")
        await self.close_dialog()

    async def options(self) -> None:
        await self.goto(f"/config/integrations/integration/{DOMAIN}")
        await self.page.evaluate(f"() => __iconButton('{MDI_COG}', __deep('ha-config-entry-row')[0]).click()")
        await self.settle("step-flow-menu")
        await self.shot("options-menu.png")
        await self.click_text(self.menu["setup"]["features"])
        await self.page.wait_for_timeout(500)
        await self.click_text(self.menu["features"]["planning_features"])
        await self.settle("step-flow-menu")
        await self.shot("planning-features.png")
        await self.height(1700)  # the reserve form is long; show it unscrolled
        await self.click_text(self.menu["planning_features"]["reserve"])
        await self.settle("step-flow-form ha-form")
        await self.page.evaluate("""(values) => { const form = __shown(__deep('step-flow-form ha-form'))[0];
            form.dispatchEvent(new CustomEvent('value-changed', {detail: {value: {...form.data, ...values}}}));
        }""", RESERVE)
        await self.settle("step-flow-form ha-form")
        await self.shot("reserve.png")
        await self.close_dialog()
        await self.height(900)

    async def device(self) -> None:
        await self.goto("/config/devices/dashboard")
        devices = await self.ws({"type": "config/device_registry/list"})
        device = next(d for d in devices if any(i[0] == DOMAIN for i in d["identifiers"]))
        await self.height(1500)
        await self.goto(f"/config/devices/device/{device['id']}")
        await self.settle("ha-device-entities-card")
        await self.shot("device.png", dialog=False)
        # More-info of the target with its attributes ("Details" view).
        await self.page.evaluate("""(id) => document.querySelector('home-assistant').dispatchEvent(
            new CustomEvent('hass-more-info', {detail: {entityId: id}, bubbles: true, composed: true}))""", TARGET)
        await self.height(1900)  # all attributes without scrolling
        await self.settle()
        await self.page.evaluate(f"() => __iconButton('{MDI_DOTS}', __deep('ha-more-info-dialog')[0]).click()")
        await self.page.wait_for_timeout(600)
        await self.page.evaluate("""() => { const label = __hass().localize('ui.dialogs.more_info_control.details');
            __shown(__deep('ha-dropdown-item')).find((i) => i.textContent.trim() === label).click(); }""")
        await self.settle()
        await self.shot("target-soc.png")
        await self.page.keyboard.press("Escape")
        await self.height(900)

    async def dashboard(self, view_file: Path, battery_soc: str) -> None:
        text = view_file.read_text("utf-8").replace("sensor.battery_soc", battery_soc)
        # The examples carry the ids of an English or a German Home Assistant;
        # the lab has whichever its language was at setup. Map by translation key.
        registry = await self.ws({"type": "config/entity_registry/list"})
        actual = {(entry["entity_id"].split(".")[0], entry["translation_key"]): entry["entity_id"]
                  for entry in registry
                  if entry.get("platform") == DOMAIN and entry.get("translation_key")}
        for example_id, key in example_entity_ids().items():
            if key in actual:
                text = re.sub(rf"\b{re.escape(example_id)}\b", actual[key], text)
        view = yaml.safe_load(text)
        dashboards = await self.ws({"type": "lovelace/dashboards/list"})
        existing = next((d for d in dashboards if d["url_path"] == DASHBOARD), None)
        meta = {"title": view["title"], "icon": view.get("icon"), "show_in_sidebar": True, "require_admin": False}
        if existing:
            await self.ws({"type": "lovelace/dashboards/update", "dashboard_id": existing["id"], **meta})
        else:
            await self.ws({"type": "lovelace/dashboards/create", "url_path": DASHBOARD, "mode": "storage", **meta})
        await self.ws({"type": "lovelace/config/save", "url_path": DASHBOARD,
                       "config": {"title": view["title"], "views": [view]}})
        await self.height(1100)
        await self.goto(f"/{DASHBOARD}/{view['path']}")
        await self.settle("hui-history-graph-card")
        await self.page.wait_for_timeout(2500)  # history fetch and chart drawing
        await self.shot("dashboard.png", dialog=False)


def slug(text: str) -> str:
    """Home Assistant's entity id slug for the names this integration uses."""
    text = text.lower()
    for umlaut, plain in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        text = text.replace(umlaut, plain)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def example_entity_ids() -> dict[str, tuple[str, str]]:
    """Every default entity id, English and German, by (platform, translation key)."""
    ids: dict[str, tuple[str, str]] = {}
    for lang in ("en", "de"):
        path = REPO / "custom_components" / DOMAIN / "translations" / f"{lang}.json"
        for platform, keys in json.loads(path.read_text("utf-8"))["entity"].items():
            for key, entry in keys.items():
                ids[f"{platform}.{DOMAIN}_{slug(entry['name'])}"] = (platform, key)
    return ids


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="Home Assistant base URL, e.g. http://127.0.0.1:8124")
    parser.add_argument("--token-file", type=Path, help="file with a long-lived access token (or set HA_TOKEN)")
    parser.add_argument("--lang", nargs="+", default=["en", "de"], choices=sorted(LOCALES))
    parser.add_argument("--battery-soc", default="sensor.icn_lab_battery_soc",
                        help="replaces sensor.battery_soc in the dashboard example")
    parser.add_argument("--out", type=Path, default=REPO / "docs/images/screenshots")
    args = parser.parse_args()
    token = args.token_file.read_text("utf-8").strip() if args.token_file else os.environ["HA_TOKEN"]
    base = args.url.rstrip("/")
    tokens = {"access_token": token, "token_type": "Bearer", "expires_in": 1800, "refresh_token": "",
              "hassUrl": base, "clientId": base + "/", "expires": 4102444800000}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        for lang in args.lang:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900}, device_scale_factor=1.5, color_scheme="light",
                locale=LOCALES[lang], timezone_id="Europe/Berlin")
            await context.add_init_script(
                f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});"
                "localStorage.setItem('dockedSidebar', '\"always_hidden\"');"
                f"localStorage.setItem('selectedLanguage', '\"{lang}\"');" + HELPERS)
            page = await context.new_page()
            shooter = Shooter(page, base, lang, args.out / lang)
            await shooter.goto("/config/integrations/dashboard")
            if lang == args.lang[0]:
                original_core_language = (await shooter.ws({"type": "get_config"}))["language"]
            finished = False
            try:
                await shooter.set_language()
                await shooter.set_core_language()
                await shooter.setup_start()
                await shooter.options()
                await shooter.device()
                example = REPO / ("docs/examples/dashboard.yaml" if lang == "en" else f"docs/examples/dashboard.{lang}.yaml")
                await shooter.dashboard(example, args.battery_soc)
                finished = True
            finally:
                try:
                    # Leave the instance as it was found: after the last language,
                    # and after a step that failed half-way through any of them
                    if lang == args.lang[-1] or not finished:
                        shooter.lang = args.lang[0]
                        await shooter.set_language()
                        shooter.lang = original_core_language
                        await shooter.set_core_language()
                finally:
                    await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
