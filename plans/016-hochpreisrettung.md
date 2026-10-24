# Plan 016: Hochpreisrettung – nur halten, was die Hochpreiszeit braucht

> Plan vom 24.10.2026, auf Pascals Wunsch aus dem Gespräch nach Release 3.11.1.
> Grundlage: Abendrettung und Ad-hoc-Fenster (Plan 013), Preisquelle (Plan 012, Stufe 3),
> Stand `develop` = `main` = 3.11.1.

## Status

- **Priorität**: P1. Pascal: „deine drei Punkte finde ich gut, macht meinen Plan und setzt das um.“
- **Aufwand**: M
- **Risiko**: mittel. Die Rettung bleibt Opt-in (Schalter, standardmäßig aus). Wer sie nicht
  eingeschaltet hat, merkt keinen Unterschied.
- **Version**: 3.12.0

## Ausgangslage: die Rettung in 3.11.1

1. **Stufe 1, halten.** Sobald der Ausblick einen Fehlbedarf meldet, öffnet ein Ad-hoc-Fenster bis
   zum Beginn der Hochpreiszeit, und zwar mit dem **aktuellen** SOC als Min-SOC. Steigt der SOC
   durch die Sonne, zieht die Untergrenze mit (`_update_window_floor`), und zwar ohne Obergrenze.
2. **Stufe 2, kaufen.** In den letzten zwei Stunden davor wird bis `required_soc` aus dem Netz
   geladen, ohne Blick auf den Preis.
3. **Nie zurück.** Die Stufe steigt nur. Wird die Lücke später kleiner oder verschwindet sie,
   bleibt die Sperre trotzdem bis zum Beginn der Hochpreiszeit.
4. **Rechnung mit Reserven.** `required_soc` enthält den Zuschlag `high_price_margin_pct`, und die
   Sonne bis dahin zählt nur mit `1 − forecast_error_margin`.

Pascals Beispiel: 10 kWh im Akku, die Hochpreiszeit braucht 5 kWh. 3.11.1 hält alle 10 kWh fest. Das
Haus zieht dann bis zum Abend Netzstrom zum Standardtarif, obwohl 5 kWh davon den Tag hätten
versorgen können. Was nach der Hochpreiszeit noch im Akku ist, geht in die günstige Nacht, und dort
ist es am wenigsten wert.

## Die Rechnung dahinter

Beispielpreise: Nacht 14 ct, Standard 30 ct, Hochpreis 35 ct, Wirkungsgrad hin und zurück
ungefähr 85 %.

| Fehler | kostet je kWh |
|---|---|
| Zu wenig gehalten: die letzte halbe Stunde der Hochpreiszeit aus dem Netz | 35 − 30 = **5 ct** |
| Zu viel gehalten, aber in der Hochpreiszeit verbraucht | ungefähr **0** |
| Zu viel gehalten, bis in das Nachtfenster getragen | Tagstrom statt Akku, dann nachts nicht gebraucht: bis **14 ct** |
| In Stufe 2 aus dem Netz gekauft | 30 ÷ 0,85 ≈ 35 ct für 35 ct Ersparnis: **0 oder Verlust** |

Ein Fehlbedarf kostet also wenig, ein Überhang viel, vor allem wenn die Hochpreiszeit direkt an das
Nachtfenster grenzt. Pascal: „Bisschen Toleranz, ja, aber nicht zu viel. Nur weil man mal eine halbe
Stunde Hochpreiszone rutscht, heißt das nicht, dass die Welt untergeht.“

## Was sich ändert

### 1. Nur halten, was die Hochpreiszeit braucht

- Stufe 1 setzt als Ziel `min(aktueller SOC, required_soc)`.
  - Liegt der SOC darüber, versorgt der Akku das Haus noch bis `required_soc` und hält dann.
  - Liegt er darunter, wird der aktuelle Stand gehalten.
- Die Untergrenze, die mit der Sonne steigt, bekommt eine **Obergrenze** `required_soc`
  (`_adhoc_floor_cap_soc`, persistiert). Sie zieht also nur bis zum Bedarf mit, nicht darüber.
- Ein Min-SOC über dem aktuellen SOC wird nie geschrieben, solange nicht gekauft werden soll. Auf
  dem Kostal löst ein solcher Wert eine Zwangsladung aus dem Netz aus.
- Andere Ad-hoc-Fenster (`block_discharge`, `charge_to`) bleiben, wie sie sind: Sie haben keine
  Obergrenze.

### 2. Der Bedarf wird knapp gerechnet

- Der Ausblick für die Rettung rechnet ohne `high_price_margin_pct` und ohne Abschlag auf die
  Prognose. Die Last kommt aus dem **gemittelten** Profil (`_house_load_profile`), nicht aus dem
  Sicherheitsniveau der Nacht.
- Die Nachtplanung behält ihre Reserven. Dort ist ein Überhang billiger, weil er im Lauf des Tages
  verbraucht wird.
- Der Sensor *Abendausblick* zeigt dieselbe knappe Rechnung, die die Rettung verwendet.

### 3. Nachkaufen nur, wenn es sich rechnet

- Stufe 2 kauft nur, wenn eine Preis-Entität konfiguriert ist und
  `Hochpreis-Mittel > Mittel bis zum Beginn ÷ (Ladewirkungsgrad × Entladewirkungsgrad) + 2 ct`.
  Das ist dieselbe Abwägung wie beim Verwerfen der Abendreserve (`prices.py`), hier mit dem ganzen
  Hin- und Rückweg.
- Ohne Preis-Entität oder bei unvollständigen Preisen wird nicht gekauft, nur gehalten. Bei einem
  Abstand von 5 ct wird also nie gekauft.

### 4. Laufend neu bewerten

Bei jeder Abfrage (Standard alle 15 Minuten) wird neu gerechnet.

- **Lösen**: Reicht der Ausblick wieder, also `projected_soc ≥ required_soc + 0,5 kWh`, endet das
  Fenster, und der Wechselrichter bekommt seinen Min-SOC zurück. Der Ausblick beantwortet genau
  die Frage „reicht es, wenn der Akku ab jetzt frei entlädt?“.
- **Wieder greifen**: Meldet er später wieder einen Fehlbedarf, öffnet die Rettung erneut.
- **Nachführen**: Ändert sich `required_soc` um mindestens 1 Prozentpunkt oder wechselt die Stufe,
  werden Ziel und Obergrenze angepasst. Sinkt die Obergrenze unter die schon gehaltene Untergrenze,
  sinkt die Untergrenze mit.
- **Gegen Schreibflattern**: Zwischen „greifen“ (Fehlbedarf > 0) und „lösen“ liegen 0,5 kWh. Im
  Band dazwischen bleibt alles, wie es ist.
- Eine Abfrage ohne Ausblick (Prognose weg, Akku unlesbar) ändert nichts. Das Fenster endet
  spätestens mit dem Beginn der Hochpreiszeit.
- Ein Ad-hoc-Fenster aus einer Aktion wird von der Rettung nicht übernommen.

### 5. Name

Aus „Abendrettung“ wird **„Hochpreisrettung“**, englisch „High-price rescue“. Das gilt für den
Schalter, den Grund des Ad-hoc-Fensters und die Dokumentation. Neue Installationen bekommen damit
neue Entitäts-IDs, bestehende behalten ihre. Intern (`evening_rescue`, Persistenz, Diagnose) bleibt
der Schlüssel, damit nach dem Update nichts verloren geht.

## Verworfen

- **Fester Zeitabstand statt Hysterese.** Die Abfrage läuft ohnehin alle 15 Minuten. Ein weiterer
  Zeitgeber verzögert nur das Lösen. Gegen das Flattern hilft das Band.
- **Feste Preisfelder für die Hochpreiszeit.** Pascals Abstand von 5 ct würde ohnehin nie kaufen.
  Wer einen größeren Abstand hat, hat meist auch eine Preis-Entität.

## Prüfung

Jede neue Regel bekommt einen Test, der ohne sie scheitert (Gegenprobe durch Zurücknehmen):

- Ziel `min(SOC, required)`: SOC 80, Bedarf 40, das Ziel ist 40.
- Die Untergrenze zieht mit der Sonne bis `required` mit und nicht darüber.
- Lösen, wenn die Sonne reicht, und erneutes Greifen bei einem späteren Fehlbedarf.
- Im Band bleibt alles unverändert.
- Kein Kauf ohne Preis-Entität und keiner bei 5 ct Abstand, aber ein Kauf bei großem Abstand.
- Der Ausblick rechnet ohne Zuschlag und ohne Prognoseabschlag.
- Ein Ad-hoc-Fenster aus einer Aktion bleibt unangetastet.

Danach die ganze Suite auf 2025.2 und der neuesten Version, mypy und pyright 3.13/3.14 und die
Link-Prüfung. Die Dokumentation (DE/EN) und das CHANGELOG werden nachgeführt.
