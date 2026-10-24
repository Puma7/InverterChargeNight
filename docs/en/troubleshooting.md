# Troubleshooting and FAQ

[🇩🇪 Deutsch](../de/troubleshooting.md) · [Documentation overview](README.md)

## First checks

| Symptom | Where to look |
|---|---|
| It does nothing | Outside the window that is normal: *Window active* is `off`, and ICN writes nothing. Check the *Automation* switch, the window times, *Skip the next window* and the active season. |
| It does not start after a restart | A required entity (minimum SOC, grid switch, state of charge) does not exist. ICN retries the setup and opens a repair issue naming the entity (**Settings → System → Repairs**). An entity that exists but is unavailable does not block the setup; ICN then waits with charging. |
| The target looks too low or too high | The attributes of *Target SOC*: `plan_reason`, both bounds, `bridge_kwh`, `surplus_kwh`, `buffer_soc`. Or call the action `plan_target_soc`. |
| The target is exactly 50 % | No usable forecast: the fallback. Check the forecast entity and its unit. |
| The charge power is never written | In Headroom mode the planned power is only shown, unless a house connection limit or an efficiency result exists. Attribute `applied` of *Planned charge power*. |
| The battery does not charge | The state of charge sensor must be available, or grid charging is stopped for safety. Check the house connection sensor (`limited`), the island entity and the log. |
| The minimum SOC did not go back | Repairs: *inverter settings not fully restored*. Check the inverter, then call `reset_inverter` or wait for the next window. |
| The efficiency search finds nothing | `last_result` and `waiting_reason` of the *Efficiency search* sensor ([details](efficiency-search.md#reading-the-result)). |
| It stopped for good after a date | The active season was absolute and has ended (Repairs: *the active date range ended*). Switch on *Repeat the date range every year*. |

## Repair issues

| Issue | Meaning |
|---|---|
| Required entity is missing | An entity from the setup does not exist. Fix it with **Reconfigure** or restore it in its own integration. |
| The battery cannot supply the house during a power cut | ICN raises the minimum SOC, but no island detection is configured. Configure [island detection](safety.md#power-cut-and-island-operation). |
| The active date range ended | See above. |
| Inverter settings not fully restored | The restore failed several times, and ICN has stopped writing. |
| The inverter does not hold the charge limit | The inverter fell back to its own limit several times. Check that it accepts external control (Kostal: *External via MODBUS*). |
| Severe PV yield shortfall | Very little PV today; the next night charges to the maximum. |

*Communication interrupted* and *connection restored* are notifications, not repair issues: a
required entity was lost or came back ([details](safety.md#lost-communication)).

## Collecting evidence for one night

A diagnostics download is a snapshot. It does not replace the history or a log of the whole night.

**Before the window**

1. **Settings → Devices & services → Inverter Charge Night → ⋮ → Enable debug logging.** Do the
   same for your inverter integration. Logging starts now; earlier messages cannot be recovered.
2. **Download diagnostics** for both integrations and note the version, the time zone, the window
   and what you expected.
3. Make sure the history records the state of charge, the minimum SOC, the grid switch, the charge
   limit, the AC and DC charge power and the ICN sensors.

**After the window**, before any update, restart or reload:

1. Download the diagnostics of both integrations again (KOSTAL KORE: also run
   `kostal_kore.export_debug_bundle`).
2. **Disable debug logging**. Home Assistant then offers the log file for download.
3. Keep the history from the window start until after the reset.

The diagnostics redact entity names. Full logs can contain private details, so check them before
you attach them to a public issue.

### What the diagnostics contain

- `charge_control`: the plan, the last power command, the charge limit as read back, the AC and
  DC power, `min_soc_feedback_pct`, `grid_charge_feedback`, `original_min_soc_pct` and
  `reset_pending`. A value of `null` means "not readable", not 0.
- `efficiency_search`: state, progress and `waiting_reason`.
- `weather`: status, slope and factor of the weather model.
- the communication state and its incident history.

## FAQ

**Why 44 % and not 50 %?** The Bridge planner buys what the morning needs, not more. If you want
a reserve on top, set a **safety buffer**. It only fills room the sun leaves free, so it can never
cause feed-in ([how](how-it-works.md#safety-buffer-safety-level-modelled-day-weather)).

**Does the buffer buy energy every night?** No. Each night plans from the battery as it finds it.
A buffer that was not used during the day is still in the battery and is not bought again.

**Does it work in summer?** It does, but in summer the sun usually fills the battery anyway, and
the target falls to your minimum. Many users let it run only from October to March (the active
season).

**Which inverters are supported?** Any whose integration offers a writable minimum SOC, a grid
charge switch and a state of charge sensor. It is developed on a Kostal Plenticore G3 with
[KOSTAL KORE](kostal-kore.md).

**Can I use it together with evcc?** Yes, if evcc controls the wallboxes and ICN the battery. Do
not let both control the battery.

**What happens during a power cut?** With island detection configured, ICN lets go of the
inverter at once ([details](safety.md#power-cut-and-island-operation)).

**Where do I report a problem?** In the
[issue tracker](https://github.com/Puma7/InverterChargeNight/issues), with the diagnostics and,
if possible, a debug log of the night.
