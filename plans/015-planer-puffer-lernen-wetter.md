# Plan 015: Ladeziel mit Puffer, gelernter Überbrückung, Sicherheitsniveau und Wetter

> Masterplan vom 24.10.2026, auf Pascals Wunsch aus dem Gespräch nach der Nacht vom 23./24.10.
> Grundlage: Planer v2 (`planner.py`, Plan 006), Abendreserve (Plan 011), Stand `develop` = `main`
> = 3.10.11.

## Status

- **Priorität**: P1 (Pascal: „alles drei sollten wir auf jeden Fall umsetzen“, dazu das Wetter)
- **Aufwand**: L, in vier Stufen, jede für sich testbar und abschaltbar
- **Risiko**: mittel. Jede Stufe ist mit ihrem Standardwert **verhaltensneutral**. Wer nichts
  einstellt, bekommt dasselbe Ziel wie in 3.10.11. Ausnahme ist Stufe B, siehe dort.
- **Version**: 3.11.0

## Ausgangslage: die Rechnung heute

```
untere Grenze = Sockel + Überbrückung + Abendlücke
obere  Grenze = 100 % − (Überschuss − Überbrückung)
Ziel          = untere Grenze, wenn untere ≤ obere
```

- **Sockel**: `user_min_soc`.
- **Überbrückung**: Hauslast vom Fensterende bis `Sonnenaufgang + 90 min`, ÷ Entladewirkungsgrad, plus
  `bridge_reserve_kwh`.
- **Abendlücke**: Hochpreiszeit-Last × (1 + Zuschlag), minus vorsichtiger Tagesüberschuss.
- **Überschuss**: Prognose × (1 ± Marge) − Taglast von der Übergangszeit bis Sonnenuntergang.
- **Hauslast**: Stundenprofil, **Mittelwert** der letzten 14 Tage je Stunde aus dem Hausverbrauchszähler.

Am 23./24.10. ergab das 44 % bei 35,8 kWh. Die eigene Abendprognose meldete am Morgen danach 0,85 kWh
Fehlbedarf. Das Ziel lag also genau auf Kante.

## Was Pascal will

1. **Puffer**: ein selbst gewählter Puffer, der nicht am Minimum kratzt. Er liegt irgendwo zwischen
   Minimum und Maximum und darf nie dazu führen, dass der Akku tagsüber voll läuft und PV eingespeist
   wird. Der Puffer wird **einmal** gekauft und dann mitgetragen. Er hilft bei Netzausfall mit
   Inselbetrieb, bei einem E-Auto oder bei einem Abend über dem Durchschnitt. Betrieb nur Oktober bis
   März, im Sommer ist die Integration aus.
2. **Jahreszeit und Dämmerung**: Im Dezember dauert es viel länger, bis PV das Haus trägt, als Anfang
   Oktober. Das gilt morgens wie abends.
3. **Hausverbrauch mit Spitzen**: Mittelwert dokumentieren, Spitzen morgens und abends beachten,
   daraus Minimum und Maximum des Bedarfs bilden.
4. **Wetter**: Mit Wärmepumpe braucht ein kalter Tag mehr Strom. Optional soll eine Wetter-Entität
   (Sammelentität `weather.*`, etwa Met.no oder DWD) die Tagesvorhersage liefern. Je kälter morgen,
   desto mehr wird eingeplant.

## Was davon schon da ist

- Ein **Stundenprofil** aus 14 Tagen, also mit Morgen- und Abendspitzen, aber nur als Mittelwert.
- **Echter Sonnenauf- und -untergang** je Tag. Die Verzögerung danach ist aber fest 90 min.
- Die **obere Grenze** hält Platz für die Prognose + Marge frei. Sie verhindert die Einspeisung und
  ist der Deckel, an dem sich der Puffer orientiert.

---

## Stufe A: Sicherheitspuffer (kWh), gedeckelt durch die obere Grenze

**Neue Option** `safety_buffer_kwh` (Seite „Planung“), Standard **0**, 0–50 kWh.

```
puffer_soc = safety_buffer_kwh / kapazität × 100
untere ≤ obere:   Ziel = min(untere + puffer_soc, obere),   nie unter untere
Konflikt:         Ziel wie bisher (der Puffer kommt nicht dazu, es ist kein Platz)
ohne Prognose:    Ziel = max(sichere Rückfallstufe, untere + puffer_soc)
immer:            geklammert auf user_min_soc … user_max_soc
```

- **Kein Überlauf**: Der Deckel ist die obere Grenze. Sie ist genau der Füllstand, ab dem der
  Prognoseüberschuss (mit Marge) tagsüber nicht mehr hineinpasst.
- **Einmal kaufen**: Der Planer rechnet jede Nacht neu vom aktuellen SOC aus. Was vom Puffer übrig
  bleibt, steht morgens noch im Akku, und die nächste Nacht lädt nur die Differenz nach. Es gibt keinen
  Zähler, der den Puffer erneut kauft.
- **Entladesperre**: Das Ziel ist zugleich der Nacht-Min-SOC. Der Puffer bleibt also auch nachts im
  Akku und steht im Inselbetrieb zur Verfügung.
- **Sichtbar**: `PlanResult.buffer_soc` (angewandt) und `buffer_capped` (vom Deckel gekürzt). Beides
  steht im Log („Bridge plan: … buffer 4.0 kWh (11.2 %), capped …“), im Sensor und in der Diagnose.

**Abgrenzung zu `bridge_reserve_kwh`:** Die Reserve erhöht die untere Grenze selbst, ist also
ungedeckelt und kann einen Konflikt erzeugen. Der Puffer nutzt nur freien Platz. Beide bleiben.

**Tests** (`tests/test_planner.py`):
- Puffer 0 ergibt exakt das bisherige Ziel (Parametrisierung über die vorhandenen Fälle).
- Puffer 4 kWh bei 35,8 kWh hebt 44 auf 55,2 %.
- Deckel: obere Grenze 50 %, Puffer 4 kWh ergibt 50 % und `buffer_capped`.
- Konflikt: kein Puffer.
- Rückfall ohne Prognose: max(50, untere + Puffer).
- user_max klammert.

## Stufe B: Überbrückung aus Prognose und Lastprofil statt fester 90 Minuten

### Problem

`Sonnenaufgang + 90 min` ist im Oktober zu lang und im Dezember zu kurz. An einem trüben Dezembertag
trägt PV das Haus womöglich nie. Dann zählt die Rechnung heute den Tag dazwischen gar nicht: Die
Überbrückung endet um 09:xx, und die Abendlücke beginnt erst um 16:30.

### Lösung: stündliche Bilanz statt Schwellzeit

PV-Leistung je Zeitpunkt aus der vorhandenen Glockenkurve `pv_power_kw_at` (Plan 014: Prognose,
Sonnenauf- und -untergang). Die Prognose nimmt die vorsichtige Seite (× (1 − Marge)):

```
defizit(t)   = max(0, last(t) − pv_vorsichtig(t))
übergang     = erster Zeitpunkt nach Sonnenaufgang mit pv_vorsichtig(t) ≥ last(t)
überbrückung = ∫ defizit(t) dt  von Fensterende bis zum Übergang  ÷ η_entlade  + Reserve
               ohne Übergang vor dem Abendbeginn: bis zum Abendbeginn
               (Hochpreisbeginn, sonst Sonnenuntergang)
```

- Ein sonniger Oktobertag kommt früher über die Last als heute angenommen. Die Überbrückung wird
  dadurch kürzer, und zusätzlich zählt nur das echte Defizit.
- Ein dunkler Dezembertag kommt nie darüber. Die Überbrückung reicht dann bis zum Abend. Das ist genau
  der Tagesbezug, den die Nacht billiger einkaufen kann.
- **Die obere Grenze bleibt unverändert** (Überschuss auf der großzügigen Seite). Kein Pfad, der den
  Platz für PV schmälert.

### Selbstlernend: Form der PV-Kurve

Die Glocke kennt keine Dachausrichtung. Ist `pv_energy_today_entity` (Tageszähler) gesetzt, lernt der
Planer aus den stündlichen Recorder-Statistiken der letzten 14 Tage, wie sich der Tagesertrag über den
Sonnentag verteilt. Die Verteilung läuft in 12 Bins über die Tageslänge, ab einem Tagesertrag von
1 kWh und mit mindestens 5 Tagen. Daraus wird `pv_shape`, und `pv_power_kw_at` nutzt die gelernte
Form statt der Glocke. Weniger Daten bedeuten Glocke wie bisher.

### Rückfallebene und Option

- Neue Option `bridge_model` mit `"modelled"` (neu, Standard) und `"fixed_delay"` (bisherige
  90-min-Regel, `pv_crossover_delay_min`).
- Ohne verwertbare Prognose rechnet der Planer immer mit `fixed_delay`.
- **Bewusst nicht verhaltensneutral**: Der neue Standard ändert das Ziel. Das ist der Kern des
  Wunsches. Wer den alten Wert will, stellt auf `fixed_delay`.

**Tests**:
- Ein Tag mit viel Prognose hat seinen Übergang vor Sonnenaufgang + 90 min, die Überbrückung wird
  kleiner.
- Ein Tag mit wenig Prognose hat keinen Übergang, die Überbrückung reicht bis zum Abendbeginn.
- Die Integration der Defizite stimmt gegen eine Hand-Rechnung.
- `fixed_delay` ergibt exakt das alte Ergebnis.
- Ohne Prognose gilt `fixed_delay`.
- Die gelernte Form normiert auf 1, die Glocke ist der Ersatz, und eine Ostdach-Form verschiebt den
  Übergang nach vorn.

## Stufe C: Sicherheitsniveau aus der Streuung des Verbrauchs

**Neue Option** `load_safety_level`: `"mean"` (Standard, bisher) / `"p75"` / `"p90"`.

- `_learn_house_load_profile` sammelt je Stunde **alle** 14 Werte statt nur Summe und Anzahl. Daraus
  entstehen zwei Profile:
  - **Bedarfsprofil** (gewähltes Niveau) für die untere Grenze, also Überbrückung, Defizit und
    Abendlücke;
  - **Platzprofil** (immer der Mittelwert) für die obere Grenze. Ein hoch geschätzter Verbrauch würde
    sonst den Platz für PV kleiner rechnen. Das ist die gefährliche Richtung.
- Perzentile je Stunde summiert liegen über dem Perzentil der Tagessumme. Für „sicher“ ist das gewollt
  und so dokumentiert.
- Weniger als 7 Werte in einer Stunde: Mittelwert für diese Stunde.
- `PlanInput` bekommt `room_load_kw_profile`. Fehlt es, gilt das Bedarfsprofil wie bisher (alte Aufrufer
  bleiben gültig).

**Tests**:
- `mean` ergibt das alte Ergebnis.
- `p90` hebt die untere Grenze und lässt die obere unverändert.
- Lückenhafte Stunden fallen auf den Mittelwert zurück.
- Perzentilberechnung (Interpolation) gegen eine Hand-Rechnung.

## Stufe D: Wetter und Temperatur (optional, für Wärmepumpe und Co.)

### Eingänge

- `weather_entity` (neu, optional): jede `weather.*`-Entität (Met.no, DWD, OpenWeatherMap …). Die
  Tagesvorhersage holt ICN über den Dienst `weather.get_forecasts` (`type: daily`, `return_response`).
  Die Sammelentität trägt seit HA 2023.9 keine Vorhersage mehr im Attribut. Für den Planungstag (das
  Datum des Fensterendes) gilt `(temperature + templow) / 2`. Ohne `templow` gilt `temperature`. Ohne
  Tageseintrag gilt der Mittelwert der stündlichen Vorhersage dieses Tages. Cache 30 min.
- `outdoor_temperature_entity` (neu, optional): ein Außentemperatur-Sensor mit Statistik. Damit lernt
  ICN sofort aus dem Recorder (Tagesmittel aus den stündlichen `mean`).
- Ohne Sensor lernt ICN selbst: Bei jedem Poll wird die aktuelle `temperature` der Wetter-Entität zum
  Tagesmittel gemittelt. Das Ergebnis wird mit dem Tagesverbrauch persistiert, höchstens 60 Tage. Bis
  genug Tage da sind, bleibt der Faktor 1.

### Modell: Heizgradtage

```
HGT(T)       = max(0, T_grenz − T)               T_grenz = 15 °C (Option heating_limit_c)
Tagesverbrauch E = a + b · HGT(T)                 kleinste Quadrate über ≤ 28 Tage, b ≥ 0
gültig, wenn ≥ 10 Tage, Spannweite der HGT ≥ 3 K und R² ≥ 0,3
faktor = (a + b · HGT(T_morgen)) / (a + b · HGT(T_profil))
         T_profil = mittlere Temperatur der Tage, aus denen das Lastprofil gelernt ist
         geklammert auf 0,85 … 1,5
```

- Der Faktor multipliziert **nur das Bedarfsprofil**, also untere Grenze und Überbrückung, nicht das
  Platzprofil. Ein falscher Kälteschätzer kann damit nie den PV-Platz wegnehmen.
- Wärmer als im Lernzeitraum ergibt einen Faktor < 1. Der Boden 0,85 verhindert, dass ein falsches
  Warm-Wetter das Ziel einbrechen lässt.
- Ohne Wärmepumpe ist `b` ≈ 0 oder R² zu klein, und der Faktor bleibt 1. Die Funktion schaltet sich
  so selbst ab.

### Sichtbar

`weather` im Sensor und in der Diagnose:
- `forecast_temp_c`, `profile_temp_c`;
- `kwh_per_kelvin` (= b), `samples`, `r2`;
- `factor`, `status` (`off` / `learning` / `active` / `forecast_unavailable`).

**Tests**:
- Der Regressionskern passt bekannte Geraden.
- Zu wenig Daten, zu wenig Spannweite und kleines R² ergeben Faktor 1.
- Klammer auf 0,85 … 1,5.
- Die Antwort von `weather.get_forecasts` wird geparst: daily mit und ohne `templow`, hourly als
  Rückfall, fehlender Tag, Dienstfehler.
- Selbstlernen über Polls und Tageswechsel, persistiert über einen Neustart.
- Ein kalter Morgen hebt die untere Grenze, die obere Grenze bleibt unverändert.

---

## Optionen-UI

| Seite | neu |
|---|---|
| Planung | Sicherheitspuffer (kWh), Sicherheitsniveau Verbrauch, Überbrückungsmodell |
| Wetter (neue Seite) | Wetter-Entität, Außentemperatur-Sensor, Heizgrenze (°C) |

Die Übersetzungen kommen nach `strings.json`, `translations/en.json` und `translations/de.json`. Die
vorhandenen Flow-Tests prüfen, dass jedes Feld eine Beschriftung hat.

## Umsetzung und Reihenfolge

1. **A** Puffer: klein, verhaltensneutral, löst den akuten Wunsch für diesen Winter.
2. **C** Sicherheitsniveau: verhaltensneutral mit `mean`.
3. **D** Wetter: verhaltensneutral ohne Wetter-Entität.
4. **B** Modellierte Überbrückung: ändert den Standard und kommt deshalb zuletzt, mit Lab-Lauf.

Jede Stufe bekommt einen eigenen Commit mit Tests und Gegenprobe. Danach laufen die ganze Suite
(ältestes und neuestes HA), mypy und pyright 3.13/3.14 sowie das Lab (`check_real_ha`,
`check_kostal`). Zum Schluss kommen CHANGELOG, README (Abschnitt Planer), Manifest 3.11.0 und ein PR
`develop` → `main`.

## Empfohlene Einstellung für Pascal

- Sicherheitspuffer **4–5 kWh**, entspricht 11–14 % bei 35,8 kWh.
- Sicherheitsniveau **p75**.
- Wetter-Entität `weather.<met.no oder DWD>` und, falls vorhanden, ein Außentemperatur-Sensor mit
  Statistik.
- Überbrückungsmodell: Standard (`modelled`).
