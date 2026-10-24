# Safety

[🇩🇪 Deutsch](../de/safety.md) · [Documentation overview](README.md)

ICN writes to a device that powers your house. These are the rules it follows, and what it does
when something goes wrong.

## Principles

- **Everything it changes, it gives back.** Before the first write of a window it reads and stores
  the minimum SOC and the charge limits. At the end of the window they go back, and grid charging
  is switched off. The stored values are persisted, so a restart inside the window continues
  where it left off, and a restart after the window still cleans up.
- **No write without a reason.** A value that is already correct is only confirmed, not written
  again. A charge limit the inverter does not hold is written again at most twice per window.
  Two things are written on purpose: a minimum SOC that someone else moved during the window is
  put back, and a KOSTAL KORE charge limit, which lapses on the inverter, is kept alive every
  20 seconds while the battery can draw from the grid.
- **Outside the window it does nothing.** Only the evening rescue and the actions `charge_to` /
  `block_discharge` act outside the window, and they run as short windows with the same capture
  and restore.
- **When in doubt, charge less.** A missing state of charge stops grid charging. A missing grid
  import reading pauses charging. A missing forecast plans around 50 %, not 100 %.
- **One controller per inverter.** Do not let two integrations (or two ICN setups) control the
  same battery. ICN refuses a minimum SOC entity that another setup already uses.

## The battery in the window

A scheduled night sets only the **planned target** as the minimum SOC. Energy above it stays
available to the house: a battery that starts the night at 100 % with a target of 30 % supplies
the house down to 30 % and is only charged if it falls below. ICN does not switch on a discharge
block, does not write a discharge limit of zero and does not raise the floor to the starting
level. Either would stop the battery from serving the house at all.

A higher floor than the charge target belongs only to an **explicit reservation**: the evening
rescue or the action `block_discharge`. Even then the battery is held only through the minimum
SOC (setting *Discharge block for reservations*: Automatic). The sensor attributes
`inverter_floor_soc` and `discharge_block` show what is in force.

## Giving the settings back

At the end of the window every setting is finished on its own. Once its restore is confirmed it
is not written again. A captured value that the entity cannot take (a Kostal charge limit reading
22 063 W through a control that accepts 12 540 W at most) is given back as the entity's limit.

If a restore fails (the entity is unavailable, the inverter does not answer), ICN tries again
after 1, 2 and 4 minutes, then twice more 15 minutes apart. After that it stops writing and opens
the repair issue **"inverter settings not fully restored"**, naming the settings to check. The
next window, the action `reset_inverter` or a successful reset take over again. During a power
cut the retries wait without using up an attempt.

## House connection

The cheap window is when every large load runs at once. Two wallboxes with 22 kW and 11 kW are
already 33 kW, and a 63 A three-phase connection is about 43 kW nominal. A short peak is not the
problem. Hours of continuous load heat the contacts at the meter, the terminals and the fuses.
The battery is the only load ICN can control, so it is the one that gives way.

With a grid import sensor and a budget configured:

```
nominal  = phases × phase voltage × fuse current     3 × 230 V × 63 A = 43 470 W
budget   = nominal × continuous share                43 470 W × 80 %  = 34 776 W
other    = grid import − what the battery draws itself
allowed  = budget − safety headroom − other
```

With 33 kW of wallboxes running, the battery gets `34 776 − 500 − 33 000 = 1 276 W` instead of
the several kW the plan asks for. If the rest of the house uses the whole budget, the charge
power goes to 0 for as long as that lasts.

- *What the battery draws itself* is the measured AC charge power (at most the setpoint). Without
  that sensor, all import counts as other load, which is the safe side.
- It only ever charges **less**. An unavailable grid import sensor, one that has not reported for
  five minutes, or one without a unit of W or kW pauses charging (0 W) until it reads again.
- Between the polls, a listener on the grid import lowers the setpoint within about 30 seconds
  when the house load rises. Raising it again waits for the next poll.
- Not even the efficiency search may order more than the connection carries.
- An inverter that does not hold the written limit gets it at most twice more per window, a
  restart included. Then ICN stops writing, opens the repair issue **"the inverter does not hold
  the charge limit"** and leaves grid charging off.
- **When throttling fails, the minimum SOC comes down.** A raised minimum SOC makes some
  inverters charge from the grid at whatever limit they hold (a Kostal's own is 22 kW). If the
  connection needs a lower limit and the inverter cannot be made to hold it, the minimum SOC goes
  down to the current state of charge, rounded down, for the rest of the window. Never lower
  than your own minimum. The battery stops charging from the grid but does not discharge either.
  This is logged as an error and shown as `min_soc_released_for_connection`.

The sensor *Charge power left by the house connection* shows the allowed power, the budget, the
measured import and whether the limit is braking.

> **ICN controls only the battery.** It cannot throttle your wallboxes or heat pump. If those
> alone can overload the connection, limit them with their own load management.

## Power cut and island operation

During a power cut ICN must let go of the inverter completely. There is nothing to charge from,
and a raised minimum SOC would stop the battery from supplying the house: the lights would go
out with a full battery.

Configure **Detect island operation** with whatever your installation offers:

- a switch or binary sensor: `on` means island operation;
- a state sensor plus the states that mean island operation. A Kostal reports `ESB` in its
  inverter state;
- **a manual transfer switch the inverter does not report**: create an `input_boolean`, select it,
  and switch it when you switch over. That works with any inverter.

The moment island operation is reported, ICN ends the running window, gives back the inverter's
minimum SOC, switches grid charging off and starts nothing new until the grid is back. An island
entity that is unavailable counts as island operation, and so does an unknown state of a plain
sensor for which you declared no states: when in doubt, ICN lets go. Without such an entity, ICN
cannot know about a power cut and opens a repair issue as a reminder.

## Lost communication

ICN watches the entities it depends on. When the state of charge, the minimum SOC, the grid
switch, the charge limit, the grid meter or the island contact become unavailable or report
nonsense, ICN:

- refreshes at once instead of waiting for the next poll;
- in a running window, asks for zero charge power and grid charging off through every path that
  still works;
- never counts a failed write as done, and keeps the captured settings until the clean-up can be
  confirmed;
- shows one notification per problem (*communication interrupted*, *connection restored*) and
  counts incidents instead of failed polls.

The action `inverter_charge_night.communication_diagnostics` returns the communication state and
its history without touching the device.

> No software can guarantee that a device stops after every control path or Home Assistant
> itself has failed. The protection of the circuit, the inverter and the battery management
> system remain necessary.

## Inverters with a forced charge switch

On some inverters the "grid charge" switch is a forced charge at full power. The Kostal's
*Battery Manual Charge* is one. ICN detects it: if switching on lifts the charge limit, grid
charging goes off again for the rest of the window, and the raised minimum SOC does the charging
under the written limit. Expect one short switch-on per night and a warning in the log. Details:
[Kostal with KOSTAL KORE](kostal-kore.md).

## Disclaimer

ICN is provided under the [MIT licence](../../LICENSE), without warranty. Controlling an
inverter and a battery is at your own risk. Check what ICN writes before you let it run
unattended, and read the [house connection](#house-connection) section if a wallbox shares
the connection.
