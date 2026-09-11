# Plan 007: Schnee-Override: die nächsten N Nächte auf das Maximum laden

> **Anweisung an den Ausführenden**: Schritt für Schritt vorgehen, jeden Prüfbefehl ausführen.
> Bei einer STOP-Bedingung anhalten und berichten. Am Ende die Statuszeile in `plans/README.md` aktualisieren.
>
> **Drift-Prüfung (zuerst ausführen)**:
> `git diff --stat 549a5ee..HEAD -- custom_components/inverter_charge_night/__init__.py custom_components/inverter_charge_night/number.py custom_components/inverter_charge_night/const.py custom_components/inverter_charge_night/strings.json custom_components/inverter_charge_night/translations/en.json`
> Änderungen aus 001–006 sind erwartet. Voraussetzung ist, dass `_persist_state`/`_restore_state` und der
> Options-Key `runtime_state` aus Plan 005 vorhanden sind (`grep -n "runtime_state" custom_components/inverter_charge_night/__init__.py`); sonst STOP.

## Status

- **Priorität**: P2
- **Aufwand**: S
- **Risiko**: LOW
- **Hängt ab von**: 004 (ein Ziel über `current_target_soc()`), 005 (Persistenz)
- **Kategorie**: direction
- **Geplant bei**: Commit `4517645`, 2026-09-11

## Warum das wichtig ist

Liegt Schnee auf den Modulen, produziert die Anlage nichts, obwohl jede Forecast-Quelle (Solcast, Forecast.Solar)
von schneefreien Modulen ausgeht und Ertrag meldet. Der Planer lässt dann Platz für Sonnenenergie, die nie kommt,
und das Haus kauft tagsüber zum Tagestarif. Der Eigentümer braucht einen Schalter, mit dem er die nächste oder die
nächsten Nächte auf das Maximum laden lässt, ohne die Konfiguration zu ändern. Der bestehende Override
(`number.min_soc_override`) hilft nicht: er gilt nur im laufenden Fenster und wird am Fensterende gelöscht.

## Ist-Zustand

- `custom_components/inverter_charge_night/number.py`: `MinSOCOverrideNumber` (Vorbild für eine Number-Entität, `EntityCategory.CONFIG`, `NumberMode.BOX`).
- `custom_components/inverter_charge_night/__init__.py`: `current_target_soc()` (Plan 004) ist die einzige Zielquelle;
  `_on_window_end` leert `override_soc`; `_persist_state()`/`_restore_state()` (Plan 005) speichern Flags in `entry.options["runtime_state"]`.
- `calculate_required_soc` klemmt auf `user_max_soc`; das Maximum ist also `float(self.config.get(CONF_USER_MAX_SOC, 100.0))`.
- `strings.json` hat unter `entity.number` den Eintrag `min_soc_override`; `en.json` ist eine Kopie (Plan 001).
- Sensor-Attribute in `sensor.py::CalculatedSOCSensor.extra_state_attributes` (Vorbild für ein neues Attribut).

## Umfang

**Im Umfang**: `const.py` (Attribut `ATTR_SNOW_NIGHTS`), `__init__.py` (Attribut `snow_nights`, Persistenz, Ziel, Herunterzählen), `number.py` (neue Entität `SnowNightsNumber`), `sensor.py` (Attribut), `strings.json`, `translations/en.json`, `icons.json`, Tests `tests/test_entities.py`, `tests/test_window_lifecycle.py`, `README.md` (Entitätsliste).

**Nicht im Umfang**: automatische Schneeerkennung (z. B. Vergleich Ist-Ertrag zu Forecast); das ist ein späterer Kandidat, siehe Wartungshinweise.

## Schritte

### Schritt 1: Zustand im Coordinator

`self.snow_nights: int = 0` im Konstruktor, Aufnahme in `runtime_state` (Plan 005). In `current_target_soc()`:
```python
if self.snow_nights > 0:
    return float(self.config.get(CONF_USER_MAX_SOC, DEFAULT_MAX_SOC))
```
**vor** der Prüfung auf `override_soc`, damit ein manueller Override im Fenster den Schneemodus trotzdem
überstimmen kann? Nein: Schnee ist die stärkere Aussage. Reihenfolge: Schnee → Override → Plan. Ein Override
unterhalb des Maximums während Schnee ist ein Bedienfehler; er wird geloggt und ignoriert.
In `_calculate_initial_soc`: bei `snow_nights > 0` Forecast nicht lesen, `initial_calculated_soc = user_max_soc`,
Log `"Snow mode: charging to %.0f%% (%d night(s) remaining)"`.
In `_on_window_end` (im `finally`, nach dem Reset): `if self.snow_nights > 0: self.snow_nights -= 1; self._persist_state()`.
Auch der Planer v2 (Plan 006) muss den Schneemodus respektieren: in `plan_target_soc` wird bei Schnee nicht
aufgerufen; der Kurzschluss sitzt in `current_target_soc()` und `_calculate_initial_soc`, also vor dem Planer.

**Prüfen**: Test in `tests/test_window_lifecycle.py`: `snow_nights = 2`, Fensterstart → Ziel = `user_max_soc`, Forecast-Entität wird nicht gelesen; Fensterende → `snow_nights == 1`; zweites Fensterende → `0`; drittes Fenster → normales Ziel.

### Schritt 2: Entität

`number.py`: `class SnowNightsNumber(InverterChargeNightEntity, NumberEntity)` mit `_attr_translation_key = "snow_nights"`,
`_attr_native_min_value = 0`, `_attr_native_max_value = 14`, `_attr_native_step = 1`, `NumberMode.BOX`,
`EntityCategory.CONFIG`, Icon `mdi:snowflake`. `native_value` liest `coordinator.snow_nights`; `async_set_native_value`
setzt `coordinator.snow_nights = int(value)`, ruft `_persist_state()`, `async_write_ha_state()` und, wenn ein
Fenster aktiv ist, `async_request_refresh()` (damit ein laufendes Fenster sofort auf das Maximum wechselt).
In `async_setup_entry` von `number.py` mit anlegen. `strings.json`/`en.json`: `entity.number.snow_nights.name = "Snow nights (charge to max)"`.
`icons.json`: `number.snow_nights: mdi:snowflake`.
`sensor.py`: Attribut `snow_nights` im `calculated_soc`-Sensor.

**Prüfen**: `tests/test_entities.py`: Setzen auf 3 → Coordinator 3, `_persist_state` aufgerufen; im aktiven Fenster → `async_request_refresh` awaited. Key-Vergleich `strings.json` ↔ `en.json` → `[]`.

### Schritt 3: Doku

`README.md` Entitätsliste: `number.inverter_charge_night_snow_nights` mit einem Satz Erklärung
("Bei Schnee auf den Modulen: Anzahl der nächsten Nächte, in denen bis zum Maximum geladen wird; zählt automatisch herunter.").

## Fertig-Kriterien

- [ ] `pytest`, `mypy`, `pyright` grün; Ratchet erfüllt
- [ ] `grep -c "snow_nights" custom_components/inverter_charge_night/__init__.py` ≥ 4
- [ ] `grep -c "SnowNightsNumber" custom_components/inverter_charge_night/number.py` ≥ 2
- [ ] Key-Vergleich `strings.json` ↔ `en.json` liefert `[]`
- [ ] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- `runtime_state` (Plan 005) fehlt.
- `current_target_soc()` (Plan 004) fehlt.

## Wartungshinweise

- Späterer Kandidat "automatische Schneeerkennung": wenn ein PV-Ertragssensor konfiguriert ist und der Tagesertrag
  unter 5 % des Forecasts bleibt, obwohl der Forecast > 2 kWh war, `snow_nights` automatisch auf 1 setzen und eine
  Repair-/Benachrichtigung auslösen. Braucht einen Ertragszähler als Eingang (Plan 006 Verbrauchszähler-Muster).
- Der Schneemodus überstimmt den Planer v2 vollständig; er darf nicht in `planner.py` einfließen, damit das Modul rein bleibt.
