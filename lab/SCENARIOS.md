# Netz, Lastmanagement und Wiederanlauf

Das Labor verwendet eine gemeinsame physikalische Bilanz. ICN schreibt über die
echte KORE-Integration, deren REST-/Modbus-Clients den lokalen Wechselrichter
ansprechen. Die unveränderte evcc-Version 0.317.1 liest denselben Netzanschluss
und regelt zwei HTTP-Wallboxen. evcc erhält keinen schreibenden Batteriezugriff.

## Aktuell ausgeführte Prüfungen

| Fall | Anregung | Beobachtete Anforderung | Nachweis |
|---|---|---|---|
| Ein Auto | 22-kW-Last | Haus, Auto und Akku erscheinen in derselben Netzbilanz | Modelltests, Dashboard |
| Zwei Autos | 2 × 22 kW, zusätzlich Akku | ICN setzt Ladegrenze auf 0 W; evcc begrenzt beide Ladepunkte | `check_grid.py` |
| Kochen und Wärmepumpe | 6 kW Wärmepumpe, 7 kW Herd, 3 kW Ofen | evcc reduziert weiter, jede Phase bleibt im Dauerbudget | `check_grid.py` |
| Weitere Verbraucher | Mikrowelle, Waschmaschine, Trockner, Spülmaschine | Leistungen und Phasenzuordnung werden addiert | Modelltests, Dashboard |
| Schieflast | 15 kW zusätzlich auf L1 | Überstrom sichtbar, obwohl die Gesamtleistung unauffällig sein kann | Modelltests |
| Manuelle Umschaltbox | Inselkontakt unabhängig vom Backup-Bit | Kein Netzbezug, keine EV-Ladung; ICN gibt Ladebefehle zurück | `check_grid.py` |
| Netzausfall ohne Umschaltung | Öffentliches Netz fehlt | Hauslast bleibt unversorgt; kein stillschweigendes automatisches Umschalten | Modelltests |
| Inselüberlast | Verbraucher über Insel-Leistungsgrenze | Nicht versorgbare Leistung wird ausgewiesen | Modelltests, `check_grid.py` |
| Überspannung | L1 = 265 V | Modell trennt den Netzpfad, ICN erhält eine Ladesperre | Modelltests, `check_grid.py` |
| Unterspannung | L2 = 190 V | Wie Überspannung | Modelltests, `check_grid.py` |
| Phasenausfall | L3 = 0 V | Kein Netzladen | Modelltests, `check_grid.py` |
| Unterstrom am Auto | Verbunden, freigegeben, 32 A angefordert, 0 W angenommen | Tatsächliche Leistung unterscheidet sich vom Sollwert | Modelltests |
| Schnee | Beide Strings vorhanden, 100 % Schneebedeckung | PV = 0 W trotz unveränderter guter Prognose | Modelltests, `check_grid.py` |
| Stringfehler | Ein String aus | Nur dessen Erzeugung entfällt; Prognose bleibt unabhängig | Modelltests, `check_grid.py` |
| Fehlende Prognose | Prognosesensor unavailable | Echter HA-Zustand und produktiver Fallback | `check_real_ha.py` |
| Wechselrichter offline | Kommunikation aus, Physik läuft weiter | Echte HA-Entitäten unavailable, gespeicherte Gerätesollwerte wirken weiter | Modelltests, `check_real_ha.py` |
| Quittierter, ignorierter Befehl | Schreibfehler-Schalter | Istwert bleibt unverändert, Audit zeigt Ablehnung | `check_real_ha.py` |
| Netzzähler offline | Messpfad aus | HTTP 503 und unavailable, kein plausibler Nullwert | `check_grid.py` |
| Akku voll / Ziel erreicht | Begrenzte Kapazität | Kein Überschwingen; Netzladen endet am Geräteziel | Modelltests, `check_real_ha.py` |
| Mitternacht | Kontrollierte Testuhr, echter Datums-Callback | Ziel und ursprüngliche Reserve bleiben erhalten | `check_recovery.py` |
| Geplanter HA-Neustart | Getrennte HA-Prozesse mit gespeicherter Konfiguration | Reserve und laufendes Nachtziel werden wieder aufgenommen | `check_recovery.py` |
| Prozessabbruch, acht Stunden Ausfall | Prozessende ohne Shutdown, anschließend Start um 09 Uhr | Geräteziel begrenzt die Ladung; altes Ladefenster wird zurückgenommen | `check_recovery.py` |
| Start in Insel | Physischer Inselkontakt ändert sich während HA aus ist | Keine neue Netzlade-Aktivierung | `check_recovery.py` |

## Modellgrenzen

Die ergänzende [Kalender- und Fehlerkampagne](EDGE_CASES.md) dokumentiert
16 vollständige Zeitläufe, deterministische Last-/Telemetrie-Gegenproben und
die daraus bestätigten Korrekturen. `check_edge_cases.py` läuft auch in der
Linux-Labor-CI; die Uhr des interaktiven Labors bleibt davon unabhängig.

- 3 × 63 A und 80 % Dauerlast sind Beispielwerte, keine erhobenen Anlagendaten.
  evcc begrenzt auf 50,4 A je Phase und 34.776 W. ICN lässt zusätzlich 500 W Reserve.
- `Grid Power` zeigt die tatsächliche Wirkleistung. `Grid Guard Power` ist ein
  konservativer Adapter: Maximum aus Gesamtleistung und höchstem Phasenstrom ×
  690 V. ICN liest diesen Adapter, weil seine eigene Leistungsgrenze keine
  individuellen Phasenströme verarbeitet. Das ist explizit eine Laborkonfiguration.
- Die Spannungsschwellen 207/253 V sind schaltbare Modellannahmen. Reale
  Schutzgeräte, Auslösezeiten, thermische Sicherungskennlinien, Kurzschluss,
  Fehlerströme, Erdung, Neutralleiter und Netzsynchronisation werden nicht nachgebildet.
  Der Schutz-Aus-Schalter ist ein eigener, verriegelter Modelleingang.
- Eine Unterstrom-Situation kann eine Ladepause oder Fahrzeugbegrenzung sein.
  Sie wird nicht pauschal als elektrischer Schutzfehler eingestuft.
- Die Ausfallnachrechnung hält die letzten physikalischen Eingänge konstant,
  maximal 24 Stunden. Sie kennt keine währenddessen erfolgten Wetteränderungen
  oder nicht aufgezeichneten Schaltvorgänge. Der Simulator speichert alle fünf Sekunden.
- Wallboxen sind dreiphasige geregelte Verbraucher bis 22 kW. Fahrzeugkennlinien,
  Anlaufströme und Protokolle bestimmter realer Wallboxen sind noch nicht validiert.
- Die Testuhr lebt ausschließlich in wegwerfbaren Testprozessen. Der Mitternachts-
  Callback wird im Test ausdrücklich ausgelöst. `Physics Speed` verstellt die
  Uhr des laufenden Home Assistant nicht.
- Gezielte Batterieentladung ins öffentliche Netz ist nicht aktiviert. PV-Überschuss
  kann exportiert werden. Ein zusätzlicher Batterie-Exportregler braucht ein eigenes
  Regelungsziel, Begrenzungen und Tests zur Koexistenz mit der Wechselrichter-Firmware.
- KORE dokumentiert offene Hardwareprüfungen zu Smart AC Charge, nativen Zeitplänen,
  EEBus und anderen Modbus-Schreibern. Der lokale Emulator ersetzt diese Prüfungen nicht.

## Beiträge

Vor jedem externen Bericht werden Reproduktion, Gegenprobe und Duplikatsuche
ausgeführt. KI-Texte werden gekennzeichnet. evcc verlangt vor einem PR eine
positive Tendenz der Maintainer in einem zugehörigen Issue oder einer Diskussion:
[CONTRIBUTING.md](https://github.com/evcc-io/evcc/blob/master/CONTRIBUTING.md).
Ein bestandener Test erzeugt keinen Fehlerbericht. Aktuell liegen bestätigte
Befunde für ICN (#9/#10) und KORE (#61/#62) vor, kein eigener evcc-Fehler.
