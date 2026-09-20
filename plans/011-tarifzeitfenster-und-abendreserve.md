# Plan 011: Tarifzeitfenster als Liste, und eine Reserve für die Hochpreiszone

> Entwurf. Noch nicht zur Ausführung freigegeben — die offenen Fragen am Ende gehören zuerst
> beantwortet. Ersetzt Backlog-Eintrag 010 in `plans/README.md`.

## Status

- **Priorität**: P1 (der heutige Datumsbereich ist für den beschriebenen Anwendungsfall kaputt)
- **Aufwand**: L
- **Risiko**: MED (greift in die Fensterbestimmung ein, also in jede Nacht)
- **Hängt ab von**: 006 (Planer v2), 009 (Entladesperre)
- **Kategorie**: direction
- **Geplant bei**: Commit `89da2e5`, 2026-09-20

## Warum das wichtig ist

Die Integration kennt heute **ein** Zeitfenster pro Tag und **einen** Preis: günstig im Fenster,
teuer außerhalb. Die Wirklichkeit beim Netzbetreiber sieht anders aus.

Pascals Anlage am 20.09.2026:

- **23:00–05:00 günstig** (§14a Modul 3, reduziertes Netzentgelt),
- **18:00–21:00 Hochpreiszone** — dort kostet der Bezug *mehr* als normal.

Daraus folgt eine Anforderung, die das Plugin heute nicht kennt: **um 18:00 Uhr muss genug im
Speicher sein, um bis nach 21:00 Uhr ohne Netzbezug durchzukommen.** Sonst kauft man Strom genau
in der teuersten Stunde des Tages ein — und zwar Strom, den man um 23:00 Uhr für einen Bruchteil
hätte kaufen können. Das ist dieselbe Arbitrage wie die Nachtladung, nur andersherum gedacht.

Dazu kommt: Netzbetreiber definieren diese Bereiche **saisonal** und teilweise **zweimal am Tag**
(z. B. günstig nachts *und* mittags), und sie gelten Jahr für Jahr.

## Der Defekt im heutigen Datumsbereich

`_is_within_date_range()` (coordinator.py) vergleicht absolute `YYYY-MM-DD`-Daten mit `heute`:

```python
if start_date and end_date and start_date > end_date:
    _LOGGER.warning("Active date range is invalid (%s > %s), ignoring date restriction", ...)
    return True
...
if start_date and today < start_date: return False
if end_date and today > end_date:     return False
```

Zwei Folgen, beide still:

1. **Eine Saison läuft ab.** Wer `2026-11-01` bis `2027-03-31` einträgt, dessen Integration
   schweigt ab dem 1. April 2027 für immer. Es gibt keine Wiederholung.
2. **Eine Winter-Saison funktioniert gar nicht.** `2026-11-01` bis `2026-03-31` ist
   `start > end`, wird als ungültig verworfen und die Datumsbeschränkung damit **komplett
   ignoriert** — das Fenster läuft dann das ganze Jahr.

Der zweite Fall ist besonders unangenehm, weil eine Warnung im Log steht, die Integration aber
weiterläuft und mehr tut als gewollt.

## Wie die Wiederholung modelliert wird

Das ist die offene Frage gewesen, und die Antwort ist kürzer als erwartet: **ein Datum, das sich
jährlich wiederholt, wird ohne Jahr gespeichert.**

| Eingabe | Bedeutung |
|---|---|
| `"11-01"` | jeder 1. November, jedes Jahr |
| `"2026-11-01"` | genau dieser eine 1. November 2026 |

Die Länge der Zeichenkette sagt, ob es sich wiederholt. Keine RRULE, kein Wiederholungsschalter,
kein zweites Feld. Ein Bereich `11-01` → `03-31` läuft über den Jahreswechsel und wird **genau so
behandelt wie ein Zeitfenster über Mitternacht** — dieses Muster gibt es im Code schon
(`start_time`/`end_time`), samt Tests, und es ist dieselbe Logik: `if from <= to: from <= x <= to`,
sonst `x >= from or x <= to`.

Der Normalfall ist die jährliche Wiederholung; die Netzentgelt-Zeiten stehen im Preisblatt und
gelten bis auf Weiteres. Das einmalige Datum bleibt für den Sonderfall (Pilotjahr, befristete
Regelung) möglich, ohne dass jemand es konfigurieren muss.

## Datenmodell

Eine Liste von Perioden in `entry.data["tariff_periods"]`:

```jsonc
[
  {"kind": "cheap", "from": "23:01", "to": "04:58"},
  {"kind": "high",  "from": "18:00", "to": "21:00"},
  {"kind": "cheap", "from": "12:00", "to": "14:00",
   "season_from": "11-01", "season_to": "03-31",
   "weekdays": ["mon", "tue", "wed", "thu", "fri"]}
]
```

- `kind`: `cheap` (laden erlaubt, Ziel-SOC rechnen) oder `high` (nicht beziehen, Reserve
  vorhalten). Ein dritter Wert `blocked` (Netzbetreiber-Sperrzeit) ist später denkbar.
- `from`/`to`: Uhrzeit, darf über Mitternacht laufen.
- `season_from`/`season_to`: optional, `MM-DD` oder `YYYY-MM-DD` wie oben, darf über den
  Jahreswechsel laufen.
- `weekdays`: optional, Vorgabe alle.

Überlappen sich zwei Perioden, gewinnt `high` vor `cheap`: nicht beziehen ist die sichere Seite.

## Oberfläche

**Korrektur zu meiner Empfehlung vom 20.09.:** Ich hatte den `schedule`-Helfer als ersten Schritt
vorgeschlagen. Für Pascals Anforderungen taugt er nicht — sein Wochenraster kennt weder Saisons
noch eine Preisklasse, es kann nur „an" und „aus" je Wochentag. Er bleibt eine Option für den
einfachen Fall, löst aber das eigentliche Problem nicht.

Was es löst, ist der **`ObjectSelector` mit `fields`, `multiple` und `label_field`** (in der
installierten Home-Assistant-Version vorhanden und geprüft): eine wiederholbare Liste
strukturierter Zeilen, im Assistenten als echtes Formular gerendert — Auswahlfeld für `kind`,
zwei Zeitfelder, zwei Datumsfelder, Wochentags-Mehrfachauswahl. Keine Textbox, in die jemand
ein Format hineinschreiben muss.

## Was der Planer daraus macht

1. **Nachtladung** zielt weiter auf das günstigste Fenster, aber das Fenster kommt jetzt aus der
   Liste statt aus zwei Feldern. Mehrere `cheap`-Perioden am Tag: die Integration nimmt die, die
   als nächste beginnt.
2. **Abendreserve (neu).** Für jede `high`-Periode wird der Verbrauch über ihre Dauer geschätzt —
   dasselbe Hausverbrauchsprofil, das der Überbrückungsplaner schon benutzt — und daraus eine
   Mindest-SOC zum Beginn der Periode:

   `reserve_kwh = last_profile(18:00→21:00) ; reserve_soc = reserve_kwh / capacity / wirkungsgrad`

   Diese Reserve wirkt an zwei Stellen: sie **hebt das Nachtladeziel** (was abends gebraucht
   wird, wird nachts billig gekauft, falls die PV es nicht liefert) und sie ist eine
   **Untergrenze für die Morgenentladung** (was abends gebraucht wird, darf morgens nicht
   verkauft werden).
3. **Ein Sensor** `next_high_price_window` mit Beginn, Dauer und der errechneten Reserve, damit
   sichtbar ist, worauf der Planer hinarbeitet.

## Migration

`start_time`, `end_time`, `active_start_date`, `active_end_date` werden beim Start in eine
einzelne `cheap`-Periode überführt. Ein bestehendes absolutes Datum bleibt absolut — es in eine
jährliche Wiederholung umzudeuten wäre eine Entscheidung, die der Nutzer nicht getroffen hat.
Wer eine Saison will, trägt sie danach ohne Jahr ein; die Feldbeschreibung sagt das.

## Offene Fragen

1. **Wie kommt die Hochpreis-Reserve an die Wirklichkeit?** Das Hausverbrauchsprofil ist ein
   Mittelwert der letzten Tage. Ein Abend mit Herd und Waschmaschine liegt darüber. Reicht ein
   konfigurierbarer Zuschlag, oder braucht es das Maximum statt des Mittels?
2. **Was, wenn die Reserve nicht reicht?** Der Speicher ist um 18:00 Uhr leerer als geplant, weil
   die PV nicht geliefert hat. Nichts tun (teuer beziehen) oder die Entladesperre in der
   Hochpreiszone aufheben und den Speicher bis zum Nutzer-Minimum ziehen?
3. **Preise oder Klassen?** Die drei Preisfelder (`night_price_ct` usw.) gibt es schon. Soll jede
   Periode ihren eigenen Preis tragen, damit die Ersparnis real gerechnet wird, oder bleibt es
   bei den Klassen `cheap`/`high` und den drei globalen Preisen?
4. **Zweite Nachtladung nach der Hochpreiszone?** Bei 18:00–21:00 teuer und 23:00 günstig liegen
   zwei Stunden dazwischen, in denen der Speicher leer sein darf. Lohnt es, dort schon zu laden,
   oder wartet man auf das günstige Fenster?
