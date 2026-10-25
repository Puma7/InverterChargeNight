# Plan 017: Effizienzsuche richtig messen, und die Nacht vom 24./25. Oktober

> Plan vom 25.10.2026, nach dem Nachtprotokoll von Pascal. Grundlage: Effizienzsuche (Plan 010),
> Zwangsladung (3.10.x), Stand `develop` = `main` = 3.12.0.

## Status

- **Priorität**: P1. Pascal: „behebe alles, was hier zu beheben ist, vollständig … und behebe vor
  allem die Probleme mit der Effizienzsuche.“
- **Version**: 3.13.0 (neues Einrichtungsfeld)

## Was das Protokoll zeigt

1. **Die Effizienzsuche hat nicht gesucht.** Von 23:01 bis 03:51 lief vierzehnmal ein Versuch bei
   1000 W, und jeder wurde verworfen: „a loss of 200.0 % is not physical“. Weil keine Messung
   gültig war, kam die Grob-Fein-Suche nie über den ersten Punkt hinaus.
   - Ursache: Als *aufgenommen* war KostalKore „Grid to Battery Power“ (`Grid2Bat_P`) eingetragen,
     als *angekommen* „Battery Power“ (`battery_P`). ICN selbst schlägt dieses Paar bei KostalKore
     vor (`_KORE_SUFFIXES`).
   - `Grid2Bat_P` ist die Aufteilung der DC-Akkuleistung nach Quelle, keine Messung der
     AC-Aufnahme. `battery_P` zählt Laden negativ. Das Verhältnis lag die ganze Nacht bei
     −0,997 bis −1,014, also praktisch −1, und daraus ergibt sich ein Verlust von 200 %.
   - Der Simulator im Lab hat `Grid2Bat_P` als AC-Leistung veröffentlicht. Deshalb ist es dort
     nie aufgefallen.
2. **Das Netzladen war das ganze Fenster gesperrt, und das zu Recht.** Eine Sekunde nach dem
   Einschalten von ManualCharge las KORE 21 460 W aus Register 1038, das Muster vom 20./21.10.
   ICN hat abgeschaltet, und der Min-SOC hat unter dem 1000-W-Limit bis 37 % geladen (04:01).
   - Schwachstelle: Jede Nacht wird erneut eingeschaltet. Bis zur Erkennung vergehen etwa 5 s, in
     denen die Zwangsladung ungebremst ziehen kann.
3. **Ein unnötiger Schreibvorgang um 04:16.** Der Min-SOC war beim Laden auf 37 % aufgerundet
   (Ziel 36,1 %). Nach Erreichen des Ziels rundet ICN ab und schrieb 36 % zurück. Das kostet einen
   Schreibvorgang pro Nacht ohne Nutzen.
4. **KORE bestätigt 1038 nicht zuverlässig.** Nach jeweils 1000 W gelesen: 21 460 W (direkt nach
   dem Einschalten), 0 W (nach dem Ausschalten), 245 W und 53 W gegen Morgen. Das betrifft KORE,
   nicht ICN. Ein Issue dort beschreibt das.

## Was sich ändert

### Die AC-Seite: Netzbezug minus Hausverbrauch

- Neues Feld `charge_power_house_entity` (Hausverbrauch, W). Zusammen mit `grid_import_entity`
  gilt `AC-Seite = Netzbezug − Hausverbrauch`. Pascals Zahlen: 1575 − 495 = 1080 W in das
  Ladegerät, 1000 W im Akku, also 7,4 % Verlust.
- Entweder dieses Paar oder ein eigener AC-Sensor, nicht beides (`ac_side_required`,
  `ac_side_twice`). Der Netzbezug ohne Budget ist dann erlaubt, weil er nur für die Messung
  gebraucht wird.
- Die Einrichtung lehnt KostalKore `Grid2Bat_P` als AC-Seite ab, wie bisher schon `ac_P`. Als
  Vorschlag für den Hausverbrauch dient `Home_P`. Der Netzbezug wird nicht vorgeschlagen, denn er
  gehört auch zum Hausanschlussschutz, und dafür empfiehlt die Doku `powermeter P`.
- Der Hausanschlussschutz nutzt dieselbe AC-Seite als eigene Ladeaufnahme. Ohne AC-Sensor zählte
  bisher der ganze Bezug als Fremdlast.
- Die Signatur der Messwege wächst nur für den neuen Weg. Bestehende Messungen bleiben gültig.

### Vorzeichen, Plausibilität, Abbruch

- **Ausrichtung pro Versuch.** Liest eine Seite beim Messbeginn mindestens ein Viertel der
  Versuchsleistung *negativ*, ist das eine Vorzeichenkonvention, und ihr Vorzeichen wird für den
  Versuch umgedreht. Das wird einmal protokolliert. Netzbezug minus Hausverbrauch wird nie
  umgedreht.
- **Untergrenze 0,5 %.** Kein AC/DC-Lader verliert weniger. Darunter messen beide Sensoren
  dasselbe.
- **Abbruch nach zwei unmöglichen Ergebnissen** im Fenster (`sensors_implausible`). Dazu kommt eine
  dauerhafte Reparaturmeldung mit AC-Seite, Akkuseite und Verhältnis. Sie verschwindet bei einer
  gültigen Messung, bei neuen Sensoren und beim Entfernen des Eintrags.

### Zwangsladung merken

- Erkennt ein Fenster die Zwangsladung (Limit beim Einschalten aufgehoben) und erreicht danach das
  Ziel über den Min-SOC, merkt sich ICN das für Schalter und Ladegrenze, 30 Tage lang.
- Folgende Fenster lassen den Schalter von Anfang an aus und laden über den Min-SOC.
- Ohne Ladeerfolg wird nichts gemerkt. Ein einzelner Schreibfehler oder ein Wechselrichter, dessen
  Min-SOC nicht aus dem Netz lädt, verliert so nie für Wochen das Netzladen.

### Min-SOC-Rundung

- Nach Erreichen des Ziels hält die Prüfung den aufgerundeten Schritt, statt ihn zurückzuschreiben.
  Das gilt nur für genau diesen einen Schritt. Liegt der Wert darüber oder darunter, wird weiter
  korrigiert.

## Verworfen

- **Die Sperre nach einer unbestätigten Rücklesung aufheben.** Die 21 460 W um 23:01:07 waren
  eine echte Zwangsladung, keine Fehlmessung.
- **Das Vorzeichen anhand des Mittelwerts am Versuchsende drehen.** Ein Akku, der zeitweise
  entlädt, würde sonst die Richtung umkehren. Die Ausrichtung beim Messbeginn mit der
  Viertel-Schwelle ist robuster.

## Prüfung

- 27 neue Tests, jeder mit Gegenprobe durch Zurücknehmen (16 Regeln).
- Lab: Der Simulator veröffentlicht `Grid2Bat_P` jetzt DC-seitig wie das Gerät.
  - `check_kostal` misst Netzbezug minus `Home_P` und findet das 8-kW-Optimum mit 4 % Verlust.
  - `check_real_ha`, `check_recovery` und `check_edge_cases` sind lokal grün.
  - `check_grid` und `check_communication` brauchen evcc und laufen in der CI.
