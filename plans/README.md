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
| L3 | **Entladung im Fenster nicht gesperrt.** (Plan 009 löst das ohne Herstellerfunktion über den Min-SOC.) Ist der Akku beim Fensterstart über dem Ziel, entlädt er sich bis zum Ziel ins Haus, obwohl Netzstrom gerade billig ist. | `__init__.py:1380-1386`, `:1467-1479` | Gespeicherte PV wird nachts zu 14 ct "verbraucht", fehlt tagsüber bei 30 ct. Nach L1 der größte Euro-Posten. |
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
| 001 | Konfigurationsassistent, Reconfigure-Flow und Übersetzungen aus 1.0.3 zurückholen | P1 | M | — | DONE |
| 002 | Startprüfung, Repair-Issues, runtime_data, PARALLEL_UPDATES zurückholen; quality_scale ehrlich machen | P1 | S | 001 | DONE |
| 003 | Verifikations-Baseline: Abhängigkeiten, CI, Coverage-Ratchet für den Coordinator, strenger Test-Hass | P1 | M | — | DONE |
| 004 | Steuerlogik-Fehler mit kleinem Umfang: ein Ziel, Override-Pfad, Backup-Listener, Veto, Fensterprüfung | P1 | M | 003 | DONE |
| 005 | Rücksetz-Robustheit und Persistenz über Neustarts | P1 | L | 003, 004 | DONE |
| 006 | Planer v2 (Design und Prototyp): Überbrückung, Sonnenaufgang, Verbrauch, Entladesperre, Ladeleistung | P2 | L | 001–005 | DONE |
| 007 | Schnee-Override: Zahl-Entität "Schnee-Nächte", lädt die nächsten N Nächte auf das Maximum und zählt herunter | P2 | S | 004, 005 | DONE |
| 008 | Ladeleistung gegen den Hausanschluss begrenzen (Dauerlast, Sicherungsgröße, Netzbezug) | P1 | M | 006 | DONE |
| 009 | Entladung im Fenster sperren: Schalter, sonst Leistungsgrenze, sonst Min-SOC anheben | P1 | M | 004, 005, 006 | DONE |
| 010 | Effizienzsuche messbar machen: Einschwingen, Energiezähler, Annahmekriterien, Sichtbarkeit | P1 | M | 008 | DONE |
| 011 | Tarifzeitfenster (Perioden, Saison, Jahreswechsel) und Abendreserve für die Hochpreiszone | P1 | M | 006 | TODO (Entwurf) |
| 012 | Masterplan: allgemeine Lade-/Entladesteuerung — Services, Tarifkalender, Preissignal, Regeln als Subentries | P1 | L | 006 | Stufen 1–2 DONE (3.1.0, 3.2.0), 3–4 TODO |
| 013 | Abendrettung und das Ad-hoc-Fenster, das sie braucht | P1 | L | 009, 011 | DONE (3.4.0 + 3.5.0) |
| 014 | Abregelung: die PV-Ladung so legen, dass die Mittagsspitze hineinpasst | P1 | L | 013, 011 | TODO (Entwurf) |

Status-Werte: TODO | IN PROGRESS | DONE | BLOCKED (mit Grund) | REJECTED (mit Begründung)

Umsetzung 001–007 am 2026-09-11 auf dem Branch `fix/code-review-followups` (PR #2). Offene Entscheidungen für den Planer v2 stehen in `plans/006-DECISIONS.md`; der Standardmodus bleibt `headroom`, bis der Eigentümer auf `bridge` umschaltet.

Am 2026-09-12 folgte eine Prüfung auf Lauffähigkeit mit dem aktuellen Home Assistant, Codequalität, Bugs und Randfälle. Ergebnis: 29 Befunde, davon 22 behoben (siehe Abschnitt 2.4). Getestet wird jetzt gegen HA 2025.2.0 (Mindestversion), 2026.2.3 und 2026.9.2 (aktuell), zusätzlich mit einem End-to-End-Test gegen einen echten HA-Kern (`scripts/smoke_real_ha.py`).

### 2.4 Nachtrag 2026-09-12: Kompatibilität, Bugs, Randfälle

| Befund | Wirkung | Status |
|---|---|---|
| HA ab 2026.5 verlangt Python 3.14; die CI-Matrix testete 3.12/3.13 | Das aktuelle HA war in der CI nicht installierbar | behoben: Matrix 3.13 (Mindest-HA) und 3.14 (aktuelles HA) |
| Deklarierte Mindestversion 2024.4.0 zu niedrig (`runtime_data`, Reconfigure-Flow) | Installation auf zu alten Versionen schlägt erst zur Laufzeit fehl | behoben: 2025.2.0, gegen die APIs belegt |
| `_on_window_end` ohne `is_active`-Prüfung | Der End-Trigger schrieb jede Nacht den Standard-Min-SOC, auch bei deaktivierter Integration; bei nicht erreichbarer Entität endlose Wiederholungskette | behoben |
| Moduswechsel über den Optionsdialog ohne Teardown | Netzladung an, während Zwangsentladung noch an ist | behoben |
| `snow_nights` zählte bei jedem Fensterende herunter | Übersprungene Nächte verbrauchten Schnee-Nächte | behoben |
| Fehlgeschlagener Reset auf den Pfaden Unload, Ausschalten, Moduswechsel | Netzladung blieb an, ohne Wiederholung | behoben |
| Absolute Ladeleistungsgrenze nicht persistiert, Original bei Fehler verworfen | Grenze blieb dauerhaft am Wechselrichter | behoben |
| Bridge-Planer nutzte `next_rising` auch für Fenster nach Sonnenaufgang | Überbrückung über 24 h, Ziel immer Maximum | behoben |
| Nebenläufiger Fenster-Check während des Resets | Konnte Listener neu setzen und das Nachtziel zurückschreiben | behoben |
| Nicht-endliche Sensorwerte (`nan`) | Zielerkennung blockiert, Netzladung stoppt nie | behoben |
| Reconfigure/Optionen ohne Eindeutigkeitsprüfung | Zwei Einträge auf einem Wechselrichter | behoben |
| Optionsdialog überschrieb Laufzeitänderungen | Z. B. Effizienz-Finder wurde wieder eingeschaltet | behoben |
| Diagnose schwärzte zwei Entitäts-IDs nicht | Entitäts-IDs im Diagnose-Download | behoben |
| `manifest.json` mit unbekanntem Schlüssel `diagnostics` | Wirkungslos, von hassfest abgelehnt | behoben |
| `icons.json` mit toten Einträgen, `_attr_icon` überstimmt es | Icon-Übersetzungen unwirksam | teilweise: `icons.json` korrigiert, `_attr_icon` in vier Dateien noch offen |
| DST-Faltung in `_window_end_datetime` | Theoretisch falsche Restdauer in der Wiederholungsstunde | offen, dokumentiert: die eigentliche Folge (unendliche Sollleistung) ist über eine Mindestdauer abgefangen |
| Koordinator weiterhin ~2700 Zeilen | Wartbarkeit | offen: Schnitte benannt (Limits, Effizienzsuche, Zeitplan) |
| `PlanInput` trägt zwei ungenutzte Felder | Irreführend | offen |
| 11 veraltete Audit-Dateien im Wurzelverzeichnis | Widersprechen dem Code | offen, Backlog 012 |


### 2.5 Nachtrag 2026-09-12: Hausanschluss und Entladesperre (Pläne 008, 009)

Beide Pläne sind umgesetzt und anschließend gegen den laufenden Code geprüft. Die Prüfung fand
zehn Befunde; alle sind behoben:

| Befund | Wirkung | Status |
|---|---|---|
| Die Anschlussgrenze wurde vom Wechselrichter genommen, sobald das Ladeziel erreicht war | Der angehobene Min-SOC lässt den Wechselrichter weiter kaufen — stundenlang ohne Grenze | behoben: die Grenze bleibt bis zum Fensterende und wird bei jeder Laständerung nachgeführt |
| Ein gespeicherter Boden konnte einen unsauberen Neustart überleben | Der Speicher bliebe dauerhaft gesperrt | behoben: ohne gesicherten Original-Min-SOC lief kein Fenster, der Boden wird verworfen |
| Netzbezugssensor ohne Einheit wurde als Watt gelesen | Ein kW-Template-Sensor hätte die Grenze aufgehoben | behoben: nur W und kW werden akzeptiert |
| 400 V (Außenleiterspannung) wurde als Strangspannung gerechnet | Budget um √3 zu groß, die Grenze greift zu spät | behoben: über 300 V wird bei drei Phasen durch √3 geteilt |
| Netzbezugs-Entität ohne Ladeleistungs-Entität konfigurierbar | Schutzfunktion sichtbar, aber wirkungslos | behoben: der Assistent verlangt beide |
| Eigenanteil am Netzbezug aus dem Sollwert statt aus der Messung | Fremdlast wird unterschätzt, solange der Speicher dem Sollwert nicht folgt | behoben: die gemessene Ladeleistung zählt, und zwar die kleinere der beiden |
| `limited` meldete "Budget kleiner als das Maximum" statt "hält gerade zurück" | Irreführende Anzeige | behoben: gemessen am zuletzt geplanten Bedarf |
| Der Netzbezugs-Listener blieb beim Ausschalten der Integration bestehen | Schreibzugriffe nach dem Ausschalten | behoben |
| Der Boden folgte der eigenen Netzladung | Rückkopplung: Boden → Ladung → höherer Boden, bis zum Nutzermaximum | behoben: der Boden folgt nur Anstiegen, die nicht von uns kommen |
| Effizienztest lief unbegrenzt weiter, wenn die Wallboxen mittendrin starteten, und buchte den Verlust auf die angeforderte Leistung | Anschlussüberlastung und eine falsche Effizienzhistorie | behoben: kein Test ohne Platz, laufender Test wird abgebrochen |

Die Nachprüfung der Korrekturen fand zwei weitere Punkte, beide behoben: die neuen Schreibpfade
umgingen die Entprellung (ein Leistungssensor meldet im Sekundentakt, der Wechselrichter hätte
jede Meldung mitbekommen), und der geschriebene Sollwert wurde auch dann als Eigenverbrauch
abgezogen, wenn das Ladeziel längst erreicht war — dann begrenzt er einen Speicher, der nichts
zieht, und hätte genau diese Leistung an Hauslast verdeckt.

Ein Test fährt beide Funktionen in einem Fenster:
`tests/test_grid_limit.py::test_the_connection_limit_and_the_discharge_block_run_together`.

### 2.6 Nachtrag 2026-09-12: Effizienzsuche und Benennung der Entitäten

Der Eigentümer fragte, ob die Effizienzsuche überhaupt funktioniert, und meldete drei gleich
benannte Entitäten in der Konfiguration. Beides bestätigt:

**Effizienzsuche** (Plan 010): Neun Befunde, alle behoben. Der schwerste: der Verlust wurde auf
[0, 1] geklemmt, also hat eine Fehlkonfiguration mit zwei Sensoren auf derselben Seite des
Ladegeräts einen Verlust von 0 % ergeben und die Suche dauerhaft gewonnen. Dazu: keine Prüfung,
ob der Wechselrichter dem Sollwert überhaupt folgte (ein fast voller Speicher drosselt), eine
starre Mindestdauer von 30 Minuten, an der jede Messung bei hoher Ladeleistung scheitert, und
eine Integration nur alle 15 Minuten mit dem Momentanwert am Intervallende.

**Benennung**: Auf `develop` fehlen in der `entity`-Sektion der Übersetzungen die Einträge für
`select.operation_mode` und `switch.skip_next`. Home Assistant fällt dann auf den Gerätenamen
zurück und zeigt sie beide als „Inverter Charge Night" — zusammen mit dem Hauptschalter, dessen
übersetzter Name ebenfalls „Inverter Charge Night" lautet. Das sind die drei gleich benannten
Einträge. In diesem Branch waren die fehlenden Einträge durch Plan 001 schon ergänzt; jetzt sind
zusätzlich alle Anzeigenamen sprechend (Automatik, Effizienzsuche, Nächstes Fenster überspringen,
Zielladestand …), und es gibt eine vollständige deutsche Übersetzung (`translations/de.json`,
383 Zeichenketten, durch einen Test gegen `strings.json` abgesichert).

### 2.7 Nachtrag 2026-09-12: Review mit Schwerpunkt elektrische Sicherheit

Auf Wunsch des Eigentümers ein eigener Durchgang über den gesamten PR-Diff mit der Frage: Wo kann
diese Integration mehr Strom ziehen, als der Anschluss trägt, oder eine Einstellung am
Wechselrichter stehen lassen? Zwölf Befunde, alle behoben:

| # | Befund | Wirkung | Status |
|---|---|---|---|
| S1 | Der Fensterzustand wurde nicht persistiert | Ein Neustart über das Fensterende hinweg (HA-Update nachts) ließ **Netzladung an und den angehobenen Min-SOC stehen** — der Speicher wird tagsüber aus dem Netz vollgekauft, jeden Tag, bis es jemand merkt | behoben: beim Fensterprüflauf wird erkannt, dass noch erfasste Werte offen sind, und zurückgesetzt |
| S2 | `_react_to_grid_import` gab auf, wenn der Planer nichts geliefert hatte | Bei unlesbarem Ladestand setzte der Anschlussschutz zwischen zwei Polls (900 s) aus | behoben: der geschriebene Sollwert ist dann die Referenz, weiterhin nur abwärts |
| S3 | `_set_ac_charge_limit_w` meldete nicht, ob wirklich geschrieben wurde | Ein fehlgeschlagener Schutz-Schreibvorgang galt als erfolgt; alle Folgevergleiche („nur tiefer schreiben") unterdrückten jeden Wiederholversuch | behoben: Rückgabewert, und nur bei Erfolg wird die Referenz gesetzt |
| S4 | `float(state.state)` statt `_as_float` auf den Steuerpfaden | Ein `nan`-Ladestand macht „Ziel erreicht" unerreichbar → **Netzladung stoppt nie**; ein `nan` als erfasster Original-Min-SOC wird am Fensterende auf den Wechselrichter geschrieben | behoben: nicht-endliche Werte gelten überall wie „nicht verfügbar" |
| S5 | `_enforce_grid_limit_now` ohne Notstrom-Prüfung | Schreibzugriff im Inselbetrieb | behoben |
| S6 | Die 400-V-Korrektur galt nur dreiphasig | 1 Phase + 400 V (Tippfehler) vergrößerte das Budget um 74 % | behoben: außerhalb 100–300 V gilt der Standardwert |
| S7 | Ohne Messsensor wurde der volle Sollwert als Eigenverbrauch gutgeschrieben | Fremdlast wird unterschätzt, solange der Speicher dem Sollwert nicht folgt | gemildert: die Gutschrift ist auf den gemessenen Netzbezug gedeckelt, und das Log nennt den fehlenden Sensor. Ganz auflösbar ist es nur mit der Messung |
| S8 | Der Schreibpfad schrieb rohe Watt auf eine Entität ohne Einheit | Eine kW-Entität bekäme 5000 statt 5 → der Wechselrichter klemmt auf sein Maximum: aus der Schutzgrenze wird Volllast | behoben: das `max`-Attribut der Entität entscheidet die Skala; ist auch das unbekannt und der Wert unzulässig, wird nicht geschrieben |
| S9 | Der „Neustart im Fenster"-Zweig hing nur an `original_min_soc` | Ein neues Fenster übernahm das Ziel der Vornacht | behoben: der Fensterstart wird mitpersistiert und ein altes Ziel verworfen |
| S10 | `_reset_ac_charge_limit` ließ die Referenz stehen | Der erste begrenzende Schreibvorgang des nächsten Fensters konnte an der 100-W-Schwelle scheitern | behoben |
| S11 | `or float(max)` behandelte einen geschriebenen Sollwert von 0 W als „nicht gesetzt" | Der Effizienz-Pfad hätte gegen das Maximum statt gegen 0 verglichen | behoben |
| S12 | Das Poll-Sicherheitsnetz beendete das Fenster ohne `scheduled=True` | Eine verpasste Endauslösung verbrauchte keine Schnee-Nacht | behoben |

Die Regressionstests dazu stehen in `tests/test_electrical_safety.py` und prüfen durchgehend
dieselbe Eigenschaft: **Ist etwas unbekannt, unlesbar oder fehlgeschlagen, muss weniger Strom
fließen, nie mehr** — und nichts, was die Integration am Wechselrichter gesetzt hat, darf sein
Fenster überleben.

### 2.8 Nachtrag 2026-09-12: Notstrom/Inselbetrieb und Kostal-Kompatibilität

Der Eigentümer betreibt eine **manuelle Netzumschaltbox**: bei Stromausfall läuft das ganze Haus
aus dem Speicher. Die Integration hatte dafür bereits das Feld „Notstrom-Entität", aber die
Erkennung war unbrauchbar und in der gefährlichen Richtung fehlertolerant.

| Befund | Wirkung | Status |
|---|---|---|
| `_is_backup_active` kannte nur `on/true/1/yes/backup/active/island` und gab bei allem anderen `False` zurück | Ein Zustandssensor, der `ESB`, `Inselbetrieb` oder `Notstrom` meldet, galt als **kein** Notstrom. Folge: Die Integration hebt im Inselbetrieb den Min-SOC an — **der Speicher versorgt das Haus nicht mehr, bei Stromausfall** | behoben: Wortschatz erweitert (`esb`, `ersatzstrom`, `insel*`, `notstrom`, `offgrid`, `gridswitchoff`, …), Binärentitäten werten Unbekanntes als Notstrom, Sensoren melden es einmalig mit Abhilfe |
| Kein Weg, herstellerspezifische Zustände anzugeben | Nicht universell | behoben: neues Feld „Zustände für Notstrombetrieb" (kommagetrennt), z. B. `ESB` oder `17` |
| Negativer Netzbezug (Einspeisung) wurde als negative Last gerechnet | Vergrößerte das Anschluss-Budget | behoben: auf 0 geklemmt |
| Ein Schreibvorgang auf die Ladeleistung galt als wirksam, obwohl die Gegenseite ihn still verwerfen kann | KostalKore verwirft **jeden** Schreibvorgang, wenn der Wechselrichter nicht auf „extern über Modbus" steht — nur mit einer Logzeile auf seiner Seite. Die Anschlussgrenze wäre wirkungslos, ohne dass es auffällt | behoben: die periodische Verifikation liest das AC-Ladelimit zurück und schreibt nach, wenn der Wechselrichter einen **höheren** Wert hält |

**Kompatibilität mit KOSTAL KORE** (`Puma7/KostalKore`) wurde vollständig gegengeprüft; das
Ergebnis steht in `docs/kostal-kore.md`. Kurzfassung: Alle Pflichtentitäten sind vorhanden, die
Entladesperre findet mit `Battery Disable Discharge` sogar den bevorzugten Herstellerschalter,
und für die Effizienzsuche gibt es mit `Battery Charge from Grid Total` genau den richtigen
Energiezähler. Drei Fallstricke: `Battery Charge Power (AC) Absolute` ist **vorzeichenbehaftet**
(negativ = laden) und darf nie als Ladelimit konfiguriert werden; Register 1038 hat innerhalb
von KostalKore mehrere Besitzer (Grid-Feed-In-Optimizer, SoC-Controller, Ladesperre, Batterietest),
die sich mit uns überschreiben würden; und `Home Power from Grid` ist der Hausverbrauch aus dem
Netz, **nicht** der Bezug am Hausanschluss — für die Anschlussgrenze muss der Zähler (KSEM) her.

### 2.9 Nachtrag 2026-09-13: Externer Review — geprüft, widerlegt, behoben

Ein externer Bericht (20 Befunde: M1–M6, L1–L14) wurde Punkt für Punkt am Code nachgeprüft.
**16 Befunde waren valide und sind behoben**, zwei Aussagen waren nachweislich falsch, drei
bleiben bewusst offen.

| Befund | Prüfung | Status |
|---|---|---|
| M1 toter Re-Arm-Pfad für den Ladestands-Listener | **bestätigt**: `async_update_entry` tauscht `coordinator.config` **vor** `update_time_triggers`, also ist `old == new` immer. Bei Entitätswechsel im laufenden Fenster lauscht der Listener bis zum Fensterende auf die alte Entität | behoben: der alte Wert wird vor dem Tausch erfasst und übergeben |
| M2 Ausschalter räumt den Effizienztest nicht auf | **bestätigt**, und schwerer als beschrieben: auch `_window_floor_soc` blieb stehen und wäre vom nächsten Fenster als eigener Startboden übernommen worden | behoben: `async_disable()` im Coordinator, ein Teardown für Schalter und Fensterende |
| M3 Effizienzsuche endet zu früh und lässt einen Zufallswert stehen | **bestätigt** in allen vier Punkten. Am schwersten: der Abschluss-Schreibvorgang läuft durch die Anschlussgrenze, und weil danach der Originalwert verworfen wurde, blieb ein zufällig gedrosselter Wert dauerhaft am Wechselrichter — der Nutzerwert war weg | behoben: unmessbare Punkte verengen das Intervall, die Suche endet erst bei Erschöpfung, der Originalwert bleibt erhalten (das Ergebnis wirkt über den Planer-Deckel), Fehlkonfiguration warnt einmal |
| M4 Datumsbereich schneidet Fenster / verliert die erste Nacht | **bestätigt** für den Start (die Nacht vor dem Startdatum verliert alles nach Mitternacht, weil es keinen Trigger gibt). Die Kappung am Ende ist dagegen **richtig**: ein Tarifzeitraum endet um 00:00 | behoben: Prüfung an der Datumsgrenze, wenn ein Bereich konfiguriert ist — beide Richtungen jetzt punktgenau statt bis zu 15 min verspätet |
| M5 Floor-Write ohne Fehlerbehandlung im Entlademodus | **bestätigt**: der einzige ungeschützte Service-Call; ein Fehlschlag riss „Netzladung aus" und „Zwangsentladung an" mit | behoben |
| M6 Monotoner Boden ignoriert eine Senkung durch den Nutzer | **bestätigt**: Override runter oder Schnee-Nächte auf 0 ließen den Boden oben; der Speicher blieb bis Fensterende blockiert | behoben: `release_window_floor_to()`, von beiden Zahl-Entitäten aufgerufen |
| L1 Einheitenlisten inkonsistent | bestätigt | behoben: eine gemeinsame Liste in `const.py` |
| L4 `device_class BATTERY` für einen Ziel-SOC | bestätigt | behoben |
| L5 `"23:59:99"` galt als gültig | bestätigt | behoben |
| L6 Docstring beschrieb die Zeitarithmetik falsch | bestätigt | behoben |
| L7 Import mitten in einer Methode | bestätigt | behoben |
| L8 Skip-Timer feuert auf verworfenem Coordinator | bestätigt (eng, aber real) | behoben |
| L9 `command_delay` außerhalb des try | bestätigt | behoben |
| L11 Dev-Abhängigkeit erlaubte HA 2024.4 | bestätigt | behoben: auf 2025.2.0 angehoben |
| L13 Gedrosselter Testpfad schrieb jeden Poll denselben Wert | bestätigt | behoben |
| L14 Verifikationsintervall wurde nur einmal gelesen | bestätigt | behoben |

**Falsifiziert:**

- „`_enforce_grid_limit_now` komplett ungetestet (Backup-Guard, Debounce-Return, Write-Pfad)" —
  nachweislich falsch. Ungedeckt sind vier defensive `return`-Zeilen; Backup-Guard und Write-Pfad
  sind durch `test_backup_mode_stops_the_limit_from_writing_after_the_target` und
  `test_the_limit_stays_on_the_inverter_after_the_target_is_reached` abgedeckt.
- „Lokal getestet mit HA 2025.1.4" — das liegt **unter** der deklarierten Mindestversion 2025.2.0
  (`hacs.json`). Ein grüner Lauf dort belegt nichts über die unterstützten Versionen.

**Bewusst offen:**

- L2 (Options-Dialog: ein Feld auf den Öffnungswert zurückzusetzen gilt als „nicht angefasst") —
  ohne Dirty-Tracking pro Feld nicht unterscheidbar; der Bericht sagt das selbst.
- L3 (Entprellung koppelt an jeden Schreibvorgang) — bei sekündlich meldenden Zählern ohne
  Wirkung; eine getrennte Entprellung wäre mehr Zustand für weniger Sicherheit.
- L10 (unbekannter Zustand eines Sensors gilt als Netzbetrieb) — dokumentierte Entscheidung:
  anders herum würde ein einziger unbekannter Zustand die Integration dauerhaft lahmlegen.

Bei der Umsetzung fiel ein eigener Fehler auf: `_as_float(wert) or default` verwirft eine
konfigurierte **0** (Befehlsverzögerung, Abfrageintervall). Der Testlauf hat ihn sofort gefangen;
beide Stellen prüfen jetzt explizit auf `None`.

### Backlog ohne eigenen Plan (nach 006 entscheiden)

- ~~**010 Zeitplanmodell für §14a-Fenster** (L5)~~ — hat jetzt einen eigenen Plan:
  `plans/011-tarifzeitfenster-und-abendreserve.md`. Dort stehen der Defekt im heutigen
  Datumsbereich (absolute Daten laufen ab, eine Winter-Saison wird still ignoriert), das
  Datenmodell als Periodenliste, die Antwort auf die Wiederholungsfrage (`MM-DD` wiederholt sich
  jährlich, `YYYY-MM-DD` einmalig, Jahreswechsel wie Mitternacht behandelt), der `ObjectSelector`
  als Oberfläche — und die **Abendreserve** für Pascals Hochpreiszone 18–21 Uhr.

- **011 Wechselrichter-Profile** (L8): erst Spike gegen die realen Entitäten der Fronius- und
  SMA-Integrationen, dann Fähigkeitsschnittstelle. Die Umbenennung `kostal_*` →
  `min_soc_entity` / `grid_charge_switch` samt Migration ist mit 3.0.2 erledigt; offen bleibt die
  Fähigkeitsschnittstelle, also die Frage, welche Steuergrößen ein Wechselrichter überhaupt
  anbietet und was die Integration tut, wenn eine davon fehlt.
- ~~**012 Morning-Discharge entscheiden** (L9)~~ — entschieden von Pascal am 20.09.: der Modus
  bleibt, als **Netzentlastungs- und Arbitragefunktion**, nicht als "Platz für die Sonne
  schaffen". Zweck: an einem Sommertag, dessen Prognose das Haus ohnehin deckt, den Speicher in
  die Morgenspitze (etwa 5–8 Uhr) entladen — dort zieht der Haushalt am meisten, die Sonne
  liefert noch nicht, der dynamische Tarif ist am teuersten und das Netz am engsten. Hochoptional
  und ausdrücklich **experimentell**; so ist er jetzt auch in der Oberfläche benannt und in
  `docs/morning-discharge.md` beschrieben. Der Zwangsentlade-Schalter ist seit 3.0.2 Pflicht für
  den Modus.
- ~~**017 PV-Ladung über den Tag strecken (Abregelung vermeiden)**~~ — hat jetzt einen eigenen
  Plan: `plans/014-abregelung-pv-ladung-strecken.md`. Dort steht auch die **Korrektur der
  Richtung**: gedrosselt gehört der *Vormittag*, nicht die Mittagszeit — während der Abregelung
  ist der Speicher die einzige Senke, die noch bleibt. Ursprünglicher Eintrag:

- **017 PV-Ladung über den Tag strecken (Abregelung vermeiden)**: Pascal am 20.09. — wenn
  mittags das Netz voll ist, darf nicht mehr eingespeist werden. Ein Speicher, der um zwölf Uhr
  schon voll ist, kann dann nichts mehr aufnehmen und die Anlage wird abgeregelt. Beispiel:
  100 kWh Prognose für den Tag, 35 kWh Speicher — der ist bis mittags voll, und der Rest der
  Mittagsspitze geht verloren. Die Ladung müsste so gestreckt werden, dass der Speicher erst am
  Nachmittag voll ist und die Spitze noch aufnehmen kann.

  Was dafür spricht, dass es geht: die Steuergröße gibt es schon. Ein Limit, das auch die
  **DC-seitige PV-Ladung** begrenzt, ist bei einem Kostal G3 Register 1280
  (`Battery Max Charge Power (G3)`, AC+DC) — genau die Entität, die diese Integration bereits als
  „absolutes Maximum" kennt. Das AC-Ladelimit allein würde nicht reichen, das begrenzt nur den
  Netzbezug.

  Was noch offen ist:
  - **Die Integration ist heute ein Nachtfenster-Programm.** Eine Ladekurve über den Tag hieße,
    dass sie auch tagsüber rechnet und schreibt — das ist eine echte Erweiterung, kein Parameter.
  - **1280 fällt zurück.** Ohne regelmäßiges Nachschreiben springt das Register nach
    `Battery Time Until Fallback (G3)` auf die Werkseinstellung (siehe `docs/kostal-kore.md`).
    Eine Kurve muss also in jedem Poll erneuert werden.
  - **Die Prognose für *heute*** ist heute ein optionales Feld (`pv_forecast_today_entity`). Für
    diese Funktion wäre sie Pflicht.
  - Zusammenspiel mit 011: die Abendreserve sagt, wie voll der Speicher am Abend sein muss — zu
    scharf gestreckt wird er das nicht mehr.

  Einfachste tragfähige Fassung: kein SOC-Fahrplan, sondern eine Leistungsobergrenze aus
  „verbleibende Prognose / verbleibende Kapazität / Stunden bis PV-Ende", bei jedem Poll neu
  geschrieben.

- **013 Preissignal** (L5): Tibber/aWATTar/EPEX-Sensor als Eingang, ersetzt den festen Zeitplan
  durch Kostenoptimierung. Von Pascal bestätigt: wer einen dynamischen Tarif hat, würde darüber
  laden *und* entladen — das ist derselbe Eingang für Nachtladung und Morgenentladung, und es ist
  das, was 012 von "Modus von Hand umschalten" zu "rechnet selbst" machen würde.
- ~~**016 Coordinator aufteilen** (`common-modules`)~~ — erledigt: der Coordinator liegt in
  `coordinator.py`, `__init__.py` ist nur noch Setup, Update und Unload. Eine feinere Aufteilung
  (Limits, Effizienzsuche, Zeitplan, Persistenz) bleibt möglich, ist aber von keiner Regel
  gefordert.
- ~~**015 Effizienz je Ladestandsband**~~ — erledigt mit 3.3.0. Nachtrag zum Eintrag unten: die
  Annahme, die Messung trage Start- und End-SOC schon mit, war **falsch** — im gesamten Messpfad
  kam `_current_battery_soc()` nicht vor. Die Aufnahme des SOC war der eigentliche Teil der Arbeit.
  Bänder à 20 Punkte, Mittelwert der Messung entscheidet, ab drei gemessenen Leistungen sticht das
  Band das globale Optimum, breitere Messungen bleiben global.

- **015 Effizienz je Ladestandsband**: Verluste hängen auch vom SOC ab; die Suche bucht heute nur
  auf die Leistung. Wer das verfeinern will, misst pro SOC-Band (Plan 010, Wartungshinweise).
  Von Pascal als sinnvoll und direkt umsetzbar eingestuft. Umfang: die Messung trägt den SOC
  ohnehin schon (Start- und End-SOC stehen im Sample), es fehlt die Ablage je Band und ein
  Optimum je Band statt eines globalen. Der Rahmen dafür — golden-section über die Leistung,
  Verwerfen unbrauchbarer Messungen — bleibt wie er ist.
- ~~**014 Doku-Bereinigung**~~ — erledigt: die README beschreibt die Prognose-Entität für den
  nächsten Tag und alle Felder, die Platzhalter-URLs sind durch die echten ersetzt, die
  Audit-Dateien liegen unter `docs/history/`, und `translations/de.json` existiert.

### Abhängigkeiten

- 002 braucht 001, weil Reconfigure-Flow und `unique_id` im Assistenten sitzen.
- 004 und 005 brauchen 003, weil der Coordinator sonst ohne Coverage und ohne echten Test-Hass umgebaut würde.
- 006 braucht 005, weil ein Planer, der die Entladung sperrt, einen zuverlässigen Reset voraussetzt.
- 007 braucht 004 (eine Zielquelle) und 005 (der Schneezähler muss einen Neustart überleben).
- 008 braucht 006, weil dort die Ladeleistungsplanung sitzt, in die die Grenze eingreift.
- 009 braucht 004, weil es das Ladeziel vom geschriebenen Min-SOC trennt.

### Geprüft und verworfen

- **Einheitentabellen durch `EnergyConverter`/`PowerConverter` ersetzen**: würde die heute akzeptierten Langschreibweisen brechen; erst mit Planer v2 entscheiden.
- **Sofortiger Umbau auf Wechselrichter-Abstraktion**: ohne zweiten realen Wechselrichter ist das eine vorzeitige Abstraktion über dem risikoreichsten Code.
- **Preisoptimierer vor korrektem Zielplaner**: Optimierung auf einer falschen Zielformel lohnt nicht.
- **`.env.example` / Devcontainer**: keine Umgebungsvariablen, keine eigenständige Laufzeit.
- **Sicherheitsbefunde**: keine. Keine Zugangsdaten, keine Netzwerksockets, alle Effekte über `hass.services`.
