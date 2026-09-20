# Plan 014: Abregelung — die PV-Ladung so legen, dass die Mittagsspitze noch hineinpasst

> Entwurf. Noch nicht zur Ausführung freigegeben.

## Status

- **Priorität**: P1 (Pascal: kostet real Geld, jeden Sommertag)
- **Aufwand**: L
- **Risiko**: **HOCH** — eine falsche Vorhersage drosselt an einem Tag, der es nicht gebraucht hätte
- **Hängt ab von**: 013 (Ad-hoc-Fenster, Tagesbetrieb), 011 (Abendreserve als Untergrenze)
- **Geplant bei**: Commit `c8c72e3`, 2026-09-20

## Das Problem, in Pascals Zahlen

100 kWh Prognose, 35 kWh Speicher. Der ist mittags voll, und genau dann nimmt das Netz die
Einspeisung nicht mehr — die Mittagsspitze geht verloren.

## Korrektur am Masterplan: die Richtung stimmt dort nicht

Plan 012 schreibt, die Ladung müsse „gestreckt" werden, damit der Speicher erst nachmittags voll
ist. So herum ist es falsch. Man muss sich den Tag in drei Abschnitten ansehen:

| | Sonne > Haus | Überschuss kann ins Netz? | Also |
|---|---|---|---|
| **Vormittag** | ja | **ja**, und wird vergütet | Überschuss ins Netz, Speicher Platz lassen |
| **Abregelung** | ja | **nein** | Speicher nimmt **alles**, was er kriegen kann |
| **Nachmittag** | ja | ja | Speicher auffüllen, Rest ins Netz |

Die Drosselung gehört also **vor** das Abregelungsfenster, nicht hinein. Während der Abregelung
ist das Gegenteil richtig: dann ist der Speicher die einzige Senke, die es noch gibt. Ein Deckel,
der über die Mittagszeit weiterläuft, verschenkt genau die Energie, für die er gedacht war.

Die Steuergröße ist deshalb keine Ladekurve, sondern **ein Platzziel zum Beginn der Abregelung**.

## Die Rechnung

```
platz_kwh   = erwarteter Überschuss im Abregelungsfenster
            = prognose_heute * pv_fraction_between(sonnenaufgang, sonnenuntergang,
                                                   abregelung_start, abregelung_ende)
              - integrate_load(profil, abregelung_start, abregelung_ende)

vormittags_ziel_soc = user_max - platz_kwh / kapazität * 100
```

und der Deckel für den Vormittag ist die Leistung, die diesen Stand genau zum Abregelungsbeginn
erreicht — `planner.required_charge_power_w(vormittags_ziel_soc, aktueller_soc, kapazität,
stunden_bis_abregelung, wirkungsgrad)`. **Beides gibt es schon**: `pv_fraction_between` kam mit
3.4.0, `required_charge_power_w` mit Plan 006. Neu ist nur, sie so herum zu benutzen.

**Zwei Untergrenzen, die der Deckel nie unterschreiten darf:**

1. **Die Abendreserve** (`evening_reserve_soc`, 3.2.0/3.3.1). Ein Tag, der so scharf gedrosselt
   wird, dass der Speicher den Abend nicht mehr trägt, hat die Abregelung vermieden und dafür die
   Hochpreiszone eingekauft. Das ist der teurere Fehler.
2. **Der Nutzer-Mindeststand.** Versteht sich, steht aber hier, weil `_clamp` es sonst niemand tut.

Ist `vormittags_ziel_soc` kleiner als eine der beiden, gilt die größere — und es wird gar nicht
gedrosselt. Auf einem 35-kWh-Speicher mit 100 kWh Prognose kommt das kaum vor; an einem trüben
Tag ständig, und dann ist Nichtstun richtig.

## Woher die Abregelungszeit kommt

Pascals Entscheidung vom 20.09.:

> „Sobald der Netzbetreiber abregelt, sind wir schon zu spät dran."

Das stimmt — **fürs Handeln**. Fürs **Lernen** ist genau dieses Signal das beste, das es gibt. Die
Auflösung des Widerspruchs: reaktiv beobachten, vorausschauend anwenden.

### Eingang A (am besten): eine Entität, die die Begrenzung meldet

Ein Kostal G3 kennt die Wirkleistungsbegrenzung als Register. Wer so eine Entität hat, trägt sie
ein; die Integration schreibt sich über `statistics_during_period` — derselbe Weg wie beim
Hausverbrauchsprofil — auf, **zu welchen Stunden des Tages** sie in den letzten 14 Tagen aktiv
war, und leitet daraus das Fenster für morgen ab.

### Eingang B: ein Einspeise- oder Erzeugungszähler

Ohne Begrenzungsentität bleibt der Rückschluss aus der Erzeugung: eine Stunde, in der die
Erzeugung deutlich unter dem liegt, was Prognose und Sonnenstand erwarten lassen, **und** in der
die Einspeisung flach auf demselben Wert steht, ist ein Abregelungsverdacht.

**Das ist der unsichere Teil dieses Plans und muss als solcher behandelt werden.** Eine Wolke
sieht genauso aus. Die Prognose selbst ist ungenau. Und ein falscher Verdacht ist nicht neutral:
er drosselt an einem Tag, der es nicht gebraucht hätte, der Speicher wird abends nicht voll, die
Abendrettung kauft nach — der Fehler kostet **zweimal**.

Deshalb: B erzeugt nur einen Vorschlag, nie eine Steuerung. Der Sensor zeigt „an 9 von 14 Tagen
zwischen 11:30 und 15:30 abgeregelt" und Pascal trägt es ein, wenn es passt.

### Eingang C: feste Uhrzeiten

Von Pascal ausdrücklich als zweite Option gewünscht. Zwei Uhrzeiten, optional saisonal, im Muster
der Hochpreiszone. **Das ist gleichzeitig der Rückfall, solange nichts gelernt ist, und die
Überschreibung für den, der seine Zeiten kennt.** Reihenfolge: eingetragene Zeiten schlagen
Gelerntes.

## Die Stellgröße

Geschrieben wird auf das **absolute Maximum (AC+DC)**, das die Integration schon kennt
(`CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY`, beim Kostal G3 Register 1280). Das AC-Ladelimit reicht
nicht: es begrenzt nur den Netzbezug, nicht die DC-seitige PV-Ladung.

**Register 1280 fällt zurück.** Ohne regelmäßiges Nachschreiben springt es nach „Battery Time
Until Fallback" auf die Werkseinstellung (`docs/kostal-kore.md`). Der Deckel muss also in jedem
Poll erneuert werden — und zwar so, dass der bestehende Capture-und-Restore-Vertrag den
Originalwert nicht mit dem selbst geschriebenen überschreibt. `_reset_absolute_charge_power` und
`_original_absolute_charge_power` gibt es; sie sind bisher auf „einmal setzen, am Fensterende
zurück" ausgelegt.

## Der Lebenszyklus

Ein Vormittags-Deckel ist ein **Ad-hoc-Fenster** (Plan 013) mit `until = abregelung_start`:
Capture, Restore, Wiederholungsleiter, Notstromverriegelung und periodische Verifikation gelten
damit unverändert, und zum Abregelungsbeginn fällt der Deckel von selbst — was genau der Moment
ist, an dem der Speicher wieder alles nehmen soll.

Dafür braucht das Ad-hoc-Fenster **eine Erweiterung**: heute trägt es ein Ziel-SOC und eine
Netzladeerlaubnis, aber keinen Leistungsdeckel. Vorschlag: ein optionales `max_charge_power_w`,
das `_plan_charge_power` als zusätzliche Obergrenze liest — dieselbe Stelle, an der schon die
Hausanschlussgrenze und das Effizienzoptimum eingreifen.

## Ausrollen in Stufen, wie bei der Abendrettung

Dasselbe Muster, das Pascal am 20.09. für die Abendrettung gewählt hat („Sperren ja, Nachladen
nur mit Schalter"):

1. **Nur anzeigen.** Sensor `curtailment_outlook`: erkanntes oder eingetragenes Fenster, erwarteter
   Überschuss darin, der Platz, der dafür nötig wäre, und das Vormittagsziel, das sich daraus
   ergäbe. Schreibt nichts. Eine Saison lang danebenhalten.
2. **Mit Schalter drosseln.** `switch.…_curtailment_pacing`, ab Werk aus.

## Offene Fragen

1. **Wie scharf muss die Erkennung sein, damit sie mehr nützt als schadet?** Konkret: ab wie
   vielen Tagen mit demselben Muster ist es ein Fenster? Mein Vorschlag: 5 von 14, und nur
   zusammenhängende Stunden.
2. **Teilabregelung.** Viele Anlagen werden auf 70 % oder 60 % begrenzt, nicht auf 0. Dann geht
   nicht alles verloren, sondern ein Teil — die Rechnung oben nimmt implizit 0 % an und
   überschätzt den nötigen Platz. Mit Eingang A ist der Prozentsatz bekannt; mit B nicht.
3. **Zwei Speicher-Ladewege.** Wenn der Wechselrichter DC-seitig lädt, ist der „Ladestrom" nicht
   dasselbe wie der AC-Ladestrom, den die Effizienzsuche vermisst. Die Bänder aus 3.3.0 sind an
   AC-Messungen gelernt — ob sie für DC-Laden gelten, ist offen.
4. **Braucht es überhaupt eine Erkennung?** Wenn Pascals Abregelung jeden Sommertag zur selben
   Zeit stattfindet, tun feste Zeiten es, und Eingang A/B sind Komfort. Das ist vor dem Bau von B
   zu klären — B ist mit Abstand der teuerste Teil dieses Plans.
