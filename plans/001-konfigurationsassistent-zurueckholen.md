# Plan 001: Konfigurationsassistent, Reconfigure-Flow und Übersetzungen aus der 1.0.3-Linie zurückholen

> **Anweisung an den Ausführenden**: Diesen Plan Schritt für Schritt abarbeiten. Jeden
> Prüfbefehl ausführen und das erwartete Ergebnis bestätigen, bevor der nächste Schritt
> beginnt. Tritt eine der STOP-Bedingungen ein: anhalten und berichten, nicht improvisieren.
> Am Ende die Statuszeile dieses Plans in `plans/README.md` aktualisieren.
>
> **Drift-Prüfung (zuerst ausführen)**:
> `git diff --stat 549a5ee..HEAD -- custom_components/inverter_charge_night/config_flow.py custom_components/inverter_charge_night/strings.json custom_components/inverter_charge_night/translations/en.json custom_components/inverter_charge_night/util.py tests/test_config_flow.py`
> Wenn sich eine dieser Dateien seit dem Planungsstand geändert hat, die Auszüge unter
> "Ist-Zustand" mit dem echten Code vergleichen; bei Abweichung STOP.

## Status

- **Priorität**: P1
- **Aufwand**: M
- **Risiko**: MED
- **Hängt ab von**: keinem
- **Kategorie**: bug (Merge-Regression) + dx
- **Geplant bei**: Commit `549a5ee`, 2026-09-11

## Warum das wichtig ist

Der Merge-Commit `1ed4376` ("resolve merge conflicts with release-1.0.2 branch") hat 549 Zeilen aus
`config_flow.py` entfernt: den vierstufigen Assistenten (`user` → `time_soc` → `power` → `advanced`),
den Reconfigure-Flow, `async_set_unique_id` und die typisierten Selektoren (TimeSelector, NumberSelector
mit Einheit und Grenzen, BooleanSelector, EntitySelector mit Domänenfilter). Die zugehörigen Übersetzungen
in `translations/en.json` wurden aber behalten. Ergebnis heute: ein einziges Formular mit 28 Feldern,
von denen 22 ohne Beschriftung gerendert werden, weil `en.json` sie nur in Schritten kennt, die der Code
nicht mehr hat. Das ist die vom Eigentümer beschriebene "hässliche, schlecht beschriftete" Einrichtung.

Nach diesem Plan gibt es wieder einen vierstufigen Assistenten mit Selektoren, einen Reconfigure-Flow,
eine eindeutige Config-Entry-ID, und `strings.json` sowie `translations/en.json` sind deckungsgleich.

## Ist-Zustand

- `custom_components/inverter_charge_night/config_flow.py` (aktuell 475 Zeilen):
  - `async_step_user` (Zeile 201) zeigt ein Formular mit 28 Feldern (Zeilen 225–303), `step_id="user"`.
  - `OptionsFlowHandler.async_step_init` (Zeile 325) wiederholt dieselben 28 Felder (Zeilen 353–472) und
    schreibt das Ergebnis mit `async_update_entry(..., data=user_input)` in `entry.data` (Zeilen 343–349).
  - Zeitfelder sind reine Strings: `vol.Required(CONF_START_TIME, default=DEFAULT_START_TIME): str` (Zeile 248).
  - Zahlenfelder sind `vol.Coerce(float)` ohne Einheit/Grenzen (Zeilen 247, 250–257).
  - `_validate_user_input(user_input, hass)` (Zeile 131) ist die gemeinsame Validierung beider Flows und
    bleibt erhalten.
  - Kein `async_set_unique_id`, kein `async_step_reconfigure`.
- `custom_components/inverter_charge_night/util.py`:
  ```python
  def parse_time_str(time_str: Any) -> tuple[int, int] | None:
      try:
          hour, minute = map(int, time_str.split(":"))
      except (ValueError, AttributeError):
          return None
  ```
  Ein `TimeSelector` liefert `"HH:MM:SS"`; drei Teile lassen diesen Parser scheitern.
- `custom_components/inverter_charge_night/translations/en.json` beschreibt für `config.step` die Schritte
  `user`, `time_soc`, `power`, `advanced`, `reconfigure`, `reconfigure_time_soc`, `reconfigure_power`,
  `reconfigure_advanced` und für `options.step` `init`, `time_soc`, `power`, `advanced`. Der Schritt `power`
  enthält drei Keys, die es im Code nicht gibt: `grid_import_energy_entity`, `battery_charge_energy_entity`,
  `home_consumption_energy_entity`. Fünf existierende Keys fehlen in `en.json` vollständig: `operation_mode`,
  `charge_power_sent_entity`, `charge_power_received_entity`, `pv_forecast_today_entity`, `force_discharge_switch`.
- `custom_components/inverter_charge_night/strings.json` ist mit dem Code synchron (28 Felder je Schritt),
  enthält aber ebenfalls die Einzelschrittstruktur (`config.step.user`, ein totes `config.step.init`,
  `options.step.init`).
- **Referenzimplementierung**: `git show 5c31112:custom_components/inverter_charge_night/config_flow.py`
  (710 Zeilen). Dort:
  - Zeilen 115–146: Hilfsfunktionen `_entity_selector(domain)`, `_time_selector()`, `_number_selector(...)`,
    `_bool_selector()`.
  - Zeilen 367–474: `async_step_user`, `async_step_time_soc`, `async_step_power`, `async_step_advanced`
    (dort wird in `advanced` mit `await self.async_set_unique_id(unique_id)` abgeschlossen).
  - Zeilen 476–580: die vier `reconfigure*`-Schritte.
  - Zeilen 605–706: Optionsflow mit denselben vier Schritten.
  - **Achtung**: Diese Version kennt die fünf 2.0-Keys nicht und enthält dafür die drei Energiezähler-Keys.
    Der Assistent wird also portiert, nicht kopiert.
- Konventionen: Config-Keys kommen aus `const.py`; Fehler-Keys (`invalid_time`, `start_end_time_must_differ`
  usw.) müssen in `strings.json` unter `config.error` und `options.error` stehen; Tests für den Flow
  liegen in `tests/test_config_flow.py` und patchen `er.async_get` per `pytest.MonkeyPatch` (Muster: Zeilen 61–96).

## Benötigte Befehle

| Zweck | Befehl | Erwartung |
|---|---|---|
| Tests | `pytest` | alle bestehen, Coverage 100 % der gemessenen Dateien |
| Typen | `mypy custom_components/inverter_charge_night/` | `Success: no issues found` |
| Typen | `pyright` | `0 errors` |
| JSON gültig | `python3 -c "import json;json.load(open('custom_components/inverter_charge_night/strings.json'));json.load(open('custom_components/inverter_charge_night/translations/en.json'))"` | keine Ausgabe |

Voraussetzung: `homeassistant`, `pytest`, `pytest-cov`, `pytest-asyncio`, `mypy`, `pyright` installiert
(siehe `AGENTS.md`). Bei HA ≥ 2025.2 mypy mit `--python-version 3.13` aufrufen.

## Umfang

**Im Umfang** (nur diese Dateien ändern):
- `custom_components/inverter_charge_night/config_flow.py`
- `custom_components/inverter_charge_night/util.py` (nur `parse_time_str`)
- `custom_components/inverter_charge_night/strings.json`
- `custom_components/inverter_charge_night/translations/en.json`
- `tests/test_config_flow.py`, `tests/test_util.py`

**Nicht im Umfang**:
- `__init__.py` (Coordinator) – Plan 002 und 004 ändern ihn; hier nichts anfassen, auch nicht `_parse_time`.
- Umbenennung der `kostal_*`-Keys – braucht eine Entry-Migration, Backlog 008.
- Energiezähler-Keys (`grid_import_energy_entity` usw.) – Plan 006 entscheidet, ob sie zurückkommen.
  In diesem Plan werden sie aus `en.json` entfernt.

## Git-Vorgehen

- Branch: `fix/001-config-flow-wizard` von `develop`
- Commits im Stil des Repos (`git log`): `fix: …`, `refactor: …`, `test: …`
- Nicht pushen und keinen PR öffnen, sofern nicht ausdrücklich beauftragt.

## Schritte

### Schritt 1: `parse_time_str` akzeptiert `HH:MM:SS`

In `util.py`:
```python
def parse_time_str(time_str: Any) -> tuple[int, int] | None:
    """Parse "HH:MM" or "HH:MM:SS" into (hour, minute), or None if invalid."""
    try:
        parts = [int(p) for p in time_str.split(":")]
    except (ValueError, AttributeError):
        return None
    if len(parts) not in (2, 3):
        return None
    hour, minute = parts[0], parts[1]
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None
```
In `tests/test_util.py` die Zeile `assert parse_time_str("12:00:00") is None` ersetzen durch
`assert parse_time_str("12:00:00") == (12, 0)` und `assert parse_time_str("1:2:3:4") is None` ergänzen.

**Prüfen**: `pytest tests/test_util.py -q` → alle bestehen.

### Schritt 2: Selektor-Helfer und ein Schema-Bauer pro Schritt

In `config_flow.py` (oberhalb der Flow-Klassen) vier Helfer nach dem Muster aus `5c31112` anlegen:

```python
def _entity_selector(domain: str | list[str] | None = None, device_class: str | None = None) -> Any:
    config = selector.EntitySelectorConfig()
    if domain:
        config["domain"] = domain
    if device_class:
        config["device_class"] = device_class
    return selector.EntitySelector(config)

def _number_selector(min_v: float, max_v: float, step: float, unit: str | None, mode: str = "box") -> Any:
    ...  # selector.NumberSelector(selector.NumberSelectorConfig(min=..., max=..., step=..., unit_of_measurement=unit, mode=...))
```

Dann vier Funktionen `_schema_entities(defaults)`, `_schema_time_soc(defaults)`, `_schema_power(defaults)`,
`_schema_advanced(defaults)`, jede gibt ein `vol.Schema` zurück und nimmt ein `Mapping[str, Any]` mit
Vorbelegungen. Feldzuordnung (die 28 heutigen Keys, keiner darf fehlen):

| Schritt | Felder |
|---|---|
| `user` | `name`, `operation_mode` (SelectSelector, translation_key `operation_mode`), `kostal_min_soc_entity` (number), `kostal_grid_charge_switch` (switch), `pv_forecast_entity` (sensor), `pv_forecast_today_entity` (sensor, optional), `battery_soc_entity` (sensor, device_class battery), `battery_capacity` (NumberSelector 0.5–200 kWh, Schritt 0.1) |
| `time_soc` | `start_time`, `end_time` (TimeSelector), `user_min_soc`, `user_max_soc`, `default_min_soc` (NumberSelector 0–100 %, Slider), `forecast_error_margin` (0–100 %) |
| `power` | `min_charge_power_w`, `max_charge_power_w` (NumberSelector 100–30000 W), `absolute_max_charge_power_w` (optional), `absolute_max_charge_power_entity` (number/input_number), `charge_power_entity` (number/input_number), `charge_power_sent_entity`, `charge_power_received_entity` (sensor, device_class power), `auto_efficient_charge` (BooleanSelector), `force_discharge_switch` (switch, optional) |
| `advanced` | `update_interval` (60–3600 s), `command_delay` (0–5 s, Schritt 0.1), `active_start_date`, `active_end_date` (DateSelector, optional), `backup_mode_entity` (binary_sensor/switch/sensor, optional) |

Die bisherigen Einzelschritt-Schemata (Zeilen 225–303 und 353–472) werden gelöscht.

**Prüfen**: `python3 -c "import ast,sys;ast.parse(open('custom_components/inverter_charge_night/config_flow.py').read())"` → keine Ausgabe.

### Schritt 3: Vierstufiger Config-Flow

`InverterChargeNightConfigFlow` bekommt `self._data: dict[str, Any] = {}` und die Schritte
`async_step_user` → `async_step_time_soc` → `async_step_power` → `async_step_advanced`. Jeder Schritt:
`if user_input is not None: self._data.update(user_input); return await self.async_step_<nächster>()`.
Im letzten Schritt:

```python
errors = _validate_user_input(self._data, self.hass)
if errors:
    return self.async_show_form(step_id="advanced", data_schema=_schema_advanced(self._data), errors=errors)
await self.async_set_unique_id(self._data[CONF_KOSTAL_MIN_SOC_ENTITY])
self._abort_if_unique_id_configured()
return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)
```

Fehler, die zu Feldern früherer Schritte gehören (z. B. `invalid_time`), werden im Schritt `advanced`
zusätzlich als `errors["base"]` gemeldet, damit sie sichtbar sind; dafür in `strings.json` unter
`config.error` den Key `fix_previous_step: "Please go back and correct the highlighted settings"` ergänzen.
Einfacher und vorzuziehen: `_validate_user_input` am Ende **jedes** Schritts nur auf die Keys des Schritts
anwenden (Hilfsfunktion `_errors_for(step_keys, errors)`), dann taucht jeder Fehler im richtigen Schritt auf.

**Prüfen**: `pytest tests/test_config_flow.py -q` → schlägt jetzt fehl (Tests noch alt); das ist erwartet
und wird in Schritt 6 behoben.

### Schritt 4: Reconfigure-Flow und Optionsflow

- `async_step_reconfigure(user_input)` startet mit `self._data = dict(self._get_reconfigure_entry().data)`
  und durchläuft dieselben vier Schritte mit `step_id="reconfigure"`, `"reconfigure_time_soc"`,
  `"reconfigure_power"`, `"reconfigure_advanced"`; Abschluss mit
  `return self.async_update_reload_and_abort(entry, data=self._data)` (ab HA 2024.4 verfügbar).
- `OptionsFlowHandler` behält seine Existenz (die Optionen enthalten `auto_efficiency_data`), bekommt aber
  dieselben vier Schritte (`init`, `time_soc`, `power`, `advanced`) und schreibt weiterhin über
  `async_update_entry(entry, data=self._data)` in `entry.data`, damit das Verhalten für bestehende
  Installationen gleich bleibt. (Die Verlagerung nach `entry.options` ist ausdrücklich nicht Teil dieses Plans.)

**Prüfen**: `grep -c "step_id=" custom_components/inverter_charge_night/config_flow.py` → `12`.

### Schritt 5: `strings.json` und `en.json` deckungsgleich machen

- `strings.json`: `config.step` bekommt die acht Schritte (`user`, `time_soc`, `power`, `advanced`,
  `reconfigure`, `reconfigure_time_soc`, `reconfigure_power`, `reconfigure_advanced`), `options.step` die vier
  (`init`, `time_soc`, `power`, `advanced`). Jeder Schritt: `title`, `description` (zwei Sätze, was der Schritt
  einstellt), `data` und `data_description` **nur** für die Felder des Schritts. Das tote `config.step.init`
  entfällt. Die vorhandenen `data_description`-Texte aus dem heutigen `strings.json` übernehmen, sie sind gut.
  Für die Zeitfelder ergänzen: "Stunde:Minute, 24-Stunden-Format. Zeitfenster darf über Mitternacht gehen."
- `config.abort` bekommt `already_configured` (vorhanden) und `reconfigure_successful: "Configuration updated"`.
- `en.json` wird eine **Kopie** von `strings.json` plus dem vorhandenen `exceptions`-Block aus dem alten `en.json`
  (dieser Block gehört auch in `strings.json`, damit beide identisch sind).
- Die drei Energiezähler-Keys kommen in keine der beiden Dateien.

**Prüfen**:
```
python3 - <<'EOF'
import json
s=json.load(open('custom_components/inverter_charge_night/strings.json'))
e=json.load(open('custom_components/inverter_charge_night/translations/en.json'))
def keys(d,p=''):
    out=set()
    for k,v in d.items():
        out.add(p+k)
        if isinstance(v,dict): out|=keys(v,p+k+'/')
    return out
print(sorted(keys(s)^keys(e)))
EOF
```
→ `[]`. Zusätzlich: jeder `CONF_*`-Key aus `const.py`, der im Schema vorkommt, muss in `strings.json`
unter genau einem `config.step.*.data` erscheinen (Skript analog, Ergebnis leer).

### Schritt 6: Tests anpassen

`tests/test_config_flow.py`:
- `test_config_flow_user_success` wird zu einem Durchlauf über vier Schritte: `async_step_user(input1)`
  liefert `FlowResultType.FORM` mit `step_id == "time_soc"`, usw., bis `CREATE_ENTRY`.
- Neuer Test: zweite Anlage mit derselben `kostal_min_soc_entity` → `FlowResultType.ABORT`,
  `reason == "already_configured"`.
- Neuer Test: Reconfigure-Durchlauf endet mit `ABORT`, `reason == "reconfigure_successful"`
  (`flow.hass.config_entries.async_update_entry` muss gemockt sein; Muster: bestehende Optionsflow-Tests).
- Neuer Test: `start_time == "22:00:00"` (TimeSelector-Format) wird akzeptiert.
- Die parametrisierten Zeitfehler-Tests (`test_validate_user_input_time_errors`) bleiben unverändert.

**Prüfen**: `pytest -q` → alle bestehen, Coverage-Meldung "Required test coverage of 100.0% reached".
`mypy custom_components/inverter_charge_night/` und `pyright` → 0 Fehler (config_flow.py ist von beiden
ausgenommen, `util.py` nicht).

## Testplan

- Neue Tests in `tests/test_config_flow.py` wie in Schritt 6 (Happy Path über vier Schritte, Abbruch bei
  Duplikat, Reconfigure, Zeitfeld mit Sekunden, Fehler landet im richtigen Schritt).
- Neue Tests in `tests/test_util.py` für `HH:MM:SS`.
- Muster: bestehende Tests in `tests/test_config_flow.py` (MonkeyPatch auf `er.async_get`).

## Fertig-Kriterien

- [ ] `pytest` endet mit Exit 0 und Coverage-Gate erfüllt
- [ ] `mypy custom_components/inverter_charge_night/` und `pyright` melden 0 Fehler
- [ ] Key-Vergleich `strings.json` ↔ `en.json` liefert `[]`
- [ ] `grep -c "async_step_reconfigure" custom_components/inverter_charge_night/config_flow.py` ≥ 1
- [ ] `grep -c "async_set_unique_id" custom_components/inverter_charge_night/config_flow.py` ≥ 1
- [ ] `grep -c "grid_import_energy_entity" custom_components/inverter_charge_night/translations/en.json` = 0
- [ ] `git status` zeigt nur Dateien aus dem Umfang
- [ ] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- Der Code an den genannten Stellen entspricht nicht den Auszügen (Drift).
- `async_update_reload_and_abort` oder `_get_reconfigure_entry` existieren in der installierten HA-Version
  nicht (dann HA-Version melden; Mindestversion ist 2024.4).
- Ein bestehender Test außerhalb von `tests/test_config_flow.py` schlägt fehl.
- Das Coverage-Gate lässt sich nur durch Ausnahmen in `.coveragerc` erfüllen.

## Wartungshinweise

- Jedes neue Konfigurationsfeld muss in genau einen `_schema_*`-Bauer und in genau einen Schritt von
  `strings.json` (dann `en.json` neu kopieren). Der Key-Vergleich aus Schritt 5 sollte als Test in
  `tests/test_config_flow.py` bleiben, damit die Dateien nicht wieder auseinanderlaufen.
- `parse_time_str` akzeptiert jetzt Sekunden; der Coordinator (`_parse_time`) ruft dieselbe Funktion,
  also profitiert er automatisch.
- Backlog 008 wird die `kostal_*`-Keys umbenennen; dafür wird `VERSION` in der Flow-Klasse auf 2 gesetzt
  und `async_migrate_entry` ergänzt. Dieser Plan lässt `VERSION = 1`.
