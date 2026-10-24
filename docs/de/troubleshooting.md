# Fehlersuche und FAQ

[🇬🇧 English](../en/troubleshooting.md) · [Übersicht der Dokumentation](README.md)

## Erste Prüfungen

| Symptom | Wo du nachsiehst |
|---|---|
| Es passiert nichts | Außerhalb des Fensters ist das normal: *Fenster aktiv* ist `off`, und ICN schreibt nichts. Prüfe den Schalter *Automatik*, die Zeiten des Fensters, *Nächstes Fenster überspringen* und die aktive Jahreszeit. |
| Nach einem Neustart startet es nicht | Eine benötigte Entität (Min-SOC, Netzladeschalter, Ladestand) existiert nicht. ICN versucht die Einrichtung erneut und öffnet einen Reparaturhinweis, der die Entität nennt (**Einstellungen → System → Reparaturen**). Eine Entität, die existiert, aber nicht verfügbar ist, blockiert die Einrichtung nicht; ICN wartet dann mit dem Laden. |
| Das Ziel wirkt zu niedrig oder zu hoch | Die Attribute von *Zielladestand*: `plan_reason`, beide Grenzen, `bridge_kwh`, `surplus_kwh`, `buffer_soc`. Oder rufe die Aktion `plan_target_soc` auf. |
| Das Ziel ist genau 50 % | Keine brauchbare Prognose: der Rückfallwert. Prüfe die Prognose-Entität und ihre Einheit. |
| Die Ladeleistung wird nie geschrieben | Im Modus Freiraum wird die geplante Leistung nur angezeigt, außer es gibt eine Grenze für den Hausanschluss oder ein Ergebnis der Effizienzsuche. Attribut `applied` von *Geplante Ladeleistung*. |
| Der Akku lädt nicht | Der Ladestandssensor muss verfügbar sein, sonst wird das Netzladen zur Sicherheit gestoppt. Prüfe den Sensor des Hausanschlusses (`limited`), die Insel-Entität und das Protokoll. |
| Der Min-SOC wurde nicht zurückgesetzt | Reparaturen: *Wechselrichter-Einstellungen nicht vollständig zurückgesetzt*. Prüfe den Wechselrichter und rufe dann `reset_inverter` auf oder warte auf das nächste Fenster. |
| Die Effizienzsuche findet nichts | `last_result` und `waiting_reason` des Sensors *Effizienzsuche* ([Details](efficiency-search.md#ergebnis-ablesen)). |
| Ab einem Datum hört es endgültig auf | Die aktive Jahreszeit war absolut angegeben und ist abgelaufen (Reparaturen: *Der aktive Datumsbereich endete*). Schalte *Datumsbereich jährlich wiederholen* ein. |

## Reparaturhinweise

| Hinweis | Bedeutung |
|---|---|
| Die benötigte Entität fehlt | Eine Entität aus der Einrichtung existiert nicht. Behebe das mit **Neu konfigurieren** oder stelle sie in ihrer eigenen Integration wieder her. |
| Der Speicher kann das Haus bei Stromausfall nicht versorgen | ICN hebt den Min-SOC an, aber es ist keine Inselerkennung eingerichtet. Richte die [Inselerkennung](safety.md#stromausfall-und-inselbetrieb) ein. |
| Der aktive Datumsbereich endete | Siehe oben. |
| Wechselrichter-Einstellungen nicht vollständig zurückgesetzt | Das Zurückgeben ist mehrmals fehlgeschlagen, und ICN schreibt nichts mehr. |
| Der Wechselrichter hält das Ladelimit nicht | Der Wechselrichter ist mehrmals auf seine eigene Grenze zurückgefallen. Prüfe, ob er externe Steuerung annimmt (Kostal: *External via MODBUS*). |
| PV-Ertrag stark ausgefallen | Heute sehr wenig PV-Ertrag; die nächste Nacht lädt bis zum Maximum. |

*Kommunikation gestört* und *Verbindung wiederhergestellt* sind Benachrichtigungen, keine
Reparaturhinweise: Eine benötigte Entität ging verloren oder ist zurück
([Details](safety.md#kommunikationsverlust)).

## Eine Nacht belegen

Ein Diagnose-Download ist eine Momentaufnahme. Er ersetzt nicht den Verlauf oder ein Protokoll der
ganzen Nacht.

**Vor dem Fenster**

1. **Einstellungen → Geräte & Dienste → Inverter Charge Night → ⋮ → Debug-Protokollierung
   aktivieren.** Mach dasselbe für deine Wechselrichter-Integration. Die Protokollierung beginnt
   jetzt; frühere Meldungen lassen sich nicht wiederherstellen.
2. **Diagnose herunterladen** für beide Integrationen. Notiere die Version, die Zeitzone, das
   Fenster und was du erwartet hast.
3. Stelle sicher, dass der Verlauf den Ladestand, den Min-SOC, den Netzladeschalter, die
   Ladegrenze, die AC- und DC-Ladeleistung und die Sensoren von ICN aufzeichnet.

**Nach dem Fenster**, vor jedem Update, Neustart oder Neuladen:

1. Lade die Diagnose beider Integrationen erneut herunter (KOSTAL KORE: zusätzlich
   `kostal_kore.export_debug_bundle` ausführen).
2. **Debug-Protokollierung deaktivieren**. Home Assistant bietet dann die Protokolldatei zum
   Herunterladen an.
3. Bewahre den Verlauf vom Fensterbeginn bis nach dem Zurücksetzen auf.

Die Diagnose schwärzt Entitätsnamen. Vollständige Protokolle können private Angaben enthalten.
Prüfe sie deshalb, bevor du sie an ein öffentliches Issue anhängst.

### Was die Diagnose enthält

- `charge_control`: den Plan, den letzten Leistungsbefehl, die zurückgelesene Ladegrenze, die AC-
  und DC-Leistung, `min_soc_feedback_pct`, `grid_charge_feedback`, `original_min_soc_pct` und
  `reset_pending`. Ein Wert `null` bedeutet „nicht lesbar“, nicht 0.
- `efficiency_search`: Zustand, Fortschritt und `waiting_reason`.
- `weather`: Status, Steigung und Faktor des Wettermodells.
- den Kommunikationszustand und den Verlauf seiner Störungen.

## FAQ

**Warum 44 % und nicht 50 %?** Der Überbrückungs-Planer kauft, was der Morgen braucht, nicht mehr.
Wenn du zusätzlich eine Reserve willst, stelle einen **Sicherheitspuffer** ein. Er füllt nur Platz,
den die Sonne frei lässt, und kann deshalb nie Einspeisung verursachen
([wie](how-it-works.md#sicherheitspuffer-sicherheitsniveau-modellierter-tag-wetter)).

**Kauft der Puffer jede Nacht Energie?** Nein. Jede Nacht plant mit dem Akku, wie sie ihn vorfindet.
Ein Puffer, der tagsüber nicht gebraucht wurde, ist noch im Akku und wird nicht noch einmal gekauft.

**Funktioniert es im Sommer?** Ja, aber im Sommer füllt die Sonne den Akku meist ohnehin, und das
Ziel fällt auf dein Minimum. Viele lassen es nur von Oktober bis März laufen (die aktive
Jahreszeit).

**Welche Wechselrichter werden unterstützt?** Jeder, dessen Integration einen beschreibbaren
Min-SOC, einen Netzladeschalter und einen Ladestandssensor bietet. Entwickelt wird es an einem
Kostal Plenticore G3 mit [KOSTAL KORE](kostal-kore.md).

**Kann ich es zusammen mit evcc nutzen?** Ja, wenn evcc die Wallboxen steuert und ICN den Akku.
Lass nicht beide den Akku steuern.

**Was passiert bei einem Stromausfall?** Mit eingerichteter Inselerkennung lässt ICN den
Wechselrichter sofort los ([Details](safety.md#stromausfall-und-inselbetrieb)).

**Wo melde ich ein Problem?** Im
[Issue-Tracker](https://github.com/Puma7/InverterChargeNight/issues), mit der Diagnose und, wenn
möglich, einem Debug-Protokoll der Nacht.
