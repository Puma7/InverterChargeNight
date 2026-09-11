# Plan 005: Rücksetz-Robustheit und Persistenz über Neustarts

> **Anweisung an den Ausführenden**: Schritt für Schritt vorgehen, jeden Prüfbefehl ausführen.
> Bei einer STOP-Bedingung anhalten und berichten. Am Ende die Statuszeile in `plans/README.md` aktualisieren.
>
> **Drift-Prüfung (zuerst ausführen)**:
> `git diff --stat 549a5ee..HEAD -- custom_components/inverter_charge_night/__init__.py custom_components/inverter_charge_night/switch.py custom_components/inverter_charge_night/number.py custom_components/inverter_charge_night/entity.py`
> Änderungen aus Plan 002 und 004 sind erwartet; die Auszüge unten gegen den Live-Code prüfen, bei Widerspruch STOP.

## Status

- **Priorität**: P1
- **Aufwand**: L
- **Risiko**: MED
- **Hängt ab von**: 003, 004
- **Kategorie**: bug
- **Geplant bei**: Commit `549a5ee`, 2026-09-11

## Warum das wichtig ist

Die Integration verändert Wechselrichter-Einstellungen (Min-SOC, Netzladung, AC-Ladelimit) und muss sie
zuverlässig zurücksetzen. Heute gilt:

- Ein fehlgeschlagener Reset um 05:00 wird nie wiederholt (F3). Der Min-SOC bleibt den Tag über auf dem Nachtziel,
  das Haus läuft am Netz, der Speicher kann keine PV aufnehmen.
- Nach einem HA-Neustart im Fenster wird das alte Ziel als "Originalwert" gespeichert und ab dann jeden Morgen
  zurückgeschrieben (F4).
- Enabled-Schalter, Skip-next, Override und Originalwert überleben keinen Neustart (F5).
- Der Fensterstart blockiert bis zu 3 Minuten in einer Warteschleife (F6).
- Das AC-Ladelimit wird nie zurückgesetzt (F7).
- Der Verifikationstask wird nicht abgewartet und kann nach dem Reset das Nachtziel zurückschreiben (F11).

## Ist-Zustand

Zeilenangaben: `custom_components/inverter_charge_night/__init__.py` bei `549a5ee`.

- `_reset_settings` (Zeile 1082–1155): setzt `reset_success = True` nur nach erfolgreichem `number.set_value`;
  bei fehlender Entität `_LOGGER.error("Cannot reset min SOC - entity state unavailable")` ohne Wiederholung.
  `original_min_soc`, `_last_soc_set`, `override_soc` werden nur bei Erfolg geleert.
- `_on_window_end` (Zeile 1060–1080): `finally` setzt `is_active = False`, leert die Ziele, entfernt beide Listener
  und stoppt die Verifikation, unabhängig vom Reset-Ergebnis.
- `_calculate_initial_soc` (Zeile 914–1058): Warteschleife `while retry_count < max_retries` mit
  `await asyncio.sleep(10)`, bis zu 17-mal; übernimmt den Wechselrichterwert als Ziel, wenn
  `abs(current_min_soc - default_min_soc) > 1.0`.
- `_control_kostal` (Zeile 1402–1432): erfasst `original_min_soc` aus dem Live-Wert; Plausibilitätsprüfung
  nur `if self.original_min_soc > default_min + 10.0`.
- `_set_ac_charge_limit_w` (Zeile 462–485): schreibt `CONF_CHARGE_POWER_ENTITY`, liest nie den Vorwert; kein Reset-Gegenstück.
  Vorbild für Erfassen/Zurücksetzen: `_apply_absolute_charge_power_limit` (Zeile 487–525) und
  `_reset_absolute_charge_power` (Zeile 527–551) mit `self._original_absolute_charge_power`.
- `_stop_periodic_verification` (Zeile 1848–1853): `task.cancel()` ohne `await`.
- Coordinator-Konstruktor (Zeile 185–223): `self.is_enabled = True`, `self.skip_next = False`, `self.override_soc = None`,
  `self.original_min_soc = None` fest verdrahtet.
- `switch.py` und `number.py`: Entitäten erben von `InverterChargeNightEntity` (`entity.py`), keine `RestoreEntity`.
- Persistenzvorbild im Repo: `get_auto_efficiency_data` / `_save_auto_efficiency_data` (Zeile 553–563) schreiben
  in `entry.options` über `hass.config_entries.async_update_entry(entry, options=options)`.

## Benötigte Befehle

| Zweck | Befehl | Erwartung |
|---|---|---|
| Tests | `pytest` | grün, Ratchet erfüllt |
| Typen | `mypy custom_components/inverter_charge_night/` / `pyright` | 0 Fehler |

## Umfang

**Im Umfang**:
- `custom_components/inverter_charge_night/__init__.py` (Methoden: Konstruktor, `_calculate_initial_soc`, `_on_window_start`, `_on_window_end`, `_reset_settings`, `_control_kostal`, `_control_discharge`, `_set_ac_charge_limit_w`, neue `_reset_ac_charge_limit`, `_stop_periodic_verification`, `_finalize_auto_test`, neue `_persist_state`/`_restore_state`)
- `custom_components/inverter_charge_night/const.py` (neuer Options-Key `CONF_RUNTIME_STATE = "runtime_state"`)
- `custom_components/inverter_charge_night/switch.py`, `number.py` (nur Aufruf der Persistenz nach Zustandsänderung)
- Tests: `tests/test_window_lifecycle.py`, `tests/test_reset_settings.py`, `tests/test_coordinator.py`, `tests/test_skip_next.py`, `tests/test_periodic_verification.py`, `.coveragerc` (`fail_under`)

**Nicht im Umfang**:
- Die Zielformel (Plan 006).
- `RestoreEntity` auf den Entitäten: Der Zustand wird zentral im Coordinator persistiert (ein Mechanismus, nicht drei); die Entitäten lesen ihn wie bisher vom Coordinator.

## Git-Vorgehen

- Branch: `fix/005-reset-and-persistence` von `develop`
- Ein Commit pro Schritt, Stil `fix: …`

## Schritte

### Schritt 1: Zustand zentral persistieren

Im Coordinator:
```python
def _persist_state(self) -> None:
    """Store the flags that must survive a restart in the entry options."""
    options = dict(self.entry.options)
    options[CONF_RUNTIME_STATE] = {
        "is_enabled": self.is_enabled,
        "skip_next_until": self._skip_next_until.isoformat() if self._skip_next_until else None,
        "override_soc": self.override_soc,
        "original_min_soc": self.original_min_soc,
        "original_ac_charge_power": self._original_ac_charge_power,
        "pending_reset": self._pending_reset,
    }
    self.hass.config_entries.async_update_entry(self.entry, options=options)
```
`_restore_state()` liest den Block im Konstruktor (Fehlertolerant: fehlende Keys → Standardwerte) und setzt
die Attribute. `_schedule_skip_next_expiry` speichert den absoluten Ablaufzeitpunkt `_skip_next_until`
(`dt_util.now() + timedelta(hours=24)`) und plant `async_call_later` auf die **verbleibende** Zeit; ist
der Zeitpunkt beim Neustart vorbei, wird `skip_next` sofort `False`.
`_persist_state()` wird aufgerufen nach: Enabled-Umschaltung (`switch.py`), Skip-next-Umschaltung, Override-Änderung
(`number.py`), Erfassen von `original_min_soc` / `_original_ac_charge_power`, Reset-Erfolg, Setzen/Löschen von `_pending_reset`.
Achtung: `async_update_entry` löst `async_update_entry` in `__init__.py` (den Update-Listener) aus. Der Listener
muss Options-only-Änderungen erkennen und dann **nicht** die Trigger neu aufsetzen: am Anfang von
`async_update_entry` `if coordinator.config == entry.data: return` (Daten unverändert → nur Optionen).

**Prüfen**: Test in `tests/test_coordinator.py`: Konstruktor mit `entry.options = {"runtime_state": {"is_enabled": False, ...}}` → `coordinator.is_enabled is False`. `pytest tests/test_coordinator.py -q` → grün.

### Schritt 2: `original_min_soc` nie aus dem Live-Wert nach Neustart raten

- In `_calculate_initial_soc` die gesamte Warteschleife (Zeile 934–1010) entfernen. Neustart-Wiederherstellung
  wird zu: `if self._pending_reset or self.original_min_soc is not None:` (aus dem persistierten Zustand)
  → das persistierte Ziel `override_soc`/`initial_calculated_soc` verwenden; sonst normal aus dem Forecast rechnen.
  Dafür `initial_calculated_soc` ebenfalls in `runtime_state` aufnehmen.
- In `_control_kostal` und `_control_discharge`: `original_min_soc` nur dann aus dem Live-Wert erfassen, wenn
  `self.original_min_soc is None` **und** kein persistierter Wert vorlag; die `default_min + 10.0`-Heuristik
  entfernen. Nach dem Erfassen `_persist_state()`.
- Test 5 aus Plan 003 ("Neustart im Fenster") anpassen: Wechselrichter meldet 65, persistierter Zustand enthält
  `original_min_soc = 8`, `initial_calculated_soc = 65` → Ziel 65, Original 8; am Fensterende `set_value(8)`.

**Prüfen**: `pytest tests/test_window_lifecycle.py -q` → grün; `grep -c "asyncio.sleep(retry_interval)" custom_components/inverter_charge_night/__init__.py` = 0.

### Schritt 3: Fensterstart ohne Blockade

`_on_window_start`: zuerst Listener und Verifikation aufsetzen, dann `_calculate_initial_soc()` (jetzt ohne Warten).
Ist die Min-SOC-Entität beim Start nicht verfügbar, wird trotzdem das Ziel berechnet; `_control_kostal` überspringt
das Setzen bei nicht verfügbarer Entität und die periodische Verifikation holt es nach, sobald sie da ist
(sie prüft heute schon `abs(current_inverter_soc - target_soc) > 0.5`, Zeile 1888).

**Prüfen**: Test: Min-SOC-Entität `None` beim Start, nach `async_set` verfügbar → `_verify_and_restore_min_soc` schreibt das Ziel. `pytest tests/test_periodic_verification.py -q` → grün.

### Schritt 4: Reset mit Wiederholung

`_reset_settings` gibt `bool` zurück (alle drei Ziele erreicht: Min-SOC gesetzt, Netzladung aus, AC-Limit
zurück). `_on_window_end`:
```python
ok = await self._reset_settings()
self._pending_reset = not ok
if not ok:
    self._schedule_reset_retry()
self._persist_state()
```
`_schedule_reset_retry` nutzt `async_call_later` mit 60 s, 120 s, 240 s, dann alle 15 min, bis `ok`; der
Timer wird im Coordinator gehalten, in `async_unload_entry` abgebrochen und nach Neustart aus `pending_reset`
wieder gestartet. Ein neuer Fensterstart bei `pending_reset` erfasst **keinen** neuen Originalwert.
Ist der Reset erledigt, `original_min_soc`, `_last_soc_set`, `override_soc`, `_original_ac_charge_power` leeren und persistieren.

**Prüfen**: Test in `tests/test_reset_settings.py`: Min-SOC-Entität `None` → `_pending_reset is True`, `async_call_later` aufgerufen; Entität erscheint, Retry-Callback ausführen → `set_value` mit Original, `_pending_reset is False`.

### Schritt 5: AC-Ladelimit zurücksetzen

`_set_ac_charge_limit_w`: vor dem ersten Schreiben eines Fensters den Live-Wert in `_original_ac_charge_power`
erfassen (Einheit wie beim Schreiben umrechnen) und persistieren. Neue Methode `_reset_ac_charge_limit()` nach dem
Muster von `_reset_absolute_charge_power`; Aufruf in `_reset_settings` und in `_finalize_auto_test`, wenn der Finder
abgebrochen wird (`switch.py::AutoEfficientChargeSwitch.async_turn_off` ruft `_reset_auto_test_state`; dort ebenfalls zurücksetzen).

**Prüfen**: Test: Finder setzt 1000 W, Fensterende → `set_value` mit dem Originalwert auf `CONF_CHARGE_POWER_ENTITY`.

### Schritt 6: Verifikation sauber beenden

`_stop_periodic_verification` wird `async`: `task.cancel()`, dann `await asyncio.gather(task, return_exceptions=True)`.
Reihenfolge in `_on_window_end`: Listener entfernen und Verifikation beenden **vor** `_reset_settings`.
In `_verify_and_restore_min_soc` unmittelbar vor dem `set_value` erneut `if not self.is_active: return`.
Alle Aufrufer (`switch.py`, `select.py`, `async_unload_entry`) auf `await` umstellen.

**Prüfen**: `grep -n "_stop_periodic_verification()" custom_components/inverter_charge_night/*.py` → jede Zeile beginnt mit `await`. `pytest -q` → grün.

### Schritt 7: Ratchet anheben

`pytest` ausführen, `fail_under` in `.coveragerc` anheben.

## Testplan

- Persistenz: Rundreise Konstruktor ↔ `_persist_state` (alle Felder), abgelaufenes `skip_next_until`.
- Reset-Retry: fehlgeschlagen → geplant → erfolgreich; Fensterstart während `pending_reset` erfasst kein neues Original.
- Neustart im Fenster mit persistiertem Zustand.
- AC-Limit-Reset am Fensterende und bei Finder-Abbruch.
- Verifikation: nach `_on_window_end` schreibt kein verspäteter Verifikationslauf mehr (Task wird awaited; Test mit `asyncio.Event` im gepatchten `_verify_and_restore_min_soc`).
- Muster: `tests/test_reset_settings.py`, `tests/test_periodic_verification.py`.

## Fertig-Kriterien

- [ ] `pytest`, `mypy`, `pyright` grün; `fail_under` gestiegen
- [ ] `grep -c "asyncio.sleep(retry_interval)" custom_components/inverter_charge_night/__init__.py` = 0
- [ ] `grep -c "default_min + 10.0" custom_components/inverter_charge_night/__init__.py` = 0
- [ ] `grep -c "_reset_ac_charge_limit" custom_components/inverter_charge_night/__init__.py` ≥ 2
- [ ] `grep -c "runtime_state" custom_components/inverter_charge_night/__init__.py` ≥ 2
- [ ] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- Drift an den genannten Stellen.
- `async_update_entry(entry, options=...)` löst in der Test-HA-Version eine Neu-Ladung des Entrys aus (dann `entry.async_on_unload`/Update-Listener-Verhalten der HA-Version berichten).
- Die Persistenz über `entry.options` kollidiert mit `auto_efficiency_data` (gleicher Options-Dict): beide Keys müssen nebeneinander bestehen; wenn ein Test zeigt, dass einer den anderen überschreibt, STOP.
- Schritt 6 führt zu einem hängenden Test (Timeout): melden statt Timeout erhöhen.

## Wartungshinweise

- `runtime_state` ist die einzige Stelle für Neustart-relevante Flags. Neue Flags gehören dort hinein, nicht in neue Entity-Restores.
- Plan 006 fügt die Entladesperre hinzu; ihr Originalwert gehört ebenfalls in `runtime_state` und in `_reset_settings`.
- Der Reset-Retry darf nie den Backup-Modus überstimmen: `_reset_settings` bleibt hinter `_is_backup_active()`-Prüfungen der Aufrufer.
