<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Puma7/InverterChargeNight/main/docs/images/logo-dark.png">
    <img src="https://raw.githubusercontent.com/Puma7/InverterChargeNight/main/docs/images/logo.png" alt="Inverter Charge Night" width="440">
  </picture>
</p>

<p align="center">
  <b>Den Hausspeicher im günstigen Nachtfenster laden – genau so viel, wie die Sonne von morgen nicht liefert.</b>
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/Puma7/InverterChargeNight/releases/latest"><img src="https://img.shields.io/github/v/release/Puma7/InverterChargeNight" alt="Neueste Version"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.2%2B-41BDF5" alt="Home Assistant 2025.2 oder neuer">
  <a href="https://github.com/Puma7/InverterChargeNight/actions/workflows/ci.yml"><img src="https://github.com/Puma7/InverterChargeNight/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Puma7/InverterChargeNight/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Puma7/InverterChargeNight" alt="MIT-Lizenz"></a>
</p>

<p align="center">
  <a href="https://github.com/Puma7/InverterChargeNight/blob/main/README.md">English</a> · <b>Deutsch</b>
</p>

---

**Inverter Charge Night** ist eine Home-Assistant-Integration für Haushalte mit zeitvariablem
Tarif: das reduzierte Netzentgelt nach §14a EnWG, ein Nachttarif oder die günstigen Stunden eines
dynamischen Tarifs. Jede Nacht entscheidet sie, **wie voll der Akku sein muss**, wenn das günstige
Fenster schließt. Sie lädt ihn genau auf diesen Stand und lässt den Rest der Kapazität für die
Sonne von morgen frei.

<p align="center">
  <img src="https://raw.githubusercontent.com/Puma7/InverterChargeNight/main/docs/images/night-and-day.de.png" alt="Eine Nacht und der folgende Tag: Der Akku wird im günstigen Fenster auf das Ziel geladen, trägt das Haus, bis die PV übernimmt, und die PV füllt ihn tagsüber" width="820">
</p>

- **Zu leer**: Das Haus kauft morgens oder abends teuren Strom.
- **Zu voll**: Die Sonne findet keinen Platz und wird für einen Bruchteil dessen eingespeist, was
  der Nachtstrom gekostet hat.
- **Das Ziel** liegt dazwischen: genug für Morgen und Abend, und Platz für die Sonne.

## Das Wichtigste

- 🔋 **Plant mit der Prognose von morgen und dem echten Verbrauch deines Hauses.** Sie lernt den
  stündlichen Verbrauch der letzten zwei Wochen und bildet den PV-Verlauf von morgen nach.
- 🛟 **Ein Sicherheitspuffer, der nie zu Einspeisung führt.** Du wählst, wie viel Reserve du willst.
  Sie füllt nur Platz, den die Sonne frei lässt, und wird einmal gekauft, nicht jede Nacht.
- 🌡️ **Wetterbewusst.** Optional lernt sie, wie viel mehr eine Wärmepumpe an einem kalten Tag
  braucht.
- 🌆 **Abendreserve.** Den Strom für eine teure Abendspitze kauft sie nachts. Dazu gibt es eine
  optionale Hochpreisrettung für Tage, die trüber werden als vorhergesagt.
- ⚡ **Schutz des Hausanschlusses.** Hängen Wallboxen am selben Anschluss, gibt der Akku nach,
  bevor die Hauptsicherung es tut.
- 📉 **Effizienzsuche.** Sie misst, bei welcher Ladeleistung dein Wechselrichter am wenigsten
  Energie verliert.
- 🎓 **Lernt aus deinen Daten.** Sie findet je Jahreszeit heraus, wie weit deine PV-Prognose
  danebenliegt und was dein Akku wirklich speichert und verliert.
- 🔌 **Erkennt Stromausfälle.** Im Inselbetrieb gibt sie den Wechselrichter sofort frei.
- ↩️ **Was sie ändert, gibt sie zurück.** Min-SOC, Schalter und Grenzen stellt sie am Ende des
  Fensters wieder her, auch nach einem Neustart.
- 🧩 **Funktioniert mit jedem Wechselrichter.** Sie braucht nur Min-SOC, Netzladeschalter und
  Ladestand als Home-Assistant-Entitäten. Entwickelt wird sie an einem Kostal Plenticore G3 mit
  KOSTAL KORE.
- 🇬🇧🇩🇪 **Komplett auf Deutsch und Englisch**, vollständig in der Oberfläche eingerichtet.

## Schnellstart

[![Home Assistant öffnen und dieses Repository in HACS öffnen.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Puma7&repository=InverterChargeNight&category=integration)

1. Füge das Repository mit dem Knopf oben zu HACS hinzu (Typ *Integration*). Lade
   **Inverter Charge Night** herunter und starte Home Assistant neu.
2. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Inverter Charge Night.**
3. Fülle die zwei Pflichtseiten aus: Min-SOC des Wechselrichters, Netzladeschalter, Ladestand,
   Akkukapazität, PV-Prognose für morgen und das Ladefenster.
4. Beobachte den Sensor **Zielladestand** ein, zwei Nächte. Ergänze dann die empfohlenen Optionen:
   Planer *Überbrückung*, einen Hausverbrauchszähler, einen Sicherheitspuffer, die
   Ladegrenze und die Erkennung des Inselbetriebs.

Schritt für Schritt: [Installation und Einrichtung](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/installation.md).

## Dokumentation

| | |
|---|---|
| [Installation und Einrichtung](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/installation.md) | Voraussetzungen, HACS, der Einrichtungsdialog, empfohlene nächste Schritte |
| [Funktionsweise](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/how-it-works.md) | Eine Nacht Schritt für Schritt, wie das Ziel entsteht, Puffer, Wetter, Abendreserve |
| [Alle Einstellungen](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/configuration.md) | Jede Einstellung mit Vorgabewert, dazu Empfehlungen |
| [Sicherheit](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/safety.md) | Rückgabe der Einstellungen, Hausanschluss, Stromausfall, Kommunikationsverlust |
| [Effizienzsuche](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/efficiency-search.md) | Die Ladeleistung mit dem geringsten Verlust finden |
| [Was ICN selbst lernt](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/learning.md) | Die Prognose je Jahreszeit, die nutzbare Kapazität, beide Wirkungsgrade |
| [Ausnahmen und Extras](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/extras.md) | Aussetzen, Vorgabe von Hand, Schneenächte, Hochpreisrettung, PV-Ausfall, Abregelung |
| [Entitäten und Aktionen](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/entities-and-actions.md) | Alle Entitäten und Attribute, Aktionen, Beispiel-Automationen |
| [Dashboard](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/dashboard.md) | Eine fertige Dashboard-Ansicht |
| [Fehlersuche und FAQ](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/troubleshooting.md) | Reparaturhinweise, Debug-Protokolle, Diagnose, häufige Fragen |
| [Kostal mit KOSTAL KORE](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/kostal-kore.md) | Welche Entität wohin gehört, und die Fallstricke |
| [Morgenentladung](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/morning-discharge.md) | Die experimentelle Gegenrichtung |
| [Entwicklung](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/development.md) | Prüfungen, Labor, Aufbau, Releases |

## Voraussetzungen

- Home Assistant **2025.2** oder neuer.
- Eine Wechselrichter-Integration, die einen schreibbaren **Min-SOC** (number), einen
  **Netzladeschalter** und den **Ladestand** (sensor) bereitstellt. Beispiele sind
  [KOSTAL KORE](https://github.com/Puma7/KostalKore), Kostal Plenticore, SMA, Fronius, Solax oder
  eine eigene Modbus-Konfiguration.
- Eine **PV-Prognose** für morgen in kWh, zum Beispiel
  [Solcast](https://github.com/BJReplay/ha-solcast-solar) oder
  [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/).

## Sicherheit zuerst

ICN schreibt an ein Gerät, das dein Haus versorgt. Außerhalb des Fensters ändert es nichts, und
alles, was es geändert hat, gibt es zurück. Fehlt ein Messwert, lädt es lieber weniger als mehr.
Bei einem Stromausfall gibt es den Wechselrichter frei.
[Lies die Seite Sicherheit](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/safety.md),
bevor du es unbeaufsichtigt laufen lässt, vor allem wenn eine Wallbox am selben Hausanschluss
hängt.

## Hilfe und Mitarbeit

- Fragen und Probleme: [GitHub-Issues](https://github.com/Puma7/InverterChargeNight/issues). Hänge
  bitte die Diagnose an, und wenn möglich ein Debug-Protokoll der Nacht
  ([so geht's](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/troubleshooting.md#eine-nacht-belegen)).
- Änderungen: siehe [Entwicklung](https://github.com/Puma7/InverterChargeNight/blob/main/docs/de/development.md).
  Pull Requests sind willkommen.
- Versionsgeschichte: [CHANGELOG](https://github.com/Puma7/InverterChargeNight/blob/main/CHANGELOG.md)
  (englisch).

## Lizenz

[MIT](https://github.com/Puma7/InverterChargeNight/blob/main/LICENSE), ohne Gewähr. Die Steuerung
von Wechselrichter und Akku geschieht auf eigenes Risiko.
