# Plan 008: Ladeleistung gegen den Hausanschluss begrenzen

> **Anweisung an den Ausführenden**: Schritt für Schritt vorgehen, jeden Prüfbefehl ausführen.
> Bei einer STOP-Bedingung anhalten und berichten. Am Ende die Statuszeile in `plans/README.md` aktualisieren.
>
> **Drift-Prüfung (zuerst)**: `git diff --stat 16c2d0a..HEAD -- custom_components/inverter_charge_night/`
> Bei Abweichungen gegen "Ist-Zustand" die Auszüge mit dem Live-Code vergleichen.

## Status

- **Priorität**: P1 (Sicherheit)
- **Aufwand**: M
- **Risiko**: MED (begrenzt reale Ströme; ein Fehler in der falschen Richtung überlastet den Anschluss)
- **Hängt ab von**: 006 (Ladeleistungsplanung)
- **Kategorie**: direction / safety
- **Geplant bei**: Commit `16c2d0a`, 2026-09-12

## Warum das wichtig ist

Im §14a-Fenster laufen die großen Verbraucher gleichzeitig: zwei Wallboxen mit 22 kW und 11 kW sind
zusammen 33 kW, dazu kommt die Akkuladung. Bei einem Hausanschluss mit 63 A (dreiphasig, rund 43 kW)
bleibt dann kaum Reserve, und anders als eine Lastspitze dauert das Fenster sechs Stunden. Diese
Dauerlast erwärmt die Kontaktstellen an Zähler, Klemmen und Sicherungen; Zählerklemmen sind die
typische Schwachstelle. Der Speicher ist der Verbraucher, den wir steuern können, also muss seine
Ladeleistung dem übrigen Haus weichen.

Nach diesem Plan begrenzt die Integration ihre Ladeleistung laufend so, dass der gesamte Netzbezug
unter einem konfigurierten Dauerlast-Budget bleibt. **Diese Grenze gilt in beiden Planer-Modi**, denn
sie ist eine Schutzfunktion und keine Optimierung.

## Ist-Zustand

- `custom_components/inverter_charge_night/__init__.py`, `_plan_charge_power(target_soc)`: berechnet
  `required_charge_power_w(...)`, klemmt auf `[CONF_MIN_CHARGE_POWER_W, CONF_MAX_CHARGE_POWER_W]`,
  deckelt optional auf das Effizienzoptimum, schreibt über `_set_ac_charge_limit_w` auf
  `CONF_CHARGE_POWER_ENTITY` — **nur im Modus `bridge`** und nicht während eines Effizienztests.
  Der Wert steht immer im Sensor `planned_charge_power`.
- `_set_ac_charge_limit_w(power_w)` erfasst den Originalwert und rechnet kW-Einheiten um.
- `CONF_CHARGE_POWER_ENTITY` ist die Steuerentität für die AC-Ladeleistung (Pflicht für dieses Feature).
- Es gibt keine Entität für den aktuellen Netzbezug und keine Anschlussgrenze.
- `PLANNED_POWER_WRITE_THRESHOLD_W` verhindert Schreibzugriffe bei kleinen Änderungen.
- Listener-Muster: `_setup_battery_soc_listener` (`async_track_state_change_event`), Aufbau im
  Fensterstart, Abbau im Fensterende und beim Entladen.

## Umfang

**Im Umfang**: `const.py`, `__init__.py`, `planner.py` (reine Rechenfunktion), `config_flow.py`,
`strings.json` + `translations/en.json`, `sensor.py` (ein neuer Sensor), Tests, README.

**Nicht im Umfang**: Steuerung der Wallboxen (die Integration regelt nur den Speicher), dynamisches
Lastmanagement über mehrere Geräte, Phasenschieflast.

## Schritte

### Schritt 1: Konfiguration

In `const.py`:
```python
CONF_GRID_IMPORT_ENTITY = "grid_import_entity"          # aktueller Netzbezug in W (oder kW)
CONF_MAIN_FUSE_A = "main_fuse_a"                        # Sicherungsgröße je Phase, A
CONF_GRID_PHASES = "grid_phases"                        # 1 oder 3
CONF_GRID_VOLTAGE_V = "grid_voltage_v"                  # Standard 230 (Strangspannung)
CONF_GRID_CONTINUOUS_PCT = "grid_continuous_pct"        # Dauerlastanteil, Standard 80
CONF_GRID_MAX_CONTINUOUS_W = "grid_max_continuous_w"    # Alternative: direkt in W
CONF_GRID_HEADROOM_W = "grid_headroom_w"                # Sicherheitsabstand, Standard 500
DEFAULT_GRID_PHASES = 3
DEFAULT_GRID_VOLTAGE_V = 230
DEFAULT_GRID_CONTINUOUS_PCT = 80
DEFAULT_GRID_HEADROOM_W = 500
GRID_LIMIT_STALE_AFTER_S = 300      # danach gilt der Netzbezug als unbekannt
GRID_LIMIT_MIN_WRITE_INTERVAL_S = 30  # Entprellung des Listeners
```
Im Assistenten in den Schritt `power`, mit Beschreibungen:
- Netzbezugs-Entität: „Momentaner Bezug aus dem Netz in W. Ohne diese Entität wird die Ladeleistung
  nicht gegen den Hausanschluss begrenzt."
- Sicherungsgröße und Phasen: „Vorsicherung des Hausanschlusses je Phase. Bei 63 A dreiphasig ergibt
  das rund 43 kW Nennleistung."
- Dauerlastanteil: „Anteil der Nennleistung, der über Stunden gezogen werden darf. 80 % lässt Reserve
  für die Erwärmung von Zähler- und Klemmkontakten. Bei älteren Zählern eher niedriger wählen."
- Maximale Dauerleistung: „Statt Sicherungsgröße direkt angeben. Hat Vorrang, wenn gesetzt."

### Schritt 2: Reine Rechenfunktion in `planner.py`

```python
def grid_budget_w(fuse_a: float | None, phases: int, voltage_v: float,
                  continuous_pct: float, explicit_max_w: float | None) -> float | None:
    """Dauerhaft zulässiger Netzbezug in W, oder None wenn nichts konfiguriert ist."""

def allowed_charge_power_w(budget_w: float, grid_import_w: float,
                           own_charge_w: float, headroom_w: float) -> float:
    """Was der Speicher zusätzlich ziehen darf.

    ``grid_import_w`` enthält die eigene Ladung bereits, daher wird sie
    herausgerechnet: der Rest ist die Last, die wir nicht steuern.
    Ergebnis ist nie negativ.
    """
```
Formeln: einphasig `voltage_v * fuse_a`, dreiphasig `3 * voltage_v * fuse_a` (entspricht
`sqrt(3) * 400 V * I` bei 230 V Strangspannung). Budget = `nennleistung * continuous_pct / 100`.
Erlaubt = `max(0, budget - headroom - (grid_import - own_charge))`.

Tests in `tests/test_planner.py`: 63 A dreiphasig bei 80 % ergibt 34 776 W; 35 A einphasig;
explizite Angabe hat Vorrang; Haus zieht mehr als das Budget ergibt 0; eigene Ladung wird
korrekt herausgerechnet; negative und unsinnige Eingaben.

### Schritt 3: Begrenzung im Coordinator

Neue Methode `_grid_limited_setpoint(planned_w: float) -> float`:
1. Budget über `grid_budget_w(...)`; ist es `None`, `planned_w` unverändert zurückgeben.
2. Netzbezug über einen Helfer lesen, der W und kW versteht (`_unit_of` nutzen). Merken als
   `self._grid_import_w` mit Zeitstempel.
3. Ist der Wert älter als `GRID_LIMIT_STALE_AFTER_S` oder nicht lesbar: auf
   `CONF_MIN_CHARGE_POWER_W` begrenzen, einmal pro Fenster warnen. Nie ungebremst weiterladen.
4. `own_charge_w` ist der zuletzt geschriebene Sollwert (`self._planned_setpoint_written_w`),
   sonst 0.
5. `allowed = allowed_charge_power_w(...)`. Ergebnis: `min(planned_w, allowed)`.
6. Liegt das Ergebnis unter `CONF_MIN_CHARGE_POWER_W`, auf 0 setzen und protokollieren: der
   Anschluss hat gerade keine Luft für den Speicher.

In `_plan_charge_power` nach der bisherigen Berechnung einsetzen, **bevor** geschrieben wird, und
die Schreibbedingung erweitern: geschrieben wird künftig auch im Modus `headroom`, sobald eine
Anschlussgrenze konfiguriert ist (die Schutzfunktion darf nicht am Planer-Modus hängen). Während
eines Effizienztests bleibt es beim bisherigen Verhalten: der Finder besitzt das Limit; dann aber
die Grenze auf den Testwert anwenden, statt ihn unbegrenzt zu lassen.

### Schritt 4: Schnelle Reaktion

Listener auf `CONF_GRID_IMPORT_ENTITY` nach dem Muster von `_setup_battery_soc_listener`, aufgebaut
im Fensterstart, abgebaut im Fensterende und beim Entladen, registriert wie die anderen. Im Callback:
Wert merken; wenn seit dem letzten Schreibvorgang mindestens `GRID_LIMIT_MIN_WRITE_INTERVAL_S`
vergangen sind und das neue Limit den aktuellen Sollwert um mehr als
`PLANNED_POWER_WRITE_THRESHOLD_W` unterschreitet, sofort nachregeln. **Nach oben** nur beim
regulären Poll, damit eine kurz abfallende Last nicht sofort wieder hochregelt.

### Schritt 5: Sichtbarkeit

Neuer Sensor `grid_charge_headroom` (W) mit dem zuletzt berechneten `allowed`-Wert und den
Attributen `budget_w`, `grid_import_w`, `other_load_w`, `limited` (bool). Damit ist im Frontend
sichtbar, ob die Anschlussgrenze gerade bremst.

### Schritt 6: Dokumentation

README: neuer Abschnitt „Hausanschluss" mit dem Rechenweg, der 63-A-Beispielrechnung, dem Hinweis
auf Dauerlast und Zählerklemmen, und der ausdrücklichen Warnung, dass die Integration nur den
Speicher regelt: Wallboxen und andere Verbraucher muss der Nutzer selbst begrenzen.

## Fertig-Kriterien

- [ ] Tests grün auf HA 2025.2.0, 2026.2.3 und 2026.9.2; `scripts/smoke_real_ha.py` grün
- [ ] mypy und pyright ohne Befund; `strings.json` und `translations/en.json` identisch
- [ ] `grep -c "grid_budget_w" custom_components/inverter_charge_night/planner.py` ≥ 1
- [ ] Ohne konfigurierte Netzbezugs-Entität ist das Verhalten unverändert (Test)
- [ ] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- Die Begrenzung würde auch dann greifen, wenn kein Fenster aktiv ist: das wäre falsch, die
  Integration steuert außerhalb des Fensters nichts.
- Ein Test zeigt, dass der Sollwert bei fehlendem Netzbezugswert steigt statt zu fallen.

## Wartungshinweise

- Die Grenze ist eine Schutzfunktion: im Zweifel weniger laden. Jede künftige Änderung an
  `_plan_charge_power` muss diese Reihenfolge erhalten (planen, dann begrenzen, dann schreiben).
- Ein echtes Lastmanagement über mehrere Geräte gehört nicht hierher; dafür gibt es in Home
  Assistant eigene Integrationen, die dann die Wallboxen regeln.
