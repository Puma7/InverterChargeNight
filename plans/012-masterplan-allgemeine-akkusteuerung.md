# 012 — Masterplan: von der §14a-Nachtladung zur allgemeinen Akkusteuerung

> Status: **Stufe 1 umgesetzt (3.1.0)**, Stufen 2–4 offen. Entscheidungen von Pascal am
> 2026-09-20: HA-Boden auf 2026.2 anheben, Ausrollen in Stufen als 3.x, Formeln als Vorlagen
> **plus** freies Feld, Sicherheitspräferenz **pro Regel**.

## Kontext

Das Plugin macht heute genau eine Sache: den Akku im günstigen Nachtfenster aus dem Netz laden,
bemessen so, dass der PV-Ertrag von morgen noch hineinpasst. Pascal will daraus eine allgemeine
Lade- und Entladesteuerung machen — Regeln mit Datums- und Zeitbereichen, Preissignale
(„lade unter 10 ct", „sperre die Entladung unter 20 ct"), Hochpreiszonen, in denen kein Netzbezug
stattfinden darf, und eigene Formeln im Frontend.

Der Auslöser sind zwei reale Anforderungen seiner Anlage:

- **Hochpreiszone 18–21 Uhr** neben dem günstigen Fenster 23–05 Uhr. Um 18 Uhr muss genug im Akku
  sein, um bis 21 Uhr durchzuhalten — sonst kauft er in der teuersten Stunde Strom, den er nachts
  für einen Bruchteil bekommen hätte.
- **Abregelung mittags**: ein Akku, der um zwölf schon voll ist, kann die Mittagsspitze nicht mehr
  aufnehmen. Die Ladung müsste über den Tag gestreckt werden.

Entschieden (20.09.): HA-Boden auf 2026.2 anheben, Ausrollen in Stufen, Formeln als **Vorlagen
plus freies Feld**, Sicherheitspräferenz **pro Regel** statt global.

## Was es schon gibt — und was davon trägt

### In Home Assistant (in der installierten Version geprüft)

| Baustein | Ab Version | Wofür |
|---|---|---|
| **Config-Subentries** (`ConfigSubentryFlow`, eigene Karte, eigener Assistent, eigener Titel) | fehlt in 2025.2.0, da in 2026.2.3 | Das „Regel hinzufügen". Vorbild in Core: `nederlandse_spoorwegen` (Routen), `bayesian`, `mqtt` |
| `async_add_entities(..., config_subentry_id=...)` | 2026.2 | Jede Regel kann eigene Entitäten haben |
| **`ObjectSelector`** mit `fields`/`multiple`/`label_field` | fehlt in 2025.2.0, da in 2026.2 | Wiederholbare Zeilen *innerhalb* einer Regel |
| **`TemplateSelector`** + `Template.async_render(variables, limited, strict)`, `ensure_valid()` | alle | Das freie Formelfeld |
| Automationen: `numeric_state`, `state`, `time`, `time_pattern`, `template` | alle | Die Regel-Engine, die HA schon hat |
| `schedule`-Helfer (grafisches Wochenraster) | alle | Kennt weder Saisons noch Preisklassen — für unseren Fall zu wenig |
| Blueprints | — | Kann eine Integration **nicht** mitliefern; HA lädt nur aus `config/blueprints/<domain>/`. Verteilung über Import-Link |

### Im Plugin

- **7 Stellgrößen**, alle mit „erst lesen und merken, dann schreiben, am Ende zurücksetzen" und
  einem `bool`-Rückgabewert, den ein fehlgeschlagener Schreibvorgang nicht belügt: Min-SOC,
  Netzladeschalter, AC-Ladelimit (`_set_ac_charge_limit_w` ist der einzige Flaschenhals und legt
  die Hausanschlussgrenze selbst an), absolutes Maximum, Entladelimit, Entladesperrschalter,
  Zwangsentladeschalter.
- **Sicherheitsverriegelungen**, die jede Regel erben muss: Notstrom/Inselbetrieb (~12 Aufrufstellen),
  Hausanschlussgrenze, Entladesperre mit monotonem Fensterboden, Min/Max-SOC-Klammer, „nie gegen
  einen unbekannten Boden entladen", Befehlsverzögerung, 30-s-Entprellung, Rücklese-Verifikation,
  Reset-Wiederholungsleiter.
- **`planner.py` (262 Zeilen, rein, ohne HA-Importe)** — der wichtigste Wiederverwendungspunkt.
  `plan_target_soc(PlanInput) -> PlanResult` rechnet schon heute eine **Untergrenze aus dem
  Hausverbrauch über ein Zeitintervall** (`integrate_load`) und löst Konflikte **preisbasiert**
  auf (`prices_ct`). Die Abendreserve ist genau dieselbe Rechnung über 18–21 Uhr.
- `_house_load_profile()` — 24 Stundenwerte, aus dem Recorder über 14 Tage gelernt, 900 s gecacht.
- `_check_current_window()` — ein Reconciler mit fester Vorrangordnung (ending > skip_next >
  Notstrom > Datumsbereich > Zeitfenster). Ein Regelsystem verallgemeinert genau das.

### Die zwei Lücken, die alles andere blockieren

1. **Das Plugin registriert null Services.** Eine Automation kann heute nur Entitäten umschalten.
   Es gibt keinen Aufruf `inverter_charge_night.charge_to(...)`. Das ist der Grund, warum sich
   „Regeln" nach eigener Engine anfühlt — es fehlt schlicht die imperative Schnittstelle.
2. **`_is_within_date_range()` ist kaputt** für Saisons: absolute Daten laufen ab, und ein Bereich
   über den Jahreswechsel wird als ungültig geloggt und dann **ganz verworfen**.

## Die architektonische Linie

> Alles, was **reagiert**, gehört in eine HA-Automation. Alles, was **vorausplant**, gehört ins Plugin.

„Lade, wenn der Preis unter 10 ct fällt" ist ein `numeric_state`-Trigger — dafür braucht es keine
Zeile Code, sobald wir einen Service anbieten. „Wie voll muss der Akku heute Nacht werden, damit
morgen die Sonne noch hineinpasst und ich abends durch die Hochpreiszone komme" kann eine
Automation prinzipiell nicht: das ist Vorausrechnung über Prognose, Verbrauchsprofil und Sonnenstand.

Daraus folgt die Aufteilung: **wir bauen keine zweite Regel-Engine.** Wir bauen (a) die imperative
Schnittstelle, damit HA-Automationen uns steuern können, und (b) den Tarifkalender als *Konfiguration*,
weil der Planer ihn im Voraus kennen muss.

## Stufen

Jede Stufe ist für sich nützlich und auslieferbar.

### Stufe 1 — Services (3.1) · Aufwand S · kein neuer HA-Boden — **UMGESETZT in 3.1.0**

**Nachtrag 20.09.:** mit 3.5.0 vollständig — `charge_to`, `block_discharge` und
`allow_discharge` sind dazugekommen, sobald das Ad-hoc-Fenster aus `plans/013` sie tragen konnte.
Offen bleiben nur `force_discharge_to` (bräuchte ein Ad-hoc-Fenster in Entladerichtung) und
`set_charge_power_limit` (schriebe ohne Fenster und damit ohne Restore).

Umgesetzt wurden zunächst die beiden Aktionen ohne eigenen Fenster-Lebenszyklus:
`plan_target_soc` (Response-Service, schreibt nichts) und `reset_inverter`. Die übrigen aus der
Tabelle (`charge_to`, `block_discharge`/`allow_discharge`, `force_discharge_to`,
`set_charge_power_limit`) brauchen einen Ad-hoc-Fensterzustand mit `until` und kommen danach.

Zwei Dinge hat erst der Smoke-Test gegen einen echten HA-Kern gezeigt, nicht der Unit-Test:
Ausnahmetexte müssen als `{"message": ...}` abgelegt sein, sonst zeigt HA dem Nutzer den
Übersetzungsschlüssel; und ein Reset im laufenden Fenster wird vom eigenen Min-SOC-Wächter
innerhalb von Millisekunden wieder überschrieben. Daraus wurde `_hands_off_until`: die Aktion
beendet das Fenster und lässt den Wechselrichter bis zum Fensterende in Ruhe.


Registriert `services.yaml` + `strings.json`-Abschnitt `services`. Alle greifen auf die
bestehenden Stellgrößen zu und erben deren Verriegelungen unverändert.

| Service | Felder | Wirkung |
|---|---|---|
| `charge_to` | `target_soc`, optional `until`, `max_power_w` | Lädt bis Ziel-SOC; endet zum Zeitpunkt oder bei Zielerreichung |
| `block_discharge` / `allow_discharge` | optional `until` | Entladesperre nach der bestehenden Dreifach-Logik |
| `force_discharge_to` | `target_soc`, optional `until` | Verlangt den Zwangsentladeschalter, sonst Fehler |
| `set_charge_power_limit` | `power_w` oder `none` | Geht durch `_set_ac_charge_limit_w`, also inklusive Hausanschlussgrenze |
| `reset_inverter` | — | Der bestehende Reset, für Automationen erreichbar |
| `plan_target_soc` | optional Überschreibungen | **Response-Service**: rechnet und gibt das Ergebnis zurück, ohne zu schreiben |

Regeln für alle: ein Aufruf im laufenden Fenster gewinnt gegen den Planer bis zu seinem `until`
(wie `min_soc_override` heute); Notstrom weist ab; jeder Aufruf, der einen Wert schreibt, benutzt
die Capture-und-Restore-Verträge. `action-exceptions` und `action-setup` in `quality_scale.yaml`
wechseln von `exempt` auf `done`.

**Das allein erfüllt schon einen großen Teil des Wunsches**: „lade bei Preis < 10 ct" ist danach
eine Standard-Automation.

### Stufe 2 — Tarifkalender (3.2) · Aufwand M · kein neuer HA-Boden

Setzt `plans/011-tarifzeitfenster-und-abendreserve.md` um, mit dem dort entworfenen Datenmodell:
Liste von Perioden mit `kind` (`cheap`/`high`), Zeitbereich über Mitternacht erlaubt, optionaler
Saison als `MM-DD` (wiederholt sich jährlich) oder `YYYY-MM-DD` (einmalig), Wochentage.

Zwei Dinge fallen dabei ab:
- Der Datumsbereich-Defekt verschwindet, weil die Jahreswechsel-Logik dieselbe wird wie beim
  Zeitfenster über Mitternacht.
- **Abendreserve**: `integrate_load(profil, 18:00, 21:00)` als neue Untergrenze in `PlanInput`.
  Hebt das Nachtladeziel und ist Boden für die Morgenentladung. Neuer Sensor
  `next_high_price_window` mit Beginn, Dauer und errechneter Reserve.

Träger in dieser Stufe bewusst **nicht** der `ObjectSelector` (der bräuchte schon 2026.2), sondern
vier zusätzliche Felder für eine zweite Periode im bestehenden Assistenten. Das deckt Pascals Fall
(ein günstiges Fenster, eine Hochpreiszone) vollständig ab, hält den Boden bei 2025.2 und macht
den Bruch erst dort, wo er wirklich nötig ist — in Stufe 4. Das Datenmodell wird aber schon hier
als Liste angelegt, damit Stufe 4 nur die Oberfläche tauscht und keine Migration braucht.

### Stufe 3 — Preissignal (3.3) · Aufwand M · kein neuer HA-Boden

Backlog 013. Eine Preisentität (Tibber/aWATTar/EPEX) als Eingang, plus optional deren
`forecast`-Attribut für die nächsten 24 h. Wirkt an drei Stellen:

- Die `prices_ct` im Planer werden dynamisch statt fest konfiguriert.
- Der Tarifkalender bekommt eine dritte Periodenart: `cheap`/`high` nicht nach Uhrzeit, sondern
  nach Schwellwert.
- Die Morgenentladung bekommt endlich ein Kriterium statt „Modus von Hand umschalten".

### Stufe 4 — Regeln als Subentries (4.0) · Aufwand L · **hebt den HA-Boden auf 2026.2**

Erst hier wird es Version 4, weil das Anheben des Bodens für Nutzer unter 2026.2 ein Bruch ist.

Jede Regel = ein `ConfigSubentry` mit eigenem Titel, eigener Karte, eigenem Assistenten und
optional eigenem Sensor („was tut diese Regel gerade"). Felder je Regel:

- **Gültigkeit**: Saison (`MM-DD`), Wochentage, Zeitbereich
- **Bedingung**: keine / Preisschwelle / Entität unter-über / freies Template
- **Wirkung**: Ziel-SOC mindestens / höchstens / Entladesperre / Ladeleistungsdeckel
- **Formel**: `SelectSelector` mit **Vorlagen je Anwendungsfall**, plus optionales
  `TemplateSelector`-Feld als Ausweg
- **Priorität** (Pascals Entscheidung: pro Regel, nicht global) und **Verhalten im Fehlerfall**
  (vorsichtig = eher laden / sparsam = eher nicht)

Der Reconciler verallgemeinert `_check_current_window()`: alle passenden Regeln sammeln, nach
Priorität ordnen, Untergrenzen maximieren und Obergrenzen minimieren, Ergebnis in die bestehende
Min/Max-Klammer. Sicherheitsverriegelungen stehen **über** jeder Regel und sind nicht abwählbar.

**Formelsicherheit** — eine Formel, die um 3 Uhr wirft, darf nie den Akku entscheiden:
`ensure_valid()` beim Speichern; Rendern mit `limited=True, strict=True`; jede Ausnahme →
letzter guter Wert, sonst Vorlage, sonst `DEFAULT_SAFE_FALLBACK_SOC`; Reparatur-Hinweis nach der
zweiten Störung; Ergebnis immer durch `_clamp(user_min, user_max)`.

### Vorarbeit, die vor Stufe 4 fällig ist

`coordinator.py` hat 4408 Zeilen und fünf getrennte Min-SOC-Schreiber ohne gemeinsamen Helfer. Ein
Regelaufsatz darauf wird unwartbar. Die Schnitte stehen schon im Backlog: Limits, Effizienzsuche,
Zeitplan, Persistenz. **Mindestens** die fünf Min-SOC-Schreiber auf einen Pfad zusammenziehen.

## Was ich nicht bauen würde

- **Keine eigene Trigger-Engine.** Zeit-, Zustands- und Schwellwert-Trigger hat HA. Wir liefern
  Services und Sensoren, die Automationen benutzen — und Blueprints per Import-Link für die
  typischen Rezepte.
- **Kein globaler Sicherheitsregler** — Pascal hat sich für die Entscheidung pro Regel entschieden.
- **Kein freies Formelfeld ohne Vorlage dahinter.** Das Feld bleibt der Ausweg, nicht der Normalfall.
- **Kein `schedule`-Helfer als Träger** des Tarifkalenders: sein Raster kennt keine Saisons und
  keine Preisklassen.
- **PV-Ladung strecken (Backlog 017) nicht in diesen Plan.** Es braucht einen Tagesbetrieb statt
  eines Nachtfensters, die Prognose für *heute* als Pflichtfeld, und ein Register, das ohne
  Nachschreiben zurückfällt. Eigener Plan, nach Stufe 3.

## Zu ändernde Dateien

| Stufe | Dateien |
|---|---|
| 1 | neu `services.yaml`; `__init__.py` (Registrierung in `async_setup_entry`); neu `services.py`; `strings.json` + beide Übersetzungen (`services`-Block); `quality_scale.yaml` |
| 2 | `const.py` (Periodenschlüssel, Legacy-Tabelle), `config_flow.py` (`_schema_time_soc`, `_validate_user_input`), `coordinator.py` (`_is_within_date_range`, `_window_times`), `planner.py` (`PlanInput.reserve_periods`), `sensor.py` |
| 3 | `const.py`, `config_flow.py`, `coordinator.py` (Preis-Lesepfad), `planner.py` (`prices_ct` dynamisch) |
| 4 | neu `rules.py` + `subentry_flow.py`; `config_flow.py` (`async_get_supported_subentry_types`), `hacs.json` + `.github/workflows/ci.yml` (Boden 2026.2), `strings.json` (`config_subentries`-Block) |

Wiederverwenden statt neu bauen: `planner.integrate_load`, `planner.plan_target_soc`,
`coordinator._house_load_profile`, `coordinator._set_ac_charge_limit_w`, `_is_backup_active`,
`_grid_limited_setpoint`, das Migrationsmuster aus `_migrate_entry_data` samt Tabellen in `const.py`.

## Prüfung

Pro Stufe, bevor sie ausgeliefert wird:

1. `pytest` — Coverage-Ratchet steht bei 96 %, darf nur steigen.
2. `mypy --python-version <matrix>` und `pyright --pythonversion <matrix>`, je 0 Fehler.
3. `python scripts/smoke_real_ha.py` gegen echtes HA-Core. **Stufe 1 erweitert ihn**: einen Service
   aufrufen und prüfen, dass der Wechselrichter den Wert bekommt. **Stufe 4 erweitert ihn**: einen
   Subentry anlegen, seine Entität prüfen, ihn wieder entfernen.
4. Die vier Strukturtests in `test_config_flow.py` (Übersetzungsgleichheit, jedes Feld genau einem
   Schritt, jeder Fehlerschlüssel übersetzt) auf `services` und `config_subentries` ausweiten.
5. Migration: eine Alt-Konfiguration ohne Perioden muss nach Stufe 2 unverändert weiterlaufen —
   im Smoke-Test, wie schon beim `kostal_*`-Umbenennen.

## Offene Fragen

1. **Abendreserve reicht nicht** (aus Plan 011, weiter offen): Akku um 18 Uhr leerer als geplant.
   Teuer beziehen oder Entladesperre aufheben und bis zum Nutzer-Minimum ziehen? Mit der
   Entscheidung „pro Regel" wird das ein Feld je Hochpreis-Regel — Vorgabe fehlt noch.
2. **Preis pro Periode oder globale Klassen?** Die drei Preisfelder existieren schon.
3. **Welche Formelvorlagen** sollen mitkommen? Mein Vorschlag: Nachtladung-Freiraum,
   Nachtladung-Überbrückung, Abendreserve, Preisschwelle.
