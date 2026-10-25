# Was ICN selbst lernt

[🇬🇧 English](../en/learning.md) · [Dokumentation](README.md)

Mehrere Zahlen in der Einrichtung sind Schätzungen: die Prognosemarge, die Akkukapazität und die
beiden Wirkungsgrade. Und die PV-Prognose kennt dein Dach nicht: die Neigung, den Abstand der
Module, ihr Verhalten bei Kälte, den Baum davor und wie weit sie gealtert sind. Seit 3.14.0 liest
ICN all das aus den eigenen Daten des Hauses und plant damit. Die Einstellungen bleiben als
Rückfallwert.

Alles ist **standardmäßig an** und braucht keine zusätzliche Einstellung, sobald die nötigen
Sensoren da sind. Jeder Wert wirkt erst, wenn genug Daten dahinterstehen. Bis dahin gilt die
Einstellung. Der Schalter **Gelernte Werte verwenden**
(`switch.inverter_charge_night_gelernte_werte_verwenden`) schaltet die Anwendung ab. Gesammelt
wird auch dann weiter.

## Die PV-Prognose, je Jahreszeit

**Was gesammelt wird:**

- Die **Abendprognose** für morgen: der letzte Wert der *Morgen*-Entität zwischen 18:00 und
  Mitternacht. Mit dieser Zahl plant die Nacht.
- Die **Morgenprognose** für heute: der letzte Wert der *Heute*-Entität vor 9:00. Mit dieser Zahl
  entscheidet die Hochpreisrettung.
- Der **Ertrag**: die Tagessumme des PV-Zählers (*PV-Ertrag heute*) aus den Statistiken des
  Recorders.

Beim ersten Start trägt ICN nach, was der Recorder noch von den Prognosen weiß, in der Regel die
letzten zehn Tage. Das Tagebuch reicht drei Jahre zurück. Ein Tag, den die Statistiken des
PV-Zählers mit weniger als 20 Stunden abdecken, bleibt offen. Dann begann die Aufzeichnung erst im
Lauf des Tages, oder Home Assistant lief zeitweise nicht.

**Was gerechnet wird**, für den Tag, der geplant wird:

- **Welche Tage zählen**: jeder Tag, dessen Kalenderdatum höchstens 45 Tage entfernt liegt, aus
  jedem Jahr. Nähere Daten zählen mehr. Ein Jahr weiter zurück zählt halb so viel, so kommen
  alternde Module und neuer Schatten von selbst mit.
- **Ab wann**: sobald **14 Tage** in diesem Fenster Prognose und Ertrag haben.
- **Faktor**: Ertrag der Jahreszeit geteilt durch die Prognose der Jahreszeit, beide als
  kWh-Summe. Ein trüber Tag mit der halben Prognose sagt wenig über das Dach, ein sonniger Tag mit
  einem Fünftel weniger sagt viel.
- **Band**: wie weit die Tage um die korrigierte Prognose streuten. Jede Kante gilt für sich: An
  vier von fünf Tagen lieferte die Sonne mindestens die untere Kante, an vier von fünf höchstens die
  obere. Beides zugleich galt an mindestens drei von fünf Tagen. Das Band ist nach beiden Seiten
  nie schmaler als **5 %**.

**Wie es angewandt wird:**

- Die Nachtplanung multipliziert die Prognose mit dem Faktor.
- Die Seite, die mit weniger Sonne rechnet (Überbrückung, Abendreserve), nimmt die untere Kante.
- Die Seite, die Platz für die Sonne freihält, nimmt die obere Kante.
- Hochpreisrettung und Abendvorschau nehmen den Faktor der Morgenprognose, aber kein Band. Sie
  rechnen absichtlich knapp.

**Voraussetzungen:** die PV-Prognose-Entitäten und der Zähler **PV-Ertrag heute** (siehe
[Einstellungen](configuration.md)).

**Sensor:** *PV-Prognosekorrektur* (`sensor.inverter_charge_night_pv_prognosekorrektur`). Der
Zustand ist der Faktor in Prozent: 92 heißt, die Sonne lieferte 8 % weniger als vorhergesagt. Die
Attribute sind:

- `margin_low_pct` / `margin_high_pct`: das Band;
- `days`: die Tage dahinter;
- `error_raw_pct` / `error_corrected_pct`: der mittlere Fehler der rohen und der korrigierten
  Prognose über diese Tage;
- `today`: dasselbe für die Morgenprognose;
- `status`: `learning`, `active` oder `no_pv_meter`.

## Der Akku: Kapazität und beide Wirkungsgrade

**Woher es kommt:** Der Recorder bewahrt von jedem Messsensor etwa zehn Tage lang 5-Minuten-Mittel
auf. Einmal am Tag geht ICN sie durch und sucht **Abschnitte** gleichmäßigen Ladens oder
Entladens. Ein Abschnitt zählt mit mindestens 300 W, mindestens 30 Minuten und mindestens 10 %
SOC-Änderung.

- **Kapazität**: die Energie auf der Akkuseite geteilt durch die SOC-Änderung.
- **Ladewirkungsgrad**, nur wenn die Module nichts liefern: die Akkuenergie gegen das, was das
  Ladegerät auf der AC-Seite aufgenommen hat (Netzbezug minus Hausverbrauch).
- **Entladewirkungsgrad**, nur wenn die Module nichts liefern: was vom Akku im Haus ankam
  (Hausverbrauch minus Netzbezug) gegen die Akkuenergie.

**Wie die Proben geprüft werden:**

- Das Vorzeichen der Akkuleistung ergibt sich aus den Daten: Ein Sensor, der das Laden negativ
  meldet (Kostal), wird erkannt.
- Eine Probe, die nicht stimmen kann, wird verworfen. Das ist eine Kapazität unter 40 % oder über
  130 % des Nennwerts oder ein Wirkungsgrad außerhalb 0,6–1,0.
- Der Wert ist der Median der Proben, neuere zählen mehr. Er gilt ab drei Proben.
- Die Kapazität ist auf 60–105 % des Nennwerts begrenzt, die Wirkungsgrade auf 0,75–0,99.

**Ladewirkungsgrad zuerst aus der Effizienzsuche:** Die Nacht lädt mit der besten Leistung, die
die Suche gefunden hat. Also nimmt der Plan den Verlust, den die Suche bei dieser Leistung gemessen
hat: Der Wirkungsgrad ist eins minus dieser Verlust. Hat ein SOC-Band ein eigenes Optimum, lädt
die Nacht dort mit dessen Leistung, und es gilt auch dessen Verlust. Die Energiebilanz gilt nur
ohne Suchergebnis.

**Voraussetzungen:**

- SOC und *Angekommen* (die Akkuseite, wie bei der [Effizienzsuche](efficiency-search.md)) für die
  Kapazität.
- Für die Wirkungsgrade zusätzlich *Hausverbrauch*, *Netzbezug* und *PV-Ertrag heute*.
- Die Sensoren brauchen Langzeitstatistiken (Zustandsklasse `measurement`). Die hat fast jeder
  Leistungssensor.

**Sensor:** *Nutzbare Akkukapazität* (`sensor.inverter_charge_night_nutzbare_akkukapazitat`) in
kWh. Die Attribute sind:

- `charge_efficiency` und `charge_efficiency_source` (`efficiency_search` oder `energy_balance`);
- `discharge_efficiency`;
- die Zahl der Proben je Art;
- `*_used`: womit der Plan gerade rechnet;
- `status`: `learning`, `active`, `no_sensors` oder `no_statistics`.

## Sicherheit

- Jeder gelernte Wert ist begrenzt und wirkt erst mit genug Daten dahinter.
- Fehlende Sensoren oder Statistiken lassen die Einstellung stehen.
- Eine neue Prognose-Entität, ein neuer PV-Zähler oder ein neuer Akkusensor beginnt das, was aus
  ihm gelernt wird, von vorn. Eine neue eingetragene Akkukapazität beginnt die Kapazität von vorn.
  Eine neue *Heute*-Entität etwa behält die Abendprognosen und die Erträge. Die Akkustatistiken
  der Tage vor der Änderung werden nicht noch einmal gelesen.
- Eine kleinere Kapazität heißt mehr Prozent je kWh und ein höheres Ziel. Das ist die sichere
  Richtung. Eine größere ist auf 105 % des Nennwerts gedeckelt.
- Die Ertragsüberwachung ([Extras](extras.md)) vergleicht weiter die rohe Prognose. Sie soll die
  Prognose selbst prüfen.
- Alles steht in der Diagnose unter `state.learning`. Die Daten liegen in einer eigenen Datei in
  `.storage`, und das Entfernen des Eintrags löscht sie.
