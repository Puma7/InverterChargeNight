# Plan 006: Entscheidungsvorlage für den Eigentümer

Stand: 2026-09-11, Branch `feat/006-planner-v2` (Prototyp hinter der Option `planner_mode`).

Der Planer v2 ist umgesetzt und getestet, aber **nicht aktiv**: `planner_mode` steht standardmäßig auf
`headroom` (heutige Formel). Erst nach Umschalten auf `bridge` in den Einstellungen (Schritt "Time & SOC")
rechnet die Integration mit Überbrückung, Sonnenaufgang und Verbrauch, schreibt die geplante Ladeleistung
und (bei konfigurierter Entität) die Entladesperre. Die folgenden Fragen entscheiden, wie der Prototyp beim
Eigentümer eingerichtet und wann er Standard wird.

## Frage 1: Welche Entität sperrt beim Kostal Plenticore die Entladung?

Die Kostal-Plenticore-Integration (HACS `kostal_plenticore`/Core `kostal_plenticore`) bietet je nach
Firmware und Integrationsversion unter anderem:

- `number.*_battery_min_soc` (wird heute bereits als Ladeziel benutzt),
- eine Zahl für die **maximale Entladeleistung** der Batterie (Modbus-Register für die
  Entladeleistungsgrenze; in der Core-Integration als `number` mit W-Einheit sichtbar, sofern das Gerät die
  Einstellung freigibt),
- die **Batterie-Betriebsart** (Select/Modbus-Register "Battery Control", extern/intern).

**Empfehlung: die Entladeleistungsgrenze.** Sie ist eine Zahl mit Einheit, lässt sich beim Fensterstart
erfassen, auf 0 setzen und am Fensterende exakt zurückschreiben, genau wie das AC-Ladelimit (Muster aus Plan 005).
Eine Betriebsart müsste als Select behandelt werden, hat keine "0"-Semantik und kollidiert mit der Netzladung
über den Min-SOC. Umgesetzt ist deshalb `discharge_limit_entity` als optionale `number`/`input_number`-Entität
(Schritt "Charge Power"). Der Wert wird in der Entitätseinheit roh gespeichert und zurückgeschrieben, damit
kein Rundungsfehler beim Umrechnen W/kW entsteht.

**Offen**: Welche Entity-ID diese Grenze im Setup des Eigentümers hat (bitte aus den Entitäten der
Kostal-Integration heraussuchen). Bietet die Integration keine solche Entität, bleibt Schritt 5 "nicht
konfiguriert": die Integration loggt dann beim Fensterstart einen Hinweis und verhält sich wie heute.

## Frage 2: Verbrauchsquelle: Hausverbrauchszähler oder fester Mittelwert?

Umgesetzt sind beide Wege:

- `house_load_entity`: kumulativer kWh-Zähler des Hausverbrauchs. Der Coordinator liest die Stundenwerte
  (`change`) der letzten 14 Tage über `statistics_during_period` aus dem Recorder, mittelt sie je Stunde des
  Tages und cached das Profil 15 Minuten. Stunden ohne Daten, Zählerrücksetzungen und ein fehlender
  Recorder fallen auf den Mittelwert zurück (Debug-Log).
- `avg_house_load_kw` (Standard 0,5 kW): flaches Profil, wenn kein Zähler konfiguriert ist oder keine
  Statistik nutzbar ist.

**Empfehlung: Hausverbrauchszähler.** Der Morgenverbrauch (05:59 bis 09:00) weicht deutlich vom Tagesmittel
ab (Warmwasser, Frühstück); der Mittelwert über- oder unterschätzt die Überbrückung je nach Haushalt um
1 bis 2 kWh. Der Zähler muss `device_class: energy` und Langzeitstatistik haben (in HA "Statistik" für die
Entität eingeschaltet). **Zum Start** den Mittelwert aus dem Energie-Dashboard eintragen, damit die erste
Nacht schon sinnvoll rechnet, dann den Zähler ergänzen.

## Frage 3: PV-Kreuzung: fester Versatz nach Sonnenaufgang oder gelernt?

Umgesetzt: `pv_crossover_delay_min` (Standard 90 Minuten) wird auf `sun.sun/next_rising` addiert. Fehlt
`sun.sun`, nimmt der Coordinator Sonnenaufgang = Fensterende + 2 h an und warnt einmal pro Fenster.

**Empfehlung: fester Versatz zum Start, 90 Minuten.** Im Winter (flache Sonne, Schnee, Nebel) liegt die
Kreuzung eher bei 2 bis 3 Stunden nach Sonnenaufgang, im Sommer bei 30 bis 60 Minuten. Ein gelernter Wert
(aus dem PV-Leistungssensor und dem Verbrauch der letzten 14 Tage: erster Zeitpunkt, an dem PV > Verbrauch
für 15 Minuten) ist die nächste Stufe, braucht aber den PV-Leistungssensor als weiteren Eingang. Vorschlag:
im ersten Winter 120 Minuten eintragen, im Sommer 60, und die Log-Zeile "Bridge plan: ... bridge X kWh until
HH:MM" mit dem tatsächlichen Kreuzungszeitpunkt aus dem Energie-Dashboard vergleichen.

## Frage 4: Preise als feste Werte oder Preissensor?

Umgesetzt: `night_price_ct`, `day_price_ct`, `feed_in_price_ct` als feste Werte (alle drei oder keiner;
der Assistent erzwingt das). Ohne Preise gewinnt bei einem Konflikt zwischen Überbrückung und PV-Platz immer
die Überbrückung (`conflict_bridge_wins`). Mit Preisen entscheidet der Kostenvergleich
`(Nacht − Einspeisung)` gegen `(Tag − Nacht)` je Konflikt-kWh.

**Empfehlung: feste Werte eintragen** (z. B. 14 / 30 / 8 ct). Bei diesen Werten gewinnt die Überbrückung
ohnehin (6 ct Verlust je verdrängte PV-kWh gegen 16 ct Ersparnis je nicht am Tag gekaufte kWh), das
Ergebnis ist also gleich, aber die Entscheidung ist im Sensor-Attribut `plan_reason` nachvollziehbar. Ein
Preissensor (Tibber, aWATTar, EPEX) ist Backlog 011 und ersetzt dann `prices_ct` durch Zeitreihen.

## Frage 5: Morning-Discharge behalten oder ersetzen?

Der Modus `morning_discharge` speist morgens gezielt ein, also genau in der Zeit, die der Planer v2
überbrücken will. Beide Funktionen schließen sich aus; deshalb wendet der Planer v2 im Entlademodus weder
Entladesperre noch Ladeleistung an und rechnet dort weiter mit der heutigen Formel.

**Empfehlung: als "dynamischer Tarif"-Funktion behalten und so benennen** (Backlog 010), nicht umbauen.
Der Modus ist für Tarife sinnvoll, bei denen die Einspeisung morgens mehr bringt als der Bezug kostet; das
ist beim §14a-Fenster nicht der Fall, aber bei dynamischen Tarifen möglich. Ein Umbau zum Überbrückungsmodus
wäre eine Dopplung des Planers v2. Sobald Backlog 011 (Preissignal) kommt, kann der Modus in die
Kostenoptimierung aufgehen.

## Umsetzungsentscheidungen, die der Eigentümer kennen sollte

1. **Ladeleistung wird nur im Modus `bridge` geschrieben.** Der Sensor `planned_charge_power` zeigt den
   Sollwert in beiden Modi, damit der Plan vor dem Umschalten beobachtet werden kann. Im Modus `headroom`
   bleibt das AC-Ladelimit unangetastet (heutiges Verhalten). Während der Effizienzsucher aktiv ist, schreibt
   der Planer ebenfalls nicht; der Sucher besitzt das Limit.
2. **Regel für die Ladeleistung**: `required = (Ziel − Ist) · Kapazität / Reststunden / Wirkungsgrad`;
   Sollwert = `required`, geklemmt auf `[min_charge_power_w, max_charge_power_w]`. Liegt ein Effizienzoptimum
   vor und ist `required` kleiner als das Optimum, ist das Optimum die Obergrenze (Sollwert also `required`,
   mindestens `min_charge_power_w`). Liegt `required` über dem Optimum, gewinnt die Fensterfrist. Damit die
   556 W aus dem Plan-Beispiel tatsächlich geschrieben werden, muss `min_charge_power_w` unter 556 W liegen;
   mit dem Standard 1000 W werden 1000 W geschrieben. Geschrieben wird nur bei mehr als 100 W Änderung.
3. **Ziel im Fenster nur nach oben.** Bei jedem Poll wird neu geplant, `Ziel = max(bisher, Plan)`. Ein
   Plan mit `reason = fallback` (Forecast im Fenster ausgefallen) verändert ein bestehendes Ziel nie.
   Ein steigendes Ziel setzt `target_reached` zurück, sodass die Netzladung wieder anläuft.
4. **Forecast-Tagwahl**: der Solartag ist jetzt der Kalendertag des Fensterendes (gilt für beide Modi).
   Für das Standardfenster 00:00 bis 05:59 ändert sich nichts; für Fenster, die nicht über Mitternacht
   gehen (z. B. 22:00 bis 23:30), wird jetzt die Heute-Entität gewählt.
5. **Fehlender Forecast im Modus `bridge`** verhält sich wie heute (sicherer Fallback 50 %), obwohl die
   Überbrückung ohne Forecast berechenbar wäre. Die Untergrenze wird trotzdem als Attribut ausgegeben;
   ein `max(Untergrenze, 50 %)` wäre die nächste Stufe, wenn der Fallback in der Praxis zu niedrig ist.
6. **Recorder-Import**: `statistics_during_period` wird erst innerhalb der Methode importiert und nur
   aufgerufen, wenn `hass.data` eine Recorder-Instanz enthält. Sollte `hassfest` den Import beanstanden,
   gehört `"after_dependencies": ["recorder"]` in `manifest.json` (nicht Teil dieses Plans).
7. **Persistenz**: `original_discharge_limit` ist neuer Schlüssel in `runtime_state`; nach einem Neustart im
   Fenster wird der Live-Wert 0 nicht als Original übernommen (Muster F4).

## Nächste Schritte nach der Entscheidung

- Entladeleistungs-Entität (Frage 1) und Mittelwert (Frage 2) eintragen, `planner_mode` auf `bridge` stellen.
- Zwei Wochen die Log-Zeilen `Bridge plan: ...` und `Planned charge power ...` sowie die Attribute des
  Sensors `calculated_soc` (`plan_reason`, `bridge_kwh`, `surplus_kwh`, `pv_crossover`) mit dem
  Energie-Dashboard vergleichen: Netzbezug nach Fensterende sollte gegen 0 gehen, Einspeisung am Tag
  nicht steigen.
- Danach `DEFAULT_PLANNER_MODE` auf `bridge` setzen und als Breaking Change in `CHANGELOG.md` dokumentieren.
