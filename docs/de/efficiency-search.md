# Effizienzsuche

[🇬🇧 English](../en/efficiency-search.md) · [Übersicht der Dokumentation](README.md)

Günstiger Strom ist nur günstig, wenn er im Akku ankommt. Ein Ladegerät, das bei 2 kW 25 %
verliert, macht aus einem Fenster mit 14 ct/kWh gespeicherte Energie für 18,7 ct/kWh; bei 7 %
Verlust kostet dasselbe Fenster 15 ct/kWh. Wechselrichter und Akku haben irgendwo zwischen ihrer
kleinsten und größten Leistung einen günstigsten Punkt, und wo er liegt, hängt von der Hardware ab.
Deshalb misst ICN ihn, statt zu raten.

## Was sie braucht

- Eine beschreibbare **Ladegrenze** in W ([Ladeleistung begrenzen](configuration.md#ladeleistung-begrenzen)).
- Zwei getrennte Leistungsmessungen: die **AC-Leistung, die in das Ladegerät fließt**, und die
  **DC-Leistung, die im Akku ankommt** (KOSTAL KORE: *Grid to Battery Power* und *Battery Power*).
- Freiwillig und besser: zwei **kWh-Zähler** für dieselben beiden Größen. Die Differenz zweier
  Zählerstände ist die Energie, die wirklich geflossen ist.
- Dunkelheit: PV, die den Akku speist, würde den Vergleich verfälschen. Deshalb misst die Suche nur,
  solange `sun.sun` unter dem Horizont steht.

Schalte sie mit der Seite *Effizienzsuche einrichten* oder mit dem Schalter **Effizienzsuche** ein.
Sie schaltet sich selbst aus, wenn sie fertig ist. Die beiden Sensoren *Effizienzsuche* und
*Effizienteste Ladeleistung* sind standardmäßig deaktiviert; aktiviere sie auf der Geräteseite, um
die Suche zu verfolgen.

## Wie sie misst

1. Sie schreibt eine Versuchsleistung und wartet **zwei Minuten**, bis sich der Wechselrichter
   eingeschwungen hat.
2. Sie misst mindestens **fünf Minuten und 0,3 kWh** lang, und so lange, bis der Standardfehler
   des Verlusts pro Minute **unter 0,15 Prozentpunkten** liegt (höchstens 20 Minuten). Mit
   Energiezählern läuft ein Versuch immer 20 Minuten.
3. Sie hält den Verlust `1 − angekommen ÷ aufgenommen` fest und geht weiter: zuerst ein
   **grober Durchgang** aufwärts in Schritten von 2 kW, bis der Verlust zweimal hintereinander
   gestiegen ist; dann werden die Lücken neben der besten Leistung auf beiden Seiten **halbiert**.
4. Sind beide Lücken 250 W oder schmaler, oder liegen die Nachbarn innerhalb der gemessenen
   Unsicherheit, speichert sie die beste Leistung und schaltet sich aus.

Ein Fenster von sechs Stunden reicht meist. Ein Versuch beginnt nur, wenn das Fenster noch genug
Zeit und genug zu ladende Energie übrig hat, um ihn abzuschließen (sonst
`not_enough_charge_left`).

Von da an lädt ICN bevorzugt mit dem Optimum und hört beim Ziel auf, statt ein weniger effizientes
Rinnsal über das Fenster zu verteilen. Die Frist kann mehr Leistung verlangen, und die größte
Ladeleistung und der Hausanschluss begrenzen sie immer.

Die Verluste hängen auch davon ab, wie voll der Akku ist. Jede Messung wird deshalb ihrem Band von
20 Prozentpunkten Ladestand zugeordnet (0–20, 20–40, …). Ein Band löst das Gesamtoptimum ab, sobald
darin drei verschiedene Leistungen gemessen wurden.

## Was sie nicht aufzeichnet

Eine falsche Messung würde jede spätere Nacht lenken. Deshalb verwirft sie:

- eine Messung, bei der der Wechselrichter dem Sollwert nicht gefolgt ist, zum Beispiel ein fast
  voller Akku, der die Ladung zurückfährt;
- eine, die zu kurz war oder zu wenig Energie bewegt hat;
- einen Verlust unter null (die beiden Sensoren messen dieselbe Seite) oder über 50 % (ein Problem
  mit Sensor, Verkabelung oder Einheit);
- nicht verfügbare oder veraltete Messwerte, einen unterbrochenen Strom von Messpunkten, eine Uhr,
  die gesprungen ist;
- alles, was gemessen wurde, während die Sonne am Himmel steht.

Sie schreibt auch nicht vergeblich weiter. Ein Versuch, bei dem der Akku nach fünf Minuten weniger
als ein Viertel der Leistung aufnimmt, endet. Ein zweiter solcher Versuch beendet die Suche für das
Fenster (`battery_not_charging`). Ebenso ein Wechselrichter, der die Grenze selbst senkt
(`inverter_lowered_limit`), zwei Versuchsstarts, die er nicht übernommen hat
(`charge_limit_not_taken`), und zwei Versuche, die an fehlenden Messwerten gescheitert sind
(`power_telemetry_lost`). Das nächste Fenster versucht es erneut.

## Ergebnis ablesen

Der Sensor **Effizienzsuche** zeigt den Zustand (*Aus*, *Wartet auf das Laden*, *Einschwingen*,
*Misst*, *Abgeschlossen*) und als Attribute:

| Attribut | Bedeutung |
|---|---|
| `best_power_w`, `best_loss_pct` | Das bisherige Optimum. |
| `loss_by_power_pct` | Jede Messung: Leistung in W gegen Verlust in %. |
| `uncertainty_by_power_pct` | Der Standardfehler jeder Messung. |
| `last_result` | Was mit dem letzten Versuch passiert ist, einschließlich des Grunds, warum er verworfen wurde. |
| `waiting_reason` | Warum gerade kein Versuch läuft. |

**Effizienteste Ladeleistung** behält das Ergebnis. Wird jede Messung verworfen, prüfe zuerst die
Sensoren: *aufgenommen* muss die AC-Seite des Ladegeräts sein und *angekommen* die Akkuseite.

Werden die Sensoren, die Ladegrenze, die kleinste oder größte Ladeleistung oder die
Akkukapazität geändert, werden alte Messungen ungültig. Das Ergebnis ist eine Schätzung unter den
gemessenen Bedingungen. Temperatur und Alterung können es verschieben, und du kannst die Suche
jederzeit neu starten.
