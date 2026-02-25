# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is a **Home Assistant custom integration** (`custom_components/inverter_charge_night/`) written in Python 3.12. It calculates and sets optimal battery SOC for overnight grid charging based on PV forecast data. There is no standalone application server — it runs as a plugin inside Home Assistant.

### Running tests

```bash
pytest -v            # all 113 tests, includes coverage (100% required, excluding __init__.py and config_flow.py)
pytest --no-cov      # skip coverage check for faster iteration
```

`pytest-asyncio` is required — many tests are async and use `@pytest.mark.asyncio`.

### Type checking

```bash
mypy custom_components/inverter_charge_night/
pyright
```

Both `mypy` and `pyright` are configured in strict mode and must pass with **0 errors**. The `__init__.py` and `config_flow.py` files are excluded from type checking (see `mypy.ini` / `pyrightconfig.json`). The `reportIncompatibleVariableOverride` rule is disabled in pyright because HA's `CoordinatorEntity` and entity base classes have a framework-level conflict on the `available` property.

### Key gotchas

- The `~/.local/bin` directory must be on `PATH` for `pytest`, `mypy`, and `pyright` to be found (they are pip-installed with `--user`).
- There is no `requirements.txt` or `pyproject.toml`. Dependencies are: `homeassistant`, `pytest`, `pytest-cov`, `pytest-asyncio`, `mypy`, `pyright`.
- The `.coveragerc` sets `fail_under = 100` but omits `__init__.py` and `config_flow.py`.
- Entity platform files use `CoordinatorEntity[InverterChargeNightCoordinator]` generic to properly type `self.coordinator`. Importing the coordinator from `.__init__` is safe (no circular imports).
- This is not a runnable standalone application. To test end-to-end beyond unit tests, you would need a full Home Assistant instance with Kostal and Solcast integrations — not feasible in this environment. Unit tests with full mocking are the primary validation method.
