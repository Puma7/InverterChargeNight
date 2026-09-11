# Masterplan: Inverter Charge Night

Erstellt am 2026-09-11 gegen Commit `549a5ee` (Branch `fix/code-review-followups`).
Grundlage: vollständige Lektüre des Coordinators (`custom_components/inverter_charge_night/__init__.py`),
von `calculation.py`, `config_flow.py`, aller Plattformdateien, der Übersetzungen, der Git-Historie
sowie vier unabhängige Audit-Durchgänge (Korrektheit, Tests/Architektur, Fachliche Richtung §14a, Konfigurations-UX).

Dieses Dokument beantwortet drei Fragen:

1. Was ist heute sauber umgesetzt, was nicht?
2. Was fehlt, damit die Integration das §14a-Ladefenster wirklich optimal nutzt?
3. In welcher Reihenfolge wird das umgesetzt? (Ausführbare Pläne in `plans/001-*.md` bis `plans/006-*.md`)

---

## 1. Zielbild (worum es geht)

Ein Haushalt mit Hybrid-Wechselrichter und Heimspeicher hat vom Netzbetreiber ein zeitvariables
Netzentgelt nach §14a EnWG (Modul 3), z. B. Avacon: 23:00 bis 05:00 Uhr in Q4 und Q1 zu ca. 1 ct/kWh
Netzentgelt, also ca. 13 bis 15 ct/kWh Gesamtpreis. Die Integration soll den Wechselrichter so steuern, dass:

- **im Fenster** das Haus aus dem Netz versorgt wird (Speicher nicht entladen) und der Speicher aus dem Netz
  auf ein geplantes Ziel geladen wird,
- **das Ziel** so gewählt ist, dass der Speicher die Zeit von Fensterende bis zum Zeitpunkt überbrückt, an dem
  die PV-Erzeugung den Hausverbrauch übersteigt (abhängig von Jahreszeit und Sonnenaufgang),
- **gleichzeitig** genug Platz im Speicher bleibt, um den PV-Überschuss des nächsten Tages aufzunehmen,
  damit keine Sonnenenergie zum Einspeisetarif verschenkt wird,
- das Ganze **wechselrichterunabhängig** über generische Home-Assistant-Entitäten funktioniert.

Ökonomisch ist das eine einfache Abwägung pro kWh Nachtladung:

| Die nachts geladene kWh ersetzt … | Gewinn/Verlust (Beispielpreise) |
|---|---|
| Netzbezug am Tag (ca. 30 ct) | +16 ct |
| eigene PV-Energie, die dadurch eingespeist wird (ca. 8 ct) | −6 ct |
| nichts (Speicher voll, Überschuss wird abgeregelt) | −14 ct |

Der Planer muss also genau so viel laden, wie tagsüber sonst aus dem Netz käme, und nicht mehr.

---

## 2. Bewertung des Ist-Zustands

### 2.1 Sauber umgesetzt (behalten)

- **Grundmechanik Kostal**: Min-SOC des Wechselrichters auf das Ziel setzen und Netzladung einschalten,
  am Fensterende zurücksetzen. Das nutzt die Plenticore-Semantik korrekt (Netzladung lädt bis Min-SOC,
  Entladung unter Min-SOC ist gesperrt). `__init__.py:1347-1517`, `:1082-1155`.
- **Ziel-erreicht-Erkennung** auf drei Wegen: Polling, State-Listener auf dem Batterie-SOC, periodische
  Verifikation. `__init__.py:1319-1335`, `:1703-1760`, `:1855-1926`.
- **Schutzmechanismen**: Backup-/Inselbetrieb stoppt alles, Datumsbereich, "Skip next" für 24 h,
  sicherer Fallback auf 50 % bei fehlendem Forecast, EEPROM-Schonung durch 0,5-%-Totband,
  Reset beim Entladen der Integration. `__init__.py:428-441`, `:1024-1042`, `:153-173`.
- **Forecast-Parsing** robust für Solcast-Zustand und -Attribute, Einheiten Wh/kWh/MWh (seit PR #2).
- **Diagnostik** mit Redaktion der Entitäts-IDs, Effizienzsuche mit Persistenz in den Entry-Optionen.
- **Werkzeuge**: pytest, mypy strict, pyright strict, 100 % Abdeckung für die Plattformdateien.

### 2.2 Nicht voll funktionsfähig (Fehler, mit Nachweis)

Alle Punkte wurden im Code nachgelesen, nicht nur vom Audit gemeldet.

| # | Befund | Nachweis | Wirkung | Plan |
|---|---|---|---|---|
| F1 | **Der Merge `1ed4376` hat die Härtung der Release-1.0.3-Linie verworfen**: 4-Schritt-Konfigurationsassistent mit Time/Number/Boolean-Selektoren, Reconfigure-Flow, `unique_id`, Startprüfung mit Repair-Issue und `ConfigEntryNotReady`, `runtime_data`, `PARALLEL_UPDATES`, das Modul `auto_efficiency.py` mit Energiezähler-Eingängen sowie 1 521 Zeilen Coordinator-Tests. Gleichzeitig wurden `__init__.py` und `config_flow.py` aus Coverage und Typprüfung ausgenommen. Die Übersetzungen des Assistenten blieben aber erhalten. | `git show 1ed4376 --stat`; `git grep` auf `5c31112` vs `HEAD` | Genau die "4 Steps" und die "nicht mehr passenden Entitäten", die du siehst: `translations/en.json` beschreibt Schritte und Felder, die der Code nicht mehr hat; 22 von 28 Feldern werden ohne Beschriftung gerendert; `quality_scale.yaml` behauptet sechs Regeln, die im Code fehlen. | 001, 002 |
| F2 | **Override nach oben funktioniert nicht.** `minimum_calculated_soc` wird nur nach unten angepasst, aber als Ziel für "erreicht" und für die Verifikation benutzt. | `number.py:83-92`, `__init__.py:1319-1334`, `:1871-1901` | Override 45 → 70 % bei 50 % Akku: Netzladung wird sofort wieder gestoppt, Min-SOC auf 45 % zurückgeschrieben. | 004 |
| F3 | **Fehlgeschlagener Reset am Fensterende wird nie wiederholt.** Bei nicht verfügbarer Entität wird nur geloggt, dann werden alle Listener abgebaut. | `__init__.py:1092-1112`, `:1069-1080` | Ein einziger Modbus-Aussetzer um 05:00 lässt Min-SOC den ganzen Tag auf dem Nachtziel stehen. | 005 |
| F4 | **Neustart im Fenster verfälscht den "Originalwert".** Wiederherstellung übernimmt den Wechselrichterwert ab 1 % Abweichung als Ziel, die Plausibilitätsprüfung greift erst ab 10 %. | `__init__.py:947-948`, `:1416-1423` | Ein niedriges Ziel (z. B. 12 %) wird als Original gespeichert und ab dann jeden Morgen statt 8 % zurückgeschrieben. | 005 |
| F5 | **Kein Zustand überlebt einen HA-Neustart.** Weder Enabled-Schalter, noch Skip-next, noch Override, noch Originalwert. | keine `RestoreEntity` im Paket; `__init__.py:197-201`, `:235-247` | Integration deaktiviert, HA startet neu, um 23:00 wird trotzdem geladen. | 005 |
| F6 | **Bis zu 3 Minuten Blockade im Fensterstart** durch `asyncio.sleep`-Schleife, ohne dass Listener gesetzt sind; parallel läuft das Polling mit einem anderen Ziel. | `__init__.py:897-912`, `:934-991` | Zwei verschiedene Ziele in einem Fenster; verwaiste Task nach Reload. | 005 |
| F7 | **AC-Ladelimit wird nie zurückgesetzt.** Die Effizienzsuche schreibt Probewerte, ein Restore gibt es nicht. | `__init__.py:462-485`, keine Reset-Funktion | Nach Abbruch bleibt z. B. 1 000 W stehen, Folgenächte erreichen das Ziel nicht. | 005 |
| F8 | **Backup-Listener wird beim Entladen nicht abgemeldet** und bei Optionsänderung nicht neu gesetzt. | `__init__.py:166-171`, `:89`, `:99-150` | Leck pro Reload; geänderte Backup-Entität wirkt erst nach Neustart. | 004 |
| F9 | **Fensterende nur per Zeit-Trigger**, das Polling prüft die Fenstergrenzen nicht. Sommerzeitumstellung kann den Trigger auslassen. | `__init__.py:1157-1178`, `:775-787` | Verpasster Trigger = Netzladung bleibt unbegrenzt an. | 004 |
| F10 | `_last_soc_set` überstimmt den gelesenen Wechselrichterwert; `number.set_value` in `_control_kostal` ist ungeschützt; Override im Entlademodus ruft den Ladepfad. | `__init__.py:1451-1460`, `:1467-1479`; `number.py:91-93` | Min-SOC wird nicht gesetzt obwohl nötig; ein Service-Fehler bricht den ganzen Update ab; Override im Entlademodus schaltet Netzladung ein. | 004 |
| F11 | Verifikationstask wird gecancelt aber nicht abgewartet; kann nach dem Reset das Nachtziel zurückschreiben. | `__init__.py:1848-1853`, `:1064-1080` | Seltene, aber stille Fehlrücksetzung. | 005 |
| F12 | **Verifikations-Baseline nicht reproduzierbar**: keine Abhängigkeitsdatei, kein CI, 79 % des Codes von Coverage und Typprüfung ausgenommen, `mock_hass` lässt verschluckte Exceptions wie Erfolg aussehen. | `.coveragerc`, `mypy.ini`, `pyrightconfig.json`, `tests/conftest.py:53-62`, kein `.github/` | Jede Qualitätsaussage im Repo ist unbelegt. | 003 |

### 2.3 Fachlich unvollständig (der Kern deiner Frage)

| # | Lücke | Nachweis | Folge |
|---|---|---|---|
| L1 | **Zielformel kennt keinen Verbrauch und keinen Sonnenaufgang.** `Ziel = (Kapazität − Forecast·(1+Marge)) / Kapazität`. | `calculation.py:8-14, 73-94` | An einem sonnigen Tag mit 20 kWh Forecast und 10 kWh Speicher wird auf `user_min_soc` (8 %) geladen. Von 05:00 bis PV > Verbrauch kauft das Haus zum Tagestarif. Im Winter landet das Ziel nur zufällig bei 100 %. |
| L2 | **Kein Sonnenstand.** `sun.sun` mit `next_rising` liegt in jeder HA-Installation vor, wird nicht genutzt. | kein `sun` im Paket | 21. Dezember und 5. Oktober bekommen bei gleichem Forecast dasselbe Ziel. |
| L3 | **Entladung im Fenster nicht gesperrt.** Ist der Akku beim Fensterstart über dem Ziel, entlädt er sich bis zum Ziel ins Haus, obwohl Netzstrom gerade billig ist. | `__init__.py:1380-1386`, `:1467-1479` | Gespeicherte PV wird nachts zu 14 ct "verbraucht", fehlt tagsüber bei 30 ct. Nach L1 der größte Euro-Posten. |
| L4 | **Ladeleistung nicht auf das Fenster geplant.** Die Effizienzsuche minimiert nur Wandlungsverluste, kennt die Fensterlänge nicht. | `__init__.py:569-607`, `:659` | Effizienzoptimum liegt meist bei kleiner Leistung, die das Ziel in 6 h nicht erreicht; kleine Nachladungen laufen mit Vollgas und maximalem Verlust. |
| L5 | **Ein Fenster, fest im Jahr.** Nur ein Start/Ende-Paar, ein optionaler Datumsbereich, kein Kalender pro Quartal, kein Preissignal. | `__init__.py:775-787`, `:403-426`; keine Preis-Keys | Avacon-Zeitfenster pro Quartal müssen manuell umgestellt werden. |
| L6 | **Plan wird beim Fensterstart eingefroren**, obwohl Solcast nachts mehrfach aktualisiert. Forecast-Tagwahl hängt an `now.hour < 12`, nicht am Fensterende. | `__init__.py:255-275`, `:1191-1207` | Falscher Solartag bei Fenstern, die nicht über Mitternacht gehen; kein Nachjustieren bei geändertem Forecast. |
| L7 | **Verbrauchsdaten waren schon entworfen** (`grid_import_energy_entity`, `battery_charge_energy_entity`, `home_consumption_energy_entity`) und sind mit dem Merge verschwunden. | `translations/en.json:44-65`; `git show 5c31112:custom_components/inverter_charge_night/auto_efficiency.py` | Günstigster Weg zu einem verbrauchsbewussten Planer. |
| L9 | **Kein Schnee-Override.** Bei Schnee auf den Modulen liefert jeder Forecast Ertrag, der nicht kommt; es gibt keinen Weg, die nächsten Nächte auf das Maximum zu laden, ohne die Konfiguration zu ändern. Der bestehende Override gilt nur im laufenden Fenster und wird am Fensterende gelöscht (`__init__.py:1076`). | Eigentümer-Anforderung; `_on_window_end` | Tage mit Schnee kaufen tagsüber zum Tagestarif. |
| L8 | **Kostal-Vertrag statt Wechselrichter-Abstraktion**: Min-SOC-`number` + Netzlade-`switch` + AC-Limit-`number` + Force-Discharge-`switch`; Keys heißen `kostal_*`. | `const.py:27-28`, `config_flow.py:235-240` | Fronius/SMA mit Betriebsart-Select und Leistungs-Sollwert sind nicht konfigurierbar. |
| L10 | **Morning-Discharge-Modus widerspricht dem Ziel**: speist morgens ins Netz ein, genau in der Zeit, in der der Speicher das Haus überbrücken soll. Override ruft dort trotzdem den Ladepfad. | `__init__.py:1519-1525`, `CHANGELOG.md:12`, `number.py:89` | Produktentscheidung nötig: dynamischer Tarif (behalten, so benennen) oder Überbrückungsmodus (umbauen). |

---

## 3. Zielarchitektur des Planers (Planer v2)

Kernidee: **zwei Schranken statt einer Formel**, beide in kWh, dann in SOC umgerechnet.

```
E_bridge   = Verbrauch(Fensterende → PV-Kreuzung) + Reserve
             Verbrauch aus Verbrauchsprofil (Stunde des Tages, gelernt aus Energiezählern),
             PV-Kreuzung = Sonnenaufgang (sun.sun next_rising) + Verzögerung (gelernt oder konfiguriert)

E_surplus  = max(0, PV_forecast_morgen − Tagesverbrauch_bis_Sonnenuntergang)
             (nur der Überschuss braucht Platz, nicht der ganze Forecast)

Untergrenze:  SOC_ziel ≥ (E_bridge + E_min) / Kapazität
Obergrenze:   SOC_ziel ≤ 1 − (E_surplus − E_bridge) / Kapazität
              (die Überbrückung schafft bis zur PV-Kreuzung wieder Platz)

Konflikt (Untergrenze > Obergrenze): Kostenvergleich
             Preis_Nacht vs. Einspeisevergütung entscheidet, ob eher überbrückt oder eher Platz gelassen wird.
             Ohne Preise: Untergrenze gewinnt (Netzbezug am Tag ist teurer als verschenkte Einspeisung).
```

Dazu drei Aktoren pro Fenster:

1. **Entladesperre** ab Fensterstart (Entladeleistung 0 W oder Betriebsart "nur laden"), Wiederherstellung am Fensterende und beim Entladen der Integration.
2. **Netzladung** bis Ziel, wie heute.
3. **Ladeleistung** = `(Ziel − Ist) · Kapazität / verbleibende Fensterstunden / Wirkungsgrad`, begrenzt durch das Effizienzoptimum nach oben und die Fensterfrist nach unten, jede Viertelstunde neu berechnet.

Der Plan wird **im Fenster fortlaufend neu berechnet** (Forecast-Updates), das Ziel darf dabei nur steigen, nie unter das bereits Geladene fallen.

Das Fenster selbst ist ein **Zeitplan**: Liste aus `{von_Datum, bis_Datum, Wochentage, Start, Ende}`;
der aktive Eintrag wird täglich um Mitternacht bestimmt. Ein optionaler Preis-Sensor (Tibber, aWATTar, EPEX)
kann den Zeitplan später ersetzen.

Der Wechselrichter wird über **Fähigkeiten** angesprochen (`hold_soc_floor`, `charge_from_grid`, `block_discharge`,
`limit_charge_power`), mit Profilen pro Hersteller. Das ist erst sinnvoll, wenn ein zweiter echter Wechselrichter
vorliegt; bis dahin bleibt der Kostal-Pfad, aber ohne Markennamen in Keys und Labels.

---

## 4. Reihenfolge und Status

| Plan | Titel | Priorität | Aufwand | Hängt ab von | Status |
|------|-------|-----------|---------|--------------|--------|
| 001 | Konfigurationsassistent, Reconfigure-Flow und Übersetzungen aus 1.0.3 zurückholen | P1 | M | — | TODO |
| 002 | Startprüfung, Repair-Issues, runtime_data, PARALLEL_UPDATES zurückholen; quality_scale ehrlich machen | P1 | S | 001 | TODO |
| 003 | Verifikations-Baseline: Abhängigkeiten, CI, Coverage-Ratchet für den Coordinator, strenger Test-Hass | P1 | M | — | TODO |
| 004 | Steuerlogik-Fehler mit kleinem Umfang: ein Ziel, Override-Pfad, Backup-Listener, Veto, Fensterprüfung | P1 | M | 003 | TODO |
| 005 | Rücksetz-Robustheit und Persistenz über Neustarts | P1 | L | 003, 004 | TODO |
| 006 | Planer v2 (Design und Prototyp): Überbrückung, Sonnenaufgang, Verbrauch, Entladesperre, Ladeleistung | P2 | L | 001–005 | TODO |
| 007 | Schnee-Override: Zahl-Entität "Schnee-Nächte", lädt die nächsten N Nächte auf das Maximum und zählt herunter | P2 | S | 004, 005 | TODO |

Status-Werte: TODO | IN PROGRESS | DONE | BLOCKED (mit Grund) | REJECTED (mit Begründung)

### Backlog ohne eigenen Plan (nach 006 entscheiden)

- **008 Zeitplanmodell für §14a-Fenster** (L5): Liste von Datumsbereich → Fenster, Migration des Config-Entrys, tägliche Neubestimmung.
- **009 Wechselrichter-Profile** (L8): erst Spike gegen die realen Entitäten der Fronius- und SMA-Integrationen, dann Fähigkeitsschnittstelle; Umbenennung `kostal_*` → `min_soc_entity` / `grid_charge_switch` mit `async_migrate_entry`.
- **010 Morning-Discharge entscheiden** (L9): entweder als "dynamischer Tarif"-Funktion dokumentieren oder zum Überbrückungsmodus umbauen. Bis dahin mindestens Override-Pfad korrigieren (in 004 enthalten).
- **011 Preissignal** (L5): Tibber/aWATTar/EPEX-Sensor als Eingang, ersetzt den festen Zeitplan durch Kostenoptimierung.
- **012 Doku-Bereinigung**: README auf 2.0 und 28 Felder bringen (Forecast-Entität für MORGEN, nicht heute), Platzhalter-URLs, elf Audit-Dateien im Wurzelverzeichnis nach `docs/history/`, `de.json` anlegen.

### Abhängigkeiten

- 002 braucht 001, weil Reconfigure-Flow und `unique_id` im Assistenten sitzen.
- 004 und 005 brauchen 003, weil der Coordinator sonst ohne Coverage und ohne echten Test-Hass umgebaut würde.
- 006 braucht 005, weil ein Planer, der die Entladung sperrt, einen zuverlässigen Reset voraussetzt.
- 007 braucht 004 (eine Zielquelle) und 005 (der Schneezähler muss einen Neustart überleben).

### Geprüft und verworfen

- **Einheitentabellen durch `EnergyConverter`/`PowerConverter` ersetzen**: würde die heute akzeptierten Langschreibweisen brechen; erst mit Planer v2 entscheiden.
- **Sofortiger Umbau auf Wechselrichter-Abstraktion**: ohne zweiten realen Wechselrichter ist das eine vorzeitige Abstraktion über dem risikoreichsten Code.
- **Preisoptimierer vor korrektem Zielplaner**: Optimierung auf einer falschen Zielformel lohnt nicht.
- **`.env.example` / Devcontainer**: keine Umgebungsvariablen, keine eigenständige Laufzeit.
- **Sicherheitsbefunde**: keine. Keine Zugangsdaten, keine Netzwerksockets, alle Effekte über `hass.services`.
