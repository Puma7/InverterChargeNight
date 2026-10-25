# So funktioniert es

[🇬🇧 English](../en/how-it-works.md) · [Übersicht der Dokumentation](README.md)

Inverter Charge Night (ICN) beantwortet jede Nacht eine Frage: **Wie voll muss der Akku sein, wenn
das günstige Fenster endet?** Ist er zu leer, kauft das Haus am Morgen oder am Abend teuren Strom.
Ist er zu voll, hat die Sonne von morgen keinen Platz und wird ins Netz eingespeist, für einen
Bruchteil dessen, was der Strom gekostet hat.

![Eine Nacht und der folgende Tag: Der Akku wird im günstigen Fenster bis zum Ziel geladen, versorgt das Haus, bis die PV übernimmt, und die PV füllt ihn tagsüber auf](../images/night-and-day.de.svg)

## Eine Nacht, Schritt für Schritt

1. **Zu Beginn des Fensters** liest ICN die PV-Prognose für den kommenden Sonnentag und berechnet
   den Zielladestand (siehe unten). Bevor es am Wechselrichter etwas ändert, liest und speichert
   es den aktuellen Min-SOC und die Ladegrenzen, damit es sie später zurückgeben kann. Die
   gespeicherten Werte werden dauerhaft abgelegt und überstehen einen Neustart.
2. **Während des Fensters** hebt es den Min-SOC des Wechselrichters auf das Ziel an und schaltet
   das Netzladen ein, wo der Wechselrichter diesen Schalter braucht. Ist eine Ladegrenze
   eingerichtet, schreibt es außerdem die gleichbleibende Leistung, mit der das Ziel genau am Ende
   des Fensters erreicht wird: im Modus Überbrückung, mit einer Begrenzung am Hausanschluss oder
   sobald die Effizienzsuche ein Ergebnis hat. Im Modus Überbrückung plant jede Abfrage
   (standardmäßig alle 15 Minuten) mit frischen Daten neu. Das Ziel sinkt aber nie unter das, das zu
   Beginn des Fensters gesetzt wurde.
3. **Ist das Ziel erreicht**, geht das Netzladen aus. Der angehobene Min-SOC bleibt: Das Haus darf
   den Akku bis zu diesem Stand nutzen, aber nicht darunter.
4. **Am Ende des Fensters** gehen der Min-SOC und die Ladegrenzen auf die Werte zurück, die ICN
   vorgefunden hat, und das Netzladen wird ausgeschaltet. Eine Einstellung, die schon ihren
   ursprünglichen Wert zeigt, wird nur bestätigt, nicht noch einmal geschrieben. Was passiert, wenn
   der Wechselrichter das Zurücksetzen nicht annimmt, steht unter [Sicherheit](safety.md).

Außerhalb des Fensters steuert ICN den Wechselrichter überhaupt nicht. Ausnahmen sind die
Hochpreisrettung und die Aktionen `charge_to` / `block_discharge`. Sie laufen als eigene kurze Fenster
mit demselben Sichern und Zurückgeben ([Extras](extras.md)).

> **Das Zurücksetzen der Untergrenze leert den Akku nicht.** Eine Nacht von 23:00 bis 05:00 mit
> einem Ziel von 30 % und einem ursprünglichen Min-SOC von 8 % lässt das Haus den Akku in der Nacht
> bis 30 % nutzen. Um 05:00 geht die Untergrenze auf 8 % zurück, und das Haus nutzt einfach weiter,
> was im Akku ist.

## Welche Prognose verwendet wird

Geplant wird für den Tag, an dem das Fenster **endet**. Ein Fenster, das morgen endet (23:00–05:00,
geplant um 23:00), verwendet die Prognose-Entität für **morgen**. Nach Mitternacht oder bei einem
Fenster, das am selben Tag endet, ist dieser Tag „heute“, und ICN verwendet die Prognose-Entität
für **heute**, sofern eine eingerichtet ist. Fehlt eine der beiden Entitäten, springt die andere
ein. Wenn du über Mitternacht lädst, richte beide ein. Solcast, Forecast.Solar und jeder andere
Sensor, der den Ertrag eines Tages in kWh meldet, funktionieren. ICN liest den Zustand oder die
üblichen Prognose-Attribute, in Wh oder kWh.

ICN korrigiert die Prognose um das, was dieses Dach zu dieser Jahreszeit geliefert hat, und
ersetzt die feste Marge durch die gemessene Streuung der Jahreszeit, siehe
[Was ICN selbst lernt](learning.md).

**Ohne brauchbare Prognose** lädt ICN nicht auf 100 %. Freiraum plant sichere 50 %. Überbrückung
plant 50 % oder ihre Untergrenze plus den Puffer, je nachdem, was höher ist. Beides wird in deinen
Min- und Max-SOC eingegrenzt.

## Wie das Ziel berechnet wird

Der **Planer** entscheidet, welche Formel gilt. Du wählst ihn auf der Seite
*PV-Prognose und Morgenreserve*.

### Freiraum (Standard)

Hält nur Platz für die PV von morgen frei:

```
Ziel = (Kapazität − Prognose × (1 + Aufschlag)) ÷ Kapazität
```

Akku mit 35,8 kWh, Prognose 5 kWh, Aufschlag 10 %: 35,8 − 5,5 = 30,3 kWh, das Ziel ist also
**84,6 %**. Eine Prognose, die größer ist als der Akku, ergibt deinen Min-SOC, und eine Prognose
von 0 ergibt deinen Max-SOC. Das Ergebnis wird immer in das eingerichtete Minimum und Maximum
eingegrenzt.

Freiraum ist einfach und braucht nur die Prognose. Er plant einmal, zu Beginn des Fensters, und
lässt den Wechselrichter mit seiner eigenen Leistung laden, außer es ist eine Begrenzung am
Hausanschluss eingerichtet oder die Effizienzsuche hat ein Ergebnis. Was er übersieht, ist der
Morgen: Vom Ende des Fensters, bis die Module das Haus tragen, zieht das Haus weiter Strom aus dem
Akku. Sicherheitspuffer, Sicherheitsniveau, modellierter Tag, Wetter und Abendreserve gehören alle
zum Planer Überbrückung.

### Überbrückung (empfohlen)

Überbrückung betrachtet beide Seiten und leitet zwei Grenzen ab:

![Der Akku von 0 bis 100 %: unter dem Min-SOC die Reserve, dann die Überbrückung, bis die PV übernimmt, dann die Abendreserve und der Sicherheitspuffer; über der Obergrenze der Platz, den die PV morgen braucht](../images/target-bounds.de.svg)

- **Untergrenze: was der Morgen braucht.** Der Hausverbrauch vom Ende des Fensters, bis die PV ihn
  deckt, plus eine kleine Reserve für die Überbrückung. Standardmäßig heißt „bis die PV ihn deckt“
  Sonnenaufgang plus eine feste Verzögerung (90 Minuten). Unterhalb dieser Grenze kauft das Haus
  nach dem Fenster Strom zum Tagtarif.
- **Obergrenze: was die Sonne braucht.** Die Prognose für morgen mit ihrem Aufschlag, abzüglich
  dessen, was das Haus tagsüber bis Sonnenuntergang verbraucht. Nur dieser Überschuss braucht Platz
  im Akku: Die am Morgen überbrückte Energie wird wieder verbraucht und macht ihren eigenen Platz
  frei.

Der Hausverbrauch kommt aus einem **Energiezähler des Hausverbrauchs**. ICN lernt ihn stündlich aus
den Recorder-Statistiken der letzten 14 Tage. Ohne Zähler verwendet ICN für jede Stunde den
**mittleren Hausverbrauch**, den du eingibst.

- **Untergrenze ≤ Obergrenze**: Alles passt. Das Ziel ist die Untergrenze, plus der
  Sicherheitspuffer, wenn du einen gesetzt hast. ICN kauft nie mehr als nötig.
- **Untergrenze > Obergrenze**: Die Grenzen widersprechen sich. Sind Preise eingerichtet,
  vergleicht der Planer zwei Kosten. Die einen entstehen, wenn über die Obergrenze hinaus geladen
  wird: der Nachtpreis minus die Einspeisevergütung, die verloren geht. Die anderen entstehen, wenn
  der Morgen nicht gedeckt ist: der Tagpreis minus der Nachtpreis. Er wählt das Günstigere. Ohne
  Preise gewinnt der Morgen, denn Netzstrom am Tag kostet mehr als die Einspeisevergütung, die
  verloren geht.

Die gewählte Grenze und ihre Eingangswerte sind Attribute des Sensors *Zielladestand*:
`plan_reason`, `lower_bound_soc`, `upper_bound_soc`, `bridge_kwh`, `surplus_kwh`, `buffer_soc`,
`pv_crossover` und `weather`.

### Sicherheitspuffer, Sicherheitsniveau, modellierter Tag, Wetter

Vier Einstellungen auf der Seite *Sicherheitspuffer, Sicherheitsniveau und Wetter* rücken das Ziel
von der Kante des reinen Bedarfs weg. Sie gehören zum Planer Überbrückung, und mit ihren
Standardwerten ändern sie nichts.

**Sicherheitspuffer (kWh).** Wird über der Untergrenze mitgeführt, aber nur in den Platz, den die
Obergrenze lässt:

```
Ziel = min(Untergrenze + Puffer, Obergrenze)
```

Der Puffer kann deshalb nie dazu führen, dass die PV von morgen ins Netz überläuft. Er wird
**einmal** gekauft und dann mitgeführt: Jede Nacht plant mit dem Akku, wie sie ihn vorfindet, und
füllt nur nach, was der Tag verbraucht hat. Er ist für einen Stromausfall an der Inselbox, ein
Elektroauto, einen überdurchschnittlichen Abend. `buffer_soc` zeigt, wie viel davon angewendet
wurde, und `buffer_capped`, ob die Obergrenze ihn gekürzt hat. Ein Widerspruch der Grenzen führt
keinen Puffer mit.

**Sicherheitsniveau des geplanten Verbrauchs.** *Durchschnitt* (wie bisher),
*Sicher (75. Perzentil)* oder *Sehr sicher (90. Perzentil)*. Jede Stunde des gelernten Profils wird
dann wie einer der lebhafteren Tage der letzten zwei Wochen geplant, und nie unter ihrem
Durchschnitt. Nur die Untergrenze nutzt das. Der Platz für die Sonne wird weiter mit dem
Durchschnitt gerechnet, damit eine hohe Verbrauchsschätzung nicht mehr Platz vortäuschen kann, als
da ist.

**Wie Morgen und Tag überbrückt werden.** Die *Feste Verzögerung nach Sonnenaufgang* (Standard)
oder **Modelliert aus Prognose und Verbrauch**. Im modellierten Modus rechnet der Planer den
morgigen Tag in Fünf-Minuten-Schritten durch, vom Ende des Fensters bis zum Ende der Hochpreiszeit:

- Der geplante Verbrauch gegen die vorsichtige Prognosekurve (Prognose minus Aufschlag) zeigt, wie
  tief der Akku fällt. Das ist die Untergrenze.
- Der durchschnittliche Verbrauch gegen die großzügige Kurve (Prognose plus Aufschlag) zeigt, wie
  hoch die Sonne ihn vor Sonnenuntergang hebt. Das ist die Obergrenze.

Ein sonniger Tag übernimmt früh und braucht weniger. Ein trüber Dezembertag trägt das Haus
vielleicht nie, dann wird der ganze Tag überbrückt. Hast du den Zähler für den PV-Tagesertrag
gesetzt (Seite *Sehr geringen PV-Ertrag berücksichtigen*), ist die PV-Kurve der Anteil des
Tagesertrags je Uhrzeitstunde, gelernt aus den letzten zwei Wochen. Das bildet Dachausrichtung,
Verschattung und Jahreszeit ab. Sonst verwendet ICN eine Glockenkurve für klaren Himmel zwischen
Sonnenaufgang und Sonnenuntergang.

**Wetter (optional, für eine Wärmepumpe).** Eine `weather.*`-Entität (Met.no, DWD,
OpenWeatherMap…), optional ein Außentemperatursensor, und die Heizgrenze (15 °C).

- ICN passt `Tages-kWh = a + b × max(0, Heizgrenze − T)` über die Tage an, die hinter dem
  Lastprofil stehen. Dann skaliert es den geplanten Verbrauch mit dem, was diese Formel für die
  vorhergesagte Temperatur von morgen im Vergleich zu den letzten zwei Wochen ergibt: höchstens
  +50 %, mindestens −15 %.
- Das greift nur, wenn zehn Tage, drei Kelvin Temperaturspanne, eine positive Steigung und
  R² ≥ 0,3 zeigen, dass der Zusammenhang echt ist. Ein Haus ohne elektrische Heizung behält den
  Faktor 1.
- Die Temperaturen kommen aus den Statistiken des Sensors oder aus einem Tagebuch, das ICN über die
  aktuelle Temperatur der Wetter-Entität führt. Dieses Tagebuch braucht etwa zehn Tage.
- Das Attribut `weather` zeigt den Status (`off`, `learning`, `active`, `forecast_unavailable`),
  die gelernten kWh pro Kelvin und den Faktor.

### Die Abendreserve

Viele Tarife haben außerdem eine **Hochpreiszeit**, oft 18:00–21:00, in der Strom mehr kostet als
der normale Tagtarif. Ist diese Zeit eingerichtet (Seite *Reserve für die teuren Abendstunden*) und
der Planer Überbrückung gewählt, sorgt die Nacht dafür, dass das Haus durch sie hindurch versorgt
werden kann:

```
Abendreserve      = Hausverbrauch in der Hochpreiszeit (+ dein Zuschlag in %)
von der PV gedeckt = Überschuss von morgen (Prognose minus Aufschlag, minus Tagesverbrauch)
nachts gekauft    = Abendreserve − von der PV gedeckt, nie unter null
```

An einem Sommertag füllt die Sonne den Akku lange vor dem Abend, und nichts ändert sich. An einem
trüben Wintertag kommt der ganze Abend zum Ziel der Nacht hinzu, zum günstigen Tarif.

Ist eine **Preis-Entität** eingerichtet, wird die Reserve zu einer Abwägung. Eine kWh, die im
Fenster zurückgelegt wird, läuft zweimal durch den Wechselrichter. Sie zu halten kostet deshalb
`Fensterpreis ÷ Wirkungsgrad`. Ist der Abend deutlich günstiger als das (um mehr als 2 ct/kWh),
entfällt die Reserve für diese Nacht. Der Sensor *Nächste Hochpreiszeit* zeigt, wann die nächste
beginnt und wie viel dafür reserviert wurde.

### Welches Ziel gilt

1. **Schnee-Nächte** > 0: der eingerichtete Max-SOC.
2. **Zielladestand überschreiben** (Zahl-Entität): ersetzt den Planer bis zum Ende des Fensters.
3. Der **Planer**: das Ziel, das zu Beginn des Fensters berechnet und währenddessen aktualisiert
   wird.

Ein erkannter starker PV-Ertragsausfall setzt eine Nacht auf den Max-SOC, wie eine Schnee-Nacht
([Extras](extras.md)).

## Wo du nachsiehst

- Der Sensor *Zielladestand* und seine Attribute: das Ergebnis und seine Gründe.
- Die Aktion `inverter_charge_night.plan_target_soc` (*Ziel-Ladestand berechnen*): führt den
  Planer Überbrückung auf Abruf aus und gibt das Ergebnis zurück, ohne etwas zu schreiben
  ([Entitäten und Aktionen](entities-and-actions.md)). Sie beachtet weder Schnee-Nächte noch das
  Überschreiben.
- Das Protokoll auf Stufe INFO: eine Zeile je Plan mit Untergrenze, Obergrenze, Puffer und dem
  Zeitpunkt, an dem die PV übernimmt.
