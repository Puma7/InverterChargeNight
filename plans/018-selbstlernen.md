# Plan 018: ICN lernt aus den eigenen Messdaten

> Plan vom 25.10.2026, auf Pascals Wunsch nach Release 3.13.0. Grundlage: Planer v2 (Plan 006),
> Plan 015 (gelernte Überbrückung, Sicherheitsniveau, Wetter), Effizienzsuche (Plan 010/017),
> Stand `develop` = `main` = 3.13.0.

## Status

- **Priorität**: P1. Pascal: „Warum nutzen wir nicht das, was wir an Daten zur Verfügung haben …
  sollten wir auf jeden Fall festhalten und auch schon mal umsetzen.“
- **Entscheidungen** (Rückfragen vom 25.10.):
  - Alle vier Bereiche: PV-Prognose, Ladewirkungsgrad, nutzbare Kapazität, Entladewirkungsgrad.
  - Standardmäßig **an**, mit einem Schalter *Gelernte Werte verwenden*. Gesammelt wird immer;
    angewandt wird ein Wert erst, wenn genug Daten dafür da sind. Bis dahin gilt die Einstellung.
  - Eine gelernte Fehlermarge darf die eingestellte unterschreiten, aber **nie unter 5 %**.
- **Version**: 3.14.0

## Was heute fest eingestellt ist

| Größe | Herkunft heute | Problem |
|---|---|---|
| PV-Prognose | Prognose-Entität, roh | Sie kennt Dachneigung, Abstände, Hinterlüftung und das Verhalten bei Kälte nicht. Im Winter liegt sie oft dauerhaft zu hoch, im Sommer zu niedrig, Pascal schätzt 10–20 %. Mit alternden Modulen wird es schlechter. |
| Fehlermarge | `forecast_error_margin` (10 %) | Eine Zahl für das ganze Jahr. Die tatsächliche Streuung ist im Dezember eine andere als im Mai. |
| Ladewirkungsgrad | `charge_efficiency` (0,90) | Die Effizienzsuche misst ihn seit 3.10, der Planer nimmt ihn nicht. |
| Kapazität | `battery_capacity` | Nennwert. Die nutzbare Kapazität ist kleiner und sinkt mit dem Alter. |
| Entladewirkungsgrad | `discharge_efficiency` (0,95) | Geschätzt. |

Gelernt wird bereits: das Lastprofil (Plan 006/015), die PV-Tageskurve (015 B), der Wetterfaktor
(015 D) und das Ladeoptimum (010/017).

## 1. PV-Prognose: Korrekturfaktor und Fehlerband je Jahreszeit

**Tagebuch** (eigener Speicher je Eintrag, drei Jahre):

- *Vortagesprognose* `f`: der letzte Wert der Morgen-Entität zwischen 18:00 und 24:00. Das ist
  die Zahl, mit der die Nacht plant.
- *Morgenprognose* `t`: der letzte Wert der Heute-Entität vor 9:00. Mit ihr rechnet die
  Hochpreisrettung.
- *Ertrag* `a`: die Tagessumme des PV-Zählers aus den Recorder-Statistiken. Die reichen über Jahre
  zurück, also ist `a` für jeden Tag nachträglich bestimmbar.
- Beim ersten Start werden `f` und `t` aus dem Recorder-Verlauf der letzten Tage nachgetragen (in
  der Regel zehn). Das Lernen beginnt also nicht bei null.

**Rechnung** für einen Tag `d` und eine Art (`f` oder `t`):

- Berücksichtigt werden Tage mit einer Prognose von mindestens 1 kWh, deren Kalendertag höchstens
  45 Tage von `d` entfernt liegt, in jedem Jahr.
- Gewicht = Jahreszeit × Aktualität:
  - Die Jahreszeit fällt linear auf 0 bei 46 Tagen Abstand.
  - Die Aktualität halbiert sich je 365 Tage. So kommt die Alterung der Module mit, und alte Jahre
    zählen weniger.
- Gilt erst ab **14 Tagen** im Fenster, vorher neutral (Faktor 1, eingestellte Marge).
- **Faktor** = Σ w·a ÷ Σ w·f. Das ist das Verhältnis der Energiesummen: kWh zählen, nicht Prozente
  bewölkter Tage. Begrenzt auf 0,5–1,5.
- **Fehlerband**: Rest r = a ÷ (Faktor·f) − 1, gewichtet mit w·f.
  - untere Marge = −P20(r), obere Marge = P80(r). Das ist ein einseitiges 80-%-Vertrauensband.
  - Beide Margen liegen mindestens bei 5 %, die untere höchstens bei 80 %, die obere höchstens
    bei 100 %.
- **Sichtbar**: der mittlere Fehler (energiegewichtet) roh und korrigiert. Daran sieht man, ob die
  Korrektur hilft, zum Beispiel von 18 % auf 7 %.

**Anwendung**:

- Nachtplanung (Brücken- und Freiraum-Modus):
  - Prognose × Faktor.
  - Die vorsichtige Seite (weniger Sonne) nimmt die untere Marge.
  - Die Freiraum-Seite (Akku soll tagsüber nicht volllaufen) nimmt die obere Marge.
  - Der Planer bekommt dafür eine zweite Marge (`error_margin_high_pct`).
- Hochpreisrettung und Abendausblick: Prognose × Faktor (Morgenprognose). Die Marge bleibt 0,
  das ist die knappe Rechnung aus Plan 016.
- Abregelungsausblick: Faktor und untere Marge der Morgenprognose.
- Die Ertragsüberwachung (Ausfall erkennen) bleibt beim Rohwert. Sie soll die Prognose selbst
  prüfen.

## 2. Akku: Kapazität, Lade- und Entladewirkungsgrad aus der Energiebilanz

**Quelle**: die 5-Minuten-Statistiken des Recorders, also Mittelwerte, die etwa zehn Tage lang
aufbewahrt werden. Sie werden einmal am Tag ausgewertet. Gebraucht werden:

- SOC und Akkuleistung (die Akkuseite der Effizienzmessung);
- für die Wirkungsgrade zusätzlich Hausverbrauch, Netzbezug und der PV-Zähler.

**Abschnitte**:

- Die **Vorzeichenkonvention** der Akkuleistung folgt aus den Daten: Steigt der SOC, während die
  Leistung negativ ist, zählt der Sensor das Laden negativ (Kostal).
- Ein **Abschnitt** ist eine lückenlose Folge von 5-Minuten-Werten mit gleichem Vorzeichen und
  mindestens 300 W.
- Er zählt mit mindestens 30 Minuten und mindestens 10 % SOC-Änderung in der passenden Richtung.
- Die Energie wird von Mitte zu Mitte integriert (Trapezregel).

**Proben**:

- Kapazität = |Energie DC| ÷ |ΔSOC| × 100.
- Nur ohne PV-Ertrag im Abschnitt (PV-Zähler unverändert):
  - Laden: η_Laden = Energie DC ÷ Σ(Netzbezug − Hausverbrauch).
  - Entladen: η_Entladen = Σ(Hausverbrauch − Netzbezug) ÷ Energie DC.
- Unplausibles wird verworfen:
  - eine Kapazität außerhalb 40–130 % des Nennwerts;
  - ein Wirkungsgrad außerhalb 0,6–1,0.

**Werte**:

- Gewichteter Median der Proben, Halbwertszeit 180 Tage, erst ab drei Proben.
- Begrenzungen:
  - Kapazität 60–105 % des Nennwerts;
  - beide Wirkungsgrade 0,75–0,99.

**Ladewirkungsgrad**:

- Vorrang hat der Verlust, den die Effizienzsuche am Optimum gemessen hat. Mit genau dieser
  Leistung lädt die Nacht.
- Sonst gilt die Bilanz.
- Sonst gilt die Einstellung.

**Anwendung**: Kapazität und Wirkungsgrade ersetzen an allen Stellen die Einstellung. Das sind
der Planer, die Ladeleistung, die Versuchsplanung der Suche, die Rettung und die Ausblicke.

## 3. Schalter, Anzeige, Speicher

- Schalter **Gelernte Werte verwenden** (`use_learned_values`), standardmäßig an, persistiert.
  Ist er aus, wird weiter gesammelt, aber nichts angewandt.
- Sensor **PV-Prognosekorrektur**: Zustand = Faktor in %. Attribute:
  - Margen, Tage und Status;
  - mittlerer Fehler roh und korrigiert;
  - dieselben Angaben für die Morgenprognose.
- Sensor **Nutzbare Akkukapazität**: Zustand = kWh. Attribute: Lade- und Entladewirkungsgrad samt
  Quelle, Probenzahl und Status.
- Diagnose mit beiden.
- Speicher: `homeassistant.helpers.storage.Store` je Eintrag. Er ist zu groß für die
  Eintragsoptionen und wird beim Entfernen des Eintrags gelöscht.

## Sicherheit

- Jede Größe ist begrenzt, und jeder Wert wirkt erst mit genug Daten.
- Fehlende Statistiken oder Sensoren lassen die Einstellung stehen. Der Status sagt, warum.
- Eine kleinere Kapazität führt zu mehr Prozent je kWh, also zu einem höheren Ziel. Das ist die
  sichere Richtung.
- Eine größere Kapazität ist auf 105 % des Nennwerts gedeckelt.

## Verworfen

- **Der Median der Verhältnisse als Faktor.** Bewölkte Tage mit 2 kWh hätten dasselbe Gewicht wie
  sonnige mit 30 kWh. Für den Plan zählen kWh.
- **Ein Faktor für das ganze Jahr.** Genau die jahreszeitliche Verzerrung ist der Punkt.
- **Die Kapazität aus dem Ladevorgang der Effizienzsuche.** Der Bilanzweg deckt jedes Laden und
  Entladen ab, auch ohne Suche.

## Prüfung

- Reine Module (`forecast_learning.py`, `battery_learning.py`) mit Tests für:
  - Faktor, Band und Untergrenze;
  - Jahreszeit und Aktualität;
  - Abschnitte, Vorzeichen, Proben und Plausibilität.
- Coordinator:
  - Sammeln, Nachtragen und Anwenden;
  - Schalter aus heißt eingestellte Werte;
  - zu wenige Daten heißen eingestellte Werte.
- Jede neue Regel bekommt eine Gegenprobe durch Zurücknehmen.
- Danach die ganze Suite auf 2025.2 und der neuesten Version, mypy und pyright auf 3.13 und 3.14,
  das Lab und die Link-Prüfung.
