# Sicherheit

[🇬🇧 English](../en/safety.md) · [Übersicht der Dokumentation](README.md)

ICN schreibt auf ein Gerät, das dein Haus mit Strom versorgt. Hier stehen die Regeln, an die es
sich hält, und was es tut, wenn etwas schiefgeht.

## Grundsätze

- **Alles, was es ändert, gibt es zurück.** Vor dem ersten Schreiben eines Fensters liest und
  speichert es den Min-SOC und die Ladegrenzen. Am Ende des Fensters gehen sie zurück, und das
  Netzladen wird ausgeschaltet. Die gespeicherten Werte werden dauerhaft abgelegt. Ein Neustart im
  Fenster macht deshalb dort weiter, wo er aufgehört hat, und ein Neustart nach dem Fenster räumt
  trotzdem auf.
- **Kein Schreiben ohne Grund.** Ein Wert, der schon stimmt, wird nur bestätigt, nicht noch einmal
  geschrieben. Eine Ladegrenze, die der Wechselrichter nicht hält, wird je Fenster höchstens zweimal
  erneut geschrieben. Zwei Dinge werden absichtlich geschrieben: Ein Min-SOC, den jemand anderes im
  Fenster verstellt hat, wird zurückgesetzt. Und eine Ladegrenze bei KOSTAL KORE, die am
  Wechselrichter verfällt, wird alle 20 Sekunden aufgefrischt, solange der Akku aus dem Netz laden
  kann.
- **Außerhalb des Fensters tut es nichts.** Nur die Hochpreisrettung und die Aktionen `charge_to` /
  `block_discharge` wirken außerhalb des Fensters. Sie laufen als kurze Fenster mit demselben
  Sichern und Zurückgeben.
- **Im Zweifel weniger laden.** Fehlt der Ladestand, stoppt das Netzladen. Fehlt der Messwert für
  den Netzbezug, pausiert das Laden. Fehlt die Prognose, plant ICN rund 50 %, nicht 100 %.
- **Ein Regler je Wechselrichter.** Lass nicht zwei Integrationen (oder zwei ICN-Einrichtungen)
  denselben Akku steuern. ICN lehnt eine Min-SOC-Entität ab, die schon eine andere Einrichtung
  verwendet.

## Der Akku im Fenster

Eine planmäßige Nacht setzt nur das **geplante Ziel** als Min-SOC. Energie darüber bleibt für das
Haus verfügbar: Ein Akku, der die Nacht mit 100 % beginnt, bei einem Ziel von 30 %, versorgt das
Haus bis 30 % und wird nur geladen, wenn er darunter fällt. ICN schaltet keine Entladesperre ein,
schreibt keine Entladegrenze von null und hebt die Untergrenze nicht auf den Anfangsstand. Jedes
davon würde den Akku daran hindern, das Haus überhaupt zu versorgen.

Eine Untergrenze über dem Ladeziel gibt es nur bei einer **ausdrücklichen Reservierung**: der
Hochpreisrettung oder der Aktion `block_discharge`. Selbst dann wird der Akku nur über den Min-SOC
gehalten (Einstellung *Entladesperre für Reservierungen*: Automatisch). Die Sensorattribute
`inverter_floor_soc` und `discharge_block` zeigen, was gerade gilt.

## Einstellungen zurückgeben

Am Ende des Fensters wird jede Einstellung für sich abgeschlossen. Ist ihr Zurücksetzen bestätigt,
wird sie nicht noch einmal geschrieben. Ein gesicherter Wert, den die Entität nicht annehmen kann
(eine Kostal-Ladegrenze, die 22 063 W liest, über ein Steuerelement, das höchstens 12 540 W
annimmt), wird als Grenzwert der Entität zurückgegeben.

Schlägt das Zurücksetzen fehl (die Entität ist nicht verfügbar, der Wechselrichter antwortet
nicht), versucht ICN es nach 1, 2 und 4 Minuten erneut, dann noch zweimal im Abstand von
15 Minuten. Danach hört es auf zu schreiben und öffnet den Reparaturhinweis
**„Wechselrichter-Einstellungen nicht vollständig zurückgesetzt“**, der die zu prüfenden
Einstellungen nennt. Das nächste Fenster, die Aktion `reset_inverter` (*Wechselrichter
zurücksetzen*) oder ein erfolgreiches Zurücksetzen übernehmen dann wieder. Während eines
Stromausfalls warten die Wiederholungen, ohne einen Versuch zu verbrauchen.

## Hausanschluss

Im günstigen Fenster laufen alle großen Verbraucher gleichzeitig. Zwei Wallboxen mit 22 kW und
11 kW sind schon 33 kW, und ein dreiphasiger Anschluss mit 63 A hat etwa 43 kW Nennleistung. Eine
kurze Spitze ist nicht das Problem. Stundenlange Dauerlast erwärmt die Kontakte am Zähler, die
Klemmen und die Sicherungen. Der Akku ist der einzige Verbraucher, den ICN steuern kann, also gibt
er nach.

Mit einem Netzbezugssensor und einem eingerichteten Budget:

```
Nennleistung = Phasen × Strangspannung × Sicherungsstrom   3 × 230 V × 63 A = 43 470 W
Budget       = Nennleistung × Dauerlastanteil              43 470 W × 80 %  = 34 776 W
andere       = Netzbezug − was der Akku selbst zieht
erlaubt      = Budget − Sicherheitsabstand − andere
```

Laufen 33 kW Wallboxen, bekommt der Akku `34 776 − 500 − 33 000 = 1 276 W` statt der mehreren kW,
die der Plan verlangt. Braucht der Rest des Hauses das ganze Budget, geht die Ladeleistung auf 0,
solange das anhält.

- *Was der Akku selbst zieht* ist die gemessene AC-Ladeleistung (höchstens der Sollwert). Ohne
  diesen Sensor zählt der gesamte Bezug als andere Last. Das ist die sichere Seite. Kommt sie aus
  Netzbezug minus Hausverbrauch, zählt sie höchstens so viel wie die Akkuleistung plus 25 %. Die
  beiden Zähler melden zu verschiedenen Zeitpunkten, und ein gerade gestiegener Hausverbrauch
  sähe sonst wie Ladeaufnahme aus. Auf eine Meldung des Hausverbrauchs reagiert ICN wie auf eine
  des Netzbezugs.
- Es lädt immer nur **weniger**. Ein Netzbezugssensor, der nicht verfügbar ist, seit fünf Minuten
  nichts gemeldet hat oder keine Einheit W oder kW hat, pausiert das Laden (0 W), bis er wieder
  einen Wert liefert.
- Zwischen den Abfragen senkt ein Listener auf den Netzbezug den Sollwert innerhalb von etwa
  30 Sekunden, wenn der Hausverbrauch steigt. Das Wiederanheben wartet auf die nächste Abfrage.
- Nicht einmal die Effizienzsuche darf mehr anfordern, als der Anschluss trägt.
- Ein Wechselrichter, der die geschriebene Grenze nicht hält, bekommt sie je Fenster höchstens
  noch zweimal, einen Neustart eingerechnet. Dann hört ICN auf zu schreiben, öffnet den
  Reparaturhinweis **„Der Wechselrichter hält das Ladelimit nicht“** und lässt das Netzladen aus.
- **Wenn das Drosseln scheitert, sinkt der Min-SOC.** Ein angehobener Min-SOC lässt manche
  Wechselrichter mit der Grenze aus dem Netz laden, die sie gerade halten (bei einem Kostal sind es
  von sich aus 22 kW). Braucht der Anschluss eine niedrigere Grenze und lässt sich der
  Wechselrichter nicht dazu bringen, sie zu halten, sinkt der Min-SOC für den Rest des Fensters auf
  den aktuellen Ladestand, abgerundet. Nie unter dein eigenes Minimum. Der Akku lädt dann nicht mehr
  aus dem Netz, entlädt aber auch nicht. Das wird als Fehler protokolliert und als
  `min_soc_released_for_connection` angezeigt.

Der Sensor *Vom Hausanschluss freigelassene Ladeleistung* zeigt die erlaubte Leistung, das Budget,
den gemessenen Bezug und ob die Begrenzung bremst.

> **ICN steuert nur den Akku.** Es kann deine Wallboxen oder deine Wärmepumpe nicht drosseln.
> Können diese allein den Anschluss überlasten, begrenze sie mit ihrem eigenen Lastmanagement.

## Stromausfall und Inselbetrieb

Bei einem Stromausfall muss ICN den Wechselrichter vollständig loslassen. Es gibt nichts, woraus
geladen werden könnte, und ein angehobener Min-SOC würde den Akku daran hindern, das Haus zu
versorgen: Das Licht ginge bei vollem Akku aus.

Richte **Inselbetrieb erkennen** mit dem ein, was deine Installation bietet:

- einen Schalter oder binären Sensor: `on` bedeutet Inselbetrieb;
- einen Zustandssensor plus die Zustände, die Inselbetrieb bedeuten. Ein Kostal meldet `ESB` in
  seinem Wechselrichterzustand;
- **einen manuellen Umschalter, den der Wechselrichter nicht meldet**: Lege ein `input_boolean` an,
  wähle es aus und schalte es um, wenn du umschaltest. Das funktioniert mit jedem Wechselrichter.

Sobald Inselbetrieb gemeldet wird, beendet ICN das laufende Fenster, gibt den Min-SOC des
Wechselrichters zurück, schaltet das Netzladen aus und startet nichts Neues, bis das Netz wieder
da ist. Eine Inselentität, die nicht verfügbar ist, zählt als Inselbetrieb. Ebenso ein unbekannter
Zustand eines einfachen Sensors, für den du keine Zustände angegeben hast: Im Zweifel lässt ICN
los. Ohne eine solche Entität kann ICN nichts von einem Stromausfall wissen und öffnet zur
Erinnerung einen Reparaturhinweis.

## Kommunikationsverlust

ICN überwacht die Entitäten, von denen es abhängt. Werden der Ladestand, der Min-SOC, der
Netzladeschalter, die Ladegrenze, der Netzzähler oder der Inselkontakt nicht verfügbar oder melden
sie Unsinn, dann:

- aktualisiert ICN sofort, statt auf die nächste Abfrage zu warten;
- fordert es in einem laufenden Fenster über jeden Weg, der noch funktioniert, null Ladeleistung
  und ausgeschaltetes Netzladen an;
- zählt es einen fehlgeschlagenen Schreibvorgang nie als erledigt und behält die gesicherten
  Einstellungen, bis das Aufräumen bestätigt werden kann;
- zeigt es eine Benachrichtigung je Problem (*Kommunikation gestört*, *Verbindung
  wiederhergestellt*) und zählt Vorfälle statt fehlgeschlagener Abfragen.

Die Aktion `inverter_charge_night.communication_diagnostics` (*Kommunikationsdiagnose*) gibt den
Kommunikationszustand und seinen Verlauf zurück, ohne das Gerät anzufassen.

> Keine Software kann garantieren, dass ein Gerät anhält, wenn jeder Steuerweg oder Home Assistant
> selbst ausgefallen ist. Die Schutzeinrichtungen des Stromkreises und des Wechselrichters sowie
> das Batteriemanagementsystem bleiben nötig.

## Wechselrichter mit Zwangsladeschalter

Bei manchen Wechselrichtern ist der Schalter für das Netzladen eine Zwangsladung mit voller
Leistung. Das *Battery Manual Charge* des Kostal ist so einer. ICN erkennt das: Hebt das
Einschalten die Ladegrenze auf, geht das Netzladen für den Rest des Fensters wieder aus, und der
angehobene Min-SOC übernimmt das Laden unter der geschriebenen Grenze. Hat ein Fenster sein Ziel
so erreicht, lässt ICN den Schalter in den folgenden Fenstern 30 Tage lang aus, damit sich das
kurze Einschalten nicht jede Nacht wiederholt. Details:
[Kostal mit KOSTAL KORE](kostal-kore.md).

## Haftungsausschluss

ICN wird unter der [MIT-Lizenz](../../LICENSE) bereitgestellt, ohne Gewährleistung. Einen
Wechselrichter und einen Akku zu steuern geschieht auf eigene Gefahr. Prüfe, was ICN schreibt,
bevor du es unbeaufsichtigt laufen lässt, und lies den Abschnitt [Hausanschluss](#hausanschluss),
wenn eine Wallbox den Anschluss mitbenutzt.
