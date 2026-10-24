# Development

[🇩🇪 Deutsch](../de/development.md) · [Documentation overview](README.md)

## Checks

The same commands run in CI (`.github/workflows/ci.yml`) against the oldest supported Home
Assistant (2025.2, Python 3.13) and the newest release (Python 3.14), together with `hassfest`
and the HACS action. All of them have to pass.

```bash
pip install -r requirements-dev.txt
pytest                                        # full suite with the coverage ratchet
mypy custom_components/inverter_charge_night  # strict
pyright                                       # strict
python scripts/smoke_real_ha.py               # end to end against a real HA core
```

- **Coverage is a ratchet.** `.coveragerc` covers the whole package, and `fail_under` is the
  last measured total, rounded down. Whoever adds tests raises it; it is never lowered.
- **Strict test hass.** The `mock_hass` fixture returns `None` for every entity a test has not
  registered. Register what the code reads, and assert positively on service calls.
- **Typing.** `mypy --strict` and `pyright` strict over the whole package, without exclusions.

`AGENTS.md` describes the conventions. `plans/` records what was found, decided and rejected,
including the bugs that shaped the safety rules. Read it before changing anything that writes to
an inverter.

## The lab

[`lab/`](../../lab/README.md) holds a complete local Home Assistant with a physical battery model,
simulated PV, house and car loads, and an optional local Kostal REST/Modbus emulator together with
KOSTAL KORE and evcc. The checks run real config flows, real entities and real services:

```bash
python lab/check_real_ha.py   # config flow, entities, services in a real HA
python lab/check_kostal.py    # with KOSTAL KORE against the emulated inverter
```

The CI workflow `ha-lab.yml` (`acceptance`) runs the whole lab on every pull request that
touches `lab/`, the integration or the workflow.
`lab/docs_screenshots.py` regenerates the screenshots in this documentation.

## Architecture

| File | Role |
|---|---|
| `__init__.py` | Setup, update and unload of the config entry. |
| `coordinator.py` | The control loop: window, planner, writes, verification, restore. Also holds `InverterChargeNightConfigEntry`. |
| `planner.py` | Bridge planner and charge power, as pure functions without Home Assistant imports. |
| `calculation.py` | Headroom formula and forecast parsing. |
| `heating.py` | Heating-degree model and temperature diary for the weather factor. |
| `prices.py` | Reads any hourly price entity. |
| `pv_yield.py` | Daily PV shortfall detection. |
| `communication.py` | Communication incident history. |
| `config_flow.py`, `setup_drafts.py` | Guided setup, options and reconfigure flows with persistent drafts. |
| `services.py`, `services.yaml` | The actions. |
| `sensor.py`, `binary_sensor.py`, `switch.py`, `select.py`, `number.py`, `entity.py` | Entities on one shared device. |
| `diagnostics.py` | Diagnostics with redacted entity ids. |
| `strings.json`, `translations/` | English (`en.json` is identical to `strings.json`) and German. |
| `icons.json`, `brand/` | Entity icons and the integration's icon and logo. |
| `quality_scale.yaml` | Status against Home Assistant's quality scale rules. |

Every Home Assistant state read happens in the coordinator. The planner, the heating model, the
price reader and the communication history are plain Python and tested without Home Assistant.

## Quality scale

Home Assistant's [integration quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
grades integrations that ship with Home Assistant Core. A custom integration is always reported
as *custom*. As a yardstick, ICN is measured against all rules in
`custom_components/inverter_charge_night/quality_scale.yaml`: 43 are implemented and 11 do not
apply to an integration that talks to other integrations' entities rather than to a device. None
is left open.

## Translations

Every user-facing text lives in `strings.json` and `translations/`. To add or change one, edit
`strings.json`, copy it to `translations/en.json` (the files must stay identical) and add the
German text to `translations/de.json`. A test checks that both languages have the same keys.
Documentation pages live in `docs/en/` and `docs/de/` and are kept in step.

## Configuration keys

All options are stored in the config entry's `data`. `options` only holds the learned efficiency
history and the persisted runtime state. Labels and help texts for every key are in `strings.json`;
defaults are in `const.py`. The [configuration reference](configuration.md) lists them by page.

## Releases

1. Raise `version` in `manifest.json` and add a section to `CHANGELOG.md`.
2. Merge the pull request into `main`.
3. Create a GitHub release `vX.Y.Z` from `main`. HACS offers the update from the release.
