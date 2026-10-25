# Inverter Charge Night mit KOSTAL KORE

[🇬🇧 English](../en/kostal-kore.md) · [Übersicht der Dokumentation](README.md)

Geprüft gegen [`Puma7/KostalKore`](https://github.com/Puma7/KostalKore) am 2026-09-12 (der Commit
zum Zeitpunkt des Schreibens), anhand der Registertabelle, der Plattformen und der Schreibwege.
Diese Integration bleibt unabhängig vom Wechselrichter: Sie spricht immer nur mit gewöhnlichen
Home-Assistant-Entitäten. Diese Seite ist die Übersetzungstabelle für einen bestimmten
Wechselrichter, dazu die Fallstricke, die echtes Geld kosten, wenn man sie falsch macht.

## Welche Entität wohin gehört

| Inverter Charge Night fragt nach | Diese KOSTAL-KORE-Entität verwenden | Hinweise |
|---|---|---|
| **Entität für den Mindestladestand (Min-SOC)** (Pflicht) | `Battery min SoC` (REST, `Battery:MinSocRel`) oder `Battery Min SoC (Modbus)` (Register 1042) | Beide funktionieren. Die Modbus-Entität nimmt 5–100 % an, ein Minimum unter 5 % lässt sich damit also nicht schreiben. |
| **Schalter für Netzladung** (Pflicht) | `Battery Manual Charge` (`Battery:ManualCharge`) | Braucht den **Installateurscode** in KOSTAL KORE, und die Entität ist standardmäßig ausgeblendet. |
| **Ladestandssensor des Speichers** (Pflicht) | `Battery SoC` | |
| **PV-Prognose (MORGEN)** (Pflicht) | — | Kommt von Solcast oder einer anderen Prognose-Integration, nicht vom Wechselrichter. |
| **Entität für die maximale AC-Ladeleistung** | `Battery Max Charge Limit (Modbus)` (Register 1038) oder `Battery Max Charge Power (G3)` (Register 1280) | Siehe die Fallstricke unten — in diese Entität schreibt die Begrenzung des Hausanschlusses. |
| **Im Speicher ankommende Ladeleistung** | `Battery Power` (`devices:local:battery` → `P`, DC-Seite) | Zählt Laden negativ; ICN dreht das für die Effizienzsuche um. |
| **Hausverbrauch (Leistung)** | `Home Power` (`devices:local` → `Home_P`) | Mit dem Netzbezug ergibt das die AC-Aufnahme des Ladegeräts für die Effizienzsuche: Netzbezug minus Hausverbrauch. |
| **AC-Eingangsleistung nur für die Batterieladung** | leer lassen | `Grid2Bat_P` (*Grid to Battery Power*) ist die Aufteilung der DC-Leistung des Akkus durch den Wechselrichter, nicht seine AC-Aufnahme: Gegen `Battery Power` ergab sie die ganze Nacht ein Verhältnis von genau −1 (24./25. Oktober 2026). Die Einrichtung lehnt sie ab. |
| **Energiezähler: aufgenommen** | vorerst leer lassen | Ob `Battery Charge from Grid Total` die AC- oder die DC-Seite zählt, ist nicht bestätigt. KOSTAL dokumentiert Modbus-Register 1054 als *gesamte AC-Ladeenergie (Netz zu Akku)* und 1046 als DC-Ladeenergie; KOSTAL KORE legt dafür noch keine Sensoren an. Mit beiden könnte die Effizienzsuche die Energie messen, die wirklich geflossen ist. |
| **Energiezähler: im Speicher angekommen** | derzeit nicht als Entität verfügbar | KOSTAL KORE liest Modbus `total_dc_charge` (1046), legt dafür aber keinen Sensor an. Ohne ihn greift die Effizienzsuche auf die Leistungssensoren zurück. |
| **Entität für die Entladeleistungsgrenze** (Altbestand) | `Battery Max Discharge Limit (Modbus)` (1040) oder `Battery Max Discharge Power (G3)` (1282) | Nur nötig, um einen Wert zurückzugeben, den eine Version vor 3.10.6 geschrieben hat. ICN schreibt ihn nicht mehr. |
| **Schalter „Entladung sperren“** (Altbestand) | `Battery Disable Discharge` (`EnergyMgmt:BatCtrl:DisabelDischarge`) | Nur nötig, um einen Zustand zurückzugeben, den eine Version vor 3.10.6 geändert hat. ICN schaltet ihn nicht mehr; der Akku wird nur noch über den Min-SOC gehalten. |
| **Netzbezug** | `devices:local:powermeter` → **`P`** (die Gesamtleistung des Zählers), nicht `Home Power from Grid` | Siehe die Fallstricke. Dasselbe Modul liefert auch `L1_I` / `L2_I` / `L3_I`, die Ströme je Phase am Zähler — das direkteste Maß dafür, was der Hausanschluss trägt. |
| **Entität für Notstrom-/Inselbetrieb** | `Inverter:State` mit **Zustände für Notstrombetrieb** = `ESB` | Kostal meldet `ESB` (Ersatzstrombetrieb) im Wechselrichterzustand. `devices:local` enthält außerdem `WorkTimeESB`, die Stunden im Inselbetrieb; das ist ein zweiter Weg, ihn zu bestätigen. Eine eigene Entität „Insel aktiv“ gibt es nicht. |
| **Energiezähler des Hausverbrauchs** | `Home Consumption Total` (`Statistic:EnergyHome:Total`) | Für das Lastprofil des Überbrückungs-Planers. |
| **Maximale Ladeleistung AC+DC** | `Battery Max Charge Power (G3)` (1280) | |

Die Prozessdaten oben stammen aus einem echten Diagnose-Download eines PLENTICORE L G3
(Firmware MC 07.00.09358, `pykoplenti`-API 0.2.0). Diese Namen gibt es also auf dieser Hardware
tatsächlich; sie sind nicht nur aus dem Code abgeleitet.

## Fallstricke

**`Battery Charge Power (AC) Absolute` hat ein Vorzeichen.** Negativ lädt, positiv *entlädt*. Es
ist ein Sollwert, keine Grenze. Trage es nie als Entität für die maximale AC-Ladeleistung ein: Eine
dort geschriebene Ladegrenze von 5000 W würde den Akku anweisen, mit 5 kW zu entladen.

**Die externe Steuerung muss am Wechselrichter freigegeben sein.** KOSTAL KORE prüft das
Modbus-Register 1080 („battery management mode“). Steht es nicht auf `External via MODBUS`,
verwirft KOSTAL KORE jeden Schreibvorgang und schreibt nur auf seiner eigenen Seite eine
Protokollzeile. Der Dienstaufruf gelingt trotzdem, nachgelagert kann es also niemand erkennen.
Inverter Charge Night liest deshalb bei jeder Überprüfung die AC-Ladegrenze zurück und warnt, wenn
der Wechselrichter einen *höheren* Wert hält, als geschrieben wurde. Wenn du
`not accepting external control` im Protokoll siehst, schützt die Begrenzung des Hausanschlusses
nichts, bis du den Wechselrichter auf externe Modbus-Steuerung umstellst.

**Register 1038 hat mehr als einen Besitzer.** Innerhalb von KOSTAL KORE übernehmen der Grid
Feed-In Optimizer, der SoC Controller, Block Battery Charging und der Battery Test es exklusiv.
Diese Sperre gilt nicht für andere Integrationen: Ist eine dieser Funktionen aktiv, während ein
Ladefenster läuft, überschreiben sich beide gegenseitig. Lass immer nur eine davon laufen, oder
richte Inverter Charge Night stattdessen auf das G3-Register (1280) aus.

**Die G3-Grenzregister fallen zurück.** 1280/1282 kehren nach `Battery Time Until Fallback (G3)`
zu den Standardwerten des Wechselrichters zurück, wenn sie nicht neu geschrieben werden. KOSTAL KORE
hält sie am Leben, solange *es selbst* den Wert besitzt. Eine über diese Entitäten geschriebene
Grenze bleibt daher bestehen; eine in 1038 geschriebene Grenze hat diesen Schutz nicht.

**`Home Power from Grid` ist nicht der Hausanschluss.** Es ist der Teil des *Hausverbrauchs*, der
aus dem Netz kommt. Das Netzladen des Akkus selbst ist darin nicht enthalten, und bei mehreren
Wechselrichtern oder einem Generator sieht der Wert ebenfalls nicht alles. Die Begrenzung des
Hausanschlusses braucht den gesamten Bezug am Zähler: Verwende den Sensor des KSEM bzw. des
Leistungsmessers. Im Zweifel vergleichst du den Sensor mit deinem Zähler, während eine Wallbox lädt.

**`Battery Manual Charge` kann eine Zwangsladung sein.** Auf einem PLENTICORE G3 (Oktober 2026)
zeigte die Ladegrenze in Register 1038 innerhalb einer Sekunde nach dem Einschalten von
`Battery:ManualCharge` die eigenen 22,3 kW des Wechselrichters, und eine Grenze, die bei
eingeschaltetem Schalter geschrieben wurde, wurde ignoriert. Allein das Anheben des Min-SOC ließ
denselben Wechselrichter aus dem Netz bis dorthin laden, unterhalb der geschriebenen Grenze — aber
nicht, wenn der Min-SOC nur 1 % über dem Akku liegt: In der Nacht vom 21. auf den 22. Oktober lud
ein Min-SOC von 22 % bei einem Akku mit 21 % die ganze Nacht nichts (0 W bei einer Grenze von
1000 W), und ab 23:31 las der Kostal seine Ladegrenze als 51 W und ignorierte Schreibvorgänge.
Inverter Charge Night schreibt die Grenze direkt nach dem Einschalten erneut und prüft sie kurz
danach noch einmal. Hat der Schalter sie angehoben, wird das Netzladen für den Rest des Fensters
wieder ausgeschaltet, und der angehobene Min-SOC übernimmt das Laden. Hat ein Fenster sein Ziel
so erreicht, lassen die folgenden Fenster den Schalter 30 Tage lang von Anfang an aus. Das kurze
Einschalten passiert also einmal statt jede Nacht. Es zählt nur eine Ladung, nachdem der Schalter
„aus“ gemeldet hat, und nur, wenn die Sonne von da an bis zum Fensterende unter dem Horizont
war. Ein neuer Schalter, eine neue Ladegrenze oder
30 Tage, und es wird erneut versucht. Im Protokoll steht beim ersten Mal
`The charge limit did not hold with ... switched on` und danach
`Grid charging stays off this window`.

In der Nacht vom 24. auf den 25. Oktober 2026 las KOSTAL KORE Register 1038 eine Sekunde nach dem
Einschalten als 21 460 W zurück, nach dem Ausschalten als 0 W und später in der Nacht als 245 W
und 53 W, jedes Mal nachdem 1000 W geschrieben worden waren. Ein Lesen von 1038 ist keine
verlässliche Bestätigung des geschriebenen Werts.

**Eine Grenze in 1038 verfällt, wenn sie nicht neu geschrieben wird.** 1000 W um 23:01 geschrieben
und bestätigt, um 23:12 etwa 8 kW Ladeleistung, ohne dass sonst etwas in das Register schrieb: Ohne
neuen Wert innerhalb seiner Zeitspanne (30–60 s) kehrt der Wechselrichter zu seinem eigenen
Batteriemanagement zurück. Inverter Charge Night schreibt die geltende Grenze deshalb alle 20 s
erneut — nur für die Steuerung über KostalKore, deren Wert verfällt —, solange der Akku in einem
Fenster aus dem Netz laden kann (KOSTAL KORE und evcc schreiben sie ebenfalls zyklisch), und nie
außerhalb eines Fensters.

**Die Ladegrenze wird als Gleitkommazahl zurückgelesen.** Geschriebene 1000 W lesen sich als 997,9
bis 999,9 W. KOSTAL KORE vergleicht den zurückgelesenen Wert exakt und meldet einen solchen
Schreibvorgang als unbestätigt; Inverter Charge Night akzeptiert ihn, sobald der nächste Messwert
bis auf 100 W an den geschriebenen Wert herangekommen ist.

**Installateurscode.** `Battery:ManualCharge`, `Battery:BackupMode:Enable` und die Steuerung der
Ladeleistung sind in KOSTAL KORE als nur für Installateure gekennzeichnet. Ohne den dort
hinterlegten Code lässt sich der Netzladeschalter nicht bedienen, und diese Integration kann ihre
Aufgabe nicht erfüllen.

## Notstrom- und Inselbetrieb

Ein Stromausfall mit manuellem Umschalter ist die eine Situation, in der diese Integration den
Wechselrichter vollständig loslassen muss. Es gibt kein Netz, aus dem geladen werden könnte, und der
angehobene Min-SOC eines laufenden Fensters würde den Akku daran hindern, das Haus zu versorgen —
das Licht ginge bei vollem Akku aus.

Stelle **Entität für Notstrom-/Inselbetrieb** = den Zustandssensor des Wechselrichters und
**Zustände für Notstrombetrieb** = `ESB` ein. Die Integration beendet dann das laufende Fenster in
dem Moment, in dem dieser Zustand auftritt, stellt den eigenen Min-SOC des Wechselrichters wieder
her, schaltet das Netzladen aus und startet nichts Neues, bis der Netzbetrieb zurück ist.

Bei einer rein manuellen Umschaltbox, die der Wechselrichter nicht meldet, legst du in Home
Assistant einen `input_boolean` an, wählst ihn als Notstrom-Entität aus und schaltest ihn um, wenn
du umschaltest. Das funktioniert mit jedem Wechselrichter und jeder Umschaltbox.
