# Morgenentladung (experimentell)

[🇬🇧 English](../en/morning-discharge.md) · [Übersicht der Dokumentation](README.md)

> **Experimentell.** Diese Betriebsart verkauft gespeicherte Energie, statt sie zu kaufen. Ob sich
> das lohnt, hängt von einem Tarif, einer Prognose und einem Wechselrichter ab, den man auf Abruf
> entladen lassen kann. Sie ist standardmäßig aus, und nichts anderes in der Integration braucht
> sie. Wenn du unsicher bist, nimm **Nachtladung**.

## Wofür sie gedacht ist

Die Nachtladung beantwortet eine Frage: *Wie viel Netzenergie braucht der Akku heute Nacht, damit
die Sonne von morgen noch hineinpasst?* Die Morgenentladung beantwortet die umgekehrte Frage:
*Die Sonne deckt heute ohnehin alles, wofür hält der Akku dann noch Energie zurück?*

An einem Sommertag lautet die Antwort oft: „Für mehr, als er sollte.“ Die Prognose liegt bei
30 kWh, das Haus braucht 15, und der Akku startet voll in den Morgen. Jede Kilowattstunde, die bei
Sonnenaufgang noch darin steckt, ist eine, die das Dach nirgendwo unterbringen kann.

Etwa zwischen **05:00 und 08:00** passiert zweierlei zugleich:

- Haushalte haben ihre tägliche Verbrauchsspitze — Duschen, Wasserkocher, die ersten Geräte, der
  Weg zur Arbeit;
- die Sonne steht noch nicht hoch genug, um sie zu decken.

Dann ist Netzstrom in einem dynamischen Tarif am teuersten und das Netz am knappsten. Ein Akku, der
sich in dieses Fenster entleert, tut zwei nützliche Dinge zugleich: Er verdient die Differenz
zwischen dem Morgenpreis und dem, was das Speichern der Energie gekostet hat, und er nimmt dem Netz
diese Last genau in der Stunde ab, in der es sie am wenigsten verkraftet. Danach füllt das Dach ihn
kostenlos wieder auf.

Das ist das Spiegelbild von §14a: Dort *kaufst* du, wenn das Netz entspannt ist und der Tarif
reduziert; hier *verkaufst* du, wenn das Netz belastet ist und der Tarif hoch.

## Was sie braucht

| | |
|---|---|
| **Zwangsentlade-Schalter** | Pflicht. Er ist das Einzige, was tatsächlich entlädt — ohne ihn hebt die Betriebsart nur den Min-SOC als Untergrenze an und wartet. Die Einrichtung speichert die Betriebsart ohne ihn nicht, aber die Auswahl *Betriebsart* prüft das nicht. Richte den Schalter deshalb zuerst ein. |
| **Ein Tarif, der sich dafür lohnt** | Ein dynamischer Tarif (Tibber, aWATTar, EPEX-basiert) oder eine Einspeiseregelung, die mehr wert ist, als dich die Energie gekostet hat. Bei einer festen Einspeisevergütung geht die Rechnung meist nicht auf. |
| **Eine Prognose wie im Sommer** | Sinnvoll ist das nur, wenn die PV-Anlage an diesem Tag das Haus versorgt *und* den Akku wieder füllt. Im Winter leert diese Betriebsart einen Akku, den du noch brauchst. |
| **Ein Fenster, das zu deiner Spitze passt** | Start- und Endzeit sind dieselben zwei Felder wie bei der Nachtladung. 05:00–08:00 ist ein vernünftiger Ausgangspunkt für einen deutschen Haushalt; sieh dir aber zuerst deinen eigenen Verbrauch an. |

## Wie sie sich verhält

1. Zu Beginn des Fensters merkt sie sich den aktuellen Min-SOC des Wechselrichters und schreibt
   dann den Zielladestand als Untergrenze — unter diesen Wert sinkt der Akku nicht.
2. Sie stellt sicher, dass das Netzladen aus ist.
3. Sie schaltet den Zwangsentlade-Schalter ein.
4. Wenn der Akku das Ziel erreicht, schaltet sie den Schalter wieder aus.
5. Am Ende des Fensters stellt sie den vorgefundenen Min-SOC wieder her und lässt den
   Wechselrichter so zurück, wie er war.

Zwei Sicherheitseigenschaften solltest du kennen:

- **Sie entlädt nie gegen eine unbekannte Untergrenze.** Wenn sich der Min-SOC nicht lesen oder
  schreiben lässt, beginnt die Entladung nicht. Einen Wechselrichter, der nicht antwortet, leert man
  nicht.
- **Bei einem Stromausfall tritt sie zurück.** Mit einer eingerichteten Entität für Notstrom- und
  Inselbetrieb geht die Betriebsart innerhalb von Sekunden aus dem Weg, denn in diesem Zustand
  versorgt der Akku dein Haus.

## Was sie noch nicht kann

Sie läuft im selben festen täglichen Fenster wie die Nachtladung. Sie liest kein Preissignal, kann
also nicht selbst die teuersten drei Stunden auswählen, und sie weiß nicht, ob die heutige Prognose
das Leeren des Akkus rechtfertigt — das entscheidest du, indem du die Betriebsart umschaltest.
Beides ist in [`plans/README.md`](../../plans/README.md) erfasst (Punkte 010 und 013).

## Die Abendreserve begrenzt sie

Seit 3.2.0 setzt eine eingerichtete Hochpreiszeit (Seite *Reserve für die teuren Abendstunden*)
auch dieser Betriebsart eine Untergrenze: Das Ziel, bis zu dem der Akku geleert wird, steigt um das,
was dieser Abend brauchen wird, abzüglich dessen, was die Sonne laut der heutigen Prognose noch
liefert. Um 06:00 zu verkaufen, was um 19:00 zu einem Spitzentarif zurückgekauft werden muss, ist
das eine Geschäft, das diese Betriebsart nicht machen darf. An einem Sommertag, dessen Prognose den
Abend ohnehin deckt, ändert sich nichts.
