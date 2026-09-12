# Plan 010: Die Effizienzsuche messbar machen

> **Anweisung an den Ausführenden**: Schritt für Schritt vorgehen, jeden Prüfbefehl ausführen.
> Bei einer STOP-Bedingung anhalten und berichten. Am Ende die Statuszeile in `plans/README.md` aktualisieren.

## Status

- **Priorität**: P1
- **Aufwand**: M
- **Risiko**: MED (verändert, welche Ladeleistung dauerhaft am Wechselrichter steht)
- **Hängt ab von**: 008 (die Grenze deckelt auch den Test)
- **Kategorie**: correctness
- **Geplant bei**: Commit `a89ee67`, 2026-09-12

## Warum das wichtig ist

Billiger Strom ist nur billig, wenn er im Speicher ankommt. Bei 25 % Verlust werden aus 14 ct/kWh
Fensterpreis 18,7 ct/kWh gespeicherter Energie, bei 7 % Verlust 15,0 ct/kWh. Der Süßpunkt liegt
irgendwo zwischen minimaler und maximaler Ladeleistung und hängt an der Hardware — er muss also
gemessen werden. Genau dafür gibt es die Effizienzsuche.

Die Suche hat bis zu diesem Plan aber **nicht gemessen, was sie zu messen glaubte**.

## Befunde (Ist-Zustand vor diesem Plan)

| # | Befund | Wirkung |
|---|---|---|
| E1 | Der Verlust wurde mit `max(0.0, min(loss, 1.0))` geklemmt | Zwei Sensoren, die dieselbe Seite des Ladegeräts messen, ergeben einen Verlust ≤ 0 → geklemmt auf 0 % → **diese Leistung gewinnt die Suche für immer**. Der häufigste Konfigurationsfehler führt zum falschen Ergebnis, ohne dass es auffällt. |
| E2 | Keine Prüfung, ob der Wechselrichter dem Sollwert folgt | Ein fast voller Speicher drosselt; dann wird bei 3 kW gemessen und das Ergebnis unter „10 kW" abgelegt. Die Suche vergleicht anschließend zwei verschiedene Dinge. |
| E3 | Mindestdauer 30 Minuten, energieunabhängig | Bei 20 kW ist der Speicher vorher voll → die Messung wird **jede Nacht** verworfen, derselbe Kandidat wird immer wieder gewählt, die Suche bleibt für immer stehen. |
| E4 | Integration nur beim Poll (900 s), Rechteck mit dem Endwert | Zwei Stützstellen in 30 Minuten, bewertet mit dem Momentanwert am Intervallende. Das ist keine Energiemessung. |
| E5 | Keine Einschwingzeit | Die Rampe auf den neuen Sollwert ging voll in die erste Stützstelle ein. |
| E6 | Ein Messpunkt pro Nacht | Die Goldene-Schnitt-Suche braucht ~10 Punkte über den Bereich 1–20 kW → gut zehn Nächte, in denen jede Nacht gelingen muss. |
| E7 | Keine Sichtbarkeit | Nur `best_charge_power`. Ob überhaupt gemessen wird und warum eine Messung verworfen wurde, war nur im Log zu sehen. |
| E8 | `history` überlebte eine Änderung von min/max Ladeleistung | Alte Messungen und ein alter Suchbereich, neue Grenzen. |
| E9 | Der Test lief unbegrenzt, wenn mitten in der Messung die Wallboxen starteten | In Plan 008 behoben (Abbruch), hier nur noch als Randbedingung. |

## Entwurf

1. **Energiezähler statt Leistungsintegration, wo vorhanden.** Zwei neue optionale Felder
   (`charge_energy_sent_entity`, `charge_energy_received_entity`). Die Differenz zweier
   Zählerstände ist die geflossene Energie — ohne jede Annahme über den Verlauf dazwischen.
2. **Sonst ereignisgetriebene Trapez-Integration**: ein Listener auf beide Leistungssensoren,
   Energie += Mittelwert zweier Messwerte × Zeitdifferenz.
3. **Einschwingzeit** von 120 s, die nicht mitzählt.
4. **Annahmekriterien**: mindestens 300 s *und* 0,3 kWh; mittlere Leistung ≥ 80 % des Sollwerts;
   Verlust zwischen 0 und 50 %. Alles andere wird verworfen — mit Begründung.
5. **Unmessbare Leistungen**: Scheitert eine Leistung zweimal an zu wenig Energie, ist sie für
   diese Anlage zu hoch (der Speicher ist vorher voll). Der Suchbereich wird dort gekappt.
6. **Mehrere Messpunkte pro Fenster**: Sobald eine Messung reif ist, wird sie gebucht und der
   nächste Kandidat gestartet. Damit reicht meist ein Fenster für die ganze Suche.
7. **Sichtbarkeit**: Sensor `efficiency_search` mit Zustand (aus / wartet / schwingt ein / misst /
   fertig) und Attributen: Messreihe, Suchbereich, Bestwert, letztes Ergebnis samt Verwerfungsgrund.

## Fertig-Kriterien

- [x] Tests grün auf HA 2025.2.0, 2026.2.3 und 2026.9.2; `scripts/smoke_real_ha.py` grün
- [x] mypy und pyright ohne Befund; `strings.json` und `translations/en.json` identisch
- [x] Ein Test je Verwerfungsgrund, einer für die Zählervariante, einer für mehrere Messungen
      in einem Fenster (`tests/test_efficiency_finder.py`)
- [x] Ohne konfigurierte Sensoren ist das Verhalten unverändert
- [x] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- Eine Messung würde unter einer Leistung abgelegt, bei der sie nicht stattgefunden hat.
- Ein verworfener Messwert würde den Suchbereich in die falsche Richtung schneiden.

## Wartungshinweise

- Die Suche ist eine Messung, kein Regler: Jede künftige Änderung muss die Frage beantworten
  können, ob die Stichprobe wirklich bei der Leistung entstanden ist, unter der sie gespeichert
  wird. Das ist der einzige Grund, warum das Ergebnis überhaupt etwas wert ist.
- Der SOC-Bereich ist noch nicht Teil der Bedingung. Verluste hängen auch vom Ladestand ab; wer
  das verfeinern will, misst pro SOC-Band. Backlog.
