# Plan 004: Steuerlogik-Fehler mit kleinem Umfang beheben

> **Anweisung an den Ausführenden**: Schritt für Schritt vorgehen, jeden Prüfbefehl ausführen.
> Bei einer STOP-Bedingung anhalten und berichten. Am Ende die Statuszeile in `plans/README.md` aktualisieren.
>
> **Drift-Prüfung (zuerst ausführen)**:
> `git diff --stat 549a5ee..HEAD -- custom_components/inverter_charge_night/__init__.py custom_components/inverter_charge_night/number.py`
> Plan 002 ändert `async_setup_entry`/`async_unload_entry` (erwartet). Alle anderen Abweichungen gegen die
> Auszüge unten prüfen; bei Widerspruch STOP.

## Status

- **Priorität**: P1
- **Aufwand**: M
- **Risiko**: MED (Code, der den Wechselrichter steuert)
- **Hängt ab von**: 003 (Coverage und strenger Test-Hass müssen vorhanden sein)
- **Kategorie**: bug
- **Geplant bei**: Commit `549a5ee`, 2026-09-11

## Warum das wichtig ist

Sechs nachgewiesene Fehler in der Steuerlogik, jeder mit kleinem Umfang, alle mit hoher Sicherheit:

1. **Override nach oben wirkungslos** (F2): das Ziel für "erreicht" und für die Verifikation ist
   `minimum_calculated_soc`, das nur sinken kann. Ein höherer Override wird sofort wieder ausgeschaltet.
2. **Override im Entlademodus** ruft den Ladepfad und schaltet Netzladung ein (F10).
3. **Backup-Listener** wird beim Entladen nicht abgemeldet und bei Optionsänderung nicht neu gesetzt (F8).
4. **`_last_soc_set`-Veto** verhindert das Setzen des Min-SOC, obwohl der gelesene Wert abweicht (F10).
5. **Ungeschütztes `number.set_value`** in `_control_kostal` bricht bei Service-Fehlern den ganzen Update ab (F10).
6. **Fenstergrenzen werden im Polling nicht geprüft** (F9): ein verpasster End-Trigger (Sommerzeit, gestörter
   Event-Loop, Fehler in `setup_time_triggers`) lässt Netzladung unbegrenzt an.

## Ist-Zustand

Alle Zeilenangaben beziehen sich auf `custom_components/inverter_charge_night/__init__.py` bei `549a5ee`.

- **Ziel-Ermittlung** existiert in vier Varianten:
  - Zeile 1330–1334 (`_async_update_data`): `check_target = self.minimum_calculated_soc if self.minimum_calculated_soc is not None else target_soc`
  - Zeile 1723–1725 (Batterie-Listener): `minimum_calculated_soc` → `override_soc` → `calculated_soc`
  - Zeile 1791 (Min-SOC-Listener) und 1871 (`_verify_and_restore_min_soc`): `minimum_calculated_soc` → `initial_calculated_soc`
  - Zeile 1286–1298: `target_soc = override_soc` sonst `calculated_soc`, dabei `minimum_calculated_soc` nur **gesenkt**.
- `number.py:83-93`:
  ```python
  self.coordinator.override_soc = value
  self.coordinator.target_reached = False
  if self.coordinator.minimum_calculated_soc is None or value < self.coordinator.minimum_calculated_soc:
      self.coordinator.minimum_calculated_soc = value
  self.async_write_ha_state()
  if self.coordinator.is_active and self.coordinator.is_enabled:
      await self.coordinator._control_kostal(value)
      await self.coordinator.async_request_refresh()
  ```
- `async_unload_entry` Zeile 166–171 ruft `remove_time_triggers`, `_remove_battery_soc_listener`,
  `_remove_inverter_min_soc_listener`, `_stop_periodic_verification`, `_cancel_skip_next_expiry`, aber nicht
  `_remove_backup_mode_listener` (definiert Zeile 1951). `async_update_entry` (Zeile 99–150) setzt den Backup-Listener nicht neu.
- Zeile 1451–1460 (`_control_kostal`), identisch in `_control_discharge` Zeile 1616–1622:
  ```python
  if min_soc_current_value is None or abs(min_soc_current_value - target_soc) > 0.5:
      if self._last_soc_set is None or abs(self._last_soc_set - target_soc) > 0.5:
          need_to_set_min_soc = True
      else:
          _LOGGER.debug("Min SOC already set to %.1f%% recently, skipping update", target_soc)
          self._last_soc_set = target_soc
  ```
- Zeile 1467–1479: `await self.hass.services.async_call("number", "set_value", {...})` ohne `try`.
- `_async_update_data` Zeile 1157–1178 prüft `is_enabled`, `is_active`, Backup, Datumsbereich, aber nicht die Uhrzeit.
  `_window_times()` (Zeile 381) und `_is_time_between` (Zeile 740) liefern alles Nötige.
- Konventionen: Service-Aufrufe sind sonst überall in `try/except Exception` mit `_LOGGER.error(..., exc_info=True)` gekapselt (Beispiel Zeile 1486–1498).

## Benötigte Befehle

| Zweck | Befehl | Erwartung |
|---|---|---|
| Tests | `pytest` | grün, Ratchet erfüllt |
| Typen | `mypy custom_components/inverter_charge_night/` / `pyright` | 0 Fehler |

## Umfang

**Im Umfang**:
- `custom_components/inverter_charge_night/__init__.py` (Methoden: `_async_update_data`, `_control_kostal`, `_control_discharge`, `_setup_battery_soc_listener`, `_setup_inverter_min_soc_listener`, `_verify_and_restore_min_soc`, `async_update_entry`, `async_unload_entry`; neue Methode `current_target_soc`)
- `custom_components/inverter_charge_night/number.py`
- `tests/test_window_lifecycle.py`, `tests/test_listeners.py`, `tests/test_update_data.py`, `tests/test_entities.py`, `tests/test_unload_entry.py`, `.coveragerc` (nur `fail_under`)

**Nicht im Umfang**:
- Reset-Logik, Neustart-Wiederherstellung, Persistenz (Plan 005).
- `minimum_calculated_soc` als Attribut entfernen: bleibt für Diagnose bestehen, verliert nur seine Rolle als Ziel.

## Git-Vorgehen

- Branch: `fix/004-control-loop` von `develop`
- Ein Commit pro Schritt, Stil `fix: …`

## Schritte

### Schritt 1: Ein Ziel

Neue Methode im Coordinator:
```python
def current_target_soc(self) -> float | None:
    """The SOC the inverter should hold right now: manual override, else the plan."""
    if self.override_soc is not None:
        return self.override_soc
    return self.initial_calculated_soc if self.initial_calculated_soc is not None else self.calculated_soc
```
Alle vier Stellen aus "Ist-Zustand" verwenden diese Methode. In `_async_update_data` bleibt die Pflege von
`minimum_calculated_soc` (nur Diagnose), aber `check_target = target_soc`.
Der `xfail`-Test "Override nach oben" aus Plan 003 wird zum normalen Test.

**Prüfen**: `pytest tests/test_window_lifecycle.py -q` → Override-Test besteht ohne `xfail`.

### Schritt 2: Override im richtigen Modus

`number.py`: die direkte `_control_kostal`-Zeile entfernen und nur `await self.coordinator.async_request_refresh()`
aufrufen; `_async_update_data` verzweigt bereits korrekt nach Modus (Zeile 1294–1300). Test in
`tests/test_entities.py::test_min_soc_override_number_set_value` anpassen: statt `_control_kostal.assert_awaited_with(90.0)`
wird `async_request_refresh.assert_awaited()` geprüft und ein zweiter Test für `is_discharge_mode = True` ergänzt.

**Prüfen**: `pytest tests/test_entities.py -q` → grün; `grep -n "_control_kostal" custom_components/inverter_charge_night/number.py` → leer.

### Schritt 3: Backup-Listener

- `async_unload_entry`: `coordinator._remove_backup_mode_listener()` ergänzen.
- `async_update_entry`: nach `update_time_triggers()` `coordinator._setup_backup_mode_listener()` aufrufen
  (die Methode entfernt einen vorhandenen Listener selbst).
- Test in `tests/test_unload_entry.py`: `_remove_backup_mode_listener` wird aufgerufen; Test in
  `tests/test_update_entry_errors.py` oder neu: `_setup_backup_mode_listener` wird bei Optionsänderung aufgerufen.

**Prüfen**: `pytest tests/test_unload_entry.py tests/test_update_entry_errors.py -q` → grün.

### Schritt 4: `_last_soc_set` nur als Entprellung

In beiden Kontrollmethoden die innere Bedingung ersetzen:
```python
if min_soc_current_value is None or abs(min_soc_current_value - target_soc) > 0.5:
    need_to_set_min_soc = True
else:
    self._last_soc_set = target_soc
```
`_last_soc_set` bleibt als Log-Information und wird in `_reset_settings` **immer** auf `None` gesetzt
(heute nur bei Erfolg, Zeile 1151–1154).

**Prüfen**: Test in `tests/test_control_kostal_more.py`: `_last_soc_set = 60`, Wechselrichter meldet 8, Ziel 60 →
`number.set_value` **wird** aufgerufen. `pytest tests/test_control_kostal_more.py -q` → grün.

### Schritt 5: `set_value` kapseln

Zeile 1467–1479 in `try/except Exception as e` mit `_LOGGER.error("Error setting min SOC: %s", e, exc_info=True)`;
bei Fehler `need_to_set_min_soc` als nicht erledigt behandeln, aber den Netzlade-Schalter **nicht** einschalten
(ohne gesetzten Min-SOC würde der Wechselrichter gegen den falschen Boden laden). Test: `async_call` wirft
beim ersten Aufruf `HomeAssistantError` → kein `switch.turn_on`, kein Abbruch von `_async_update_data`
(Rückgabewert ist ein dict).

**Prüfen**: `pytest tests/test_control_kostal_extra.py -q` → grün.

### Schritt 6: Fenstergrenzen im Polling

In `_async_update_data` nach der Datumsbereichsprüfung:
```python
start, end = self._window_times()
if not self._is_time_between(dt_util.now().time(), start, end):
    _LOGGER.warning("Window end was missed (now outside %s-%s); ending window from polling update", start, end)
    await self._on_window_end(dt_util.now())
    return inactive_data
```
Achtung: `_is_time_between` ist an beiden Enden inklusiv; ein Poll genau in der Endminute beendet das Fenster
nicht, der Trigger tut es. Test in `tests/test_update_data.py`: Fenster 23:00–05:00, `dt_util.now` auf 07:00
gepatcht, `is_active = True` → `_on_window_end` awaited, `data["is_active"] is False`.

**Prüfen**: `pytest tests/test_update_data.py -q` → grün.

### Schritt 7: Ratchet anheben

`pytest` ausführen, neuen Coverage-Wert in `.coveragerc` eintragen.

## Testplan

- `tests/test_window_lifecycle.py`: Override nach oben (Netzladung bleibt an, Verifikation schreibt 70 statt 45), Override im Entlademodus.
- `tests/test_listeners.py`: Batterie-Listener nutzt `current_target_soc()`.
- `tests/test_update_data.py`: verpasstes Fensterende.
- `tests/test_control_kostal_*`: Veto entfernt, Service-Fehler gekapselt.
- Muster: bestehende Tests derselben Dateien.

## Fertig-Kriterien

- [ ] `pytest`, `mypy`, `pyright` grün; `fail_under` gestiegen
- [ ] `grep -c "minimum_calculated_soc if self.minimum_calculated_soc is not None" custom_components/inverter_charge_night/__init__.py` = 0
- [ ] `grep -c "_control_kostal" custom_components/inverter_charge_night/number.py` = 0
- [ ] `grep -c "_remove_backup_mode_listener()" custom_components/inverter_charge_night/__init__.py` ≥ 2
- [ ] kein `xfail` mehr mit `reason="plans/004"`
- [ ] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- Drift an den genannten Stellen.
- Schritt 1 lässt einen Test in `tests/test_morning_discharge.py` oder `tests/test_skip_next.py` fehlschlagen, dessen Erwartung sich nicht offensichtlich aus dem "ein Ziel"-Prinzip ergibt.
- Es zeigt sich, dass `INVERTER_BEHAVIOR_VERIFICATION.md` Abschnitt "Minimum SOC Tracking" von jemandem als
  gewolltes Verhalten verteidigt wird: dann Rücksprache, der Plan setzt das Gegenteil um.

## Wartungshinweise

- `current_target_soc()` ist ab jetzt die einzige Wahrheit über das Ziel; Plan 006 ersetzt nur ihre Berechnung, nicht ihre Verwendung.
- Der Polling-Schutz in Schritt 6 ist ein Sicherheitsnetz, kein Ersatz für Trigger; loggt er, war ein Trigger verloren, das gehört untersucht.
