# Plan 003: Verifikations-Baseline: Abhängigkeiten, CI, Coverage-Ratchet für den Coordinator, strenger Test-Hass

> **Anweisung an den Ausführenden**: Schritt für Schritt vorgehen, jeden Prüfbefehl ausführen.
> Bei einer STOP-Bedingung anhalten und berichten. Am Ende die Statuszeile in `plans/README.md` aktualisieren.
>
> **Drift-Prüfung (zuerst ausführen)**:
> `git diff --stat 549a5ee..HEAD -- .coveragerc mypy.ini pyrightconfig.json pytest.ini tests/conftest.py AGENTS.md`
> Bei Abweichungen gegen "Ist-Zustand" STOP.

## Status

- **Priorität**: P1
- **Aufwand**: M
- **Risiko**: MED (die erste ehrliche Messung wird rot sein; das ist der Zweck)
- **Hängt ab von**: keinem
- **Kategorie**: tests + dx
- **Geplant bei**: Commit `549a5ee`, 2026-09-11

## Warum das wichtig ist

Alle Qualitätsaussagen des Repos (100 % Coverage, mypy/pyright strict) gelten nur für die kleinen
Plattformdateien: `__init__.py` (1 956 Zeilen, 79 % des Codes, die gesamte Steuerlogik) und `config_flow.py`
sind aus Coverage **und** aus beiden Typprüfern ausgenommen. Diese Ausnahmen wurden im Merge `1ed4376`
eingeführt, als 1 521 Zeilen Coordinator-Tests verworfen wurden. Es gibt keine Abhängigkeitsdatei und kein CI,
also kann niemand die Gates reproduzieren. Außerdem liefert `mock_hass.states.get` für jede unbekannte
Entität ein `MagicMock`, sodass `float(state.state)` einen `TypeError` wirft, der im Produktcode verschluckt
wird, und der Test trotzdem besteht.

Pläne 004 und 005 bauen die Steuerlogik um. Ohne diesen Plan geschieht das blind.

## Ist-Zustand

- `.coveragerc`:
  ```
  [run]
  omit =
      custom_components/inverter_charge_night/__init__.py
      custom_components/inverter_charge_night/config_flow.py
  [report]
  fail_under = 100
  ```
- `mypy.ini` Zeile 7: `exclude = custom_components/inverter_charge_night/__init__.py|custom_components/inverter_charge_night/config_flow.py`
- `pyrightconfig.json` Zeilen 4–7: dieselben zwei Dateien unter `exclude`.
- `pytest.ini`: `addopts = --cov=custom_components/inverter_charge_night --cov-report=term-missing`; kein `asyncio_mode`.
- `tests/conftest.py:53-62`:
  ```python
  @pytest.fixture
  def mock_hass() -> HomeAssistant:
      hass = MagicMock(spec=HomeAssistant)
      hass.data = {}
      hass.states = MagicMock()
      hass.services = MagicMock()
      hass.async_create_task = MagicMock(side_effect=lambda coro, **kwargs: coro.close())
      hass.config_entries = MagicMock()
      return hass
  ```
  Davor (Zeilen 30–42) eine Autouse-Fixture, die `frame.report_usage` neutralisiert.
- Kein Verzeichnis `.github/`, keine `requirements*.txt`, kein `pyproject.toml`.
- `AGENTS.md` nennt die Abhängigkeiten als Prosa: `homeassistant`, `pytest`, `pytest-cov`, `pytest-asyncio`, `mypy`, `pyright`.
- `hacs.json` setzt die Mindest-HA-Version `2024.4.0`.
- Bekannt aus der Arbeit an PR #2: mit HA 2026.x muss mypy mit `--python-version 3.13` laufen, weil HA 3.13-Syntax nutzt; `mypy.ini` pinnt `python_version = 3.12`.

## Benötigte Befehle

| Zweck | Befehl | Erwartung |
|---|---|---|
| Installation | `pip install -r requirements-dev.txt` | Exit 0 |
| Tests | `pytest` | siehe Ratchet unten |
| Typen | `mypy custom_components/inverter_charge_night/` | `Success` |
| Typen | `pyright` | `0 errors` |

## Umfang

**Im Umfang**:
- `requirements-dev.txt` (neu), `.github/workflows/ci.yml` (neu)
- `.coveragerc`, `pytest.ini`, `mypy.ini`, `pyrightconfig.json`
- `tests/conftest.py` und **neue** Testdateien `tests/test_window_lifecycle.py`, `tests/test_setup_real_coordinator.py`
- `AGENTS.md` (Abschnitt "Running tests")

**Nicht im Umfang**:
- Produktcode in `custom_components/` – hier werden nur Tests und Gates geändert. Zeigt ein neuer Test einen
  echten Fehler, wird er als `xfail(strict=True)` mit Verweis auf den Plan (004/005) markiert, nicht behoben.

## Git-Vorgehen

- Branch: `chore/003-verification-baseline` von `develop`
- Commits: `chore: …`, `test: …`, `ci: …`

## Schritte

### Schritt 1: Abhängigkeiten festschreiben

`requirements-dev.txt`:
```
homeassistant>=2024.4.0
pytest>=8
pytest-cov>=5
pytest-asyncio>=0.23
mypy>=1.10
pyright>=1.1.380
pyyaml
```
`pytest.ini` ergänzen: `asyncio_mode = auto` ist **nicht** gewünscht (Tests markieren explizit); stattdessen
`asyncio_default_fixture_loop_scope = function`, damit pytest-asyncio keine Warnung ausgibt.

**Prüfen**: frisches Venv, `pip install -r requirements-dev.txt && pytest -q` → gleiche Zahl bestandener Tests wie vor dem Plan (172 bei `549a5ee`).

### Schritt 2: mypy/pyright-Ausnahmen entfernen, Python-Version anheben

- `mypy.ini`: `exclude`-Zeile löschen, `python_version = 3.13` (HA-Versionen ab 2025.2 brauchen es; für 3.12
  bleibt der Code kompatibel, nur die Prüfung läuft als 3.13).
- `pyrightconfig.json`: `exclude: []`, `pythonVersion: "3.13"`.
- Beide Prüfer ausführen und die Fehlerzahl notieren. Jeden Fehler entweder mit einer gezielten
  `# type: ignore[code]`-Anmerkung plus Kommentar versehen (nur wo HA-Typen fehlen) oder in einer Liste in
  der PR-Beschreibung festhalten. Ziel dieses Plans: 0 Fehler, ohne Produktlogik zu ändern; wenn das
  mehr als 20 Anmerkungen braucht, STOP und die Liste berichten.

**Prüfen**: `mypy custom_components/inverter_charge_night/` → `Success`; `pyright` → `0 errors`.

### Schritt 3: Coverage-Ratchet

- `.coveragerc`: `omit`-Block entfernen. `pytest` ausführen, die echte Gesamtabdeckung ablesen (Erwartung:
  deutlich unter 100 %).
- `fail_under` auf den gemessenen Wert (abgerundet) setzen und in `AGENTS.md` dokumentieren:
  "Coverage-Gate ist ein Ratchet: der Wert darf nur steigen. Wer Tests ergänzt, hebt `fail_under` an."

**Prüfen**: `pytest -q` → grün mit dem neuen Gate; `grep fail_under .coveragerc` zeigt den gemessenen Wert.

### Schritt 4: Strenger Test-Hass

In `tests/conftest.py` die Fixture so ändern, dass unbekannte Entitäten `None` liefern und Zustände explizit
registriert werden:
```python
@pytest.fixture
def mock_hass() -> HomeAssistant:
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {}
    states: dict[str, Any] = {}
    hass.states = MagicMock()
    hass.states.get = MagicMock(side_effect=states.get)
    hass.states.async_set = MagicMock(side_effect=lambda eid, st, attributes=None: states.__setitem__(eid, create_mock_state(eid, st, attributes)))
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro, **kwargs: coro.close())
    hass.config_entries = MagicMock()
    return hass
```
Danach `pytest -q` ausführen. Tests, die bisher stillschweigend vom `MagicMock`-Zustand lebten, schlagen jetzt
fehl. Jeden dieser Tests so ergänzen, dass er die benötigten Zustände mit `mock_hass.states.async_set(...)`
setzt (oder wie bisher `mock_hass.states.get.side_effect = ...` überschreibt) **und** eine positive
Behauptung enthält (`assert_awaited_with` auf den Service-Aufruf), nicht nur "wurde nicht aufgerufen".

**Prüfen**: `pytest -q` → grün; `grep -c "assert not mock_hass.services.async_call.called" tests/*.py` sinkt gegenüber dem Ausgangswert.

### Schritt 5: Charakterisierungstests für den Fensterlebenszyklus

Neue Datei `tests/test_window_lifecycle.py` mit echtem Coordinator (Muster: `_make_coordinator` in
`tests/test_time_triggers.py`), ohne die Kollaborateure zu mocken, außer `hass.services.async_call`:

1. **Fensterstart Nachtladung**: Zustände `number.min_soc = 8`, `switch.grid = off`, `sensor.soc = 40`,
   `sensor.pv = 5` (kWh). `await coordinator._on_window_start(now)` → `is_active is True`,
   `initial_calculated_soc == calculate_required_soc(5, cap, margin, min, max)`, Listener gesetzt
   (`_battery_soc_listener is not None`), Verifikationstask erzeugt.
2. **Update im Fenster**: `await coordinator._async_update_data()` → `number.set_value` mit dem Ziel und
   `switch.turn_on` wurden aufgerufen (Reihenfolge prüfen über `mock_hass.services.async_call.await_args_list`).
3. **Ziel erreicht**: Batterie auf Ziel setzen, Update → `switch.turn_off`, `target_reached is True`.
4. **Fensterende**: `await coordinator._on_window_end(now)` → `number.set_value` mit `original_min_soc`,
   `is_active is False`, alle Listener `None`.
5. **Neustart im Fenster**: neuer Coordinator, `number.min_soc = 65` (unser altes Ziel), `_on_window_start`
   → `initial_calculated_soc == 65`. Dieser Test dokumentiert das heutige Verhalten; Plan 005 ändert es und
   passt den Test an.
6. **Override nach oben** (bekannter Fehler F2): Override 70 bei Ziel 45 und Batterie 50 → erwartet: Netzladung
   bleibt an. Als `@pytest.mark.xfail(strict=True, reason="plans/004")` markieren.

Neue Datei `tests/test_setup_real_coordinator.py`: `async_setup_entry` mit echtem Coordinator gegen den
strengen Hass, `hass.config_entries.async_forward_entry_setups = AsyncMock()`; prüft, dass `setup_time_triggers`
zwei Trigger registriert hat und der Backup-Listener gesetzt ist, wenn `backup_mode_entity` konfiguriert ist.

**Prüfen**: `pytest tests/test_window_lifecycle.py tests/test_setup_real_coordinator.py -q` → alle grün außer dem `xfail`; Gesamtabdeckung gestiegen, `fail_under` entsprechend anheben.

### Schritt 6: CI

`.github/workflows/ci.yml`:
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - run: pip install -r requirements-dev.txt
      - run: pytest
      - run: mypy custom_components/inverter_charge_night/
      - run: pyright
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```
Hinweis: `hassfest` prüft `manifest.json` streng; bekannte Kandidaten für Beanstandung sind fehlende
`brands`-Einträge (für Custom Integrations tolerierbar) und das Feld `version`. Fehlermeldungen in der
PR-Beschreibung festhalten, nicht wegkonfigurieren.

**Prüfen**: Der Workflow ist YAML-gültig (`python3 -c "import yaml;yaml.safe_load(open('.github/workflows/ci.yml'))"`). Der erste Lauf auf GitHub darf rot sein; berichten, welche Jobs.

### Schritt 7: `AGENTS.md`

Abschnitt "Running tests" auf `pip install -r requirements-dev.txt`, den Ratchet und die mypy-Python-Version
aktualisieren; die Aussage "excluding `__init__.py` and `config_flow.py`" entfernen.

## Fertig-Kriterien

- [ ] `pip install -r requirements-dev.txt && pytest && mypy custom_components/inverter_charge_night/ && pyright` läuft in einem frischen Venv durch
- [ ] `.coveragerc` enthält kein `omit`
- [ ] `mypy.ini` und `pyrightconfig.json` enthalten keine Ausnahmen für Produktdateien
- [ ] `tests/test_window_lifecycle.py` existiert und enthält mindestens die sechs beschriebenen Tests
- [ ] `.github/workflows/ci.yml` existiert
- [ ] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- Drift an den genannten Stellen.
- Schritt 2 braucht mehr als 20 Typ-Anmerkungen oder Änderungen an Produktlogik.
- Schritt 4 lässt mehr als 15 bestehende Tests fehlschlagen, deren Ursache nicht der Mock ist (dann Liste berichten).
- Ein Charakterisierungstest deckt einen Fehler auf, der nicht in `plans/README.md` Abschnitt 2.2 steht: als
  `xfail` markieren und den Befund berichten, nicht beheben.

## Wartungshinweise

- Der Ratchet-Wert in `.coveragerc` wird von Plan 004 und 005 jeweils angehoben.
- Die `xfail(strict=True)`-Tests werden in 004/005 auf normale Tests zurückgesetzt; ein unerwartet
  bestehender `xfail` bricht die Suite absichtlich, damit das nicht vergessen wird.
- Spätere Option: `pytest-homeassistant-custom-component` für einen echten `hass`; das ersetzt Schritt 4
  vollständig, ist aber eine größere Umstellung aller Fixtures.
