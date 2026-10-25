# Inverter Charge Night with KOSTAL KORE

[🇩🇪 Deutsch](../de/kostal-kore.md) · [Documentation overview](README.md)

Checked against [`Puma7/KostalKore`](https://github.com/Puma7/KostalKore) on 2026-09-12
(commit at the time of writing) by reading its register table, its platforms and its write
paths. This integration stays inverter-agnostic: it only ever talks to plain Home Assistant
entities. This page is the translation table for one particular inverter, plus the traps that
cost real money if you get them wrong.

## Which entity goes where

| Inverter Charge Night asks for | Use this KOSTAL KORE entity | Notes |
|---|---|---|
| **Min SOC entity** (required) | `Battery min SoC` (REST, `Battery:MinSocRel`) or `Battery Min SoC (Modbus)` (register 1042) | Either works. The Modbus one accepts 5–100 %, so a user minimum below 5 % cannot be written. |
| **Grid charge switch** (required) | `Battery Manual Charge` (`Battery:ManualCharge`) | Needs the **installer code** in KOSTAL KORE, and the entity is hidden by default. |
| **Battery SOC sensor** (required) | `Battery SoC` | |
| **PV forecast** (required) | — | Comes from Solcast or another forecast integration, not from the inverter. |
| **AC charge limit entity** | `Battery Max Charge Limit (Modbus)` (register 1038) or `Battery Max Charge Power (G3)` (register 1280) | See the traps below — this is the entity the house connection limit writes to. |
| **Charge power arriving in the battery** | `Battery Power` (`devices:local:battery` → `P`, DC side) | Counts charging as negative; ICN turns it for the efficiency search. |
| **House consumption (power)** | `Home Power` (`devices:local` → `Home_P`) | With the grid import it gives the charger's AC intake for the efficiency search: grid import minus house consumption. |
| **AC input power used only for battery charging** | leave empty | `Grid2Bat_P` (*Grid to Battery Power*) is the inverter's split of the battery's DC power, not its AC intake: against `Battery Power` it read a ratio of exactly -1 all night (24/25 October 2026). The setup refuses it. |
| **Energy meter: drawn for charging** | leave empty for now | Whether `Battery Charge from Grid Total` counts the AC or the DC side is not confirmed. KOSTAL documents Modbus register 1054 as *total AC charge energy (grid to battery)* and 1046 as the DC charge energy; KOSTAL KORE does not create sensors for them yet. With both, the efficiency search could measure the energy that really flowed. |
| **Energy meter: stored in the battery** | not exported as an entity today | KOSTAL KORE reads Modbus `total_dc_charge` (1046) but does not create a sensor for it. Without it the efficiency search falls back to the power sensors. |
| **Discharge power limit entity** (legacy) | `Battery Max Discharge Limit (Modbus)` (1040) or `Battery Max Discharge Power (G3)` (1282) | Only needed to give back a value a version before 3.10.6 wrote. ICN no longer writes it. |
| **Block discharge switch** (legacy) | `Battery Disable Discharge` (`EnergyMgmt:BatCtrl:DisabelDischarge`) | Only needed to give back a state a version before 3.10.6 changed. ICN no longer switches it; the battery is held through the min SOC only. |
| **Grid import power** | `devices:local:powermeter` → **`P`** (the meter's total power), not `Home Power from Grid` | See the traps. The same module also exposes `L1_I` / `L2_I` / `L3_I`, the per-phase currents at the meter — the most direct measure of what the connection carries. |
| **Backup / island mode entity** | `Inverter:State` with **Backup mode states** set to `ESB` | Kostal reports `ESB` (Ersatzstrombetrieb) in its inverter state; `devices:local` also carries `WorkTimeESB`, the hours spent in island operation, which is a second way to confirm it. There is no dedicated "island active" entity. |
| **House consumption energy meter** | `Home Consumption Total` (`Statistic:EnergyHome:Total`) | For the bridge planner's load profile. |
| **Absolute max charge power (AC+DC)** | `Battery Max Charge Power (G3)` (1280) | |

The process data above was read from a real diagnostics download of a PLENTICORE L G3
(firmware MC 07.00.09358, `pykoplenti` API 0.2.0), so these names exist on that hardware rather
than being inferred from the code.

## Traps

**`Battery Charge Power (AC) Absolute` is signed.** Negative charges, positive *discharges*. It
is a setpoint, not a limit. Never configure it as the AC charge limit entity: a charge limit of
5000 W written there would order the battery to discharge at 5 kW.

**External control has to be enabled on the inverter.** KOSTAL KORE checks Modbus register 1080
("battery management mode") and, when it is not `External via MODBUS`, drops every write with a
log line on its own side. The service call still succeeds, so nothing downstream can tell.
Inverter Charge Night therefore reads the AC charge limit back on every verification run and
warns when the inverter holds a *higher* value than it wrote — if you see
`not accepting external control` in the log, the house connection limit is not protecting
anything until you switch the inverter to external Modbus control.

**Register 1038 has more than one owner.** Inside KOSTAL KORE the Grid Feed-In Optimizer, the
SoC Controller, Block Battery Charging and the Battery Test take exclusive ownership of it. That
lock does not extend to other integrations: if one of those features is active while a charge
window runs, the two will overwrite each other. Run only one of them at a time, or point
Inverter Charge Night at the G3 register (1280) instead.

**The G3 limit registers fall back.** 1280/1282 revert to the inverter's defaults after
`Battery Time Until Fallback (G3)` unless they are rewritten. KOSTAL KORE keeps them alive while
*it* owns the value. A limit written through those entities therefore survives; a limit written
to 1038 does not have that protection.

**`Home Power from Grid` is not the house connection.** It is the part of the *house
consumption* that comes from the grid — it does not contain the battery's own grid charging, and
on a multi-inverter or generator site it does not see everything either. The house connection
limit needs the total import at the meter: use the KSEM / power-meter sensor. When in doubt,
compare the sensor against your meter while a wallbox is charging.

**`Battery Manual Charge` can be a forced charge.** On a PLENTICORE G3 (October 2026) the
charge limit on register 1038 read the inverter's own 22.3 kW within a second of switching
`Battery:ManualCharge` on, and a limit written while it was on was ignored. Raising the min SOC
alone made the same inverter charge from the grid up to it, under the written limit - but not
when the min SOC is only 1 % above the battery: on 21/22 October a 22 % min SOC over a 21 %
battery charged nothing all night (0 W at a 1000 W limit), and from 23:31 the Kostal read its
charge limit as 51 W and ignored writes. Inverter
Charge Night writes the limit again right after switching on and rechecks it shortly after; if
the switch lifted it, grid charging goes off again for the rest of the window and the raised min
SOC does the charging. Once a window has reached its target that way, later windows leave the
switch off from the start for 30 days, so the short switch-on happens once rather than every
night. Only a charge after the switch has reported off, and with the sun down from then until
the window ends, counts. A new switch or charge limit entity, or 30 days, and it is tried again. The log says
`The charge limit did not hold with ... switched on` the first time, and
`Grid charging stays off this window` after that.

On 24/25 October 2026 KOSTAL KORE read register 1038 back as 21 460 W a second after the
switch-on, 0 W after the switch-off and 245 W and 53 W later in the night, each time after
1000 W had been written. A read of 1038 is not a reliable confirmation of what was written.

**A limit on 1038 lapses unless it is written again.** 1000 W written and confirmed at 23:01,
about 8 kW charging at 23:12, with nothing else writing the register: without a new value within
its timeout (30-60 s) the inverter goes back to its own battery management. Inverter Charge Night
writes the limit in force again every 20 s - for KostalKore's control only, whose value lapses - while the battery can draw from the grid in a window
(KOSTAL KORE and evcc write it cyclically too), and never outside a window.

**The charge limit reads back as a float.** A written 1000 W reads 997.9 to 999.9 W. KOSTAL KORE
compares its readback exactly and reports such a write as unconfirmed; Inverter Charge Night
accepts it once the next reading has moved to within 100 W of the written value.

**Installer code.** `Battery:ManualCharge`, `Battery:BackupMode:Enable` and the charge-power
controls are marked as installer-only in KOSTAL KORE. Without the code stored there, the grid
charge switch cannot be operated and this integration cannot do its job.

## Backup / island operation

A power cut with a manual transfer switch is the one situation where this integration must let
go of the inverter completely. There is no grid to charge from, and the raised min SOC of a
running window would stop the battery from supplying the house — the lights would go out with a
full battery.

Configure **Backup / island mode entity** = the inverter state sensor and **Backup mode states**
= `ESB`. The integration then ends the running window the moment the state appears, restores the
inverter's own min SOC, switches grid charging off and starts nothing new until grid operation
is back.

With a purely manual transfer box that the inverter does not report, create an
`input_boolean` in Home Assistant, select it as the backup entity and flip it when you switch
over. That works with any inverter and any transfer box.
