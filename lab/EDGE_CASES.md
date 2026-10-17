# Kalender, fehlerhafte Messungen und verweigerte Befehle

Die Kampagne vom 17.10.2026 priorisiert einen konservativen Lade-Stopp vor einem
günstigen oder vollständig erreichten Ladeziel. Mehr laden bedeutet weiterhin:
innerhalb der konfigurierten SoC- und Leistungsgrenzen. Physisches Überladen
ist kein zulässiger Kostenkompromiss.

## Bestätigte Fehler und Korrekturen

| Auslöser | Vorheriger Stand | Jetzt geprüfte Reaktion |
|---|---|---|
| Sommer-/Winterzeit | Subtraktion zweier lokaler Zeiten ignorierte den Offsetwechsel | Restlaufzeit und Ad-hoc-Dauer werden über UTC berechnet |
| Zweites Auftreten von 02 Uhr | Ein bereits beendetes Fenster konnte wieder als offen gelten | Ein zusammenhängendes Intervall mit erster Endzeit und exklusivem Ende |
| Nicht existierende Uhrzeit | Eine Zeit in der Frühjahrslücke war kein realer Zeitpunkt | Grenze wird auf die erste existierende Minute verschoben; vollständig entfallene Fenster werden ausgelassen |
| Netzmessung fehlt, ist veraltet, liegt in der Zukunft oder ist ungültig | Noch bis zur Mindestladeleistung freigegeben | 0 W und zusätzliche Aufforderung zum Ausschalten des Netzladens |
| Eigene Ladeleistung nicht messbar | Geschriebener Grenzwert wurde als Verbrauch abgezogen | Kein fiktiver Eigenverbrauch; nur frische W-/kW-Messung zählt |
| Inselkontakt fehlt oder hat unbekannten Zustand | Teilweise als Netzbetrieb behandelt | Konfigurierte, unbestätigte Kontakte sperren die Regelung |
| SoC fehlt, ist NaN/unendlich oder außerhalb 0–100 % | Ungültige Werte konnten bis in den Steuerpfad gelangen | Laufende Netzladung wird pausiert, kein neuer Start |
| Grenzwert quittiert, aber ignoriert | Erfolgreicher Serviceaufruf konnte als wirksame Grenze gelten | Begrenzung wird vor Einschalten zurückgelesen; zu hoher Istwert löst einen unabhängigen Stopp aus |
| Inselbetrieb, Fensterende, Deaktivierung, Entladen der Integration oder Zielerreichung während eines Befehls | Früher geprüfte Freigabe konnte veralten | Erneute Prüfung direkt vor dem Einschalten und nach einer abgewarteten Begrenzung |
| Rücksprung nach abgeschlossenem Fenster | Kalenderzeit konnte dieselbe Nacht erneut öffnen | Gespeicherter Abschluss verhindert erneutes Öffnen, auch nach Neustart |
| Rückwärts gestellte Uhr | Ein negativer Messwert-Abstand/Schreibabstand konnte plausibel wirken | Zukunftsmessung ist ungültig; Schreibbremse blockiert nicht unbegrenzt |
| Beschädigte Anschlussgrenze | Ein ungültiger konfigurierter Wert konnte die Begrenzung abschalten | Explizit ungültige Sicherungs-/Dauergrenze ergibt kein Ladebudget |

Herstellerspezifische explizite Zuordnungen der Backup-Zustände bleiben eine
vertrauenswürdige Konfiguration. Ein nicht konfigurierter Kontakt oder eine
nicht konfigurierte Anschlussbegrenzung wird durch diese Änderung nicht erfunden.

## Reproduzierbare Nachweise

- Vollständige Suite: **1.173 Tests bestanden**, jeweils mit Python 3.14 / HA
  2026.10.2 und Python 3.13 / HA 2025.2.0. Gesamtabdeckung mindestens **96,3 %**;
  die bestehende 96-%-Schwelle wird beibehalten. Mypy und Pyright jeweils 0 Fehler.
- **4.096 deterministische Kombinationen** aus Hauslast, tatsächlicher
  Batterielast, Sollwert und defekten Leistungsdaten, mit einem unabhängigen
  physikalischen Budget als Vergleich. Diese sind in 32 der Tests enthalten.
- Gegenprobe auf unverändertem Commit
  `7d809de3e08ef927625faba57d032514541fbf8f`: Die 130 Tests aus
  `test_calendar_fault_campaign.py` ergeben **76 fehlgeschlagen, 54 bestanden**.
  Das sind fehlgeschlagene Fälle, nicht 75 voneinander unabhängige Fehler.
- **16 getrennte echte HA-Prozesse**, **2.306 Prüfungen**, zusammen etwa
  **80,84 simulierte physikalische Stunden**. Sommerzeit, Winterzeit,
  wiederholtes Fensterende, fehlender Anfang/Ende, 29.02.2028, 2100 ohne
  Schalttag, Jahreswechsel, Uhrsprung −1/+8 Stunden, wiederholte/übersprungene
  Sekunde, Rücksprung in eine abgeschlossene Nacht, Telemetrieverlust, selektiv ignorierte Grenze, Ad-hoc-Ladung über
  die doppelte Stunde. Details stehen nach dem Lauf in `reports/edge-cases.json`.
- Zusätzliche reine Kalenderprüfungen behandeln Lord Howes 30-Minuten-Wechsel,
  den übersprungenen 30.12.2011 in Samoa und ungültige Uhrzeitangaben.

```powershell
../ha-venv/Scripts/python.exe -X utf8 -m pytest
../ha-venv/Scripts/python.exe -X utf8 lab/check_edge_cases.py
../ha-venv/Scripts/python.exe -X utf8 lab/check_edge_cases.py --only fall telemetry_faults
```

`check_grid.py` prüft zusätzlich den tatsächlichen KORE-REST-/Modbus-Pfad und
das offizielle evcc mit zwei Wallboxen. Die selektive Fehlerquelle ignoriert
nur den Ladegrenzwert; der getrennte REST-Stopp bleibt bedienbar. Der Test
verlangt sowohl eine nachgewiesene Ablehnung als auch den tatsächlichen Stopp.
Die bestehenden Prozess-Neustart-/Abbruchprüfungen stehen in `check_recovery.py`.

## Bedienung und Grenzen der Aussage

Im Dashboard unter Netz/Umschaltbox stehen zusätzlich **Grid Contact Online**
und **Ignore Charge Limit Only**. Das zweite Fehlerbild lässt den Wechselrichter
einen Grenzwert quittieren, ohne ihn anzuwenden. Der Schalter **Lab SoC Sensor
Online** ist nur für den generischen HA-Simulator; er macht den nativen
KORE-SoC-Sensor nicht unabhängig offline. **Online** unterbricht den gesamten
Wechselrichterpfad. Fehler lassen sich über ihre Schalter wieder zurücknehmen.

Die Testuhr verändert ausschließlich wegwerfbare Unterprozesse. Sie verstellt
die Rechneruhr und das interaktive Labor nicht. Polling und Datums-Callbacks
werden ausdrücklich aufgerufen; das ist keine Beschleunigung aller HA-Timer
oder der evcc-Uhr. Die HA-Instanz muss bei jeder Stichprobe tatsächlich laufen.
Ihre temporäre HTTP-Konfiguration wird über HAs eigene API bestätigt, damit
deren normaler Fünf-Minuten-Rücksetzschutz die Messung nicht verfälscht.

Es gibt keinen separaten Gregorianischen „Schaltmonat“ oder eine Schaltminute.
Python/POSIX stellt eine Sekunde 60 nicht als normale Uhrzeit dar. Die Kampagne
prüft deshalb synthetisch wiederholte und übersprungene Sekunden; sie behauptet
keine angekündigte Schaltsekunde an den verwendeten Beispieldaten.
Siehe [Python datetime](https://docs.python.org/3/library/datetime.html).

Der Test weist korrekte Software-Reaktionen unter den genannten Annahmen nach.
Er zertifiziert keine elektrische Anlage und deckt nicht jeden unbekannten
Fehler ab. Eine eingefrorene, weiterhin plausibel gemeldete Messung ist ohne
unabhängigen Sensor/Heartbeat nicht erkennbar. Wenn sämtliche Schreibwege
ausfallen, kann HA einen Stopp anfordern, aber nicht physisch erzwingen.
Geräteinterne BMS-, Netz-, Temperatur-, Strom- und Spannungsschutzfunktionen
müssen unabhängig wirksam sein. Modellgrenzen zu Phasenströmen, Schutzzeiten,
Firmware, weiteren Schreibern und Wallbox-Protokollen stehen in
[SCENARIOS.md](SCENARIOS.md).

Die CI nutzt für die fest vorgegebene GitHub-Release-Metadatenadresse ihren
lesenden Job-Token. Prüfsummen- und Archiv-Downloads erhalten diesen Token nicht;
die SHA256- und Release-Digest-Prüfung bleibt verpflichtend. Zwei Tests prüfen
beide Anmeldemodi und dass veränderte Downloads kein installiertes Binary ersetzen.
