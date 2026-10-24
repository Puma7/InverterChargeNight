# Entwicklung

[🇬🇧 English](../en/development.md) · [Übersicht der Dokumentation](README.md)

## Prüfungen

Dieselben Befehle laufen in der CI (`.github/workflows/ci.yml`) gegen das älteste unterstützte Home
Assistant (2025.2, Python 3.13) und das neueste Release (Python 3.14), zusammen mit `hassfest` und
der HACS-Action. Alle müssen bestehen.

```bash
pip install -r requirements-dev.txt
pytest                                        # ganze Testsuite mit der Coverage-Ratsche
mypy custom_components/inverter_charge_night  # strikt
pyright                                       # strikt
python scripts/smoke_real_ha.py               # durchgehend gegen einen echten HA-Core
```

- **Die Coverage ist eine Ratsche.** `.coveragerc` deckt das ganze Paket ab, und `fail_under` ist
  der zuletzt gemessene Gesamtwert, abgerundet. Wer Tests hinzufügt, hebt ihn an; gesenkt wird er
  nie.
- **Strenges Test-hass.** Die Fixture `mock_hass` gibt für jede Entität `None` zurück, die ein Test
  nicht registriert hat. Registriere, was der Code liest, und prüfe Dienstaufrufe positiv.
- **Typisierung.** `mypy --strict` und `pyright` im strikten Modus über das ganze Paket, ohne
  Ausnahmen.

`AGENTS.md` beschreibt die Konventionen. `plans/` hält fest, was gefunden, entschieden und verworfen
wurde, einschließlich der Fehler, aus denen die Sicherheitsregeln entstanden sind. Lies es, bevor
du etwas änderst, das in einen Wechselrichter schreibt.

## Das Labor

[`lab/`](../../lab/README.md) enthält ein vollständiges lokales Home Assistant mit einem
physikalischen Akkumodell, simulierter PV-Anlage, Haus- und Autolasten und einem optionalen lokalen
Kostal-REST/Modbus-Emulator zusammen mit KOSTAL KORE und evcc. Die Prüfungen nutzen echte
Konfigurationsabläufe, echte Entitäten und echte Dienste:

```bash
python lab/check_real_ha.py   # Konfigurationsablauf, Entitäten, Dienste in einem echten HA
python lab/check_kostal.py    # mit KOSTAL KORE gegen den emulierten Wechselrichter
```

Der CI-Workflow `ha-lab.yml` (`acceptance`) lässt das ganze Labor bei jedem Pull Request laufen, der
`lab/`, die Integration oder den Workflow berührt.
`lab/docs_screenshots.py` erzeugt die Bildschirmfotos in dieser Dokumentation neu.

## Architektur

| Datei | Aufgabe |
|---|---|
| `__init__.py` | Einrichten, Aktualisieren und Entladen des Konfigurationseintrags. |
| `coordinator.py` | Die Regelschleife: Fenster, Planer, Schreiben, Überprüfung, Zurückgeben. Enthält auch `InverterChargeNightConfigEntry`. |
| `planner.py` | Überbrückungs-Planer und Ladeleistung, als reine Funktionen ohne Home-Assistant-Importe. |
| `calculation.py` | Freiraum-Formel und Auswertung der Prognose. |
| `heating.py` | Heizgradtag-Modell und Temperaturtagebuch für den Wetterfaktor. |
| `prices.py` | Liest eine beliebige stündliche Preis-Entität. |
| `pv_yield.py` | Tägliche Erkennung von geringem PV-Ertrag. |
| `communication.py` | Verlauf der Kommunikationsstörungen. |
| `config_flow.py`, `setup_drafts.py` | Geführte Einrichtung, Optionen und Neukonfiguration mit dauerhaft gespeicherten Entwürfen. |
| `services.py`, `services.yaml` | Die Aktionen. |
| `sensor.py`, `binary_sensor.py`, `switch.py`, `select.py`, `number.py`, `entity.py` | Entitäten an einem gemeinsamen Gerät. |
| `diagnostics.py` | Diagnose mit geschwärzten Entity-IDs. |
| `strings.json`, `translations/` | Englisch (`en.json` ist identisch mit `strings.json`) und Deutsch. |
| `icons.json`, `brand/` | Symbole der Entitäten sowie Symbol und Logo der Integration. |
| `quality_scale.yaml` | Stand gegenüber den Regeln der Qualitätsskala von Home Assistant. |

Jedes Lesen eines Zustands aus Home Assistant passiert im Coordinator. Der Planer, das Heizmodell,
der Preisleser und der Kommunikationsverlauf sind reines Python und werden ohne Home Assistant
getestet.

## Qualitätsskala

Die [Qualitätsskala für Integrationen](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
von Home Assistant bewertet Integrationen, die mit Home Assistant Core ausgeliefert werden. Eine
benutzerdefinierte Integration wird immer als *custom* geführt. Als Maßstab wird ICN an allen
Regeln in `custom_components/inverter_charge_night/quality_scale.yaml` gemessen: 43 sind umgesetzt,
und 11 gelten nicht für eine Integration, die mit den Entitäten anderer Integrationen spricht statt
mit einem Gerät. Keine ist offen.

## Übersetzungen

Alle Texte, die Nutzer sehen, stehen in `strings.json` und `translations/`. Um einen hinzuzufügen
oder zu ändern, bearbeitest du `strings.json`, kopierst die Datei nach `translations/en.json` (die
Dateien müssen identisch bleiben) und trägst den deutschen Text in `translations/de.json` ein. Ein
Test prüft, dass beide Sprachen dieselben Schlüssel haben. Die Seiten der Dokumentation liegen in
`docs/en/` und `docs/de/` und werden gemeinsam aktuell gehalten.

## Konfigurationsschlüssel

Alle Optionen werden in `data` des Konfigurationseintrags gespeichert. `options` enthält nur den
gelernten Verlauf der Effizienzsuche und den gespeicherten Laufzeitzustand. Beschriftungen und
Hilfetexte für jeden Schlüssel stehen in `strings.json`, die Standardwerte in `const.py`. Die
[Referenz der Einstellungen](configuration.md) führt sie nach Seiten geordnet auf.

## Releases

1. Erhöhe `version` in `manifest.json` und ergänze einen Abschnitt in `CHANGELOG.md`.
2. Führe den Pull Request in `main` zusammen.
3. Erstelle aus `main` ein GitHub-Release `vX.Y.Z`. HACS bietet das Update anhand des Releases an.
