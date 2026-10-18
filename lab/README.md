# Home-Assistant-Labor für Inverter Charge Night

Der rekonstruierte Entwicklungsstand vom 18.10.2026 steht in
[docs/DEVELOPMENT_HANDOFF.md](../docs/DEVELOPMENT_HANDOFF.md).

Eine eigene Home-Assistant-Instanz zum Ausprobieren der Nachtladung, mit echtem
Einrichtungsassistenten, echten Entitäten, Serviceaufrufen, Recorder und Oberfläche.
Der Wechselrichter ist ein Energiemodell. Die Integrationen werden aus den beiden
Checkouts kopiert, einschließlich der lokal geprüften Korrekturen.
Die [Szenarienmatrix](SCENARIOS.md) nennt Nachweise und Modellgrenzen.
Die [Kalender- und Fehlerkampagne](EDGE_CASES.md) ergänzt Sommer-/Winterzeit,
Schaltjahre, Uhrsprünge, Messausfälle und quittierte, ignorierte Ladegrenzen.

## Schnellstart mit Home Assistant Container

Voraussetzung: laufender Linux Docker Engine mit Compose. Die Konfiguration
verwendet Bridge Networking; für diese Simulation sind weder Host Networking,
USB-Geräte, privilegierte Container noch LAN-Gerätesuche notwendig.

Beide Repositories liegen am besten nebeneinander:

```text
InverterChargeNight/
KostalKore/
```

Im InverterChargeNight-Checkout:

```bash
python lab/prepare.py --hacs
python lab/evcc_config.py
docker compose -f lab/compose.yaml up -d homeassistant
docker compose -f lab/compose.yaml run --rm evcc settings set --force --config /config/evcc.yaml --database /config/evcc.db lp1.maxCurrent 32
docker compose -f lab/compose.yaml run --rm evcc settings set --force --config /config/evcc.yaml --database /config/evcc.db lp2.maxCurrent 32
docker compose -f lab/compose.yaml up -d evcc
```

Dann <http://127.0.0.1:8123> öffnen und den lokalen Benutzer selbst anlegen.
Anschließend links **ICN Labor** öffnen oder <http://127.0.0.1:8123/icn-lab> aufrufen.
Inverter Charge Night wird automatisch durch seinen echten vierstufigen
Konfigurationsfluss mit den simulierten Entitäten eingerichtet.

Der Standard ist **Home Assistant 2026.10.2**, die am 17.10.2026 überprüfte stabile
Version. Die Version bleibt für reproduzierbare Versuche fest. Für einen
bewussten Test der jeweils neuesten Version `HA_VERSION=stable` in `lab/.env`
eintragen und `docker compose -f lab/compose.yaml pull` ausführen. `HA_PORT`
kann in derselben Datei einen anderen lokalen Port festlegen.

```bash
docker compose -f lab/compose.yaml logs -f --tail=100
docker compose -f lab/compose.yaml stop
```

`lab/runtime` enthält Konfiguration, Datenbank und Anmeldedaten und ist von Git
ausgenommen. `prepare.py` synchronisiert nur die drei verwalteten Komponenten.
Vorhandene YAML-Dateien und die HA-Konfiguration in `.storage` bleiben erhalten.
Nach Codeänderungen erneut vorbereiten und HA neu starten:

```bash
python lab/prepare.py
docker compose -f lab/compose.yaml restart
```

`--refresh-config` ersetzt bewusst die beiden YAML-Dateien durch die Vorlagen.
Die verwendeten Git-Revisionen stehen in `runtime/lab-sources.json`. Lokale
Änderungen an der Integration werden ebenfalls kopiert; der SHA bezeichnet
deshalb die Basisrevision, keinen unveränderlichen Build.

## Lokaler Windows-Entwicklungsmodus

Für den vorhandenen Arbeitsplatz ist auch ein experimenteller Start ohne Docker
vorbereitet. Es laufen der echte HA-Core und das echte HA-Frontend. Das ist
**keine offiziell unterstützte Home-Assistant-Installation** und enthält weder
Supervisor noch HAOS-Apps. Sprachassistent und Multimedia werden nicht als
Laborfunktionen geprüft. Die Audio-Bibliotheken müssen trotzdem installiert sein:
HA lädt sie auch beim Abrufen der allgemeinen Diensteliste für das Frontend.

Der normale HA-Prozessstarter benötigt POSIX-Funktionen. `run_native.py` nutzt
deshalb direkt den HA-Bootstrap. Ein Windows-Adapter normalisiert ausschließlich
die Pfade der Stack-Erkennung; er schaltet die HA-Prüfungen nicht aus. Es werden
keine Dateien in Home Assistant verändert. HTTP bindet in diesem Modus nur an
`127.0.0.1`.

Neuinstallation mit `uv` und Python 3.14:

```powershell
uv venv .venv-lab --python 3.14
uv pip install --python .venv-lab/Scripts/python.exe -r lab/requirements-native.txt -r requirements-dev.txt
.venv-lab/Scripts/python.exe -X utf8 lab/build_windows_audio.py
.venv-lab/Scripts/python.exe -X utf8 lab/prepare.py --hacs
.venv-lab/Scripts/python.exe -X utf8 lab/install_evcc.py
.venv-lab/Scripts/python.exe -X utf8 lab/evcc_config.py
pwsh -File lab/manage.ps1 start
pwsh -File lab/manage_evcc.ps1 start
```

`manage.ps1` findet `.venv-lab` oder die bereits eingerichtete benachbarte
`ha-venv`. Ein anderer Interpreter kann mit `-Python C:\Pfad\python.exe`
angegeben werden. Dieser muss HA 2026.10.2 und die Labor-Abhängigkeiten enthalten.

### Zweites Labor neben einer bereits laufenden Instanz

Die frühere Instanz kann die Standardports weiter belegen. Dafür unterstützt
das Labor eine getrennte IPv4-Loopback-Adresse für den Wechselrichter sowie
eigene Ports für HA, evcc und die simulierten Wallboxen:

```powershell
.venv-lab/Scripts/python.exe -X utf8 lab/evcc_config.py --device-port 18082 --ui-port 7071
pwsh -File lab/manage.ps1 start -Port 8124 -InverterHost 127.0.0.2 -DeviceApiPort 18082
pwsh -File lab/manage_evcc.ps1 start
```

Danach HA unter <http://127.0.0.1:8124> und evcc unter
<http://127.0.0.1:7071> öffnen. Beide verwenden ausschließlich die neue
Konfiguration in diesem Checkout. Für spätere Starts dieselben HA-Parameter
verwenden. `status` zeigt die tatsächlich gestartete HA-Adresse aus dem
Prozessnachweis; `stop` betrifft nur den von diesem Checkout gestarteten Prozess.

Der evcc-Start setzt auch getrennte Protokollports: Bei UI-Port 7071 verwendet
EEBUS Port 17071 und OCPP Port 27071. Bestehende EEBUS-Zertifikate und
Kopplungsdaten bleiben erhalten. Die Grid-/Proxy-Kampagnen verwenden dieselbe
Initialisierung für ihre eigenen UI-Ports.

Vor den gekoppelten Tests das interaktive Labor dieses Checkouts stoppen und
warten, bis `status` keinen Prozess mehr meldet. Tests auf der getrennten
Wechselrichteradresse starten:

```powershell
$env:ICN_LAB_INVERTER_HOST = '127.0.0.2'
.venv-lab/Scripts/python.exe -X utf8 lab/check_kostal.py
.venv-lab/Scripts/python.exe -X utf8 lab/check_grid.py
.venv-lab/Scripts/python.exe -X utf8 lab/check_communication.py
```

Der Standard bleibt `127.0.0.1`; andere IPv4-Loopback-Adressen sind erlaubt.
LAN-Adressen werden abgelehnt. Die Portsperre des KORE-Tests prüft dieselbe
Adresse, zu der sich seine Clients anschließend verbinden.

Der Audio-Build benötigt Visual Studio C++ Build Tools. Der Helfer lädt die
festgelegten offiziellen PyPI-Quellen, prüft ihre SHA256-Werte und ändert nur die
Buildpfade in `setup.py` von absolut auf relativ. Dadurch bleiben die von MSVC
erzeugten Objektpfade kurz genug; die Implementierung der Bibliotheken bleibt
unverändert. Der Buildnachweis steht in `lab/reports/windows-audio-build.json`.
Der native Starter prüft die vollständige HA-Diensteliste vor seiner
Bereitschaftsmeldung, damit eine solche Abhängigkeit nicht erst nach dem Login
zu einer hängenden Oberfläche führt.

```powershell
pwsh -File lab/manage.ps1 status
pwsh -File lab/manage.ps1 stop
pwsh -File lab/manage.ps1 test
```

Stop sendet eine lokale Stop-Anforderung, damit HA seine Daten sauber schreibt.
Die Startprotokolle liegen in `runtime/native.stdout.log` und
`runtime/native.stderr.log`. Nicht gleichzeitig Container und nativen Start mit
derselben Konfiguration betreiben. Änderungen an den Dateien werden erst nach
einem Neustart geladen.

## Was sich einstellen lässt

Die voreingestellten Werte sind **Beispieldaten**, keine aus dem echten Haus
ausgelesene Konfiguration:

| Größe | Voreinstellung |
|---|---|
| Nutzbare Akkukapazität | 35,8 kWh; in `configuration.yaml` änderbar |
| Lade-/Entladewirkungsgrad | 90 % / 95 % |
| Hauslast | 600 W |
| Ladeleistung | maximal 5.000 W |
| SoC-Grenzen des Planers | 8–95 % |
| Tarifzeit | 23:00–05:00 Uhr, Europe/Berlin |
| Saison | jährlich 1. Oktober bis 31. März |
| Planungsmodus | Bridge |
| Hausanschluss | 3 × 63 A, 80 % Dauerlastbudget, 500 W Reserve |

Im Labor-Dashboard lassen sich aktueller SoC, PV-Leistung, Hauslast, externe
Autolast, heutige und morgige PV-Prognose verändern. Die Autolast beeinflusst
den gemeinsamen Netzbezug, wird aber nicht durch Inverter Charge Night gesteuert.
Der simulierte Hausverbrauchszähler zählt die Hauslast ohne diese externe Last.

Die Szenarien `winter_cloudy`, `winter_sunny`, `summer_surplus`, `ev_charging`,
`almost_full`, `forecast_failure`, `inverter_offline`, `rejected_commands` und
`island` setzen die Anlage samt Energiezählern zurück. Die entsprechenden
Schalter erlauben Fehler auch während eines laufenden Versuchs.

Für einen Versuch tagsüber: **Winter · bewölkt** wählen, **Auf 70 % laden**
drücken und `Physics Speed` etwa auf `60` setzen. Den Ladezustand und die
Serviceaufrufe am echten Wechselrichtermodell beobachten. Danach den
Wechselrichter über die Schaltfläche zurücksetzen.

**Zeitraffer betrifft ausschließlich das Energiemodell.** Die HA-Uhr, Tarifzeit,
Sonnenzeiten, Recorder-Zeitstempel und die Frist der Ladeplanung bleiben in
Echtzeit. Ein schneller Akku führt deshalb bei laufender Neuberechnung zu
anderen Leistungsprofilen als eine echte sechsstündige Nacht. Für Zeitgrenzen,
Mitternacht, Saisonwechsel und Sommerzeit gibt es außerdem die bestehenden
deterministischen Tests. Dieses Labor behauptet keine beschleunigte vollständige
HA-Zeitreise.

Die Prognosen sind unabhängige Eingaben in kWh. Die tatsächliche PV-Leistung
wird separat eingestellt, damit Prognosefehler bewusst ausprobiert werden können.
Es wird kein Wetterdienst angefragt. Das Modell berücksichtigt Leistungsgrenzen,
SoC-Grenzen, Ladeverluste, Entladeverluste, Eigenverbrauch, Einspeisung und
ungedeckte Last im Inselbetrieb. Es modelliert keine Zellchemie, BMS-Kennlinie,
Temperaturabhängigkeit oder echte Wechselrichterfirmware.

## KostalKore und HACS

`prepare.py` installiert beide lokalen Integrationen in die getrennte
HA-Konfiguration. Ein abweichender zweiter Checkout ist über
`--kostal-repo C:\Pfad\KostalKore` auswählbar.

Mit `--hacs` kommen die Dateien der aktuellen offiziellen HACS-Veröffentlichung
dazu; Version und SHA256 stehen im Quellenprotokoll. Nach dem Neustart kann HACS
über **Einstellungen → Geräte & Dienste → Integration hinzufügen → HACS**
eingerichtet werden. Die GitHub-Gerätefreigabe erledigt der Benutzer selbst.
Beide Repository-URLs können dann als benutzerdefinierte Integrationen hinzugefügt
werden. HACS-Downloads ersetzen die Entwicklungskopien in `runtime`, nicht die
Quelldateien im Git-Checkout. Für Arbeit am lokalen Code wieder `prepare.py`
ausführen.

Der Standardversuch richtet auch KORE ein (`kostal_emulator: true` und
`auto_configure_kore: true`). Für ein reines ICN-Labor beide Optionen abschalten.
Nach dem Neustart
stehen **innerhalb derselben Instanz** ein virtueller Plenticore über
`127.0.0.1:80` und Modbus TCP über `127.0.0.1:1502`, Unit-ID `71`, bereit. Diese
Ports werden im Container nicht auf dem Host veröffentlicht.

KostalKore kann im Einrichtungsassistenten mit diesen öffentlichen Testdaten
verbunden werden: Host `127.0.0.1`, Passwort `lab-only`, Servicecode `123456`,
Modbus aktiv, Port `1502`, Unit-ID `71`. Nur diese lokalen Adressen verwenden.
Die verwaltete Instanz `ICN Lab` wird auf die KORE-Entitäten umgestellt;
es wird kein zweiter Planer angelegt. Andere Konfigurationseinträge werden nicht verändert.

Der Emulator implementiert SCRAM-SHA256/AES-GCM-Anmeldung, REST-Telemetrie,
Min-SoC, manuelles Netzladen, Entladesperre sowie die benötigten Modbus-Limits
1038/1040 und 1280/1282. Andere Funktionen sind teilweise nominale Simulationswerte
oder werden ausdrücklich abgelehnt. Keine Aussage über reale Firmware,
Installateurberechtigungen, G3-Fallback oder die Koexistenz mit nativen Reglern
aus einem erfolgreichen Simulationsversuch ableiten.

## Automatische Prüfung

Im aktivierten Python-Testenvironment:

```bash
python -X utf8 -m pytest
python -X utf8 lab/check_real_ha.py
python -X utf8 lab/check_kostal.py
python -X utf8 lab/install_evcc.py
python -X utf8 lab/check_grid.py
python -X utf8 lab/check_recovery.py
```

Der erste Befehl prüft die vorhandene Suite samt Energiebilanztests und unverändertem
Coverage-Gate. `check_real_ha.py` prüft den echten vierstufigen Konfigurationsfluss,
die Scheduler-Aktivierung, Forecast-Eingaben, physische Ladung, Zielabschaltung,
Verluste, Reset, Inselbetrieb, ignorierte Schreibbefehle und Wiederverbindung.

`check_kostal.py` lädt beide Integrationen aus den Checkouts in eine zusätzliche
temporäre HA-Instanz. Es prüft mit den echten Clients die Anmeldung,
REST-Schreibbefehle, Modbus-Limits, KORE-Einrichtung und die Verbindung von ICN
mit KORE-Entitäten. Dafür müssen lokal Port 80 und 1502 frei sein. Der zweite
Checkout kann mit `KOSTAL_REPO_PATH` gewählt werden.

Ergebnisse werden unter `lab/reports` als JSON gespeichert. Die zusätzliche
GitHub-Actions-Datei `ha-lab.yml` beschreibt dieselben Prüfungen unter Linux;
sie läuft auf dem Labor-PR sowie nach dessen Übernahme.

## evcc und gemeinsamer Netzanschluss

evcc läuft als eigene Anwendung unter <http://127.0.0.1:7070>, mit zwei
32-A-Ladepunkten. Der Installer prüft die offizielle Release-Prüfsumme und den
GitHub-Asset-Digest. Beim ersten Öffnen kann die normale evcc-Administratoreinrichtung
erscheinen; die Authentifizierung wird nicht abgeschaltet. Das native evcc bindet
laut upstream an alle Netzwerkschnittstellen, auch wenn `network.host` lokal ist.
Der Gerätesimulator selbst bindet ausschließlich an Loopback, Port 18080.
Im Compose-Entwurf werden HA und evcc über dieselbe Netzwerk-Namespace gekoppelt,
die veröffentlichten Ports sind auf Host-Loopback begrenzt. Dieser Container-Start
wurde auf dem vorhandenen Windows-Rechner mangels laufendem Docker-Daemon nicht ausgeführt.

```powershell
pwsh -File lab/manage_evcc.ps1 status
pwsh -File lab/manage_evcc.ps1 stop
```

Die Aktualisierung von evcc erfolgt getrennt vom HA-Prozess. Vor `check_kostal.py`
oder `check_grid.py` das interaktive HA-Labor stoppen: beide verwenden ebenfalls
die lokalen REST-/Modbus-Ports 80/1502. Die Testinstanzen verwenden getrennte Datenbanken.
`check_recovery.py` benutzt separate Prozesse, eine kontrollierte Testuhr, einen
absichtlichen Prozessabbruch und eine Kopie der simulierten physischen Anlage.

## Befunde und Grenzen des aktuellen Stands

Auf diesem Windows-Arbeitsplatz läuft die Instanz im oben beschriebenen
Entwicklungsmodus. Der Docker-Client ist installiert, der Daemon war aus dieser
Sitzung nicht zugänglich; Compose wurde syntaktisch validiert, der Container hier
noch nicht ausgeführt. Das Linux-CI wurde lokal ebenfalls nicht ausgeführt.

Der KORE-Ausgangsstand ließ lesbare RW-Modbus-Limits dauerhaft `unknown`.
Die lokale Korrektur aus KORE-PR #62 liest die erlaubten Einstellungen auf dem
ersten Poll, nach Schreibbefehlen und danach im langsamen Intervall. Beide
gemeinsamen Prüfungen verwenden jetzt auch die tatsächlichen KORE-Limit-Entitäten.
Der ICN-Befund #9 wird in PR #10 korrigiert: eine notwendige Reduzierung auf 0 W
wartet nicht mehr auf die 30-Sekunden-Schreibsperre. Die Fehler wurden jeweils
vor der Korrektur reproduziert. Beide PRs bleiben während der CI-Prüfung Entwürfe.

Die vorhandenen Typprüfungen der unveränderten ICN-Produktivdateien melden mit
HA 2026.10.2 jeweils neun Schema-Typfehler: HA annotiert dort inzwischen
`probatio.Schema`, während ICN `voluptuous.Schema` übergibt. Die realen
Konfigurationsflüsse und Serviceaufrufe funktionieren im ausgeführten Test.
Die Typfehler sind ein separater Kompatibilitätsbefund; das Labor verändert
diese Schema-Dateien nicht und unterdrückt die Fehler nicht. Der Linux-CI-Lauf
von ICN-PR #10 bestätigt den Fehler auf aktuellem HA; der vollständige Floor-Lauf
auf HA 2025.2.0 besteht inklusive beider Typprüfungen und echtem HA-Smoke-Test.
