# Efficiency search

[🇩🇪 Deutsch](../de/efficiency-search.md) · [Documentation overview](README.md)

Cheap energy is only cheap if it arrives in the battery. A charger that loses 25 % at 2 kW turns a
14 ct/kWh window into 18.7 ct/kWh of stored energy; at 7 % loss the same window costs 15 ct/kWh.
Inverter and battery have a sweet spot somewhere between their minimum and maximum power, and
where it lies depends on the hardware. So ICN measures it rather than guessing.

## What it needs

- A writable **charge limit** in W ([Limit charging power](configuration.md#limit-charging-power)).
- The **AC power going into the charger** and the **DC power arriving in the battery**, measured
  separately (see [Where the AC side comes from](#where-the-ac-side-comes-from)).
- Optional and better: two **kWh meters** for the same two quantities. A difference of two meter
  readings is the energy that really flowed.
- Darkness: PV feeding the battery would spoil the comparison, so the search only measures while
  `sun.sun` is below the horizon.

### Where the AC side comes from

Few houses have a meter for the charger alone. ICN therefore takes it from the meter at the
connection point: **grid import minus house consumption** is what goes into the charger while the
sun is down. Set *House consumption (power)* and *Grid import (power)* on the page *Set up
efficiency search* and leave *AC input power used only for battery charging* empty.

An example from 24/25 October 2026: 1575 W from the grid, 495 W in the house, so 1080 W into the
charger. 1000 W arrived in the battery: a loss of 80 W, 7.4 %.

Only a sensor that really measures the charger's AC input may stand in for the pair. An
inverter's *grid to battery* power is usually its own split of the battery's DC power by source.
Against the battery power it reads a ratio of exactly 1, and the loss it shows is always zero. On
a Kostal with KOSTAL KORE that is *Grid to Battery Power*, which the setup refuses for this
reason.

A battery sensor that counts charging as negative, as the Kostal's *Battery Power* does, is
turned for the measurement and the log says so once. The sign is judged on the first reading of
at least a quarter of the trial's power, not on a battery that has not begun to charge yet, and
it is remembered for the next nights.

Switch it on with the page *Set up efficiency search* or the switch **Efficiency search**. It
switches itself off when it is done. The two sensors *Efficiency search* and *Most efficient
charge power* are disabled by default; enable them on the device page to follow it.

## How it measures

1. It writes a trial power and waits **two minutes** for the inverter to settle. The measurement
   then starts once the battery takes the power up - a Kostal answers a raised min SOC minutes
   late, and its register 1038 reads about 50 W while the battery still rests. After **15
   minutes** without a draw the trial counts as idle; a power the inverter never read back, and
   KOSTAL KORE never recorded, counts as a start not taken.
2. It measures for at least **five minutes and 0.3 kWh**, and until the standard error of the
   per-minute loss is **below 0.15 percentage points** (at most 20 minutes). With energy meters a
   trial always runs 20 minutes.
3. It records the loss `1 − received ÷ drawn` and moves on: first a **coarse pass** upwards, 2 kW
   apart, until the loss has risen twice in a row; then the gaps next to the best power are
   **halved** on either side.
4. When both gaps are 250 W or narrower, or the neighbours lie within the measured uncertainty, it
   stores the best power and switches itself off.

A six-hour window is usually enough. A trial only starts when the window has enough time and
charge left to finish it (`not_enough_charge_left` otherwise).

From then on, charging prefers the optimum and stops at the target, instead of spreading a less
efficient trickle over the window. The deadline may need more power, and the maximum charge power
and the house connection always limit it.

Losses also depend on how full the battery is. Each measurement is therefore filed under its
20-point band of state of charge (0–20, 20–40, …). A band takes over from the overall optimum once
three different powers have been measured in it.

## What it refuses to record

A wrong sample would steer every later night, so it discards:

- a measurement where the inverter did not follow the setpoint, for example a nearly full battery
  that tapers the charge;
- one that was too short or moved too little energy;
- a loss below 0.5 % (the two sensors measure the same thing; no charger loses less) or above
  50 % (a sensor, wiring or unit problem);
- unavailable or stale readings, an interrupted sample stream, a clock that jumped;
- anything measured while the sun is up.

It also does not keep writing in vain. A trial whose battery draws less than a quarter of its
power after five minutes ends. A second such trial ends the search for the window
(`battery_not_charging`). So do an inverter that lowers the limit itself
(`inverter_lowered_limit`), two trial starts it did not take (`charge_limit_not_taken`) and two
trials lost to missing readings (`power_telemetry_lost`). Two trials with an impossible loss end
it too (`sensors_implausible`), and a repair issue names the sensors and the ratio they read. The
next window tries again.

## Reading the result

The sensor **Efficiency search** shows the state (*off*, *waiting for charging*, *settling*,
*measuring*, *finished*) and, as attributes:

| Attribute | Meaning |
|---|---|
| `best_power_w`, `best_loss_pct` | The optimum so far. |
| `loss_by_power_pct` | Every measurement: power in W against loss in %. |
| `uncertainty_by_power_pct` | The standard error of each measurement. |
| `last_result` | What happened to the last trial, including why it was discarded. |
| `waiting_reason` | Why no trial is running right now. |

**Most efficient charge power** keeps the result. If every measurement is discarded, check the
sensors first: the AC side must be the charger's intake, best grid import minus house
consumption, and *received* the battery side.

Changing the sensors, the charge limit, the minimum or maximum charge power or the battery
capacity invalidates old measurements. The result is an
estimate under the measured conditions. Temperature and ageing can move it, and you can start the
search again at any time.
