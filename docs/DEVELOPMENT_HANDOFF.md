# Entwicklungsstand und Wiederaufnahme – 18.10.2026

## Nachtrag: Pruefung der veroeffentlichten Versionen am 18.10.2026

Dieser Nachtrag ersetzt die weiter unten dokumentierten historischen PR- und
Branch-Zustaende. ICN PR #17 und KORE PR #66 sind gemergt. Die neuen Releases
`v3.8.3` (ICN, Commit `ad7ea56`, Manifest 3.10.1) und `v3.0.3` (KORE, Commit
`e499590`, Manifest 3.0.5) wurden lokal installiert und gemeinsam geprueft.
Die abweichenden Release-/Manifest-Versionen stehen in ICN Issue #23 und KORE
Issue #67; veroeffentlichte Tags wurden nicht verschoben.

ICN [PR #25](https://github.com/Puma7/InverterChargeNight/pull/25) enthaelt
Version **3.10.2** und schliesst Issues #24, #26 und #27. Er behebt verzoegerte
Ladegrenzen-Rueckmeldung bei Effizienzmessungen, bestaetigt den Messpunkt auch
ohne periodischen Verifikationslauf, behandelt einheitenlose kW-Regler auf
allen AC-Grenzen-Lesepfaden konsistent und wertet den letzten Messpunkt vor
der eigenen Ruecksetzung am Fensterende aus. Die Laborreparaturen warten auf
beobachtete KORE-Werte, umgehen fremde HA-Hintergrundtasks und isolieren die
EEBUS-/OCPP-Ports einschliesslich konfigurierter Geraete-/Proxy-Endpunkte.
Den verbindlichen Review-/Merge-Status direkt im PR pruefen. Ein Merge erstellt
kein Release: 3.10.2 muss danach separat veroeffentlicht werden.

Nach den Reviewkorrekturen: 1.401 lokale ICN-Tests, 96,66 % Gesamt-Coverage,
mypy und pyright ohne Fehler. Zusaetzlich wurden sechs Abnahmekampagnen mit
echtem HA, KORE, evcc und Simulatoren sowie 16 Problemfaelle mit 2.306
Einzelpruefungen ausgefuehrt. Eine fuenfminuetige stationaere Beobachtung
beider zuletzt geprueften Codebaeume lieferte je 93 erfolgreiche HTTP-Abfragen
und keine neuen WARN-/ERROR-/CRITICAL-Meldungen. Die laengere gekoppelte
Effizienzkampagne verlangt auch die bestaetigte Rueckgabe der urspruenglichen
Grenzen (Min-SOC 8 %, AC-Limit 5.000 W, Netzladen aus).

Nachweise sind privat/generiert unter `lab/reports/release-audit-20261018`.
Startmeldungen bleiben getrennt sichtbar: Windows-FFmpeg-Probe, nicht emulierte
KORE-Schattenmanagement-API, Custom-Integration-Hinweise und evccs einmalige
Cache-Warnung. Ein externer HA-Analytics-DNS-Fehler wurde ebenfalls beobachtet.
Dies sind keine Nachweise fuer Hardwarefehler oder vollstaendige Firmware-
Unterstuetzung. Das interaktive Labor weiterhin nur auf HA 8124, evcc 7071,
Geraete-API 18082 und Inverter 127.0.0.2 starten; die andere Instanz nicht stoppen.

## Ziel

Inverter Charge Night (ICN) plant die Netzladung des Hausakkus anhand von
PV-Prognose, Verbrauch, Tarif und Anschlussbudget. Für diesen Anwendungsfall:
35,8 kWh Bruttokapazität, günstiges Fenster 23–05 Uhr, Saison Q4/Q1.
KostalKore liefert die Wechselrichter-Entitäten und übernimmt REST-/Modbus-I/O.
evcc regelt zwei simulierte Wallboxen am selben Netzanschluss; es erhält keinen
schreibenden Batteriezugriff. Die Tests steuern ausschließlich Simulatoren.

## Wiedergefundener Stand auf GitHub

| Bestandteil | Stand |
|---|---|
| ICN `main` | `66f565f0ee8f68fc39451faacf2e48117de1420e`, Version 3.10.0, PR #16 gemergt |
| Aktueller Arbeitszweig | `fix/min-soc-window-handoff`, Basis `c1d34dc6813a736ad380a5ad5f2e7da7d1cff046`, Version 3.10.1 |
| Offener PR | [#17](https://github.com/Puma7/InverterChargeNight/pull/17), als Entwurf angelegt |
| KostalKore `main` | `7dd51b5a7925a6280ef5152f6e540da871f619f9`, Version 3.0.5 |
| Home Assistant | 2026.10.2, Python 3.14.7, echtes Core und Frontend |
| evcc | 0.317.1, offizielles Windows-Binary mit geprüfter SHA256 |
| HACS-Dateien | 2.0.5; GitHub-Gerätefreigabe ist Benutzerschritt |

Beide Repositories liegen nebeneinander im aktuellen Arbeitsverzeichnis.
Die GitHub-Arbeit war nicht verloren. Die Laufzeitdateien des früheren
Arbeitsplatzes wurden für diesen Checkout nicht übernommen. Die frühere
Instanz lief bei der Rekonstruktion weiterhin auf den Standardports.
Ihr Benutzer, ihre Datenbank und ihre Einstellungen wurden nicht verändert.

## Der offene Min-SOC-Fehler

Nach dem Nachtladen wurde 8 % wiederhergestellt. Eine unmittelbar folgende
Abendreserve konnte jedoch den noch veralteten HA-Zustand von 16 % als
ursprünglichen Wert übernehmen. Der GitHub-Fix wartet auf den Serviceaufruf
und vergisst den ursprünglichen Wert erst bei passender Rückmeldung.
Fehlende Rückmeldung erhält den Wert und aktiviert die Wiederholung.

Der Labor-CI-Lauf auf dem Basiscommit scheiterte anschließend: `allow_discharge`
beendete die Reserve und setzte physisch 8 %, meldete bei verzögertem KORE-Poll
aber irreführend „nichts freizugeben“. Der Test kam dadurch nicht mehr zu seinem
bereits vorgesehenen Poll und Reset-Wiederholungsversuch.

Lokal ergänzt:

- Die Aktion unterscheidet ausstehende Rücksetzung (`reset_failed`) von einer
  nicht vorhandenen Reserve (`nothing_to_release`). Die Übersetzungen erklären
  die noch fehlende Bestätigung und die automatische Wiederholung.
- Der gekoppelte Test prüft diese Rückmeldung, liest tatsächlich über KORE
  zurück, wiederholt den Reset und kontrolliert anschließend physische Entladung.
- Tests und interaktives Labor können einen eigenen Wechselrichter-Loopback
  verwenden. Nicht lokale IPv4-Adressen werden abgelehnt. Der KORE-Test erhält
  außerdem einen freien HTTP-Port für sein temporäres HA.
- Der Effizienztest hält seinen Nachtzustand in der temporären HA-Instanz fest,
  statt ihn vom nächsten realen Sonnenstandsupdate überschreiben zu lassen.
- Die native Abhängigkeitsliste enthält auch die von HA importierten Pakete
  `aiohasupervisor` und `infrared-protocols` in den HA-Manifestversionen.

Diese Ergänzungen werden mit PR #17 zur Prüfung veröffentlicht.
Die Fehler sind in Issues #18 (Min-SOC), #19 (Effizienzsuche) und #20
(Simulatorstopp) dokumentiert. Ein Release oder Merge wurde nicht ausgeführt.

## Dieses Labor starten

Alle Befehle im ICN-Checkout ausführen. Abhängigkeiten liegen in `.venv-lab`.

```powershell
.venv-lab/Scripts/python.exe -X utf8 lab/prepare.py
.venv-lab/Scripts/python.exe -X utf8 lab/evcc_config.py --device-port 18082 --ui-port 7071
pwsh -File lab/manage.ps1 start -Port 8124 -InverterHost 127.0.0.2 -DeviceApiPort 18082
pwsh -File lab/manage_evcc.ps1 start
```

| Oberfläche oder Protokoll | Adresse |
|---|---|
| HA und lokale Erstanmeldung | <http://127.0.0.1:8124> |
| ICN-Dashboard nach Anmeldung | <http://127.0.0.1:8124/icn-lab/charging> |
| evcc | <http://127.0.0.1:7071> |
| Gemeinsames Anlagenmodell | `http://127.0.0.1:18082/api/state` |
| KORE-Wechselrichter-REST / Modbus | `127.0.0.2:80` / `127.0.0.2:1502`, Unit 71 |

Die neue Instanz besitzt ihre eigene lokale Anmeldung. Benutzer und Passwort
werden vom Benutzer angelegt; Zugangsdaten nicht in Git oder Übergaben ablegen.
Das Labor richtet KORE und ICN automatisch über deren Konfigurationsflüsse ein.
Ein erneuter Start muss dieselben drei HA-Parameter verwenden.

```powershell
pwsh -File lab/manage_evcc.ps1 stop
pwsh -File lab/manage.ps1 stop
pwsh -File lab/manage.ps1 status
```

`stop` fordert HA zum sauberen Herunterfahren auf. Vor einem gekoppelten Test
warten, bis kein verwalteter Prozess mehr läuft. Niemals eine Portkollision
ignorieren oder Tests auf eine bereits laufende interaktive Instanz richten.

## Prüfbefehle und Nachweise

```powershell
.venv-lab/Scripts/python.exe -X utf8 -m pytest -q
.venv-lab/Scripts/python.exe -X utf8 -m mypy --python-version 3.14 custom_components/inverter_charge_night/
.venv-lab/Scripts/python.exe -X utf8 -m pyright --pythonversion 3.14 --pythonpath .venv-lab/Scripts/python.exe
$env:ICN_LAB_INVERTER_HOST = '127.0.0.2'
.venv-lab/Scripts/python.exe -X utf8 lab/check_real_ha.py
.venv-lab/Scripts/python.exe -X utf8 lab/check_kostal.py
.venv-lab/Scripts/python.exe -X utf8 lab/check_grid.py
.venv-lab/Scripts/python.exe -X utf8 lab/check_communication.py
.venv-lab/Scripts/python.exe -X utf8 lab/check_recovery.py
.venv-lab/Scripts/python.exe -X utf8 lab/check_edge_cases.py
```

Die komplette ICN-Suite bestand nach der vertieften Nachprüfung lokal mit
1.380 Tests und 96,64 % Coverage (vorher 1.373 / 96,62 %).
Mypy und Pyright: jeweils 0 Fehler mit dem expliziten Python-3.14-Interpreter.
Der echte HA-Test bestand 21 Prüfungen, einschließlich der Diensteliste des
Frontends. Alle zusätzlichen Kampagnen wurden abgeschlossen:

| Kampagne | Ergebnis |
|---|---|
| Echter HA-Konfigurationsfluss, Dienste und Energiemodell | 21/21 bestanden |
| KORE REST/Modbus, Min-SOC-Übergabe und Effizienzsuche | 48/48 bestanden |
| evcc und gemeinsamer Anschluss unter Last/Netzfehlern | 19/19 bestanden |
| REST-/Modbus-/Proxy-Ausfälle und Wiederverbindung | 28/28 bestanden |
| HA-Neustart, Prozessabbruch, Ausfallnachrechnung, Inselstart | 7/7 bestanden |
| Kalender, Sommerzeit und fehlerhafte Telemetrie | 16 Szenarien, 2.306 Einzelprüfungen, keine Fehler |

Die Effizienzsuche fand die unabhängige Referenzkurve bei 8.000 W / 4 % Verlust
und der normale Planer setzte diesen Wert anschließend tatsächlich über KORE.
Nach der Reservefreigabe war Min-SOC wieder 8 %; der Akku lieferte anschließend
rund 632 W ins Haus und sein SoC sank. Das belegt die Wirkung im Energiemodell,
nicht nur einen erfolgreich quittierten Serviceaufruf.

Die interaktive Instanz läuft auf 8124, evcc auf 7071. Beide Startseiten und die
Anlagen-API auf 18082 antworteten mit HTTP 200. KORE und ICN sind automatisch
angelegt. Im Browser wurden die HA-Willkommensseite und die evcc-Oberfläche
geprüft. Der Benutzer hat anschließend die HA-Anmeldung abgeschlossen. Die
angemeldete Laboransicht wurde ebenfalls geprüft: Min-SOC 8 %, Netzladen aus,
Entladesperre aus und etwa 632 W Versorgung des Hauses aus dem Akku.
Es wurden durch den Agenten keine Zugangsdaten angelegt.

## Vertiefte Nachprüfung des Min-SOC-Fixes

Der Benutzer bat anschließend ausdrücklich um weitere Problemfälle. Zwölf
zusätzliche deterministische Tests prüfen die ursprünglichen Reserven 3, 8 und
11 %, einen ignorierten Nacht-Reset direkt vor der Abendreserve, wiederholte
Fehlversuche nach Rekonstruktion des gespeicherten Koordinatorzustands sowie
einen Wiederanlauf während der Reserve oder erst nach verpasster Freigabe.
Alle zwölf bestanden ohne weitere Änderung am Min-SOC-Produktivcode.

`lab/min_soc_recovery_campaign.py` ergänzt den echten HA/KORE-Lauf: ursprüngliche
Reserve 11 %, Anhebung auf 32 %, Reload während der Reserve, ignorierte Freigabe,
Reload während ausstehender Rücksetzung, wieder funktionierende Schreibbefehle,
Rückkehr auf 11 % und danach messbare Entladung. Ein anschließend manuell
gewählter Min-SOC von 17 % bleibt bei einem inaktiven Fensterende unangetastet.
Die zehn zusätzlichen Prüfungen dieses Abschnitts bestanden.

Dabei wurden zwei andere Fehler bestätigt und korrigiert:

- Der Simulator wartete in `Server.wait_closed()` auf offene Modbus-Clients,
  bevor er diese schloss. Der echte Laborstopp lief deshalb in den HA-Timeout.
  Ein Regressionstest mit echter TCP-Verbindung scheiterte vorher; nun bestehen
  sowohl ein wartender Client als auch ein Client mit unvollständigem Header.
  Der anschließende echte native HA-Stopp mit verbundenem KORE-Modbus-Client
  war nach 3,03 Sekunden abgeschlossen, ohne den bisherigen Close-Timeout.
- Die periodische Leistungsprüfung und die Ladefreigabe verglichen eine laufende
  Effizienzsuche mit dem älteren normalen Ladeplan und setzten erlaubte
  Testleistungen zurück. Vier Regressionen scheiterten vor der ersten Korrektur;
  der gekoppelte Lauf identifizierte anschließend auch die Ladefreigabe als
  zweiten Pfad. Sieben gezielte Regressionen decken beide Pfade ab. Die Prüfungen
  verwenden nun den aktuellen Messpunkt und weiterhin das Hausanschlussbudget.
  Zu hoher Fremdverbrauch und unlesbare Netzmessung brechen die Messung ab und
  begrenzen auf 0 W. Drift über den Messpunkt fordert weiterhin einen Stopp an.
  Die gekoppelte Effizienzkampagne ruft die Leistungsprüfung zusätzlich in jedem
  Simulationsschritt auf, damit ihr Erfolg nicht vom realen Timerabstand abhängt.
  Im abschließenden erfolgreichen Lauf wurden dabei 168 Prüfungen während
  laufender Messpunkte ausgeführt; das gefundene Optimum blieb 8.000 W / 4 %.

Min-SOC-Fix und zusätzlicher Effizienz-Fix liegen auf
`fix/min-soc-window-handoff` und werden über GitHub-PR #17 geprüft.
Bei Beginn der Nachprüfung war dieser noch ein Entwurf mit Kopfcommit
`c1d34dc`. Die lokalen Ergänzungen wurden als `fa7f9a6` und `4e9616d`
gesichert; die Veröffentlichung erfolgt über die GitHub-Verbindung mit
identischem Git-Baum. Issues #18, #19 und #20 werden durch den PR geschlossen.
Vor einer Übernahme sind die aktuellen GitHub-Checks zu prüfen.

Die JSON-Nachweise und Konsolenprotokolle liegen in `lab/reports` und sind
generierte, nicht versionierte Daten. `lab/runtime` enthält die private
HA-Konfiguration, `.storage`, Datenbank und Prozessnachweise. Nicht committen.
`prepare.py` übernimmt lokale Codeänderungen in die Laufzeitkopie; anschließend
HA neu starten. Es ersetzt weder `.storage` noch vorhandene YAML ohne
`--refresh-config`.

## Grenzen und nächste Arbeit

Die PRs [ICN #17](https://github.com/Puma7/InverterChargeNight/pull/17) und
[KostalKore #66](https://github.com/Puma7/KostalKore/pull/66) sind offen und
direkt zur Prüfung freigegeben. Codex lieferte in der ersten Runde fünf
ICN- und zwei KORE-Befunde; beide Security-Reviews waren ohne Befund.
Die sieben Code-Befunde wurden mit Regressionen nachgearbeitet:

- Min-SOC benötigt frische Rückmeldung; ein alter passender Wert reicht nicht.
  Ein expliziter Entity-Refresh kann die Rücksetzung bestätigen.
- Vor dem Unload-Reset werden Listener und periodische Verifikation gestoppt.
  Ein Lock ordnet eine bereits laufende blockierende Verifikation vor dem Reset;
  wartende Verifikationen dürfen beim Unload nicht erneut schreiben.
- Eine abgesenkte Leistung verwirft einen laufenden Effizienz-Messpunkt.
- Ein fremder ausstehender Reset wird nicht als fehlgeschlagene Reservefreigabe
  gemeldet. Der Test erzwingt verzögerte HA-Publikation und erwartet den Fehler.
- KORE speichert auch nach einem während des vorigen Schreibens eingetroffenen
  Update den letzten Snapshot. Gleichzeitiges Stoppen und Entladen ist serialisiert.
- KORE bereinigt SoH vor möglicherweise fehlschlagendem Plattform-Rollback.

Die ICN-Issues #18–#22 und KORE-Issue #65 dokumentieren diese Arbeit.
KOREs erste CI-Runde bestand auf Python 3.12 und 3.14 einschließlich des
100%-Coverage-Gates. Nach den Review-Korrekturen bestanden lokal 1.108 Tests;
das SoH-Modul erreicht 100% Branch-Coverage. Der Windows-Gesamtwert ist 99,87%,
weil zwei bestehende POSIX-Pfadvergleiche im Stack-Logger dort nicht durchlaufen.
Der Gesamt-Typcheck gegen aktuelles HA hat bekannte fremde API-Typabweichungen;
der CI-Gate prüft ihn gemäß KOREs Workflow auf Python 3.12.

Der gemeinsame Lauf mit beiden nachgearbeiteten Codebäumen bestand 48/48
Prüfungen einschließlich 168 Effizienz-Verifikationen. Der schnellere KORE-Start
benötigt im Labor jetzt ausdrücklich beobachtete Rückmeldungen aller Bindungen;
der frühere SoH-Debounce hatte diese Startbedingung nur zufällig überbrückt.
Neue Commits benötigen erneut GitHub-CI und Codex Review. Nichts wurde gemergt
oder als Release veröffentlicht.

- Windows nutzt den vorhandenen Entwicklungsstarter. Docker Engine lief hier
  nicht; ein unterstützter Linux-/Containerlauf wurde lokal nicht behauptet.
- Multimedia gehört nicht zur Prüfung. Der Windows-Selector-Loop kann bei
  FFmpeg-Probes `NotImplementedError` protokollieren, und die optionale
  TurboJPEG-DLL fehlt. Der vollständige Frontend-Dienstekatalog wurde trotzdem
  erfolgreich geladen. Nicht emulierte KORE-Register melden ausdrücklich
  `ILLEGAL_DATA_ADDRESS`; das ist keine Bestätigung ihrer Hardwareunterstützung.
- Die Abendreserve darf vorhandene Energie schon morgens für den teureren
  Abend festhalten. Der Schalter für zusätzliches Netzladen deaktiviert nicht
  diese Reservierung. Ob dieses Verhalten so gewünscht ist, bleibt eine
  Produktentscheidung; es wurde bei der Rekonstruktion nicht umgestellt.
- `adhoc_reason`, `adhoc_until`, `original_min_soc` und `pending_reset` am
  Zielsensor unterscheiden eine gewollte Reserve von einem hängenden Reset.
- Das Energiemodell ist keine echte Kostal-Firmware. `Physics Speed` beschleunigt
  nur das Modell, nicht HA-Zeit, Tarif, Timer oder Sonnenstand.
- Native Firmware-Regler, Smart AC Charge und andere Modbus-Schreiber brauchen
  weiterhin reale Hardwareprüfung; siehe KostalKores `HARDWARE_VALIDATION_TODO.md`.
- Der KostalKore-Checkout meldet unter Windows einen fehlenden Linux-Symlink
  `tests -> Tests` wegen der Groß-/Kleinschreibung. Produktivdateien wurden dort
  außer dem SoH-Lifecycle-Fix aus PR #66 nicht bearbeitet.
- Vor einem Release: vollständige gekoppelte Laborkampagne prüfen, Änderungen
  reviewen, GitHub-Labor-CI erneut ausführen und erst dann PR #17 abschließen.
