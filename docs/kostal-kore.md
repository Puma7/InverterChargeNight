# Inverter Charge Night with KOSTAL KORE

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
| **Charge power arriving in the battery** | `Battery Power` (`devices:local:battery` → `P`, DC side) | |
| **Charge power drawn for charging** | no direct AC-side sensor | `Home Power from Grid` is the *house* consumption from the grid, not the charger's intake. Use the energy meters below instead. |
| **Energy meter: drawn for charging** | `Battery Charge from Grid Total` (kWh) | Exactly the right quantity: grid energy that went into the battery. |
| **Energy meter: stored in the battery** | not exported as an entity today | KOSTAL KORE reads Modbus `total_dc_charge` (1046) but does not create a sensor for it. Without it the efficiency search falls back to the power sensors. |
| **Discharge power limit entity** | `Battery Max Discharge Limit (Modbus)` (1040) or `Battery Max Discharge Power (G3)` (1282) | |
| **Block discharge switch** | `Battery Disable Discharge` (`EnergyMgmt:BatCtrl:DisabelDischarge`) | This is the vendor switch the discharge block prefers over every other way. Hidden by default in KOSTAL KORE. |
| **Grid import power** | a **meter** entity (KSEM / Power Meter), not `Home Power from Grid` | See the traps. |
| **Backup / island mode entity** | `Inverter State` with **Backup mode states** set to `ESB` | Kostal reports `ESB` (Ersatzstrombetrieb) in its inverter state. There is no dedicated "island active" entity. |
| **House consumption energy meter** | `Home Consumption Total` (`Statistic:EnergyHome:Total`) | For the bridge planner's load profile. |
| **Absolute max charge power (AC+DC)** | `Battery Max Charge Power (G3)` (1280) | |

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
