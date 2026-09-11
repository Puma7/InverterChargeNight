# Plan 006: Planer v2 (Design und Prototyp): Morgenüberbrückung, Sonnenaufgang, Verbrauch, Entladesperre, Ladeleistung

> **Anweisung an den Ausführenden**: Dies ist ein Design- und Prototyp-Plan. Ergebnis sind (a) ein reines
> Berechnungsmodul mit Tests, (b) eine Entladesperre im Coordinator, (c) eine Entscheidungsvorlage für den
> Eigentümer zu den offenen Fragen. Die Zielformel wird **hinter einer Option** eingeführt; das heutige
> Verhalten bleibt Standard, bis der Eigentümer umschaltet. Bei einer STOP-Bedingung anhalten und berichten.
>
> **Drift-Prüfung (zuerst ausführen)**:
> `git diff --stat 549a5ee..HEAD -- custom_components/inverter_charge_night/calculation.py custom_components/inverter_charge_night/__init__.py custom_components/inverter_charge_night/const.py`
> Änderungen aus 002/004/005 sind erwartet. Die Auszüge unten gegen den Live-Code prüfen, bei Widerspruch STOP.

## Status

- **Priorität**: P2
- **Aufwand**: L
- **Risiko**: MED (steuert reale Netzbezüge; deshalb Option und Prototyp statt Umschaltung)
- **Hängt ab von**: 001–005
- **Kategorie**: direction
- **Geplant bei**: Commit `549a5ee`, 2026-09-11

## Warum das wichtig ist

Die heutige Zielformel `Ziel = (Kapazität − Forecast·(1+Marge)) / Kapazität` beantwortet nur die Frage
"wie viel Platz braucht der Speicher morgen", nicht die Frage "wie viel Energie braucht das Haus, bis die PV
den Verbrauch übernimmt". Sie kennt weder Verbrauch, noch Sonnenaufgang, noch Jahreszeit. An sonnigen Tagen
lädt sie auf das Minimum und das Haus kauft ab Fensterende zum Tagestarif; im Winter trifft sie 100 % nur, weil
der Forecast klein ist. Außerdem wird im Fenster die Entladung des Speichers nicht gesperrt, sodass gespeicherte
PV nachts "verbraucht" wird, obwohl Netzstrom gerade günstig ist. Und die Ladeleistung wird nicht auf die
Fensterlänge geplant.

Nach diesem Plan gibt es ein Berechnungsmodul, das aus Forecast, Verbrauchsprofil, Sonnenaufgang, Speicherdaten
und Fensterzeiten ein Ziel und eine Ladeleistung ableitet, sowie eine Entladesperre mit sauberem Reset.

## Ist-Zustand

- `custom_components/inverter_charge_night/calculation.py:8-14, 73-94`: `calculate_required_soc(forecast_energy, battery_capacity, error_margin, user_min_soc, user_max_soc)`; reine Funktion, 100 % getestet in `tests/test_calculation.py`.
- Aufrufer: `__init__.py:1014-1020` (`_calculate_initial_soc`) und `:1200-1207` (`_async_update_data`, Fallback).
- Forecast-Auswahl `__init__.py:255-275`: `if now.hour < 12: heute-Entität sonst morgen-Entität`.
- Sonnenstand: kein `sun.sun`, kein `astral` im Paket. HA stellt `sun.sun` mit Attributen `next_rising`,
  `next_setting` (ISO-Zeitstempel) bereit; `hass.config.latitude/longitude` sind ebenfalls verfügbar.
- Verbrauch: keine Verbrauchsentität. Die Release-Linie `5c31112` hatte `CONF_GRID_IMPORT_ENERGY_ENTITY`,
  `CONF_BATTERY_CHARGE_ENERGY_ENTITY`, `CONF_HOME_CONSUMPTION_ENERGY_ENTITY` (kumulative kWh-Zähler) und las sie in
  `auto_efficiency.py::_read_energy_kwh` (`git show 5c31112:custom_components/inverter_charge_night/auto_efficiency.py`, Zeile 155).
  HA bietet außerdem `homeassistant.components.recorder.statistics.statistics_during_period` für Stundenwerte eines Energiezählers.
- Entladung: `_control_kostal` (`__init__.py:1467-1479`) setzt nur den Min-SOC; bei `current_soc >= target_soc`
  (Zeile 1380–1386) wird nur das Laden übersprungen, der Speicher entlädt bis zum Ziel.
  Vorbild für Erfassen/Setzen/Zurücksetzen einer Leistungsgrenze: `_apply_absolute_charge_power_limit` /
  `_reset_absolute_charge_power` (`__init__.py:487-551`).
- Ladeleistung: `_select_next_auto_test_power_w` (`__init__.py:569-607`) sucht das Effizienzoptimum über
  `[min_charge_power_w, max_charge_power_w]`; `_window_times()` (Zeile 381) liefert Start/Ende, die Fensterlänge wird nirgends berechnet.
- Preise: keine Keys; das Zielbild in `plans/README.md` Abschnitt 3 beschreibt die Kostenlogik.
- Kostal Plenticore (Eigentümer-Setup): Entladung lässt sich über die Modbus-Register für die Batterie-Betriebsart
  bzw. eine Entladeleistungsgrenze sperren; welche Entität die Kostal-Integration des Eigentümers dafür anbietet, ist
  **offen** (siehe Fragen).

## Benötigte Befehle

| Zweck | Befehl | Erwartung |
|---|---|---|
| Tests | `pytest` | grün, Ratchet erfüllt |
| Typen | `mypy custom_components/inverter_charge_night/` / `pyright` | 0 Fehler |

## Umfang

**Im Umfang**:
- Neu: `custom_components/inverter_charge_night/planner.py` (reine Funktionen, keine HA-Importe außer Typen)
- `custom_components/inverter_charge_night/calculation.py` bleibt, wird von `planner.py` aufgerufen
- `custom_components/inverter_charge_night/const.py` (neue Keys, siehe Schritt 1)
- `custom_components/inverter_charge_night/__init__.py` (Forecast-Tagwahl, Aufruf des Planers hinter Option, Entladesperre, Ladeleistung)
- `custom_components/inverter_charge_night/config_flow.py`, `strings.json`, `translations/en.json` (neue Felder in den Schritten `power` und `advanced`)
- Tests: neu `tests/test_planner.py`, Ergänzungen in `tests/test_window_lifecycle.py`
- `plans/006-DECISIONS.md` (neu; Fragen an den Eigentümer mit Empfehlung)

**Nicht im Umfang**:
- Zeitplanmodell mit mehreren Fenstern (Backlog 008) und Preissignal (Backlog 011).
- Wechselrichter-Profile (Backlog 009): die Entladesperre wird als eine weitere optionale Entität umgesetzt, nicht als Abstraktion.
- Umbau des Morning-Discharge-Modus (Backlog 010).

## Git-Vorgehen

- Branch: `feat/006-planner-v2` von `develop`
- Commits: `feat: …`, `test: …`, `docs: …`

## Schritte

### Schritt 1: Neue Konfigurations-Keys

In `const.py`:
```python
CONF_PLANNER_MODE = "planner_mode"                     # "headroom" (heute) | "bridge" (neu)
CONF_HOUSE_LOAD_ENTITY = "house_load_entity"           # kumulativer kWh-Zähler Hausverbrauch (optional)
CONF_AVG_HOUSE_LOAD_KW = "avg_house_load_kw"           # Fallback, wenn kein Zähler: mittlere Last in kW
CONF_PV_CROSSOVER_DELAY_MIN = "pv_crossover_delay_min" # Minuten nach Sonnenaufgang bis PV > Verbrauch
CONF_BRIDGE_RESERVE_KWH = "bridge_reserve_kwh"         # Sicherheitsreserve
CONF_CHARGE_EFFICIENCY = "charge_efficiency"           # 0.80–1.0, Standard 0.90
CONF_DISCHARGE_LIMIT_ENTITY = "discharge_limit_entity" # number: Entladeleistungsgrenze (W), optional
CONF_FEED_IN_PRICE_CT = "feed_in_price_ct"             # optional
CONF_NIGHT_PRICE_CT = "night_price_ct"                 # optional
CONF_DAY_PRICE_CT = "day_price_ct"                     # optional
DEFAULT_PLANNER_MODE = "headroom"
DEFAULT_PV_CROSSOVER_DELAY_MIN = 90
DEFAULT_CHARGE_EFFICIENCY = 0.90
```
Im Config-Flow: `planner_mode` (SelectSelector, Schritt `time_soc`), Verbrauchs-/Preisfelder (Schritt `advanced`),
`discharge_limit_entity` (Schritt `power`). Texte in `strings.json`, `en.json` neu kopieren.

**Prüfen**: `pytest tests/test_config_flow.py -q` → grün; Key-Vergleich `strings.json` ↔ `en.json` → `[]`.

### Schritt 2: `planner.py` als reine Funktion

```python
@dataclass(frozen=True)
class PlanInput:
    capacity_kwh: float
    current_soc: float            # %
    user_min_soc: float
    user_max_soc: float
    forecast_kwh_next_day: float
    forecast_available: bool
    error_margin_pct: float
    window_end: datetime
    pv_crossover: datetime        # Sonnenaufgang + Verzögerung, am Tag nach window_end
    sunset: datetime
    house_load_kw_profile: Sequence[float]  # 24 Werte, Stunde des Tages; Fallback: 24 × avg
    reserve_kwh: float
    charge_efficiency: float
    prices_ct: tuple[float, float, float] | None  # (nacht, tag, einspeisung)

@dataclass(frozen=True)
class PlanResult:
    target_soc: float
    bridge_kwh: float
    surplus_kwh: float
    lower_bound_soc: float
    upper_bound_soc: float
    reason: str                    # "bridge" | "headroom" | "conflict_bridge_wins" | "conflict_headroom_wins" | "fallback"

def plan_target_soc(p: PlanInput) -> PlanResult: ...
def required_charge_power_w(target_soc: float, current_soc: float, capacity_kwh: float,
                            hours_remaining: float, efficiency: float) -> float: ...
def integrate_load(profile: Sequence[float], start: datetime, end: datetime) -> float: ...
```
Rechenweg (aus `plans/README.md` Abschnitt 3):
- `bridge = integrate_load(profile, window_end, pv_crossover) + reserve`
- `daytime_load = integrate_load(profile, pv_crossover, sunset)`
- `surplus = max(0, forecast·(1+margin/100) − daytime_load)`
- `lower = (bridge + capacity·user_min/100) / capacity·100`, geklemmt auf `[user_min, user_max]`
- `upper = (1 − max(0, surplus − bridge)/capacity)·100`, geklemmt
- `lower ≤ upper` → `target = lower` (nicht mehr kaufen als nötig), `reason="bridge"`
- `lower > upper` → ohne Preise `target = lower`, `reason="conflict_bridge_wins"`; mit Preisen: Vergleich
  `(lower − upper)/100·capacity·(nacht − einspeisung)` gegen `bridge_kwh_ungedeckt·(tag − nacht)`; die günstigere Seite gewinnt.
- `forecast_available == False` → `DEFAULT_SAFE_FALLBACK_SOC`-Logik wie heute, `reason="fallback"`.
- `required_charge_power_w = max(0, (target − current)/100 · capacity · 1000 / hours_remaining / efficiency)`.

Tests in `tests/test_planner.py` (mindestens): Wintertag (Forecast 2 kWh, lange Überbrückung → Ziel nahe max),
Sommertag (Forecast 40 kWh, 10 kWh Speicher → Ziel = Überbrückung, nicht Minimum), Konflikt ohne Preise,
Konflikt mit Preisen (beide Ausgänge), Fallback, Leistungsformel (2 kWh in 4 h bei 0,9 → 556 W), Profilintegration über Mitternacht.

**Prüfen**: `pytest tests/test_planner.py -q` → grün; `mypy`/`pyright` → 0 Fehler (`planner.py` ist nicht ausgenommen).

### Schritt 3: Eingänge im Coordinator sammeln

Neue Methoden: `_sun_times()` liest `sun.sun` (`next_rising`, `next_setting`, per `dt_util.parse_datetime`),
Fallback bei fehlender Entität: Sonnenaufgang = `window_end + 2 h`, geloggt als Warnung.
`_house_load_profile()` liefert 24 Werte: aus `avg_house_load_kw` (flach) oder, wenn `house_load_entity` gesetzt,
aus `statistics_during_period` der letzten 14 Tage (stündliche `sum`-Differenzen, Mittel je Stunde des Tages);
Recorder-Abfragen laufen über `await get_instance(hass).async_add_executor_job(...)`. Ergebnis 15 Minuten cachen.
`_get_active_forecast_entity` bestimmt den Solartag als **Kalendertag des Fensterendes** statt `now.hour < 12`.

**Prüfen**: Tests mit gemocktem `sun.sun` und gemockter Statistikfunktion; `pytest tests/test_discharge_forecast.py -q` (Tagwahl) → angepasst und grün.

### Schritt 4: Planer hinter Option einhängen

In `_calculate_initial_soc` und im Fallback von `_async_update_data`: `if planner_mode == "bridge":` →
`plan_target_soc(...)`, sonst wie heute `calculate_required_soc`. `PlanResult` in `self.last_plan` ablegen
und als Attribute des Sensors `calculated_soc` ausgeben (`bridge_kwh`, `surplus_kwh`, `reason`, `pv_crossover`).
Neuberechnung **bei jedem Poll im Fenster**, Ziel darf nur steigen: `new_target = max(previous_target, plan.target_soc)`
(Rückgang unter bereits Geladenes würde Energie verschenken); `current_target_soc()` aus Plan 004 bleibt die einzige Zielquelle.

**Prüfen**: `tests/test_window_lifecycle.py`: Forecast steigt im Fenster → Ziel steigt; Forecast fällt → Ziel bleibt.

### Schritt 5: Entladesperre

Wenn `discharge_limit_entity` konfiguriert: beim Fensterstart Live-Wert erfassen (`_original_discharge_limit`,
in `runtime_state` persistieren, Plan 005), auf `0` setzen; in `_reset_settings` zurücksetzen; in der periodischen
Verifikation prüfen und neu setzen; unter Backup-Modus nie setzen. Ohne die Entität: Verhalten wie heute, aber
ein `_LOGGER.info` beim Fensterstart, dass die Entladesperre nicht konfiguriert ist.

**Prüfen**: Tests: Setzen beim Start, Zurücksetzen am Ende, Zurücksetzen beim Entladen der Integration, kein Setzen im Backup-Modus.

### Schritt 6: Ladeleistung nach Fenster

Jeder Poll im Fenster (Nachtlademodus, nicht während eines Effizienztests): `required = required_charge_power_w(...)`,
`setpoint = clamp(required, min_charge_power_w, max_charge_power_w)`; liegt ein Effizienzoptimum vor
(`get_auto_efficiency_data()["best_power_w"]`) und `required ≤ best`, dann `best`. Nur schreiben, wenn sich der
Sollwert um mehr als 100 W ändert (Schreibschonung). Neuer Sensor `planned_charge_power` (W) in `sensor.py`.

**Prüfen**: Test: 4 h verbleibend, 2 kWh fehlen, Effizienzoptimum 3000 W → Sollwert 556 W wird geschrieben; 30 min verbleibend, 5 kWh fehlen → `max_charge_power_w`.

### Schritt 7: Entscheidungsvorlage

`plans/006-DECISIONS.md` mit folgenden Fragen, je mit Empfehlung:
1. Welche Entität sperrt beim Kostal Plenticore die Entladung (Entladeleistungsgrenze oder Betriebsart)? Empfehlung: Leistungsgrenze, weil sie zum Erfassen/Zurücksetzen passt.
2. Verbrauchsquelle: Hausverbrauchszähler (empfohlen) oder fester Mittelwert?
3. PV-Kreuzung: fester Versatz nach Sonnenaufgang (Start 90 min) oder aus Zählern gelernt (später)?
4. Preise als feste Werte eintragen (empfohlen als Start) oder Preissensor (Backlog 011)?
5. Morning-Discharge: behalten als Tarif-Funktion oder ersetzen (Backlog 010)?

## Testplan

- `tests/test_planner.py`: die sieben Fälle aus Schritt 2 plus Grenzfälle (Kapazität 0 → Fehler, Fensterende vor Sonnenaufgang am selben Tag).
- `tests/test_window_lifecycle.py`: Planer-Option aktiv, Entladesperre, Ladeleistung, monotones Ziel.
- Muster: `tests/test_calculation.py` (reine Funktionen), `tests/test_charge_limit_helpers.py` (Leistungsgrenzen).

## Fertig-Kriterien

- [ ] `pytest`, `mypy`, `pyright` grün; `fail_under` gestiegen
- [ ] `custom_components/inverter_charge_night/planner.py` existiert, keine HA-Laufzeitimporte
- [ ] `grep -c "now.hour < 12" custom_components/inverter_charge_night/__init__.py` = 0
- [ ] `grep -c "sun.sun" custom_components/inverter_charge_night/__init__.py` ≥ 1
- [ ] Standardmodus bleibt `headroom`: bestehende Tests der Zielberechnung unverändert grün
- [ ] `plans/006-DECISIONS.md` existiert
- [ ] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- Drift an den genannten Stellen.
- `statistics_during_period` ist in der Test-HA-Version anders signiert (dann Signatur berichten und Schritt 3 mit festem Mittelwert abschließen).
- Die Kostal-Integration des Eigentümers bietet keine Entität für Entladeleistung/Betriebsart (Frage 1): Schritt 5 dann als "nicht konfiguriert" abschließen und berichten.
- Ein Plan-Ergebnis würde bei realistischen Eingaben (Beispiele aus den Tests) ein Ziel unter `user_min_soc` oder über `user_max_soc` liefern: Klemmung prüfen, nicht weiterbauen.

## Wartungshinweise

- `planner.py` bleibt frei von HA-Imports, damit es als reine Funktion testbar ist; alle Zustandslesungen leben im Coordinator.
- Backlog 008 (Zeitplan) ändert nur, woher `window_end` kommt; Backlog 011 (Preise) ersetzt `prices_ct` durch Zeitreihen.
- Beim Wechsel des Standardmodus auf `bridge` (nach Erprobung beim Eigentümer) `DEFAULT_PLANNER_MODE` ändern und `CHANGELOG.md` als Breaking Change dokumentieren.
