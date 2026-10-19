# Einen Nachtlauf untersuchen

Ein Diagnosedownload ist eine Momentaufnahme. Er ersetzt weder den HA-Verlauf
noch ein Protokoll der ganzen Nacht. Die KORE-Schreibhistorie ist begrenzt und
wird beim Neustart neu aufgebaut; das Debugpaket exportiert die letzten 100
Einträge. Ein bestätigtes Schreiben und die tatsächliche Ladeleistung sind
unterschiedliche Nachweise.

## Vor und während des Ladefensters

1. Bei ICN und KORE unter Einstellungen → Geräte & Dienste im Menü der
   Integration die Debug-Protokollierung aktivieren. Sie beginnt erst jetzt;
   vorher verworfene Debugmeldungen lassen sich nicht nachträglich erzeugen.
2. Die Diagnosen beider Integrationen herunterladen und Version, HA-Zeitzone,
   Ladefenster und gewünschtes Verhalten notieren.
3. Im HA-Verlauf prüfen, dass Akku-SoC, Min-SoC, Netzladen, Ladegrenze, AC/DC-
   Ladeleistung, geplante Ladeleistung und Effizienzsuche aufgezeichnet werden.
   Deaktivierte oder vom Recorder ausgeschlossene Entitäten liefern keinen
   vollständigen Verlauf. Vorhandene Recorder-Einstellungen nicht ersetzen.
4. Bei einer Abweichung Uhrzeit und Einheit notieren und währenddessen ein
   KORE-Debugpaket exportieren. Netzbezug des Hauses, AC-Ladeleistung und
   DC-Batterieleistung sind verschiedene Messpunkte.

## Nach dem Ladefenster

Vor Updates, Neustarts oder einem Neuladen der Integrationen:

1. Erneut die Diagnosen beider Integrationen herunterladen und das KORE-
   Debugpaket exportieren (`kostal_kore.export_debug_bundle`, ohne Steuerdaten).
2. Die Debug-Protokollierung beider Integrationen ausschalten und die angebotenen
   Dateien herunterladen. Zusätzlich können die vollständigen HA-Core-Logs
   gesichert werden; die Oberfläche mit zusammengefassten Fehlern reicht nicht.
3. Den Verlauf vom Fensterstart bis nach der Rücksetzung sichern. Alte und neue
   Exporte getrennt aufbewahren. Die Standarddiagnose schwärzt Entitätsnamen;
   private vollständige Logs gehören nicht in öffentliche GitHub-Issues.

## ICN-Diagnosen ab 3.10.3

`state.charge_control` enthält Planung, den als angenommen gespeicherten
Leistungsbefehl und die separat gelesene Ladegrenze sowie AC/DC-Leistung.
`input_configured` zeigt, ob die erforderlichen Felder belegt sind, ohne ihre
Entitätsnamen zu veröffentlichen. Ein vorhandenes Feld bestätigt weder die
Verfügbarkeit noch die passende Semantik des gewählten Gerätesensors.

`state.efficiency_search` enthält den Zustand, Messfortschritt und bei
`waiting` den zuletzt ermittelten `waiting_reason`. Beispiele sind
`grid_charge_not_confirmed`, `missing_configuration`, `night_not_confirmed`,
`connection_headroom` und `charge_limit_write_failed`. Vor der ersten Prüfung
kann `not_evaluated` erscheinen. Diese Angaben beschreiben den ICN-Steuerpfad;
sie ersetzen keine aktuelle Rückmeldung des Wechselrichters.

Zu prüfen sind die Reihenfolge der Befehle, API-/Modbus-Fehler, die tatsächliche
Leistung, das Erreichen des Ladeziels und die bestätigte Rückgabe aller zuvor
gespeicherten Einstellungen. Eine neue Abendreserve kann den Min-SoC danach
erneut anheben; dafür müssen Grund und Ende der Reserve berücksichtigt werden.

## Update und Rueckmeldungen ab 3.10.4

Ein normales HACS-Update von 3.10.2/3.10.3 auf 3.10.4 uebernimmt die vorhandene
Einrichtung, Entitaetszuordnungen und zur Messstrecke passende Effizienzdaten.
Die Integration muss dafuer nicht entfernt oder neu hinzugefuegt werden.
Beide Integrationen vor dem Nachtlauf aktualisieren, HA neu starten und erst
danach die Debug-Protokollierung einschalten.

`charge_control.min_soc_feedback_pct` und `grid_charge_feedback` enthalten
die aktuellen HA-Rueckmeldungen. Nicht nutzbare Werte bleiben `null`; das ist
weder 0 Prozent noch ausgeschaltetes Netzladen. `original_min_soc_pct` und
`reset_pending` zeigen den gespeicherten Ausgangswert und eine ausstehende
Ruecksetzung. Die Konfiguration wird beim Download nicht veraendert.

Fuer die Auswertung werden gemeinsam benoetigt: beide Debug-Logdateien, beide
Integrationsdiagnosen und das KORE-Debugpaket. Fuer die tatsaechliche Leistung
zusaetzlich den aufgezeichneten HA-Verlauf der genannten Entitaeten sichern.
Ein Export morgens liefert keine vollstaendige Zeitreihe der Nacht; insbesondere
koennen die letzten 100 KORE-Schreibereignisse den Fensterstart bereits verdraengt
haben. Bei einer sichtbaren Abweichung auch waehrenddessen einen Export erstellen.
