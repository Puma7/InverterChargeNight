# Ausnahmen und Extras

[🇬🇧 English](../en/extras.md) · [Übersicht der Dokumentation](README.md)

## Eine Nacht überspringen

Der Schalter **Nächstes Fenster überspringen (24 h)** überspringt das kommende Fenster und schaltet
sich 24 Stunden nach dem Einschalten selbst aus. Schaltest du ihn während eines laufenden Fensters
ein, beendet er dieses Fenster sofort; er läuft dann während des Fensters der nächsten Nacht ab,
das ab diesem Zeitpunkt beginnt. Nutze ihn für Gäste, eine lange Reise oder eine Nacht, in der du
lieber das Auto laden willst.

## Das Ziel von Hand setzen

Die Zahl **Zielladestand überschreiben** ersetzt das Ziel des Planers bis zum Ende des Fensters.
Sie wird auf ein ganzes Prozent gerundet und in deinem Min- und Max-SOC gehalten. In einem
laufenden Fenster wirkt sie sofort, und sie übersteht einen Neustart.

## Schnee-Nächte

Schnee auf den Modulen macht jede Prognose falsch. Setze **Schnee-Nächte** auf die Zahl der
Nächte, in denen die Module bedeckt bleiben (0–14). Jede dieser Nächte lädt auf den Max-SOC, ohne
Rücksicht auf die Prognose und das Überschreiben, und der Zähler sinkt nach jedem abgeschlossenen
Nachtladefenster um eins.

## Sehr geringer PV-Ertrag

Ist **Sehr geringen PV-Ertrag berücksichtigen** eingerichtet (Prognose für heute plus ein Zähler
für den PV-Tagesertrag), merkt sich ICN die Prognose vom Morgen und vergleicht sie nach
Sonnenuntergang mit dem, was erzeugt wurde. Mindestens 5 kWh erwartet und höchstens 15 % davon
geliefert setzt die **nächste** reguläre Nacht auf den Max-SOC, wie eine Schnee-Nacht. Ein
Reparaturhinweis (*PV-Ertrag stark ausgefallen*) und die `pv_yield`-Attribute des Sensors
*Zielladestand* erklären die Entscheidung. Geringer Ertrag kann Schnee, Verschattung, einen
Strangfehler oder eine falsche Prognose bedeuten; ICN versucht nicht, das zu unterscheiden.

## Hochpreisrettung

Der Nachtplan kauft für die Hochpreiszeit im Voraus ein. Die Rettung ist für den Fall, dass sich
dieser Plan als zu optimistisch herausstellt, zum Beispiel bei Schnee, ohne dass Schnee-Nächte
gesetzt sind. Der Sensor **Abendvorschau** zeigt den Fehlbetrag in kWh, normalerweise 0. Er
braucht die Prognose für **heute**.

Die Vorschau rechnet mit Absicht knapp: mit dem durchschnittlichen Verbrauch der Hochpreiszeit,
ohne Zuschlag und mit der vollen Prognose. Was über den Bedarf hinaus gehalten wird, landet nach
der Hochpreiszeit in der günstigen Nacht. Fehlt dagegen ein wenig, kostet das nur die letzte
halbe Stunde der Hochpreiszeit zum Spitzentarif statt zum Tagtarif.

Ist der Schalter **Hochpreisrettung (festhalten und nachladen)** an, prüft ICN bei jeder
Aktualisierung neu (standardmäßig alle 15 Minuten):

1. **Bei einem Fehlbetrag wird gehalten, was die Hochpreiszeit braucht.** Der Min-SOC wird auf
   den Bedarf angehoben oder auf den aktuellen Stand, wenn der Akku weniger enthält. Über dem
   Bedarf versorgt der Akku weiter das Haus. Darunter folgt die Untergrenze der Sonne bis zum
   Bedarf und nicht weiter. Der Min-SOC wird nie über den Stand des Akkus gesetzt, denn manche
   Wechselrichter laden dann aus dem Netz.
2. **Gekauft wird nur, wenn es sich rechnet.** In den letzten zwei Stunden vor der Hochpreiszeit
   wird der Rest aus dem Netz geladen, aber nur mit einer
   [Strompreis-Entität](configuration.md#strompreise-berücksichtigen), deren Preise die
   Hochpreiszeit teurer machen als den Kauf jetzt samt Verlust durch den Akku (Lade- und
   Entladewirkungsgrad), und zwar um mindestens 2 ct. Bei wenigen Cent Abstand zwischen Tag- und
   Spitzentarif ist das nie der Fall, und die Rettung hält nur.
3. **Losgelassen wird, wenn die Sonne reicht.** Zeigt die Vorschau, dass der Akku es mit 0,5 kWh
   Luft schafft, bekommt der Wechselrichter seine eigenen Einstellungen zurück. Ein späterer
   Fehlbetrag löst die Rettung erneut aus. Dazwischen wird nichts geschrieben.
4. **Zu Beginn der Hochpreiszeit** bekommt der Wechselrichter seine Einstellungen in jedem Fall
   zurück, und der Akku trägt den Abend.

Ein Beispiel: 10 kWh im Akku, die Hochpreiszeit braucht 5 kWh, und der Nachmittag bleibt trüb.
Der Akku versorgt das Haus, bis noch 5 kWh übrig sind, und hält diese für die Hochpreiszeit.

Der Schalter ist mit Absicht standardmäßig aus: Beobachte den Vorschau-Sensor eine Weile, bevor du
ihn handeln lässt. Ausschalten beendet eine laufende Rettung sofort. Bis Version 3.11 hieß die
Funktion *Abendrettung*; Installationen aus dieser Zeit behalten die Entitäts-ID
`switch.inverter_charge_night_abendrettung_festhalten_und_nachladen`.

## Abregelungsvorschau

Ist deine Einspeisung am Netzanschlusspunkt begrenzt (die deutsche 60-%- oder 70-%-Regel), heißt
ein voller Akku an einem sonnigen Tag, dass alles über der Grenze verloren geht. Der Sensor
**Abregelungsvorschau** (standardmäßig deaktiviert) schätzt, wie viel. Er braucht die Grenze in W
(Seite *Weitere Einstellungen*: 0,60 × deine Anlage in W, also 9000 für 15 kWp bei 60 %) und die
Prognose für heute. Mit einer Entität für die Einspeiseleistung kann er das Modell prüfen:
`measured_overflow_kwh_envelope` summiert für jede Stunde des Tages, wie weit die höchste
Einspeisung der letzten 14 Tage über der Grenze lag, und `model_vs_envelope_pct` setzt den heute
modellierten Überlauf dazu ins Verhältnis. Ein Modell über 100 % dieser Hüllkurve behauptet einen
Überlauf, in dessen Nähe dein Wechselrichter nie gekommen ist. Gedrosselt wird dadurch nichts; der
Sensor misst nur.

## Morgenentladung (experimentell)

Das Gegenstück zur Nachtladung: An einem Sommertag, dessen Prognose das Haus ohnehin deckt, leert
sie den Akku in die Morgenspitze. Sie braucht einen Zwangsentlade-Schalter und einen Tarif, der das
bezahlt. Siehe [Morgenentladung](morning-discharge.md).
