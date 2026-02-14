# Feedback zu custom_components/inverter_charge_night

Hier ist eine Analyse des Codes basierend auf Best Practices für Home Assistant Integrationen und allgemeiner Softwarequalität.

## 1. Architektur & Struktur

### Monolithischer Coordinator
Die Klasse `InverterChargeNightCoordinator` in `__init__.py` ist sehr groß und übernimmt zu viele Aufgaben (Gott-Klasse). Sie kümmert sich um:
- Konfigurations-Management
- Zeit-Fenster-Logik
- Berechnung des SoC (`calculate_required_soc` ist ausgelagert, aber der Aufruf ist hier)
- Steuerung der Kostal-Inverter (Side-Effects)
- "Auto Efficient Charge"-Algorithmus (komplexe Optimierungslogik)
- State-Tracking (Backup-Modus, PV-Forecast, Batterie-Status)

**Empfehlung:**
- Den "Auto Efficient Charge"-Algorithmus in eine eigene Klasse auslagern (z.B. `EfficiencyOptimizer`), die vom Coordinator genutzt wird.
- Die Inverter-Steuerung (Kostal APIs) in eine Service-Schicht oder Helper-Funktionen abstrahieren, um die Business-Logik von den HA-Service-Calls zu trennen.

### Side-Effects in `_async_update_data`
Die Methode `_async_update_data` sollte idealerweise nur Daten abrufen und den internen State aktualisieren. In diesem Code führt sie jedoch direkte Steuerbefehle aus (`_control_kostal`, `_stop_grid_charging`, `_handle_auto_charge`).
- Das macht das Debuggen schwer: Ein Daten-Update löst Aktionen aus.
- Wenn das Update fehlschlägt, ist der Zustand der Steuerung unklar.

**Empfehlung:**
- Trennung von "Status beobachten" und "Aktion ausführen". Die Aktionen könnten explizit durch State-Change-Listener (die bereits teilweise vorhanden sind) oder einen dedizierten Control-Loop getriggert werden, statt implizit beim Polling-Update.

## 2. Code Qualität & Stil

### Hardcodierte Werte & Magic Numbers
Es gibt diverse hardcodierte Werte:
- `0.5` Toleranz für SoC-Abweichungen.
- `180` Sekunden Retry-Zeit.
- `0.1` Sekunden Command-Delay.
- `100` Watt Steps für die Optimierung.
- `1800` Sekunden Mindestdauer für Effizienz-Tests.

**Empfehlung:**
- Diese Werte als Konstanten in `const.py` definieren. Das macht den Code lesbarer und wartbarer.

### Komplexität der Auto-Charging Logik
Der Algorithmus in `_select_next_auto_test_power_w` (Golden Section Search?) ist schwer zu lesen und direkt in den Coordinator integriert.
- Die Persistenz via `entry.options` ist gut, aber die Logik vermischt sich stark mit dem Rest.

**Empfehlung:**
- Kapselung der Optimierungslogik. Der Coordinator sollte nur fragen: "Welche Power testen wir als nächstes?" oder "Hier ist das Ergebnis des letzten Tests".

## 3. Fehlerbehandlung & Robustheit

### Breite Exception-Handler
Oft wird `except Exception as e:` verwendet (z.B. in `_control_kostal`, `_reset_settings`).
- Das fängt *alles* ab, was gut für die Stabilität von HA ist (die Integration stürzt nicht ab), kann aber eigentliche Programmierfehler verschleiern.

**Empfehlung:**
- Spezifischere Exceptions fangen wo möglich (`ValueError`, `ServiceValidationError` etc.).
- Sicherstellen, dass kritische Fehler nicht nur geloggt, sondern ggf. auch den Status der Entities auf "unavailable" setzen oder eine Repair-Issue in HA erstellen, wenn etwas dauerhaft kaputt ist.

### Unload & Reset
In `async_unload_entry` wird versucht, Settings zurückzusetzen (`_reset_settings`).
- Das ist eine Netzwerkkoperation. Während des Shutdowns von HA kann es sein, dass dies nicht mehr rechtzeitig durchgeht oder abgebrochen wird.
- Es ist löblich, dass es versucht wird, aber man sollte sich nicht zu 100% darauf verlassen.

## 4. Spezifische Bugs & Risiken

### Concurrent Tasks
`self.hass.async_create_task(self._check_current_window())` wird in `update_time_triggers` aufgerufen.
- Es wird keine Referenz auf den Task gehalten. Wenn die Integration entladen wird, könnte dieser Task theoretisch weiterlaufen (obwohl `unload` meistens sauber ist).
- `_verification_task` wird sauber gehalten. Gute Praxis wäre, alle Tasks so zu tracken.

### State-Flapping Risiko
In `_control_kostal` wird geprüft: `if abs(min_soc_current_value - target_soc) > 0.5`.
- Wenn der User manuell am Inverter etwas ändert, kämpft die Integration dagegen an. Das Listening (`_setup_inverter_min_soc_listener`) erkennt das und steuert gegen.
- **Risiko:** Wenn der Inverter langsam reagiert oder Werte springen, könnte das zu einer Endlosschleife von "Setzen -> Lesen -> Ungleich -> Setzen" führen (Ping-Pong).
- Die Integration hat zwar Checks (`_last_soc_set`), aber eine "Cool-down" Periode nach einem Setzen wäre sicherer.

## 5. Production Readiness Assessment (Strict Audit)

Ist der Code **100% ready for production**?
**Nein, Score: 80%**

### Letzte Detail-Prüfung (Final Audit):
Der Code hat noch "tote" Stellen und Inkonsistenzen, die auf eine unsaubere Entwicklung hindeuten:
1.  **Ungenutzte Konstanten**: In `const.py` sind Konstanten definiert (`MIN_SOC_TOLERANCE = 0.5`, `AUTO_EFFICIENCY_STEP_W = 100`, etc.), aber im Code (`__init__.py`) werden knallhart die Zahlen `0.5` und `100` hardcodiert verwendet. Die Konstanten sind also "Dead Code".
    - `__init__.py:1308`: `abs(... > 0.5)` statt `MIN_SOC_TOLERANCE`
    - `__init__.py:429`: `step_w = 100` statt `AUTO_EFFICIENCY_STEP_W`
2.  **Architektur-Schwäche**: Die Architektur ist monolithisch (`Coordinator` macht alles) und vermischt Polling mit Steuerung. Das ist funktional okay, aber schwer zu testen und zu warten.
3.  **Race-Conditions**: Der "Ping-Pong" Schutz verlässt sich rein auf den schnellen Wert-Vergleich. Bei einem trägen Inverter-API könnte das System ins Schwingen geraten.

**Fazit**: Der Code funktioniert vermutlich für den "Happy Path", aber er ist **nicht** sauber refactored ("Clean Code") und enthält Flüchtigkeitsfehler (unused constants). Für Production-Level (Open Source Release, Zertifizierung) müsste er bereinigt werden.

### Action Plan für 100%:
1.  [Major] Alle Hardcoded Values in `__init__.py` durch die bereits vorhandenen Konstanten ersetzen.
2.  [Major] Refactoring des `Coordinators`: Auslagern von `EfficiencyOptimizer`.
3.  [Medium] Implementierung von `MIN_SOC_RESTORE_COOLDOWN_S` (existiert in `const.py`, wird nicht genutzt), um Ping-Pong Effekte zu verhindern.
