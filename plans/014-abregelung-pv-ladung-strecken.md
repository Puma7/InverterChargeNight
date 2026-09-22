# Plan 014: Abregelung — den Platz freihalten, den die Mittagsspitze braucht

> **Stufe 1 umgesetzt mit 3.7.0** (Anzeige und Messung). **Stufe 2 (Drosselung) wartet
> ausdrücklich auf eine Messsaison** — siehe „Warum Stufe 2 noch nicht gebaut wird".
>
> Der Entwurf vom 20.09. ging von einem *geschalteten* Abregelungsfenster aus, das aus der
> Einspeisekurve gelernt werden müsste. Diese Annahme ist gefallen, und mit ihr der teuerste Teil
> des Plans. Was unten steht, ist der berichtigte Stand.

## Status

- **Priorität**: P1 (Pascal: kostet real Geld, jeden Sommertag)
- **Aufwand**: M (war L, bevor die Lernerkennung entfiel)
- **Risiko**: Stufe 1 keins (schreibt nichts); Stufe 2 **hoch**
- **Hängt ab von**: 013 (Ad-hoc-Fenster), 011 (Abendreserve als Untergrenze)

## Das Problem, in Pascals Zahlen

100 kWh Prognose, 35 kWh Speicher, Einspeisung dauerhaft auf **60 %** begrenzt. Der Speicher ist
mittags längst voll, und was dann über die Grenze läuft, ist weg.

## Die Korrektur: eine dauerhafte Grenze ist kein Ereignis

Pascals Antwort vom 20.09.: die 60 % sind die **EEG-Einspeisebegrenzung am
Netzverknüpfungspunkt**, rund um die Uhr in Kraft. Der Netzbetreiber schaltet nichts. Damit gibt
es kein Fenster, das gelernt oder eingetragen werden müsste: die Grenze beißt genau in den
Stunden, in denen

```
PV-Leistung(t) − Hauslast(t) > Grenze
```

und die folgen aus Prognose, Sonnenstand und Lastprofil. An einem trüben Tag kommt korrekt „gar
nicht" heraus, ohne dass jemand etwas abschaltet.

**Damit entfällt Eingang B (Erkennung aus der Einspeisekurve) ersatzlos** — der unsichere und mit
Abstand teuerste Teil des alten Entwurfs. Feste Uhrzeiten (Eingang C) bleiben nur als
Überschreibung für den, dessen Abregelung doch geschaltet wird; gebaut sind sie noch nicht, weil
niemand sie braucht.

## Die Rechnung, ebenfalls berichtigt

Der alte Entwurf nahm den **ganzen** Mittagsüberschuss als Platzbedarf. Bei 60 % geht aber nur der
Teil **oberhalb** der Grenze verloren:

```
overflow_kwh       = ∫ max(0, pv_power(t) − last(t) − grenze) dt
room_needed_kwh    = overflow_kwh × ladewirkungsgrad          (Speicherseite)
morning_target_soc = user_max − room_needed_kwh / kapazität × 100
                     geklammert auf max(evening_reserve_soc, user_min_soc)
```

`pv_power` ist neu und ist die **exakte Ableitung** von `pv_fraction_between` (3.4.0):
`(π/2)·sin(π·x)·prognose/tageslänge`. Ein Eigenschaftstest hält beide zusammen — integriert man
die Leistung über ein beliebiges Intervall, kommt die Fraktion heraus, auf 1e-4 genau. Läuft das
auseinander, widerspricht die Anzeige dem späteren Deckel, und sonst merkt es niemand.

**Die Wirkungsgradrichtung ist umgekehrt zur Nachtladung**: hier wird *mal* η gerechnet, nicht
geteilt — es kommt weniger an, als umgeleitet wurde.

**`forecast_error_margin` wirkt gegenläufig.** `surplus_kwh` bläht die Prognose auf; hier wird sie
gedämpft. Beide Male lehnt sich der Fehler weg von einem Speicher, der abends leer ist.

## Warum Stufe 2 noch nicht gebaut wird

Zwei Zahlen fehlen, und Pascal kennt beide nicht: die Anlagengröße und ob die Stellgröße
überhaupt eingetragen ist. Das Modell allein klärt sie nicht — es **widerspricht** der
Beobachtung:

```
100 kWh Prognose, 16 h Sonnentag -> modellierte Mittagsspitze ~9,8 kW
   10 kWp -> Grenze  6,0 kW -> 17,2 kWh Überlauf
   12 kWp -> Grenze  7,2 kW ->  8,2 kWh
   14 kWp -> Grenze  8,4 kW ->  1,9 kWh
   ab 16 kWp                ->  gar keiner
```

Oberhalb von rund 16 kWp beißt eine 60-%-Grenze an einem 100-kWh-Tag im Modell nie — Pascal
beobachtet aber, dass sie beißt. Einer von beiden irrt. Vermutlich die Glocke: eine reine
Sinuskurve läuft oben spitzer und niedriger als eine echte Schönwetterkurve.

**Solange das offen ist, würde Stufe 2 an einem unbekannten Anteil der Tage grundlos drosseln —
und ein solcher Tag kostet zweimal**: der Speicher ist abends zu leer, und die Abendrettung kauft
aus dem Netz nach. Deshalb misst 3.7.0 erst.

## Was 3.7.0 liefert

- `planner.pv_power_kw_at` und `planner.curtailment_outlook` (rein, ohne HA-Importe).
- `sensor.…_curtailment_outlook`, diagnostisch und per Vorgabe deaktiviert (die stündliche
  Spitzenreihe ist Zustand, den der Recorder sonst für jeden Nutzer mitschreibt). Zustand:
  `overflow_kwh`, im Normalfall 0 — `None`, wenn die Frage gar nicht gestellt werden kann.
- Die **Gegenprobe**: stündliche Maxima der Einspeiseleistung über 14 Tage aus dem Recorder.
  Eigener `statistics_during_period`-Aufruf mit `{"max"}` — eine Leistung ist `measurement` und
  hat `change` überhaupt nicht, also lässt sich der Aufruf des Lastprofils nicht mitbenutzen.
  Daraus `model_vs_envelope_pct`, die Zahl, wegen der es diese Version gibt: das Tagesmodell gegen
  eine obere Schranke, die kein einzelner Tag überbieten kann.
- Zwei Felder: `curtailment_limit_w` (die Grenze in W, nicht der Prozentsatz) und
  `curtailment_feed_in_entity`.
- Die Funktion ist **abgeschaltet, solange keine Heute-Prognoseentität eingetragen ist**:
  `_get_active_forecast_entity` fällt sonst auf die Morgen-Entität zurück, und das ist für eine
  Vormittagsentscheidung still der falsche Tag.

## Stufe 2, wenn die Messung sie rechtfertigt

Die Stellgröße ist das **absolute Maximum (AC+DC)** (`CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY`,
Kostal G3 Register 1280). Das AC-Ladelimit reicht nicht: es begrenzt nur den Netzbezug, nicht die
DC-seitige PV-Ladung.

Zu klären ist dabei dreierlei, und das erste ist mit 3.7.0 bereits erledigt:

1. ~~Der Schreibpfad konnte den selbst geschriebenen Deckel als „Originalwert des Nutzers"
   übernehmen.~~ **Behoben in 3.7.0**, unabhängig von der Abregelung, weil es schon heute das
   Nachtfenster betraf.
2. `_apply_absolute_charge_power_limit` feuert bisher nur unter
   `grid_charge_switch and not should_skip_charging` — ein Vormittagsdeckel ohne Netzladen
   erreicht es nie. Zweiter Aufrufort nötig.
3. Register 1280 fällt auf Werk zurück und muss in **jedem** Poll erneuert werden.

Der Lebenszyklus ist ein **Ad-hoc-Fenster** (Plan 013) mit `until = beginn der bindenden Stunden`
und einem neuen optionalen `max_charge_power_w`, das `_plan_charge_power` als zusätzliche
Obergrenze liest. Dabei ist die Lehre aus der Abendrettung zu beachten: ein Ad-hoc-Fenster setzt
`is_active`, und was daran hängt, schaltet sich still ab. Ein eigener Grund
(`ADHOC_REASON_CURTAILMENT`) samt Ausnahmen ist Pflicht, nicht Kür.

## Offene Fragen

1. **Wie weit trägt die Glocke?** Die erste Frage, die `model_vs_envelope_pct` beantwortet. Reicht
   sie nicht, ist der nächste Schritt kein Deckel, sondern ein besseres Tagesprofil.
2. **Zwei Ladewege.** Lädt der Wechselrichter DC-seitig, ist der Ladestrom nicht derselbe, den die
   Effizienzbänder aus 3.3.0 an AC-Messungen gelernt haben.
3. **Teilabregelung anderer Höhe.** 70 % statt 60 % ist nur eine andere Zahl im selben Feld — das
   trägt der Entwurf schon, weil die Grenze in Watt eingetragen wird und nicht als Prozentsatz.
