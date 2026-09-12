# Plan 009: Entladung im Fenster sperren, mit Min-SOC als Rückfallebene

> **Anweisung an den Ausführenden**: Schritt für Schritt vorgehen, jeden Prüfbefehl ausführen.
> Bei einer STOP-Bedingung anhalten und berichten. Am Ende die Statuszeile in `plans/README.md` aktualisieren.
>
> **Drift-Prüfung (zuerst)**: `git diff --stat 16c2d0a..HEAD -- custom_components/inverter_charge_night/`

## Status

- **Priorität**: P1
- **Aufwand**: M
- **Risiko**: MED (verändert, welcher Wert auf den Min-SOC geschrieben wird)
- **Hängt ab von**: 004 (eine Zielquelle), 005 (Persistenz), 006 (Entladesperre über Leistungsgrenze)
- **Kategorie**: direction
- **Geplant bei**: Commit `16c2d0a`, 2026-09-12

## Warum das wichtig ist

Im Fenster ist Netzstrom günstig, gespeicherte PV-Energie dagegen wertvoll: sie ersetzt tagsüber
teuren Netzbezug. Entlädt der Speicher nachts ins Haus, wird billige Energie durch teure ersetzt.
Plan 006 hat dafür eine optionale Entladeleistungsgrenze eingeführt, aber **kein Wechselrichter
garantiert eine solche Entität**. Der Kostal bietet einen Schalter dafür, andere Hersteller nicht.

Es gibt jedoch einen Weg, der immer funktioniert, weil er nur die Entität braucht, die die
Integration ohnehin steuert: **den Min-SOC anheben**. Ein Speicher entlädt nicht unter seinen
Min-SOC. Setzt man ihn während des Fensters auf den aktuellen Ladestand, bleibt die gespeicherte
Energie liegen; am Fensterende wird der Originalwert zurückgeschrieben, wie heute schon.

Nach diesem Plan gibt es drei Wege, in dieser Reihenfolge: Schalter, Leistungsgrenze, Min-SOC.

## Ist-Zustand

- `current_target_soc()` liefert das **Ladeziel** und ist die einzige Zielquelle (Plan 004).
- `_control_kostal(target_soc)` schreibt genau diesen Wert auf `CONF_KOSTAL_MIN_SOC_ENTITY` und
  schaltet die Netzladung ein; `_capture_original_min_soc` sichert vorher den Originalwert.
- `_verify_and_restore_min_soc()` vergleicht den Wechselrichterwert mit `current_target_soc()` und
  schreibt bei Abweichung über 0,5 % zurück.
- `_apply_discharge_block()` / `_reset_discharge_limit()` setzen optional
  `CONF_DISCHARGE_LIMIT_ENTITY` auf 0 und stellen den Originalwert wieder her.
- `_on_window_end` stellt über `_reset_settings()` den Min-SOC auf `original_min_soc` zurück.
- Beim Kostal heißt der passende Schalter sinngemäß „Batterieentladung sperren"; er existiert dort,
  ist aber nicht bei jedem Hersteller vorhanden.

## Entwurf: zwei getrennte Größen

Heute ist „Ladeziel" und „was auf den Min-SOC geschrieben wird" dasselbe. Das trennt dieser Plan:

| Größe | Bedeutung |
|---|---|
| `current_target_soc()` | Ladeziel: bis hierhin wird aus dem Netz geladen. Unverändert. |
| `inverter_floor_soc()` | Wert, der auf die Min-SOC-Entität geschrieben wird. |

```
inverter_floor_soc():
    ziel = current_target_soc()
    wenn Entladesperre nicht über Min-SOC läuft:  -> ziel
    wenn kein aktives Fenster:                    -> ziel
    -> min(user_max_soc, max(ziel, hoechster_soc_im_fenster))
```

`hoechster_soc_im_fenster` wird beim Fensterstart mit dem aktuellen Ladestand initialisiert und
steigt monoton (nie fallend), damit ein zitternder Messwert keine Schreibzugriffe erzeugt. Er wird
am Fensterende zurückgesetzt und gehört in `runtime_state` (Plan 005), damit ein Neustart im
Fenster den Boden nicht verliert.

Warum monoton: Fällt der Ladestand doch (die Sperre greift nicht), bleibt der Boden oben und der
Wechselrichter lädt aus dem Netz nach — im Fenster genau richtig, weil Netzstrom dann günstig ist.

Wichtig: **Alle Stellen, die heute den geschriebenen Wert mit `current_target_soc()` vergleichen,
müssen auf `inverter_floor_soc()` umgestellt werden** — sonst schreibt die Verifikation den
angehobenen Boden sofort wieder herunter. Die Prüfung „Ziel erreicht" bleibt beim **Ladeziel**.

## Umfang

**Im Umfang**: `const.py`, `__init__.py`, `config_flow.py`, `strings.json` + `translations/en.json`,
`sensor.py` (ein Attribut), Tests, README.

**Nicht im Umfang**: Wechselrichter-Profile (Backlog 009 im Masterplan), Morning-Discharge-Modus.

## Schritte

### Schritt 1: Konfiguration

```python
CONF_DISCHARGE_BLOCK_SWITCH = "discharge_block_switch"   # switch, optional
CONF_DISCHARGE_BLOCK_MODE = "discharge_block_mode"       # "off" | "auto"
DEFAULT_DISCHARGE_BLOCK_MODE = "auto"
```
`auto` wählt selbst: Schalter, sonst Leistungsgrenze, sonst Min-SOC. `off` lässt die Entladung zu
(bisheriges Verhalten). Im Schritt `power` des Assistenten, mit Beschreibung:
„Sperrt die Entladung des Speichers während des Fensters, damit gespeicherte Energie für den Tag
bleibt. Ein Schalter des Wechselrichters wird bevorzugt; gibt es keinen, hebt die Integration
stattdessen den Min-SOC an und setzt ihn am Fensterende zurück."

### Schritt 2: Auswahl des Wegs

`_discharge_block_method() -> "switch" | "limit" | "min_soc" | None`, geprüft in dieser Reihenfolge
gegen die Konfiguration. Ergebnis einmal pro Fensterstart protokollieren, damit im Log steht,
welcher Weg genutzt wird.

### Schritt 3: Schalter unterstützen

Analog zu den bestehenden Erfassen/Setzen/Zurücksetzen-Paaren: Zustand vor dem Einschalten sichern
(`_original_discharge_block`, in `runtime_state`), am Fensterende zurückschreiben, in
`_reset_settings` in das Gesamtergebnis einrechnen, bei Backup-Modus nie setzen.

### Schritt 4: Min-SOC-Rückfallebene

`inverter_floor_soc()` wie oben einführen. Danach ersetzen:
- `_control_kostal`: schreibt `inverter_floor_soc()` statt des Ladeziels; die Entscheidung
  „Netzladung ein" bleibt am **Ladeziel** (`current_soc < current_target_soc()`).
- `_verify_and_restore_min_soc`: vergleicht und restauriert gegen `inverter_floor_soc()`.
- `_control_discharge` (Morgenentladung) bleibt unverändert: dort ist eine Sperre sinnlos.
- Die Bereichsprüfung in `_control_kostal` (`user_min <= wert <= user_max`) gilt weiter.

### Schritt 5: Sichtbarkeit

Im Sensor `calculated_soc` zwei Attribute ergänzen: `inverter_floor_soc` und
`discharge_block` (`switch`/`limit`/`min_soc`/`off`). So ist erkennbar, warum der Wechselrichter
einen höheren Wert zeigt als das Ladeziel.

### Schritt 6: Tests

- Schalter konfiguriert: wird eingeschaltet, am Fensterende in den Originalzustand zurück.
- Nur Leistungsgrenze konfiguriert: bisheriges Verhalten.
- Keines von beiden: Min-SOC wird auf den Ladestand angehoben, wenn dieser über dem Ziel liegt.
- Ladestand unter dem Ziel: Boden bleibt das Ziel (kein Anheben).
- Boden fällt nicht, wenn der Ladestand fällt.
- Verifikation schreibt den angehobenen Boden nicht herunter.
- „Ziel erreicht" richtet sich weiter nach dem Ladeziel, nicht nach dem Boden.
- Fensterende stellt den Originalwert her (auch bei angehobenem Boden).
- Neustart im Fenster: Boden bleibt erhalten.
- `discharge_block_mode = off`: alles wie bisher.

### Schritt 7: Dokumentation

README: Abschnitt „Entladung im Fenster", der die drei Wege und die Reihenfolge nennt, erklärt
warum der Min-SOC-Weg ohne Herstellerfunktion auskommt, und ausdrücklich festhält, dass der
Wechselrichter dann einen höheren Min-SOC anzeigt als das Ladeziel — das ist beabsichtigt und wird
am Fensterende zurückgesetzt.

## Fertig-Kriterien

- [ ] Tests grün auf allen drei HA-Versionen; `scripts/smoke_real_ha.py` grün
- [ ] mypy und pyright ohne Befund; `strings.json` und `translations/en.json` identisch
- [ ] `grep -c "inverter_floor_soc" custom_components/inverter_charge_night/__init__.py` ≥ 4
- [ ] Kein Vergleich des geschriebenen Min-SOC gegen `current_target_soc()` mehr übrig
- [ ] Mit `discharge_block_mode = off` ist das Verhalten unverändert (Test)
- [ ] Statuszeile in `plans/README.md` aktualisiert

## STOP-Bedingungen

- Der angehobene Boden würde das Fensterende überdauern: dann wäre der Speicher dauerhaft blockiert.
- Ein Test zeigt, dass die Verifikation gegen das Ladeziel prüft und den Boden herunterschreibt.
- Der Boden könnte `user_max_soc` überschreiten oder unter `user_min_soc` fallen.

## Wartungshinweise

- Der angehobene Min-SOC ist der Grund, warum der Wechselrichter im Fenster einen anderen Wert
  zeigt als der Sensor `calculated_soc`. Wer künftig eine Stelle findet, die beide gleichsetzt,
  hat einen Fehler gefunden.
- Kommen Wechselrichter-Profile (Backlog), wird `_discharge_block_method` deren Fähigkeitsabfrage.
