# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is a **Home Assistant custom integration** (`custom_components/inverter_charge_night/`) written in Python 3.12. It calculates and sets optimal battery SOC for overnight grid charging based on PV forecast data. There is no standalone application server — it runs as a plugin inside Home Assistant.

### Running tests

```bash
pip install -r requirements-dev.txt   # Home Assistant, pytest, pytest-cov, pytest-asyncio, mypy, pyright, pyyaml
pytest -v            # full suite, includes coverage of the whole package (see ratchet below)
pytest --no-cov      # skip coverage check for faster iteration
```

`pytest-asyncio` is required — many tests are async and use `@pytest.mark.asyncio`
(tests mark themselves explicitly; `asyncio_mode = auto` is deliberately not set).

**Coverage gate is a ratchet.** `.coveragerc` covers the whole package (no `omit`) and its
`fail_under` is the last measured total, rounded down. The value may only go up: whoever adds
tests raises `fail_under` to the new measurement in the same change. Never lower it.

**Strict test hass.** The `mock_hass` fixture in `tests/conftest.py` returns `None` from
`hass.states.get` for every entity a test has not registered with
`mock_hass.states.async_set(entity_id, state, attributes)`. Register every entity the code under
test reads; do not rely on `MagicMock` states. Assert positively on `hass.services.async_call`
(`assert_awaited_with`, `await_args_list`), not only "was not called".

The same commands run in CI (`.github/workflows/ci.yml`) against the oldest supported Home
Assistant (the floor from `hacs.json`, on Python 3.13) and the newest release (on Python 3.14),
together with `hassfest` and the HACS action.

### Type checking

```bash
mypy custom_components/inverter_charge_night/
pyright
```

Both `mypy` and `pyright` are configured in strict mode and must pass with **0 errors**. The config
files set `python_version` / `pythonVersion` 3.13, which suits a 3.13 environment; the integration
itself stays compatible with 3.12. Both checkers also parse Home Assistant's own sources, so the
setting has to match the interpreter in use: Home Assistant 2026.9 on Python 3.14 uses 3.14-only
syntax that a checker pinned to 3.13 rejects before it reaches this package. CI therefore passes
`mypy --python-version <matrix version>` and `pyright --pythonversion <matrix version>`; do the same
when checking against a 3.14 environment. Nothing is excluded from either checker: both
cover the whole package, `config_flow.py` included (plan 001 landed its rewrite). The
`reportIncompatibleVariableOverride` rule is disabled in pyright because HA's `CoordinatorEntity`
and entity base classes have a framework-level conflict on the `available` property.

### Key gotchas

- The `~/.local/bin` directory must be on `PATH` for `pytest`, `mypy`, and `pyright` to be found (they are pip-installed with `--user`).
- Dependencies are pinned as minimum versions in `requirements-dev.txt`; there is no `pyproject.toml`.
- The `.coveragerc` `fail_under` is a ratchet over the whole package (see "Running tests").
- Entity platform files use `CoordinatorEntity[InverterChargeNightCoordinator]` generic to properly type `self.coordinator`. The coordinator and the `InverterChargeNightConfigEntry` alias live in `coordinator.py`; `__init__.py` only holds setup, update and unload. Import them from `.coordinator`, not from the package root.
- This is not a runnable standalone application. To test end-to-end beyond unit tests, you would need a full Home Assistant instance with Kostal and Solcast integrations — not feasible in this environment. Unit tests with full mocking are the primary validation method.
