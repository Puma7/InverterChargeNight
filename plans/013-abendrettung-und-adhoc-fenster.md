# Plan 013: Abendrettung — und das Ad-hoc-Fenster, das sie braucht

> **Umgesetzt mit 3.4.0 (Teil 1) und 3.5.0 (Teil 2).** Abweichung vom Entwurf: keine — das
> Ad-hoc-Fenster kam wie beschrieben, samt persistierter Frist. Dazu kam ein Befund von Codex auf
> PR #5: die Vorschau rechnete über die Nachtladung hinweg, sobald die Zone des Tages begonnen
> hatte. Sie wird jetzt nur noch gestellt, solange man noch handeln kann.
>
> Offen aus diesem Plan: die drei Fragen am Ende, und die restlichen Aufrufer des Ad-hoc-Fensters
> (`charge_to`, `force_discharge_to`, `block_discharge` mit `until`).

## Status

- **Priorität**: P1 (Pascal am 20.09.: der Fall kostet real Geld)
- **Aufwand**: L
- **Risiko**: **HOCH** — schreibt zum ersten Mal außerhalb eines Fensters an den Wechselrichter
- **Hängt ab von**: 009 (Entladesperre), 011 (Hochpreiszone), 3.3.1 (Entladeverluste), Teil 1
- **Geplant bei**: Commit `8853e51`, 2026-09-20

## Das Szenario

Pascals Worten nach: die Prognose war zu gut — Schnee auf den Panelen, und der Schnee-Schalter
blieb aus. Nachmittags ist absehbar, dass der Akku den Abend nicht trägt. Heute passiert
daraufhin **nichts**: die Abendreserve aus 3.2.0 hebt nur das *Nachtladeziel*. Ist die Nacht
vorbei und der Tag läuft schief, gibt es keinen Eingriff mehr, und um 18:00 beginnt der
Netzbezug zum Hochpreistarif.

**Die Regel, die über allem steht:** in der Hochpreiszone **darf kein Netzbezug stattfinden**.
Nicht „möglichst wenig" — gar keiner.

**Teilabdeckung zählt.** Es muss nicht reichen, den Abend vollständig aus dem Akku zu fahren.
Jede Kilowattstunde aus dem Akku ist mehr wert als dieselbe zum Hochpreistarif. Es gibt also kein
„lohnt sich nur, wenn es ganz reicht" — eine Rettung, die die Hälfte schafft, hat die Hälfte
gebracht.

## Was Teil 1 schon kann

`planner.evening_outlook()` rechnet den Akku bis zum Zonenbeginn vor: Hausverbrauch heraus,
erwarteter Sonnenertrag hinein (Schönwetterglocke, nicht linear), Vergleich mit dem, was die Zone
selbst braucht. Ergebnis: `missing_kwh`, im Normalfall 0. Sichtbar als
`sensor.…_evening_outlook`. Geschrieben wird nichts.

## Warum Teil 2 nicht einfach die Entladesperre aufruft

Die naheliegende Abkürzung — `_apply_discharge_block()` nachmittags aufrufen — ist gefährlich,
und zwar aus zwei Gründen, die beide zum selben Ergebnis führen: **ein Akku, der gesperrt bleibt
und den niemand wieder freigibt.**

1. **Die Min-SOC-Variante** (`DISCHARGE_BLOCK_VIA_MIN_SOC`) schreibt über `_update_window_floor()`,
   und der Boden leitet sich aus dem *Fensterziel* ab. Außerhalb eines Fensters gibt es keines.
2. **Schalter- und Limit-Variante** merken sich den Originalzustand in `_original_discharge_block`
   bzw. `_original_discharge_limit`. Zurückgesetzt wird beides ausschließlich von
   `_reset_settings()`, und das läuft am **Fensterende** — ein Pfad, der ohne aktives Fenster nie
   betreten wird (`_on_window_end` steigt bei `not self.is_active` sofort wieder aus).

Dazu kommt die Wiederholungsleiter: ein fehlgeschlagener Reset wird über `_pending_reset` und
`_record_reset_outcome` erneut versucht — ebenfalls an den Fensterlebenszyklus gebunden.

Ein zweiter, paralleler Sperrmechanismus wäre also ein zweiter Capture-und-Restore-Vertrag neben
dem bestehenden. Zwei Verträge über dieselben Stellgrößen sind genau die Sorte Konstruktion, die
irgendwann um drei Uhr nachts nicht mehr auseinanderzuhalten ist.

## Die Lösung: das Ad-hoc-Fenster

Statt eines zweiten Mechanismus bekommt der bestehende einen zweiten **Anlass**. Ein Ad-hoc-Fenster
ist ein Fenster wie jedes andere — mit Start, Ende, Ziel-SOC, Capture, Restore, Wiederholungsleiter,
Notstromverriegelung, Hausanschlussgrenze und periodischer Verifikation — nur dass sein Start nicht
aus der Uhrzeit kommt, sondern aus einem Aufruf.

```python
async def async_open_adhoc_window(
    self,
    *,
    target_soc: float,
    until: datetime,
    reason: str,          # "evening_rescue" | "service" - steht im Log und im Sensor
    allow_grid_charge: bool,
) -> bool
```

Damit fällt dreierlei zusammen, das bisher getrennt geplant war:

- **Die Abendrettung** ist ein Aufrufer: Ziel `required_soc`, Ende `zone_start`.
- **Die offenen Aktionen aus Stufe 1** des Masterplans (`charge_to`, `force_discharge_to`,
  `block_discharge` mit `until`) sind Aufrufer desselben Wegs — genau der
  „Ad-hoc-Fensterlebenszyklus", den Plan 012 ihnen zuschreibt.
- **Strang C** (PV-Ladung strecken) braucht denselben Tagesbetrieb.

### Vorrangordnung

`_check_current_window()` hat heute eine feste Reihenfolge (ending > skip_next > hands_off >
Notstrom > Datumsbereich > Zeitfenster). Das Ad-hoc-Fenster reiht sich **unter** die Verriegelungen
und **über** das Zeitfenster ein:

```
_ending > skip_next > hands_off > Notstrom > Datumsbereich > Zeitfenster > Ad-hoc
```

- Beginnt ein reguläres Fenster, während ein Ad-hoc-Fenster läuft, **gewinnt das reguläre**: es
  hat den Tarifvertrag hinter sich. Das Ad-hoc-Fenster endet vorher regulär, mit Reset.
- Notstrom beendet beide sofort, wie bisher.
- Ein Ad-hoc-Fenster endet **spätestens** zu seinem `until` — und das ist der Punkt, an dem die
  Abendrettung die Sperre wieder löst.

### Was persistiert werden muss

`_adhoc_until`, `_adhoc_reason`, `_adhoc_target_soc` gehen in `runtime_state`, neben
`hands_off_until` und `skip_next_until`. Nach einem Neustart mitten in einer Rettung muss die
Freigabe zum Zonenbeginn trotzdem stattfinden — **ein Akku, dessen Freigabe ein Neustart
verschluckt hat, hängt bis zum nächsten regulären Fensterende fest.** Das ist der eine Fehlerfall,
den dieser Plan am strengsten zu behandeln hat.

## Die Abendrettung darauf

Geprüft wird bei jedem Poll zwischen PV-Crossover und Zonenbeginn:

| Stufe | Wirkung | Automatisch? |
|---|---|---|
| 1 | **Entladung sperren**, damit nicht weiter verbraucht wird, was der Abend braucht | **ja** — kostet nichts und ist umkehrbar |
| 2 | **Aus dem Netz nachladen** bis `required_soc`, zum Tagestarif | **nein** — nur mit Schalter (Pascals Entscheidung vom 20.09.) |
| 3 | Zu Beginn der Zone: **Sperre lösen**, Netzladen aus | immer |

Neue Entität: `switch.…_evening_rescue_charge`, ab Werk **aus**, Kategorie `config`. Solange sie
aus ist, zeigt der Sensor aus Teil 1, dass Stufe 2 greifen *würde* — der Nutzer sieht eine Saison
lang zu, bevor er es scharf schaltet.

Stufe 2 lädt frühestens zu einem Zeitpunkt, ab dem sich die Prognose kaum noch dreht (Vorschlag:
zwei Stunden vor Zonenbeginn). Vorher gesperrt zu haben kostet nichts; vorher gekauft zu haben
schon.

## Offene Fragen

1. **Was, wenn die Zone beginnt und der Akku trotzdem nicht reicht?** Aktuelle Antwort: nichts
   Besonderes — die Sperre ist gelöst, der Akku liefert, was er hat, der Rest kommt aus dem Netz.
   Die Alternative (Netzbezug in der Zone aktiv verhindern, notfalls mit Lastabwurf) hat diese
   Integration nicht in der Hand.
2. **Zweimal am Tag?** Zwei Hochpreiszonen (morgens und abends) sind bei manchen Netzbetreibern
   üblich. Der Entwurf rechnet nur auf die *nächste* — das reicht, solange zwischen beiden eine
   Sonnenphase liegt.
3. **Wie interagiert die Rettung mit der Morgenentladung?** Der Boden aus 3.2.0 verhindert schon,
   dass morgens verkauft wird, was der Abend braucht. Eine Rettung am selben Tag wäre der Beweis,
   dass dieser Boden zu niedrig war — das ist eher eine Diagnose als ein Konflikt.
