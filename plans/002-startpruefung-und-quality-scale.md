# Plan 002: Startprüfung, Repair-Issues, runtime_data und PARALLEL_UPDATES zurückholen; quality_scale ehrlich machen

> **Anweisung an den Ausführenden**: Schritt für Schritt vorgehen, jeden Prüfbefehl ausführen.
> Bei einer STOP-Bedingung anhalten und berichten. Am Ende die Statuszeile in `plans/README.md` aktualisieren.
>
> **Drift-Prüfung (zuerst ausführen)**:
> `git diff --stat 549a5ee..HEAD -- custom_components/inverter_charge_night/__init__.py custom_components/inverter_charge_night/sensor.py custom_components/inverter_charge_night/switch.py custom_components/inverter_charge_night/number.py custom_components/inverter_charge_night/select.py custom_components/inverter_charge_night/binary_sensor.py custom_components/inverter_charge_night/diagnostics.py custom_components/inverter_charge_night/quality_scale.yaml`
> Plan 001 ändert `config_flow.py` und die Übersetzungen; diese Änderungen sind erwartet. Alle anderen
> Abweichungen gegen "Ist-Zustand" prüfen; bei Widerspruch STOP.

## Status

- **Priorität**: P1
- **Aufwand**: S
- **Risiko**: LOW
- **Hängt ab von**: 001 (für die Übersetzungs-Keys der Repair-Issues und das `reconfiguration_flow`-Häkchen)
- **Kategorie**: bug (Merge-Regression) + docs
- **Geplant bei**: Commit `549a5ee`, 2026-09-11

## Warum das wichtig ist

Der Merge `1ed4376` hat aus `__init__.py` die Startprüfung entfernt, die fehlende Pflicht-Entitäten
als Repair-Issue meldet und den Setup mit `ConfigEntryNotReady` verzögert, außerdem `entry.runtime_data`
und `PARALLEL_UPDATES`. `quality_scale.yaml` behauptet diese Regeln aber weiterhin als `done`. Praktische
Folge für den Eigentümer: Er hat Entitäten in der Kostal-Integration umbenannt, und die Integration
läuft seither still im Leerlauf, statt beim Start eine Reparaturmeldung mit der fehlenden Entitäts-ID
zu zeigen.

## Ist-Zustand

- `custom_components/inverter_charge_night/__init__.py:74-95`:
  ```python
  async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
      hass.data.setdefault(DOMAIN, {})
      coordinator = InverterChargeNightCoordinator(hass, entry)
      await coordinator.async_config_entry_first_refresh()
      hass.data[DOMAIN][entry.entry_id] = coordinator
  ```
  `_async_update_data` (Zeile 1157) gibt bei jedem Fehler `inactive_data` zurück und wirft nie `UpdateFailed`,
  also kann der erste Refresh nie fehlschlagen.
- Plattformen lesen den Coordinator so: `coordinator: InverterChargeNightCoordinator = hass.data[DOMAIN][entry.entry_id]`
  (`sensor.py:23`, `switch.py:26`, `number.py:30`, `select.py:30`, `binary_sensor.py:20`); `diagnostics.py:33`
  nutzt `hass.data.get(DOMAIN, {}).get(entry.entry_id)`.
- Kein Modul definiert `PARALLEL_UPDATES`.
- `quality_scale.yaml` markiert `runtime_data`, `test_before_setup`, `parallel_updates`, `reconfiguration_flow`,
  `repair_issues`, `exception_translations`, `unique_config_entry`, `strict_typing` als `done`.
- **Referenz** `git show 5c31112:custom_components/inverter_charge_night/__init__.py` Zeilen 99–125:
  ```python
  type InverterChargeNightConfigEntry = ConfigEntry[InverterChargeNightCoordinator]

  async def async_setup_entry(hass, entry: InverterChargeNightConfigEntry) -> bool:
      for key in (CONF_BATTERY_SOC_ENTITY, CONF_KOSTAL_MIN_SOC_ENTITY, CONF_KOSTAL_GRID_CHARGE_SWITCH):
          entity_id = entry.data.get(key)
          if entity_id and hass.states.get(entity_id) is None:
              ir.async_create_issue(hass, DOMAIN, f"entity_not_available_{entity_id}", is_fixable=False,
                  issue_domain=DOMAIN, severity=ir.IssueSeverity.ERROR,
                  translation_key="entity_not_available", translation_placeholders={"entity_id": entity_id})
              raise ConfigEntryNotReady(f"Required entity {entity_id} is not yet available")
      coordinator = InverterChargeNightCoordinator(hass, entry)
      await coordinator.async_config_entry_first_refresh()
      entry.runtime_data = coordinator
  ```
  Die `type`-Alias-Syntax braucht Python 3.12; das Repo setzt 3.12 voraus (`mypy.ini`).
- Übersetzung: `translations/en.json` enthält bereits `issues`/`exceptions`-Texte für `entity_not_available`
  (nach Plan 001 auch `strings.json`). Vor dem Einbau prüfen: `grep -n "entity_not_available" custom_components/inverter_charge_night/strings.json`.
- Tests: `tests/test_setup.py` patcht den Coordinator komplett (Zeilen 26–30); `mock_hass.states.get` ist ein
  `MagicMock`, liefert also für jede ID ein Objekt (nie `None`). Das ist beim Testen der Startprüfung zu beachten.

## Benötigte Befehle

| Zweck | Befehl | Erwartung |
|---|---|---|
| Tests | `pytest` | alle bestehen |
| Typen | `mypy custom_components/inverter_charge_night/` | `Success` |
| Typen | `pyright` | `0 errors` |

## Umfang

**Im Umfang**:
- `custom_components/inverter_charge_night/__init__.py` (nur `async_setup_entry`, `async_update_entry`, `async_unload_entry`, Importe)
- die fünf Plattformdateien und `diagnostics.py` (nur der Zugriff auf den Coordinator)
- `custom_components/inverter_charge_night/quality_scale.yaml`, `strings.json` + `translations/en.json` (nur Block `issues`)
- `tests/test_setup.py`, `tests/test_unload_entry.py`, `tests/test_platform_setup.py`, `tests/test_diagnostics.py`

**Nicht im Umfang**:
- Coordinator-Logik unterhalb von `async_unload_entry` (Pläne 004/005).
- `.coveragerc`, `mypy.ini`, `pyrightconfig.json` (Plan 003).

## Git-Vorgehen

- Branch: `fix/002-startup-checks` von `develop` (nach Merge von 001)
- Commits: `fix: …`, `chore: …`

## Schritte

### Schritt 1: Startprüfung mit Repair-Issue

In `__init__.py` importieren: `from homeassistant.exceptions import ConfigEntryNotReady` und
`from homeassistant.helpers import issue_registry as ir`. Den Block aus der Referenz (Ist-Zustand) vor
`InverterChargeNightCoordinator(hass, entry)` einsetzen. Wenn alle drei Entitäten vorhanden sind, ein
eventuell früher erzeugtes Issue löschen: `ir.async_delete_issue(hass, DOMAIN, f"entity_not_available_{entity_id}")`
für jede der drei IDs.

`strings.json` und `en.json` bekommen (falls nach Plan 001 noch nicht vorhanden):
```json
"issues": {
  "entity_not_available": {
    "title": "Required entity {entity_id} is missing",
    "description": "Inverter Charge Night needs the entity {entity_id}, but Home Assistant does not know it. Check the entity ID in the integration's settings (Reconfigure) or restore the entity in its own integration."
  }
}
```

**Prüfen**: `pytest tests/test_setup.py -q` → die bestehenden Tests bestehen weiterhin (sie patchen den
Coordinator; `mock_hass.states.get` liefert Objekte, also greift die Prüfung nicht).

### Schritt 2: `runtime_data` statt `hass.data`

- `type InverterChargeNightConfigEntry = ConfigEntry[InverterChargeNightCoordinator]` nach der Klasse definieren
  (oder oberhalb mit String-Forward-Reference), `entry.runtime_data = coordinator` setzen.
- `hass.data[DOMAIN]`-Zugriffe in `async_update_entry` (Zeile 105–109), `async_unload_entry` (Zeile 157, 171)
  und den sechs Plattform-/Diagnosedateien durch `entry.runtime_data` ersetzen. In `async_unload_entry` entfällt
  `hass.data[DOMAIN].pop(...)`.
- `diagnostics.py`: `coordinator = getattr(entry, "runtime_data", None)`.
- Tests: `tests/test_platform_setup.py` und `tests/test_diagnostics.py` setzen heute `mock_hass.data[DOMAIN] = {...}`;
  auf `mock_config_entry.runtime_data = coordinator` umstellen.

**Prüfen**: `grep -rn "hass.data\[DOMAIN\]" custom_components/` → keine Treffer. `pytest -q` → alle bestehen.

### Schritt 3: `PARALLEL_UPDATES`

In `sensor.py`, `switch.py`, `number.py`, `select.py`, `binary_sensor.py` direkt unter den Importen:
`PARALLEL_UPDATES = 0` (Coordinator-basierte Entitäten, keine eigenen Abfragen).

**Prüfen**: `grep -c "PARALLEL_UPDATES = 0" custom_components/inverter_charge_night/*.py | grep -v ":0"` → fünf Dateien.

### Schritt 4: `quality_scale.yaml` ehrlich machen

Nach diesem Plan sind belegt: `runtime_data`, `test_before_setup`, `parallel_updates`, `repair_issues`,
`reconfiguration_flow` (durch 001), `unique_config_entry` (durch 001). Auf `todo` setzen mit Kommentar:
- `exception_translations` (es werden keine übersetzten `HomeAssistantError` geworfen)
- `strict_typing` → `todo` mit Kommentar "coordinator and config flow are excluded from strict checking" bis Plan 003 die Ausnahmen entfernt.

**Prüfen**: `python3 -c "import yaml;yaml.safe_load(open('custom_components/inverter_charge_night/quality_scale.yaml'))"` → keine Ausgabe
(falls `yaml` fehlt: `pip install pyyaml`).

### Schritt 5: Test für die Startprüfung

Neuer Test in `tests/test_setup.py`: `mock_hass.states.get = MagicMock(side_effect=lambda eid: None if eid == "number.min_soc" else MagicMock())`,
`ir.async_create_issue` per `patch("custom_components.inverter_charge_night.ir.async_create_issue")` mocken,
`async_setup_entry` muss `ConfigEntryNotReady` werfen und `async_create_issue` mit `translation_placeholders={"entity_id": "number.min_soc"}` aufgerufen worden sein.
Zweiter Test: alle Entitäten vorhanden → `async_delete_issue` dreimal aufgerufen, Setup gibt `True`.

**Prüfen**: `pytest tests/test_setup.py -q` → bestehen.

## Fertig-Kriterien

- [ ] `pytest`, `mypy`, `pyright` grün
- [ ] `grep -rn "hass.data\[DOMAIN\]" custom_components/` leer
- [ ] `grep -c "ConfigEntryNotReady" custom_components/inverter_charge_night/__init__.py` ≥ 2
- [ ] `quality_scale.yaml` enthält kein `done` mehr für eine Regel ohne Code-Nachweis (Liste oben)
- [ ] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- Drift an den genannten Stellen.
- Die HA-Version im Test-Venv kennt `ConfigEntry.runtime_data` nicht (gibt es seit 2024.4; sonst Version melden).
- Ein Test außerhalb der genannten Testdateien schlägt fehl.

## Wartungshinweise

- Jede neue Pflicht-Entität gehört in das Tupel der Startprüfung.
- `ConfigEntryNotReady` sorgt dafür, dass HA den Setup mit Backoff wiederholt; bei Modbus-Integrationen,
  die nach einem Neustart 1–2 Minuten brauchen, ist genau das erwünscht. Plan 005 entfernt deshalb die
  3-Minuten-Warteschleife im Fensterstart, sie wird durch diese Prüfung überflüssig.
