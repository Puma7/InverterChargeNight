# How it works

[🇩🇪 Deutsch](../de/how-it-works.md) · [Documentation overview](README.md)

Inverter Charge Night (ICN) answers one question every night: **how full does the battery have
to be when the cheap window closes?** Too empty, and the house buys expensive energy in the
morning or the evening. Too full, and tomorrow's sun has nowhere to go and is fed into the grid
for a fraction of what it cost.

![A night and the following day: the battery is charged to the target in the cheap window, carries the house until the PV takes over, and the PV fills it up during the day](../images/night-and-day.en.svg)

## One night, step by step

1. **At the start of the window** ICN reads the PV forecast for the coming solar day and works
   out the target state of charge (below). Before it changes anything on the inverter, it
   reads and stores the current minimum SOC and charge limits, so that it can give them back
   later. The stored values are persisted and survive a restart.
2. **During the window** it raises the inverter's minimum SOC to the target and switches grid
   charging on where the inverter needs that switch. With a charge limit configured, it also
   writes the constant power that reaches the target at the end of the window: in Bridge mode,
   with a house connection limit, or once the efficiency search has a result. In Bridge mode
   every poll (15 minutes by default) re-plans with fresh data, but the target never drops below
   the one set at the start of the window.
3. **When the target is reached** grid charging goes off. The raised minimum SOC stays: the
   house may use the battery down to that level, but not below it.
4. **At the end of the window** the minimum SOC and the charge limits go back to the values ICN
   found, and grid charging is switched off. A setting that already shows its original value is
   only confirmed, not written again. See [Safety](safety.md) for what happens when the inverter
   does not accept a reset.

Outside the window ICN does not control the inverter at all. The high-price rescue and the actions
`charge_to` / `block_discharge` are the exceptions, and they run as short windows of their own
with the same capture and restore ([Extras](extras.md)).

> **Restoring the floor does not drain the battery.** A 23:00–05:00 night with a target of
> 30 % and an original minimum SOC of 8 % lets the house use the battery down to 30 % during the
> night. At 05:00 the floor goes back to 8 %, and the house simply carries on using what is in the
> battery.

## Which forecast is used

The plan is made for the day on which the window **ends**. A window that ends tomorrow
(23:00–05:00, planned at 23:00) uses the forecast entity for **tomorrow**. Once it is after
midnight, or for a window that ends on the same day, that day is "today", and ICN uses the
forecast entity for **today** if one is configured. Each entity stands in for the other when it
is missing. If you charge across midnight, configure both. Solcast, Forecast.Solar and
any other sensor that reports a day's yield in kWh work. ICN reads the state, or the usual
forecast attributes, in Wh or kWh.

**Without a usable forecast** ICN does not charge to 100 %. Headroom plans a safe 50 %. Bridge
plans 50 % or its lower bound plus the buffer, whichever is higher. Both are clamped into your
minimum and maximum SOC.

## How the target is calculated

The **planner mode** decides which formula is used. You choose it on the page
*PV forecast and morning reserve*.

### Headroom (default)

Only keeps room for tomorrow's PV:

```
target = (capacity − forecast × (1 + margin)) ÷ capacity
```

35.8 kWh battery, 5 kWh forecast, 10 % margin: 35.8 − 5.5 = 30.3 kWh, so the target is
**84.6 %**. A forecast larger than the battery gives your minimum SOC, and a forecast of 0 gives
your maximum SOC. The result is always clamped into the minimum and maximum you configured.

Headroom is simple and needs nothing but the forecast. It plans once, at the start of the
window, and lets the inverter charge at its own power unless a house connection limit is
configured or the efficiency search has a result. What it ignores is the morning: from the end
of the window until the panels carry the house, the house still draws from the battery. The
safety buffer, the safety level, the modelled day, the weather and the evening reserve all
belong to the Bridge planner.

### Bridge (recommended)

Bridge looks at both sides and derives two bounds:

![The battery from 0 to 100 %: below the minimum SOC the reserve, then the bridge until the PV takes over, then the evening reserve and the safety buffer; above the upper bound the room the PV needs tomorrow](../images/target-bounds.en.svg)

- **Lower bound, what the morning needs.** The house load from the end of the window until
  the PV covers it, plus a small bridge reserve. By default "until the PV covers it" means
  sunrise plus a fixed delay (90 minutes). Below this bound the house buys energy at the day
  tariff after the window.
- **Upper bound, what the sun needs.** Tomorrow's forecast with its margin, minus what the house
  consumes during the day until sunset. Only that surplus needs room in the battery: the energy
  bridged in the morning is used up again and frees its own room.

The house load comes from a **house consumption energy meter**, learned per hour from the
last 14 days of recorder statistics. Without a meter, ICN uses the **average house load** you
enter for every hour.

- **Lower ≤ upper**: everything fits. The target is the lower bound, plus the safety buffer if
  you set one. ICN never buys more than needed.
- **Lower > upper**: the bounds conflict. With prices configured, the planner compares two
  costs. One is charging past the upper bound: the night price minus the feed-in tariff that is
  lost. The other is leaving the morning uncovered: the day price minus the night price. It takes
  the cheaper one. Without prices the morning wins, because grid energy by day costs more than
  the feed-in tariff that is lost.

The chosen bound and its inputs are attributes of the *Target SOC* sensor: `plan_reason`,
`lower_bound_soc`, `upper_bound_soc`, `bridge_kwh`, `surplus_kwh`, `buffer_soc`, `pv_crossover`
and `weather`.

### Safety buffer, safety level, modelled day, weather

Four settings on the page *Safety buffer, safety level and weather* move the target off the edge
of the bare need. They belong to the Bridge planner, and at their defaults they change nothing.

**Safety buffer (kWh).** Carried above the lower bound, but only into the room the upper bound
leaves:

```
target = min(lower + buffer, upper)
```

The buffer therefore can never make tomorrow's PV spill into the grid. It is bought **once**
and then carried: every night plans from the battery as it finds it and only tops up what the
day used. It is for a power cut on the island box, an electric car, an evening above average.
`buffer_soc` shows how much of it was applied, and `buffer_capped` shows whether the upper bound
cut it short. A conflict carries no buffer.

**Safety level of the planned consumption.** *Average* (as before), *safe* (75th percentile) or
*very safe* (90th percentile). Every hour of the learned profile is then planned like one of the
busier days of the last two weeks, and never below its average. Only the lower bound uses it.
The room for the sun is still reckoned with the average, so a high load estimate cannot pretend
there is more room than there is.

**How the morning and the day are bridged.** The *fixed delay after sunrise* (default), or
**modelled**. In modelled mode the planner runs tomorrow in five-minute steps from the end of the
window to the end of the high-price period:

- The planned load against the cautious forecast curve (forecast minus margin) shows how low the
  battery falls. That is the lower bound.
- The average load against the generous curve (forecast plus margin) shows how high the sun
  lifts it before sunset. That is the upper bound.

A sunny day takes over early and needs less. A dull December day may never carry the house, and
then the whole day is bridged. If you have set the daily PV energy meter (page *Account for very
low PV yield*), the PV curve is the share of the day's yield per clock hour, learned from the last
two weeks. That captures roof orientation, shading and season. Otherwise ICN uses a clear-sky bell
between sunrise and sunset.

**Weather (optional, for a heat pump).** A `weather.*` entity (Met.no, DWD, OpenWeatherMap…),
optionally an outdoor temperature sensor, and the heating limit (15 °C).

- ICN fits `daily kWh = a + b × max(0, limit − T)` over the days behind the load profile. It
  then scales the planned load by what that predicts for tomorrow's forecast temperature against
  the last two weeks: at most +50 %, at least −15 %.
- It only applies when ten days, three kelvin of temperature spread, a positive slope and
  R² ≥ 0.3 show that the relationship is real. A house without electric heating keeps a factor
  of 1.
- Temperatures come from the sensor's statistics, or from a diary ICN keeps of the weather
  entity's current temperature. That diary needs about ten days.
- The `weather` attribute shows the status (`off`, `learning`, `active`, `forecast_unavailable`),
  the learned kWh per kelvin and the factor.

### The evening reserve

Many tariffs also have a **high-price period**, often 18:00–21:00, in which energy costs more
than the normal day tariff. With that period configured (page *Reserve for expensive evening
hours*) and the Bridge planner, the night makes sure the house can be carried through it:

```
evening reserve   = house load during the period (+ your allowance in %)
covered by the PV = tomorrow's surplus (forecast minus margin, minus the daytime load)
bought at night   = evening reserve − covered by the PV, never below zero
```

On a summer day the sun fills the battery long before the evening, and nothing changes. On a dull
winter day the whole evening is added to the night's target, at the cheap tariff.

With a **price entity** configured, the reserve becomes a decision. A kWh put aside in the window
passes through the inverter twice, so holding it costs `window price ÷ efficiency`. If the
evening is clearly cheaper than that (more than 2 ct/kWh), the reserve is dropped for that night.
The sensor *Next high-price period* shows when the next one starts and how much was reserved for
it.

### Which target wins

1. **Snow nights** > 0: the configured maximum SOC.
2. A **target SOC override** (number entity): replaces the planner until the window ends.
3. The **planner**: the target calculated at the window start and updated during it.

A detected severe PV shortfall arms one night at the maximum SOC, like a snow night
([Extras](extras.md)).

## Where to look

- The *Target SOC* sensor and its attributes: the result and its reasons.
- The action `inverter_charge_night.plan_target_soc`: runs the Bridge planner on demand and
  returns the result without writing anything ([Entities and actions](entities-and-actions.md)).
  It ignores snow nights and the override.
- The log at INFO level: one line per plan with lower bound, upper bound, buffer and crossover.
