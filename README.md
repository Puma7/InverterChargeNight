# Inverter Charge Night

**Charge your home battery from the grid during a cheap tariff window — only as much as the next
day's solar production will not cover.**

A Home Assistant integration for households on a time-based electricity tariff: Germany's §14a
EnWG reduced grid fee, a night tariff, a dynamic tariff with cheap hours, anything where energy
between two times of day costs less than during the day. It decides *how full* the battery has to
be when the cheap window closes, charges it to exactly that level, and leaves the rest of the
capacity free for tomorrow's sun.

Two numbers decide the target:

- **How much solar is forecast for tomorrow** — every kWh the roof will deliver is a kWh the
  battery must not buy tonight, or the surplus goes to the grid for a feed-in tariff that is a
  fraction of what it cost.
- **How long the battery has to carry the house** from the end of the window until solar output
  exceeds consumption — in December that is hours after sunrise; on a clear day in October it is
  minutes.

Between those two bounds the integration picks a target state of charge, works out the charge
power that reaches it exactly at the end of the window, and hands the inverter back to you when
the window closes.

## What this is — and what it is not

**It is a scheduler and planner.** It talks to whatever entities your setup already has: a number
entity for the battery's minimum state of charge, a switch for grid charging, a sensor for the
state of charge, a PV forecast sensor. It does not speak Modbus, it has no cloud account and no
vendor protocol of its own.

**It is not an inverter integration.** You need one of those as well — and you almost certainly
already have it:

| You need | Examples |
|---|---|
| An integration that exposes your inverter to Home Assistant | [KOSTAL KORE](https://github.com/Puma7/KostalKore), the built-in `kostal_plenticore`, SMA, Fronius, Solax, a Modbus configuration of your own — anything that gives you a min SOC number entity and a grid charge switch |
| A PV forecast | [Solcast](https://github.com/BJReplay/ha-solcast-solar), [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/), or any sensor that reports tomorrow's expected yield in kWh |

Because it only uses plain Home Assistant entities, it works with **any** inverter that exposes
those two or three controls — the development and testing happened on a Kostal PLENTICORE, but
nothing in the code knows that. Inverter-specific notes live in
[docs/kostal-kore.md](docs/kostal-kore.md).

## Why §14a EnWG

Since 2024, German grid operators must offer a reduced grid fee to households with a controllable
consumer — a heat pump, a wallbox, a home battery. One of the variants ("Modul 3") charges the
reduced fee during fixed hours, typically at night. A concrete example from one operator:

| | Grid fee | Total price |
|---|---|---|
| 23:00 – 05:00 | ~1 ct/kWh | ~14 ct/kWh |
| rest of the day | ~10 ct/kWh | ~30 ct/kWh |

A 35 kWh battery filled in the cheap window carries a household through most of a winter day for
roughly a third of what the same energy costs at noon. The catch is the same one every battery
owner knows: **fill it too far and the next day's sun has nowhere to go.** That is the calculation
this integration does for you, every night, with tomorrow's forecast in hand.

The window is configurable, so none of this is specific to §14a or to Germany — any two times of
day will do.

## Features

- **Two planner modes** — `Headroom` keeps room in the battery for tomorrow's PV forecast;
  `Bridge` additionally covers the house load from the window end until solar output exceeds it.
  See [Planner Modes](#planner-modes).
- **Two operation modes** — `Night Charge` fills the battery from the grid during the window.
  `Morning discharge` does the opposite and is **experimental**: on a summer day it empties the
  battery into the 05:00–08:00 household peak, for the spread on a dynamic tariff and to take
  that load off the grid. See [Morning discharge](docs/morning-discharge.md). Switchable at
  runtime.
- **Any time window**, including one that spans midnight, and an optional date range for tariffs
  that only apply in certain months.
- **Charge power planning** — the constant power that reaches the target exactly at the end of the
  window, rather than charging at full power and stopping early.
- **House connection limit** — with a grid import sensor and your main fuse size, the charge power
  is held so the total import stays inside a continuous-load budget. Wallboxes and a battery on
  one 63 A connection is exactly the situation this exists for. See
  [House connection](#house-connection).
- **Night floor and reservations** - scheduled nights let the battery supply the house down to
  the planned min SOC. Explicit reservations can hold more energy through a raised min SOC
  only. See
  [Discharge in the Window](#discharge-in-the-window).
- **Efficiency search** — measures at which charge power the least energy is lost between the grid
  and the battery, and then keeps to it. See [Efficiency search](#efficiency-search).
- **Backup/island aware** — during a power cut the integration hands the inverter back, so the
  battery can supply the house. See [Backup and island operation](#backup-and-island-operation).
- **Everything it changes, it changes back** — minimum SOC, grid charge switch and charge limits
  are captured before the first write and restored at the end of the window. A failed reset is
  retried a bounded number of times; after that the integration stops writing and files a repair
  issue naming what is still to check.
- **Survives a restart** — the runtime state is persisted, so a restart inside a window continues
  where it left off, and a restart *after* the window still cleans up.
- **Manual overrides** — a target SOC override, a `Snow nights` counter that charges to the
  maximum while the modules are covered, and a `Skip next` switch for one-off exceptions.
- **Full UI configuration** — a guided overview with short forms for setup, reconfigure and options, English and
  German, with diagnostics and repair issues.

## Requirements

- Home Assistant **2025.2.0** or later
- An integration that exposes your inverter's **minimum SOC** (number), **grid charge switch**
  (switch) and **battery state of charge** (sensor) — see the table above
- A **PV forecast** sensor reporting tomorrow's expected yield in kWh
- For the Bridge planner: the built-in `sun` integration, and optionally a cumulative house
  consumption meter (kWh)
- For the house connection limit: a sensor for the current grid import in W

## Installation

### With HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Puma7&repository=InverterChargeNight&category=integration)

1. Install [HACS](https://hacs.xyz/docs/setup/download) if you do not have it yet.
2. **HACS → three-dot menu → Custom repositories**, add
   `https://github.com/Puma7/InverterChargeNight` with category **Integration**.
   (The button above does both steps for you.)
3. Search for **Inverter Charge Night**, download it, and restart Home Assistant.
4. **Settings → Devices & Services → Add integration → Inverter Charge Night**.

Updates then arrive through HACS like any other integration.

### Manually

1. Copy the folder `custom_components/inverter_charge_night` from this repository into your
   Home Assistant configuration directory, so that you end up with
   `<config>/custom_components/inverter_charge_night/manifest.json`.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add integration → Inverter Charge Night**.

### Removing it

**Settings → Devices & Services → Inverter Charge Night → three-dot menu → Delete.** The
integration restores everything it changed on the inverter before the entry goes away. Then
remove it from HACS (or delete the folder) and restart.

## Configuration

### Step 1: Find Your Entity IDs

Before configuring, you need to identify the following entities in Home Assistant:

1. **Minimum SOC entity**:
   - A `number` entity from your inverter integration that sets the battery's minimum state of
     charge (for example `number.kostal_plenticore_min_soc`, `number.wr_battery_min_soc`)
   - This entity controls the minimum state of charge

2. **Grid charge switch**:
   - A `switch` entity that makes the inverter charge the battery from the grid (for example
     `switch.kostal_plenticore_grid_charge`, or KOSTAL KORE's `Battery Manual Charge`)
   - This switch controls whether the inverter charges from the grid

3. **PV Forecast Entity**:
   - Look for your Solcast forecast entity (e.g., `sensor.solcast_forecast_today`)
   - This should provide forecast data in kWh

4. **Battery SOC Sensor**:
   - Look for your battery state of charge sensor (e.g., `sensor.battery_soc`)
   - This should show current battery SOC as a percentage (0-100)

To find entities:
- Go to **Settings** → **Devices & Services** → **Entities**
- Use the search/filter to find entities by name or domain

### Step 2: Configure the Integration

1. **Add the integration**:
   - Go to **Settings** → **Devices & Services**
   - Click **Add Integration** (bottom right)
   - Search for "Inverter Charge Night"
   - Click on it

2. **Complete the two basic pages** from the setup overview:
   - **Inverter and battery:** name, writable minimum-SOC number, grid-charge switch,
     measured battery SOC, tomorrow’s PV forecast and **gross** battery capacity in kWh.
   - **Charging window and state of charge:** start/end in Home Assistant’s configured
     time zone, and the lower/upper bounds for the planned charging target. Windows may
     cross midnight. For example, 35.8 kWh gross with an 8% floor leaves 32.936 kWh usable.

3. **Review and apply.** No partial config entry controls the inverter while setup is in
   progress. Optional features can be configured now or later under **Configure**.

Each confirmed page is saved as a private Home Assistant draft and returns to the overview.
Reopen a page to go back and edit it; other sections keep their values. **Save draft and close**
pauses setup; reopen the same initial setup, Configure, or Reconfigure dialog and select
**Resume saved draft**. Drafts survive a restart and are kept for 30 days. Text that has not
been submitted from a browser form cannot be saved. Home Assistant’s standard form API does
not offer an arbitrary extra Back button; the overview provides direct navigation between
confirmed pages. Only **Apply settings now** creates or changes the active configuration.

**KostalKore mapping examples** (the `kostal_lab` prefix depends on your device name):

| ICN input | KostalKore entity name | Example entity ID |
|---|---|---|
| Set minimum SOC | Battery min SoC | `number.kostal_lab_battery_min_soc` |
| Enable grid charging | Battery Manual Charge | `switch.kostal_lab_battery_manual_charge` |
| Measure battery SOC | Battery SoC | `sensor.kostal_lab_battery_soc` |
| Set battery charging power | Battery max charge limit (Modbus) | `number.kostal_lab_battery_max_charge_limit_modbus` |
| AC input for battery charging | Grid to Battery Power | `sensor.kostal_lab_grid_to_battery_power` |
| DC received by battery | Battery Power | `sensor.kostal_lab_battery_power` |

The first three bindings are suggested when there is one unambiguous KostalKore inverter.
Suggestions follow registry identity, including renamed entities. Check the selected inverter
before applying. Disabled entities and ambiguous multiple-inverter setups are not guessed.
PV forecasts come from a forecast integration; ICN’s evening outlook is not a forecast input.
**Active Power Set Point in percent is not a battery watt control. AC Power is inverter
output, not the AC input required for efficiency search.** Hardware capabilities vary by model.

**Optional features** have their own pages:

- **Charging power:** normal charging limits and a verified watt control.
- **House connection:** total grid import, permitted continuous watt budget, safety headroom
  and the power control are required together. Alternatively calculate a budget from the
  fuse rating under additional hardware settings. A direct watt budget takes precedence;
  saving the fuse-based page removes the direct budget.
- **Efficiency search:** requires the watt control, separate AC/DC power measurements and
  a permitted power range. Saving this page enables search; results are learned during
  suitable night charging. Separate energy counters are optional but must be configured as a pair.
- **Forecast, reserves and season:** select PV headroom or morning bridging, today’s forecast,
  house consumption history/average load, evening high-price reserve, seasonal dates,
  low-PV-yield detection and tariffs individually.
- **Other settings:** island-mode input, extra hardware ceilings, refresh interval and
  command delay. Extra AC/DC ceilings need both a separate control and a value; ordinary
  setups can leave them empty. Experimental morning discharge needs a force-discharge switch.

Initial setup, Configure and Reconfigure use the same overview. Existing configuration,
learned efficiency history and runtime state are retained when unrelated sections are edited.

**Different appearance after a HACS installation:** the installed release tag can be older
than `main` used by the lab. Compare the integration version with the release notes. Select
an available release containing this setup, or explicitly redownload `main` in HACS, then
restart Home Assistant and reopen the dialog. The local lab dashboard is separate from the
integration setup and is not installed by HACS.

### Step 3: Verify Installation

After configuration you should see these twelve entities, all on the device **Inverter Charge
Night**:

```
switch.inverter_charge_night_enabled                       Automation
switch.inverter_charge_night_auto_efficient_charge_finder  Efficiency search
switch.inverter_charge_night_skip_next                     Skip the next window (24 h)
select.inverter_charge_night_operation_mode                Operation mode
number.inverter_charge_night_min_soc_override              Target SOC override
number.inverter_charge_night_snow_nights                   Snow nights
binary_sensor.inverter_charge_night_active                 Window active
sensor.inverter_charge_night_calculated_soc                Target SOC
sensor.inverter_charge_night_planned_charge_power          Planned charge power
sensor.inverter_charge_night_best_charge_power             Most efficient charge power
sensor.inverter_charge_night_efficiency_search             Efficiency search
sensor.inverter_charge_night_grid_charge_headroom          Charge power left by the house connection
```

What each one is for: [Entity List](#entity-list-end-user).

## How It Works

### Planner Modes

The planner decides the target SOC for the window. Which formula it uses is set by **Planner
Mode** under optional forecast and reserve settings; both clamp the result into the configured minimum and maximum
SOC, and both fall back to a safe 50 % (instead of charging to the maximum) when no forecast is
available.

#### Headroom (default)

Only keeps room for tomorrow's PV production:

```
Target SOC = (Battery Capacity - Forecast Energy × (1 + Error Margin)) / Battery Capacity × 100
```

**Step-by-step:**
1. Apply error margin to forecast: `Forecast × (1 + Error Margin / 100)`
2. Calculate remaining capacity: `Battery Capacity - Adjusted Forecast`
3. Convert to percentage: `(Remaining Capacity / Battery Capacity) × 100`
4. Clamp to user-defined limits: `max(user_min_soc, min(calculated_soc, user_max_soc))`

**Example:**
- Battery Capacity: 35.8 kWh
- Forecast Energy: 5 kWh
- Error Margin: 10%
- Adjusted Forecast: 5 × 1.10 = 5.5 kWh
- Remaining Capacity: 35.8 - 5.5 = 30.3 kWh
- Calculation: (30.3 / 35.8) × 100 = 84.6%
- Final (if min=8%, max=100%): 84.6%

**Edge Cases:**
- If forecast exceeds battery capacity: Uses minimum SOC
- If forecast is 0: Uses maximum SOC (or user_max_soc)
- Result is always clamped between user_min_soc and user_max_soc

#### Bridge

Bridge answers a second question the headroom formula ignores: what does the house consume
*after* the cheap window, before the PV covers it? It derives two bounds, both in kWh and then
converted to SOC:

- **Lower bound (bridge)**: the house load from the window end until the PV crossover
  (sunrise from `sun.sun` plus the configured **PV Crossover Delay**), plus the **Bridge
  Reserve**. Below this the house buys energy at the day tariff after the window.
- **Upper bound (headroom)**: tomorrow's forecast with margin, minus the daytime load until
  sunset. Only that surplus needs room in the battery, because the bridge energy is consumed
  again before the crossover and frees its own room.

The hourly load profile comes from the **House Consumption Energy Meter** (learned from the last
14 days of recorder statistics, cached for 15 minutes); without a meter the **Average House
Load** is used for every hour.

- `lower <= upper`: both fit, and the lower bound wins -- never buy more than needed.
- `lower > upper`: the bounds conflict. With the three tariffs configured, the planner compares
  the cost of charging past the headroom bound (night price minus lost feed-in tariff) with the
  cost of leaving the bridge uncovered (day price minus night price) and takes the cheaper one.
  Without tariffs the bridge wins, because grid energy by day costs more than the feed-in
  tariff that is lost when PV finds no room.

  With a price entity configured, the night and day prices come from it instead: the window's
  own mean price, and the mean over the bridge from the window end to the PV crossover. They are
  taken **as a pair or not at all** -- the decision turns on their difference, and a difference
  between an entity's night and a hand-entered day would partly be a difference between two ways
  of writing a price down. When the entity does not cover both stretches, both come from the
  fixed fields. The feed-in price is always the fixed field, because no price source publishes
  one. `sensor.…_price_signal` shows in `conflict_prices_source` where the prices behind the last
  conflict decision came from -- `entity` or `static` -- and nothing when the last plan had no
  conflict to decide.

  The day price is the mean over the whole bridge. Strictly, the energy left uncovered is the
  *end* of the bridge, the last hours before the crossover, and those are often the dearest of
  the morning; pricing just that tail needs the size of the conflict, which only the planner
  knows. It is a known approximation, not a guarantee.

The chosen bound and its inputs are exposed as attributes of
`sensor.inverter_charge_night_calculated_soc` (`plan_reason`, `bridge_kwh`, `surplus_kwh`,
`lower_bound_soc`, `upper_bound_soc`, `pv_crossover`).

#### Target precedence

Snow nights beat everything, then the manual override, then the planner:

1. `snow_nights > 0` -> the configured maximum SOC
2. a manual override set on `number.inverter_charge_night_min_soc_override`
3. the SOC the planner calculated at the window start (re-planned during the window, but in
   Night Charge mode the target never drops below what was already reached)

### The evening reserve

A §14a tariff is rarely just "cheap at night". Many grid operators also define a **peak period**
in which a kilowatt-hour costs *more* than the normal day tariff — commonly 18:00 to 21:00, the
hours in which a household draws most and the sun delivers nothing. Buying there is the most
expensive energy of the day, and it is energy that could have been bought overnight for a
fraction.

With **High-price period** set, the planner works out what the house will draw between those two
times, from the same hourly load profile the Bridge planner uses, and makes sure it is in the
battery by the time the period starts. It does not simply buy all of it:

```
evening reserve   = house load over the period   (+ your allowance)
covered by the PV = tomorrow's surplus           (forecast − daytime load)
bought at night   = evening reserve − covered by the PV, never below zero
```

On a summer day whose forecast covers the house anyway, this changes nothing: the sun fills the
battery long before the evening. On a dull winter day the whole evening is added to the night's
target, at the cheap tariff. Without a usable forecast the surplus is not counted at all — a
surplus nobody can see is one nobody may plan on.

The period may cross midnight, and the reserve is bounded by your maximum SOC like every other
target. `sensor.…_next_high_price_window` shows when the next one starts, how long it lasts, how
much was reserved and how much of that this window is buying.

**With a price entity configured, the reserve becomes a decision rather than an assumption.**
A kilowatt-hour put aside in the window passes through the inverter twice, so holding it really
costs `window price ÷ efficiency`. If the evening is *clearly* cheaper than that — more than
2 ct/kWh cheaper, so a marginal difference does not move anything — there is nothing to save for
and the reserve is dropped for that night. Unknown prices hold it, which is what configuring the
period asked for. `sensor.…_calculated_soc` carries `evening_reserve_dropped` when it happens.

The same reserve bounds the other direction: in `Morning Discharge` mode the target the battery
is emptied to is raised to the reserve, so the mode cannot sell in the morning what has to be
bought back at the evening's peak tariff. Today's forecast is subtracted there as well, so a
summer morning discharges as before.

> The reserve raises the **night charge target** and floors the **morning discharge**. It does
> not, by itself, stop the battery from being emptied before the evening by the house — that is
> what the discharge block in step 3 is for.

### The evening rescue

The night plan buys for the high-price period in advance. The rescue is what happens when that
plan turns out to have been too optimistic — snow on the panels, say, with the snow nights not
set. By mid-afternoon it is already decidable, and there is still time.

`sensor.…_evening_outlook` carries the shortfall in kWh, normally 0. It needs a forecast entity
for **today**: in the afternoon the tomorrow entity is tomorrow, and the rescue would act on the
wrong day's sun. Without one the sensor stays empty and nothing is rescued. When it is not 0:

1. **The discharge is blocked** — automatically. The house then runs from the sun, and from the
   grid at the day tariff when the sun is not enough, rather than from a battery that is needed
   in three hours at the peak tariff. Nothing is spent that the same evening does not pay back.
2. **The rest is bought** — only with `switch.…_evening_rescue_charge` on, and only in the last
   two hours before the period, when the forecast hardly turns any more. Bought at noon is
   bought for a sun that might still have come.
3. **At the period's start the block is released** and the inverter goes back to the settings it
   had before, so the battery carries the evening.

The switch is off to begin with on purpose: watch the sensor for a season and see whether it
would have been right before letting it spend money.

Both stages run as an **ad-hoc window** — the same machinery as a configured window, with the
same capture of your inverter's settings, the same restore, the same retry if a write fails, and
the same standing down during a power cut. A configured window always wins over it: that one has
the tariff behind it.

> Partial cover still counts. It does not have to reach the level that carries the whole evening
> — every kilowatt-hour that comes from the battery instead of the peak tariff is worth having.

### Backup and island operation

A power cut is the one situation in which this integration must let go of the inverter
completely. There is no grid to charge from, and — far more important — the raised min SOC of a
running window would stop the battery from supplying the house: the lights would go out with a
full battery.

Configure **Backup / island mode entity** under optional island-mode settings with whatever your installation offers:

- a switch or binary sensor: `on` means island operation;
- a state sensor: add the states that mean island operation under **Backup mode states**, comma
  separated. A Kostal inverter reports `ESB` (Ersatzstrombetrieb) in its inverter state;
- **a manual transfer switch the inverter does not report**: create an `input_boolean`, select
  it here, and flip it when you switch over. Works with any inverter and any transfer box.

The moment the state appears, the integration ends the running window, restores the inverter's
own min SOC, switches grid charging off and starts nothing new until grid operation is back. A
state that is neither recognised nor declared is reported in the log once, with the remedy —
on a switch or binary sensor it counts as island operation, because a binary entity has no
third meaning.

Inverter-specific notes for Kostal are in [docs/kostal-kore.md](docs/kostal-kore.md).

### Efficiency search

Cheap energy is only cheap if it arrives in the battery. A charger that loses 25 % at 2 kW
turns a 14 ct/kWh window tariff into 18.7 ct/kWh of stored energy; at 7 % loss the same window
costs 15 ct/kWh. Inverter and battery have a sweet spot somewhere in between their minimum and
their maximum, and where it lies depends on the hardware — so the integration measures it
rather than guessing.

Switch on **Efficiency search** (its setup page, or the switch of the same name) and,
while charging from the grid, it works through the charge power range:

1. It writes a charge power and waits **two minutes** — the ramp to a new setpoint belongs to
   no power in particular.
2. It then measures for at least **five minutes and 0.3 kWh**, integrating the power sensors
   between their readings, or reading the two energy meters if you configured them.
3. It records the loss `1 − received / drawn` for that power and moves on to the next
   candidate, halving the remaining range each time (golden-section search).
4. When the range is narrower than 100 W it stores the best measured power and switches itself
   off. `sensor.…_best_charge_power` keeps the result. Subsequent charging prefers that power
   and stops at target SOC, rather than spreading a less efficient trickle across the window.
   The deadline may require more power; the configured maximum and free house connection
   capacity always constrain the final setpoint. For example, a 40 kW budget minus 22 kW and
   11 kW cars and 1 kW house leaves 6 kW, even with an 11 kW optimum.

Because it takes several measurements per window, a six-hour window is usually enough to find
the optimum; it does not need a week of nights.

**What it refuses to record**, because a wrong sample steers every later night:

- a measurement where the inverter did not follow the setpoint — a battery that is nearly full
  tapers the charge, and a sample taken at 3 kW must not be filed under 10 kW;
- a measurement that was too short or moved too little energy (this also happens when the
  battery is full before the measurement is done); after two failures the unresolved search
  waits for another charging window without excluding unmeasured powers;
- a loss below zero, which would mean the battery received more than was drawn: the two
  sensors are measuring the same side of the charger;
- a loss above 50 %, which suggests a sensor, wiring or unit problem;
- unavailable or stale power readings, an interrupted sample stream or a backwards clock;
- daylight or an unknown `sun.sun` state, because PV entering the battery would contaminate
  the AC/DC comparison. Regular charging continues under the normal planner while waiting.

The search assumes a smooth, single-basin loss curve and has 100 W setpoint resolution. Its
result is an estimate from the measured conditions, not an exact lifetime hardware constant.
Temperature, SOC and changed hardware can move it. Sensor bindings and battery capacity are
fingerprinted: changing them invalidates old measurements. Measurements from versions before
3.9.0 are discarded once because their sensor provenance cannot be established; enable the
search again to obtain a fresh result.

`sensor.…_efficiency_search` shows what is going on, and its `last_result` attribute names the
reason whenever a measurement was discarded. If every measurement is discarded, the sensors are
the place to look first: **drawn** must be the AC side of the charger and **received** the
battery side.

> The search needs the AC charge limit entity and both charge power sensors. Two kWh meters are
> optional, but they make the result exact: a difference of two meter readings is the energy
> that really flowed, with no assumption about what the power did in between.

> **Per state of charge.** Losses depend on how full the battery already is, not only on the
> power, so each measurement is filed under the 20-point band it was taken in (0–20, 20–40, …).
> A band takes over from the battery-wide optimum once it has been *searched* rather than merely
> sampled — three distinct powers — so an installation that measured before this existed keeps
> working on its old result until the bands fill in. A measurement that runs across more than two
> bands is a blend and is kept battery-wide only. `sensor.…_efficiency_search` shows what has been
> measured where, and which bands are in use.

### Automatic PV shortfall detection

In Advanced options, **Automatically allow for severe PV shortfalls** requires a full-day
forecast for today and **Actual PV energy today**, a whole-array daily energy counter
(Wh/kWh/MWh, reset daily). The morning forecast is retained before sunrise plus two hours;
after sunset it is compared with an actual reading reported within 30 minutes. At least
5 kWh expected and no more than 15% delivered arms charging to your configured maximum SOC
for the next regular night only. Missing/stale data or a partial first observation day does
not arm it. Your manual Snow nights counter remains independent.

A repair warning, persistent notification and the calculated-SOC sensor's `pv_yield`
attributes explain the decision. Low yield can mean snow, shading, a string fault or a
wrong forecast; the integration does not identify the cause. For an immediate one-night
snow override before a complete day has been observed, set **Snow nights** to 1.

### House connection

The cheap-tariff window is exactly when every big load runs at once. Two wallboxes with 22 kW
and 11 kW are already 33 kW, and a 63 A three-phase connection is only about 43 kW nominal. A
short peak is not the problem: the window lasts six hours, and that *continuous* load heats the
contacts at the meter, the terminals and the fuses. Meter terminals are the usual weak point.
The battery is the only load this integration can control, so it is the one that gives way.

With a grid import sensor and a fuse size configured, the integration keeps the total import
under a continuous budget:

```
nominal  = phases x phase voltage x fuse current     3 x 230 V x 63 A = 43 470 W
budget   = nominal x continuous share                43 470 W x 80 %  = 34 776 W
other    = grid import - the battery's own setpoint
allowed  = budget - safety margin - other
```

With 33 kW of wallboxes running, `allowed` is `34 776 - 500 - 33 000 = 1 276 W`, and the
battery is held to that instead of the several kW the planner would otherwise ask for. If the
rest of the house uses the budget entirely, the charge power goes to 0 for as long as that
lasts. Instead of the fuse size you can enter the maximum continuous power directly
(`grid_max_continuous_w`); it then takes precedence.

The limit is a protection, not an optimisation, so it behaves accordingly:

- It applies in **both planner modes**. In Headroom mode the AC charge limit is normally not
  written at all; once a house connection limit is configured, it is.
- It only ever charges **less**. If the grid import sensor is unavailable, has not reported for
  five minutes, or uses a unit the integration cannot read as watts, the charge power falls
  back to the configured minimum rather than carrying on blind.
- It never engages **outside a window**: outside the window the integration does not control
  the inverter at all.
- Not even the efficiency finder may order more than the connection carries.
- Between the regular polls a listener on the grid import sensor lowers the setpoint within
  about 30 seconds when the house load rises. It only ever lowers; raising it again waits for
  the next poll, so a load that drops for a moment does not push the battery straight back up.

`sensor.inverter_charge_night_grid_charge_headroom` shows the current `allowed` value and,
in its attributes, the budget, the measured import, the load the integration does not control
and whether the limit is currently braking.

> **The integration controls only the battery.** It cannot throttle your wallboxes, heat pump
> or anything else. If those alone can overload the connection, you need to limit them
> yourself - with their own charge management, or with one of Home Assistant's load-management
> integrations. Leave this feature off (no grid import entity) and nothing changes.

The limit stays on the inverter for the **whole window**, not only while the battery is
charging: once the min SOC floor is raised (see below) the inverter can buy power at any
moment, so the cap is only handed back at the window end. And the efficiency finder never
runs a test the connection cannot carry - a test that is already running when the wallboxes
start is abandoned rather than continued at a power it was not measuring.

### Discharge in the Window

A **scheduled night** sets only the planned target min SOC. Battery energy above that
floor remains available to the house. ICN never turns on the discharge-block switch,
sets the discharge limit to zero, or raises this floor to the initial battery SOC.

For example, a 23:00-05:00 night starts at 100% with a planned target of 30% and an
original min SOC of 8%. The inverter may supply the house down to 30%. If the battery
lasts all night above that floor, no grid charging is needed. If it reaches 30%, it
holds there. A battery starting below 30% is charged only to the planned target.
At 05:00 ICN restores the original 8% floor. **Restoring the floor does not drain the
battery to 8%**; it lets the house use the remaining energy normally.

An **explicit ad-hoc reservation**, including evening rescue, can intentionally hold
existing energy for a later period. It does so **only through the min SOC**: with
**Discharge block for reservations** on `Automatic` the floor is raised to the level being
held, on `Off` only the target min SOC is set. Since 3.10.6 no window turns a discharge
block switch on or writes a discharge limit of zero - either stops the battery from serving
the house at all, and on a Kostal each write is an external battery-control command. The
two entities stay configurable only so a value an older version captured is given back.

The target sensor attributes `inverter_floor_soc` and `discharge_block`
(`min_soc` / `off`) expose the mechanism. For scheduled nights,
`min_soc` means the planned partial floor, not a complete discharge lock. A higher
floor than the charge target belongs only to an explicit reservation. The inverter's
number step may require a rounded floor; the semantic charge target stays unchanged.

When resuming an older ICN night, a captured ICN-owned block is released once at the
window start (a previously off switch or positive discharge limit), and a saved higher
floor is replaced by the planned target. A failed release is retried at the window end,
not on every verification. ICN does not cancel
an independent user/third-party discharge lock without captured ownership.

Morning Discharge windows do not apply a discharge block.

### Operation Flow

**At Window Start:**

1. The forecast entity for the time of day is read (today's before noon, tomorrow's after)
2. The planner derives the target SOC in the configured planner mode
3. The current minimum SOC, the AC charge limit and the discharge limit are stored so they
   can be restored later, and the values are persisted so they also survive a restart
4. In a scheduled Night Charge window only the target min SOC is applied. Existing
   battery energy above it may supply the house (see "Discharge in the Window").

**During Active Window (e.g., 00:00 - 05:59):**

1. Target SOC is validated against the user limits
2. The inverter's minimum SOC is set to the floor -- the charge target, or the raised
   explicit reservation floor if that is higher (only when it differs from the current value)
3. Grid charging is switched on (only when it is off), after the configured command delay
4. The charge power needed for the remaining window time is planned, capped at what the house
   connection still carries, and published as
   `sensor.inverter_charge_night_planned_charge_power`; in Bridge mode - and in either mode once
   a house connection limit is configured - it is also written to the AC charge limit entity,
   unless the efficiency finder currently owns that entity
5. Battery SOC is watched by a state listener and by the update interval (15 minutes by default)
6. When the target is reached, grid charging is switched off (the min SOC stays set)
7. A periodic verification re-writes the min SOC if something outside the integration changed
   it, measured against the inverter floor, not the charge target
8. Nothing is written while backup/island mode is active

**In Morning discharge mode** the same window drives the battery *down* to the target instead:
the min SOC acts as a floor, grid charging is kept off, and the force discharge switch is turned
on until the target is reached. That switch is required in this mode — it is the only thing that
discharges. The mode is experimental; [docs/morning-discharge.md](docs/morning-discharge.md)
explains what it is for and when it pays.

**At Window End (e.g., 05:59):**

1. The minimum SOC is reset to the stored original (or the configured default if unknown)
2. Grid charging is switched off, as is the force discharge switch
3. The AC charge limit, the absolute charge power limit, the discharge limit and the
   discharge block switch are restored, and the raised min SOC floor is dropped
4. State flags (is_active, target_reached), the manual override and the stored originals are
   cleared, and `snow_nights` counts down by one if this was a night-charge window that
   reached its configured end time
5. Each setting is finished on its own: once its restore is confirmed it is not written again,
   and a setting that already shows its original value is only confirmed, not rewritten
6. A captured value outside what the entity accepts (a Kostal charge limit that reads 22063 W
   through a control that takes at most 12540 W) is restored as the entity's limit instead
7. A reset that failed (e.g. an unavailable entity) is retried after 1, 2 and 4 minutes and
   twice more 15 minutes apart. Then the integration stops writing and files the repair issue
   "inverter settings not fully restored"; the next window, the `reset_inverter` action or a
   successful reset takes over again

**Safety Mechanisms:**
- Automatic reset if the integration is unloaded during an active window
- State flags always reset even if the reset operation fails
- Original values stored, persisted and restored; a pending reset is picked up again after a
  restart
- Fallback to the configured default if the original value is unavailable
- A missing or unavailable battery SOC sensor stops grid charging instead of guessing
- The charge power is capped at what the house connection can carry continuously; an
  unreadable, stale or oddly-united grid import sensor lowers it instead of removing the cap
- Comprehensive error handling with logging


### The feed-in cap, and what it throws away

If your system is capped at the grid connection point — the German 60 % or 70 % EEG limit, or
anything like it — then on a good day the battery is full by noon and everything above the cap is
simply lost. `sensor.…_curtailment_outlook` puts a number on that.

It is **disabled by default**: its attributes carry an hourly series that the recorder would keep
for every user, including the ones who are not capped at all. Enable it under the device page if
the question applies to you.

Two settings turn it on, both in the advanced step:

- **Feed-in cap** — the cap itself in watts, not the percentage: `0.60 × your array in watts`, so
  `9000` for 15 kWp at 60 %.
- **Feed-in power entity** — optional, and worth configuring. The cap applies to what actually
  goes out to the grid, so this is the one measurement that can check the figure above. The sensor
  reads its hourly maxima over 14 days and shows `model_vs_envelope_pct`: today's modelled spill
  against `measured_overflow_kwh_envelope`, which is the highest each hour ever reached in those 14
  days, summed. That envelope is an upper bound no single day can beat, so a model above 100 % of it
  is claiming an overflow your inverter has never come close to. Below it says nothing on its own.

It also needs a PV forecast entity for **today**. Without one the integration would fall back to
tomorrow's forecast, which for a decision made in the morning is the wrong day — so the feature
stays off and says so rather than answering about the wrong day.

**Nothing is throttled from this.** Actually capping the morning charge to leave room for midday
is the next step and is deliberately not built yet: the model says a 60 % cap on a large array
should never bite, while owners of such systems report that it does. Throttling on a day that did
not need it costs twice — the battery is short in the evening, and the evening rescue buys it back
from the grid. So this release measures first. See `plans/014`.

## Entity List (End-User)

Every entity below belongs to the device **Inverter Charge Night**, so Home Assistant shows it
as "Inverter Charge Night <name>". The entity ids are stable; the display names are translated
(English and German ship with the integration).

| What you see | Entity | What it is for |
|---|---|---|
| Automation | `switch.…_enabled` | The main switch. Off means the integration controls nothing. |
| Efficiency search | `switch.…_auto_efficient_charge_finder` | Starts the search for the most efficient charge power. Switches itself off when it is done. |
| Skip the next window (24 h) | `switch.…_skip_next` | Skips one window, then expires by itself. |
| Evening rescue: charge from the grid | `switch.…_evening_rescue_charge` | Lets the rescue buy for the evening, not only hold what is there. Off by default. |
| Operation mode | `select.…_operation_mode` | Night Charge or Morning Discharge. |
| Target SOC override | `number.…_min_soc_override` | Overrules the planner for this window. |
| Snow nights | `number.…_snow_nights` | Charge the next N nights to the maximum. |
| Window active | `binary_sensor.…_active` | Whether a window is running right now. |
| Target SOC | `sensor.…_calculated_soc` | What the planner wants in the battery. |
| Planned charge power | `sensor.…_planned_charge_power` | What the battery is being charged with. |
| Most efficient charge power | `sensor.…_best_charge_power` | The result of the efficiency search. |
| Efficiency search | `sensor.…_efficiency_search` | What the search is doing and what it has measured. |
| Charge power left by the house connection | `sensor.…_grid_charge_headroom` | What the connection still allows the battery. |
| Evening outlook | `sensor.…_evening_outlook` | How much the battery will be short when the expensive hours start. 0 means it will make it. |
| Next high-price period | `sensor.…_next_high_price_window` | When the next peak period starts, how long it lasts, and the reserve the planner put aside for it. |
| Curtailment outlook | `sensor.…_curtailment_outlook` | How much of today's PV a permanent feed-in cap throws away because the battery was already full. Disabled by default. |

> **The three entities that all used to be called "Inverter Charge Night".** Before this
> version the operation mode select and the skip switch had no translated name, so Home
> Assistant fell back to the device name and showed three identical entries under
> Configuration. They are now called *Automation*, *Efficiency search* and *Skip the next
> window*.

- **`sensor.inverter_charge_night_calculated_soc`** - The target SOC the planner calculated
  - Unit: `%`
  - Attributes: `is_active`, `target_reached`, `current_soc`, `operation_mode`, `skip_next`,
    `snow_nights`, `inverter_floor_soc`, `discharge_block`, and in Bridge mode `plan_reason`,
    `bridge_kwh`, `surplus_kwh`, `lower_bound_soc`, `upper_bound_soc`, `pv_crossover`
  - `inverter_floor_soc` is the semantic floor for the inverter's min SOC entity. It can be higher
    than the target during an explicit reservation using a raised min SOC (see
    [Discharge in the Window](#discharge-in-the-window)); `discharge_block` names the way in use
    (`switch` / `limit` / `min_soc` / `off`)

- **`sensor.inverter_charge_night_best_charge_power`** - Best AC charge power found
  - Unit: `W`, diagnostic entity
  - The charge power with the smallest measured loss, see [Efficiency search](#efficiency-search)

- **`sensor.inverter_charge_night_efficiency_search`** - What the search is doing
  - States: `Off`, `Waiting for charging`, `Settling`, `Measuring`, `Finished`
  - Attributes: `test_power_w`, `best_power_w`, `best_loss_pct`, `loss_by_power_pct`,
    `search_range_w`, `measured_energy_kwh`, `measuring_for_min`, `last_result`,
    `measurement_source`
  - `loss_by_power_pct` is the whole measurement series: charge power in W against the measured
    loss in percent. `last_result` also says why a measurement was discarded, if it was

- **`sensor.inverter_charge_night_planned_charge_power`** - AC power planned for the rest of the window
  - Unit: `W`, diagnostic entity
  - The constant power that still reaches the target before the window ends, clamped to the
    configured min/max, to the efficiency optimum and to what the house connection can carry.
    In Bridge mode - and in either mode once a house connection limit is configured - it is
    also written to the AC charge limit entity; otherwise it is shown for information only.

- **`sensor.inverter_charge_night_grid_charge_headroom`** - What the house connection still allows
  - Unit: `W`, diagnostic entity
  - Attributes: `budget_w`, `grid_import_w`, `other_load_w`, `limited`
  - Only has a value while a window runs and a house connection limit is configured. See
    [House connection](#house-connection).

- **`binary_sensor.inverter_charge_night_active`** - Whether the window is currently running
  - `on`: inside the configured time window
  - `off`: outside the window

- **`switch.inverter_charge_night_enabled`** - Enable/disable the integration
  - When switched off: resets all inverter settings and stops controlling it

- **`switch.inverter_charge_night_auto_efficient_charge_finder`** - Efficiency search
  - Measures the charge power with the smallest loss and turns itself off when it has found it.
    See [Efficiency search](#efficiency-search)

- **`switch.inverter_charge_night_skip_next`** - Skip the next cycle
  - Skips one window for 24 hours and then expires by itself. Turning it on during a running
    window ends that window immediately.

- **`select.inverter_charge_night_operation_mode`** - Operation mode
  - `Night Charge` or `Morning Discharge`. Switching while a window runs resets the inverter
    first, then re-evaluates the window in the new mode.

- **`number.inverter_charge_night_min_soc_override`** - Manual override for the target SOC
  - Range 0 - 100, step 1, clamped into the configured min/max SOC
  - Replaces the planner's target until the window ends

- **`number.inverter_charge_night_snow_nights`** - Snow on the modules
  - Range 0 - 14, step 1
  - Charges the next N nights to the maximum SOC, ignoring both the forecast and the manual
    override, and counts down by one after each night-charge window that reaches its
    configured end time (a skipped, aborted or morning-discharge window does not use one up)

## Use Cases

- **Winter / low PV forecast**: Use higher target SOC to ensure enough overnight capacity.
- **Summer / high PV forecast**: Use lower target SOC to create more daytime storage headroom.
- **Backup/Island mode**: Disable integration when backup mode is active to avoid AC charging.
- **Efficiency tuning**: Switch on the efficiency search; it measures the charge power with the
  smallest loss, usually within a single window, and then switches itself off.
- **Snow on the modules**: Set `Snow nights` to the number of nights the panels will stay
  covered; each of those nights charges to the maximum and the counter drops by one.
- **One-off exception (EV charging, guests)**: Turn on `Skip Next` to skip the coming window;
  it expires by itself after 24 hours.
- **House load after the window**: Switch the planner to `Bridge` so the battery also carries
  the house until the PV output takes over in the morning.

## Actions

Two actions make the integration reachable from an automation or a script. Both take an
optional `config_entry_id` (a picker in the UI); with a single inverter configured it can be
left out.

### `inverter_charge_night.plan_target_soc`

Runs the planner on the current inputs and **answers with the result without writing anything**.
Useful to decide in an automation whether tonight is worth charging at all, and to see why a
target came out as it did.

```yaml
alias: Tell me tonight's target
trigger:
  - platform: time
    at: "22:30:00"
action:
  - service: inverter_charge_night.plan_target_soc
    response_variable: plan
  - service: notify.persistent_notification
    data:
      message: >-
        Tonight: {{ plan.target_soc }} % ({{ plan.reason }}),
        bridge {{ plan.bridge_kwh }} kWh, surplus {{ plan.surplus_kwh }} kWh
```

The response carries `target_soc`, `reason` (`bridge`, `headroom`, `conflict_bridge_wins`,
`conflict_headroom_wins` or `fallback`), the two bounds `lower_bound_soc` / `upper_bound_soc`,
the energies `bridge_kwh` / `surplus_kwh`, and the inputs they came from: `current_soc`,
`forecast_kwh`, `forecast_available`, `window_end`, `pv_crossover`, `sunset`.

### `inverter_charge_night.reset_inverter`

Puts the minimum SOC, the charge limits and the switches back to the values captured before the
window — the same reset the window end performs.

Called **inside a running window it ends that window** and leaves the inverter alone until the
window's end time. That is deliberate: without it the reset would not survive the second it was
written in, because the min SOC watchdog puts the window's floor straight back whenever
something else moves it. The next window runs as usual; switching the integration off and on
again takes control back immediately. Backup mode refuses the call.

```yaml
alias: Hand the inverter over to the wallbox
trigger:
  - platform: state
    entity_id: binary_sensor.wallbox_charging
    to: "on"
action:
  - service: inverter_charge_night.reset_inverter
```

### `inverter_charge_night.charge_to`

Charges the battery from the grid to a level, for a while (`duration`, two hours by default).
Everything the night window does applies — the house connection limit, the settings captured
beforehand and restored when it ends. Refused while the configured window is running: that one
has the tariff behind it. Also refused in the `Morning Discharge` mode, together with
`block_discharge` and the evening rescue: there the window machinery drives the battery down, and
a charge or a hold would do nothing while reporting success.

```yaml
alias: Top the battery up before the expensive block
trigger:
  - platform: time
    at: "16:00:00"
condition:
  - condition: numeric_state
    entity_id: sensor.inverter_charge_night_evening_outlook
    above: 0.5
action:
  - service: inverter_charge_night.charge_to
    data:
      target_soc: 60
      duration: {hours: 2}
```

### `inverter_charge_night.block_discharge` and `allow_discharge`

`block_discharge` keeps the battery from running the house for a while without buying anything —
the same thing the evening rescue does by itself. `allow_discharge` ends a hold or a charge early
and hands the inverter back; it only affects one an action or the rescue started.

Both actions report failure as an error the automation can catch: an unknown or unloaded entry,
a plan the planner could not produce, or an inverter that did not accept the reset (which is
retried by the integration regardless).

## Example Automations

**Enable nightly charging**
```yaml
alias: Enable Inverter Charge Night
trigger:
  - platform: time
    at: "00:00:00"
action:
  - service: switch.turn_on
    target:
      entity_id: switch.inverter_charge_night_enabled
```

**Disable in backup mode**
```yaml
alias: Disable Inverter Charge Night on Backup
trigger:
  - platform: state
    entity_id: binary_sensor.inverter_backup_mode
    to: "on"
action:
  - service: switch.turn_off
    target:
      entity_id: switch.inverter_charge_night_enabled
```

**Run Auto Efficient Charge Finder**
```yaml
alias: Run Auto Efficient Charge Finder
trigger:
  - platform: time
    at: "01:00:00"
action:
  - service: switch.turn_on
    target:
      entity_id: switch.inverter_charge_night_auto_efficient_charge_finder
```

## Dashboard Examples (Lovelace)

**Simple status card**
```yaml
type: entities
title: Inverter Charge Night
entities:
  - switch.inverter_charge_night_enabled
  - switch.inverter_charge_night_skip_next
  - select.inverter_charge_night_operation_mode
  - binary_sensor.inverter_charge_night_active
  - sensor.inverter_charge_night_calculated_soc
  - sensor.inverter_charge_night_planned_charge_power
  - number.inverter_charge_night_min_soc_override
  - number.inverter_charge_night_snow_nights
```

**Efficiency search card**
```yaml
type: entities
title: Efficiency search
entities:
  - switch.inverter_charge_night_auto_efficient_charge_finder
  - sensor.inverter_charge_night_efficiency_search
  - sensor.inverter_charge_night_best_charge_power
```

## Configuration Examples

### Example 1: Standard Setup

- **Battery Capacity**: 35.8 kWh
- **Start Time**: 00:00
- **End Time**: 05:59
- **Min SOC**: 8%
- **Max SOC**: 100%
- **Error Margin**: 10%
- **Default Min SOC**: 8%

### Example 2: Conservative Setup

- **Battery Capacity**: 20.0 kWh
- **Start Time**: 22:00
- **End Time**: 06:00
- **Min SOC**: 10%
- **Max SOC**: 90%
- **Error Margin**: 15%
- **Default Min SOC**: 10%

## Advanced Usage

### Manual Override

You can manually override the calculated SOC using the
`number.inverter_charge_night_min_soc_override` entity. The value is rounded to a whole percent
and clamped into the configured minimum and maximum SOC, and it replaces the planner's target
until the window ends. Inside a running window it takes effect immediately, on the control path
that matches the operation mode. The override lives in the coordinator and is persisted, so it
also survives a restart inside the window -- and `Snow nights` still takes precedence over it.

### Disabling the Integration

Use the `switch.inverter_charge_night_enabled` entity to temporarily disable the integration. When disabled:
- Everything the integration wrote is reset to the value it found
- Integration stops controlling the inverter
- State flags are reset
- Integration remains configured but inactive

### Monitoring

Monitor the integration status using:
- **`sensor.inverter_charge_night_calculated_soc`** - Current calculated target SOC
  - Check attributes for `is_active`, `target_reached`, `current_soc`, `operation_mode`,
    `skip_next`, `snow_nights` and, in Bridge mode, the planner's bounds and reason
- **`sensor.inverter_charge_night_planned_charge_power`** - The AC power planned for the rest
  of the window
- **`binary_sensor.inverter_charge_night_active`** - Whether currently in active window
- **Diagnostics** - **Download diagnostics** on the entry dumps the configuration (entity ids
  redacted) together with the coordinator's runtime state
- **Home Assistant Logs** - Detailed operation logs at INFO/DEBUG level

### Update Interval

The integration updates every **15 minutes** (900 seconds) by default. This is defined in `const.py` as `DEFAULT_UPDATE_INTERVAL`. During each update:
- Forecast data is read
- SOC is recalculated
- The inverter entities are driven when needed
- Battery SOC is checked for target reached condition

## Troubleshooting

**Integration does not start or stops immediately**
- Check that all required entities exist and are available. If the min SOC entity, the grid
  charge switch or the battery SOC sensor is unknown at startup, setup is retried and a repair
  issue appears under **Settings → System → Repairs** naming the missing entity.
- Verify the time window and optional date range settings.

**Settings need to be changed**
- **Settings → Devices & Services → Inverter Charge Night → Configure** opens the same guided
  overview as the initial setup and saves into the config entry.
- The three-dot menu of the entry offers **Reconfigure** for the same fields. Both ways accept a
  new min SOC entity, so a replaced inverter or a renamed entity can be pointed at without losing
  the entry -- only an entity that another entry already drives is refused, because two
  controllers would push the same inverter towards opposite targets.

**The integration is switched on but seems to do nothing**
- It only controls the inverter *inside the time window*. Outside it,
  `binary_sensor.…_active` is `off` and nothing is written to the inverter — that is the normal
  state for most of the day.
- **Active start/end date** (step 4) is empty by default, which means the window runs all year.
  With dates set and **Repeat the date range every year** on (the default), only day and month
  count and the season comes back every year, including one that crosses the new year. With it
  off the dates are absolute, and once the end date has passed the integration stops for good —
  which it now reports in the repairs page instead of going quiet.
- `sensor.…_calculated_soc` shows the target of the *running* window and its `is_active`
  attribute says whether one is running at all.

**Battery SOC entity is unavailable**
- The integration will skip grid charging for safety until the SOC entity is available again.

**The efficiency search finds nothing**
- The AC charge limit entity and both charge power sensors have to be configured.
- Look at `sensor.…_efficiency_search`: its `last_result` attribute says why the last
  measurement was discarded. The usual causes are a battery that is full before a measurement
  completes, and two sensors that measure the same side of the charger (the loss then comes out
  at or below zero). See [Efficiency search](#efficiency-search).

**Download diagnostics**
- Go to **Settings → Devices & Services → Inverter Charge Night → Download diagnostics**.

### Integration Not Appearing

- **Check file structure**: Ensure all files are in `custom_components/inverter_charge_night/`
- **Check manifest.json**: Verify it's valid JSON
- **Restart Home Assistant**: A restart is required after installation
- **Check logs**: Look for errors in **Settings** → **System** → **Logs**

### Entities Not Found

- **Verify entity IDs**: Use **Settings** → **Devices & Services** → **Entities** to find correct entity IDs
- **Check entity domains**: 
  - The minimum SOC has to be a `number` entity
  - Grid charge should be a `switch` entity
  - Battery SOC should be a `sensor` entity

### Calculation Not Working

- **Check forecast entity**: Verify the Solcast entity is providing data in kWh
- **Check battery capacity**: Ensure it's entered correctly in kWh (not Wh)
- **Check logs**: Look for calculation errors in the logs

### Charging Not Starting

- **Check time window**: Verify current time is within the configured window
- **Check switch state**: Ensure the integration switch is enabled
- **Check the inverter entities**: they have to exist and must not be `unavailable`
- **Check logs**: Look for control errors in the logs

### SOC Not Resetting

- **Check end time**: Verify the end time is configured correctly
- **Check logs**: Look for reset errors at end time
- **Manual reset**: You can set the inverter's minimum SOC by hand at any time

## Code quality

Home Assistant's [integration quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
grades integrations that are part of Home Assistant Core. A custom integration installed through
HACS is classified as **Custom** and cannot hold a Bronze/Silver/Gold/Platinum tier — that grade
is awarded by the Home Assistant core team when an integration is accepted into Core.

The rules are still a useful yardstick, so this integration is measured against all 54 of them in
`custom_components/inverter_charge_night/quality_scale.yaml`, with an honest status per rule.
What it currently meets:

| | |
|---|---|
| Setup | UI config flow, reconfigure flow, options flow, one entry per inverter, startup entity check with `ConfigEntryNotReady` and a repair issue |
| Code | `runtime_data` instead of `hass.data`, `PARALLEL_UPDATES` on every platform, fully async, no third-party dependencies |
| Typing | `mypy --strict` and `pyright` strict over the whole package, no exclusions |
| Entities | unique ids, `has_entity_name`, entity categories, translated names and states, icon translations |
| Tests | 96 % coverage over the whole package enforced in CI, 100 % on the config flow, plus an end-to-end test against a real Home Assistant core |
| Docs | this file, in English, with a German UI translation shipped in the integration |

Measured against all 54 rules: 39 are implemented and 15 do not apply to an integration that
talks to other integrations' entities rather than to a device or a cloud service (no polling
protocol, no discovery, no authentication). Nothing is left open. The file names the reason for
each exemption.

| Tier | Implemented | Not applicable |
|---|---|---|
| 🥉 Bronze | 14 | 4 |
| 🥈 Silver | 8 | 2 |
| 🥇 Gold | 16 | 7 |
| 🏆 Platinum | 1 | 2 |

Platinum has three rules. `strict-typing` is the one that applies here, and it passes over the
whole package with both checkers; the other two are about an external dependency and a shared
HTTP session, neither of which this integration has. That still does not make it a Platinum
integration: Home Assistant reports `custom` for every integration it did not ship itself
(`homeassistant/loader.py`, `Integration.quality_scale`), so the tiers are for Core
integrations only. The rules are a useful yardstick either way.

## Technical Details

### Architecture

The integration uses Home Assistant's standard patterns:

- **Coordinator Pattern**: `DataUpdateCoordinator` manages state and updates, stored in
  `entry.runtime_data`
- **Platform Entities**: sensor, binary_sensor, switch, select and number, all built on the
  shared base class in `entity.py` so they share one device and the unique-id scheme
- **Pure Planner**: `planner.py` holds the Bridge arithmetic as pure functions with no Home
  Assistant imports; every state read happens in the coordinator
- **Config Flow**: Four-step wizard with entity selectors, shared by the setup, reconfigure and
  options flows
- **Time-based Triggers**: `async_track_time_change` for window start/end
- **Icon Translations**: `icons.json` is the single source for entity icons

### File Structure

```
custom_components/inverter_charge_night/
├── __init__.py              # Integration setup, update and unload
├── coordinator.py           # The control loop: window, planner, writes, verification
├── manifest.json            # Integration metadata
├── config_flow.py           # Four-step setup, reconfigure and options flows
├── const.py                 # Constants and configuration keys
├── calculation.py           # Headroom SOC formula and forecast helpers
├── planner.py               # Bridge planner (pure functions) and charge power
├── util.py                  # Small shared helpers (time parsing)
├── entity.py                # Shared entity base class (device info, unique id)
├── diagnostics.py           # Diagnostics dump with redacted entity ids
├── sensor.py                # Calculated SOC, best and planned charge power
├── switch.py                # Enable, efficiency finder and skip next switches
├── select.py                # Operation mode selector
├── number.py                # Min SOC override and snow nights
├── binary_sensor.py         # Active window binary sensor
├── icons.json               # Icon translations for every entity
├── quality_scale.yaml       # Quality scale status per rule
├── strings.json             # UI strings and translations
└── translations/en.json     # Byte-identical copy of strings.json
```

### Configuration Keys

All configuration options are stored in `entry.data`; `entry.options` only holds the
efficiency history (`auto_efficiency_data`) and the persisted runtime state (`runtime_state`).
The full list with labels and help texts lives in `strings.json`.

Step 1 -- entities:
- `operation_mode` - `night_charge` or `morning_discharge` (experimental, see
  [docs/morning-discharge.md](docs/morning-discharge.md))
- `min_soc_entity` - the minimum SOC number entity (also the entry's unique id)
- `grid_charge_switch` - the grid charge switch entity

  Both carried a `kostal_` prefix until 3.0.2, from the first version of this integration.
  Nothing in the code was ever Kostal-specific; entries configured earlier are migrated on
  startup and keep working.
- `pv_forecast_entity` - PV forecast entity ID for the next day (Solcast)
- `pv_forecast_today_entity` - optional forecast entity ID for today
- `battery_soc_entity` - Battery SOC sensor entity ID
- `battery_capacity` - Battery capacity in kWh (float)

Step 2 -- time and SOC:
- `start_time` / `end_time` - Window bounds (HH:MM; may span midnight)
- `user_min_soc` / `user_max_soc` - Hard SOC bounds (0-100, max must be > min)
- `default_min_soc` - Min SOC to restore at end time (0-100)
- `forecast_error_margin` - Forecast error margin percentage (0-100)
- `planner_mode` - `headroom` (default) or `bridge`

Step 3 -- charge power:
- `min_charge_power_w` / `max_charge_power_w` - Charge power band in W
- `absolute_max_charge_power_w` + `absolute_max_charge_power_entity` - Optional AC+DC limit
  (both or neither)
- `charge_power_entity` - Optional number entity for the AC charge limit
- `charge_power_sent_entity` / `charge_power_received_entity` - Optional power sensors for the
  efficiency finder
- `auto_efficient_charge` - Efficiency finder enabled (also written at runtime by the switch)
- `force_discharge_switch` - Optional switch that forces discharge to the grid
- `discharge_limit_entity` - Optional; only used to give back a zero limit an older version
  set. No window writes it any more
- `grid_import_entity` - Optional sensor with the current grid import in W or kW; without it
  the charge power is not limited against the house connection
- `main_fuse_a` - Main fuse of the house connection, per phase
- `grid_phases` - 1 or 3
- `grid_voltage_v` - Phase voltage (230 V in Germany)
- `grid_continuous_pct` - Share of the nominal power allowed as continuous load
- `grid_max_continuous_w` - The budget in W directly; takes precedence over the fuse size
- `grid_headroom_w` - Safety margin kept below the budget
- `discharge_block_switch` - Optional; only used to give back a state an older version changed
- `discharge_block_mode` - For explicit reservations: `auto` (raised min SOC) or `off`;
  scheduled nights always use only their target floor

Step 4 -- advanced:
- `update_interval` - Coordinator refresh interval in seconds (default 900)
- `command_delay` - Delay between min SOC and grid charge commands in seconds
- `active_start_date` / `active_end_date` - Optional seasonal restriction (YYYY-MM-DD)

- `active_range_yearly` - Repeat the range every year (default `true`)

  > The dates are entered with the calendar picker, so they always carry a year. With
  > `active_range_yearly` on, that year is ignored: only day and month count and the season
  > returns every year. A range whose start falls after its end (`2026-11-01` to `2027-03-31`)
  > crosses the new year and is read as a season either way — absolutely it could not contain a
  > single day, which is why it used to be dropped altogether and the window then ran all year.
  > Entries written before 3.2.0 keep the absolute meaning if they have a range set; the setting
  > is theirs to switch on.
- `backup_mode_entity` - Optional backup/island mode entity
- `house_load_entity` - Optional cumulative house consumption meter (kWh)
- `avg_house_load_kw` - Fallback average house load in kW
- `pv_crossover_delay_min` - Minutes after sunrise until PV exceeds the load
- `bridge_reserve_kwh` - Safety reserve added to the bridge energy
- `charge_efficiency` - Grid-to-battery efficiency used for the charge power plan
- `night_price_ct` / `day_price_ct` / `feed_in_price_ct` - Optional tariffs, all three or none

### Default Values

Defined in `const.py`:
- `DEFAULT_MIN_SOC = 8.0`
- `DEFAULT_MAX_SOC = 100.0`
- `DEFAULT_START_TIME = "00:00"`
- `DEFAULT_END_TIME = "05:59"`
- `DEFAULT_FORECAST_ERROR_MARGIN = 10.0`
- `DEFAULT_UPDATE_INTERVAL = 900` (15 minutes)
- `DEFAULT_SAFE_FALLBACK_SOC = 50.0` (used when no forecast is available)
- `DEFAULT_PLANNER_MODE = "headroom"`
- `DEFAULT_AVG_HOUSE_LOAD_KW = 0.5`
- `DEFAULT_PV_CROSSOVER_DELAY_MIN = 90`
- `DEFAULT_BRIDGE_RESERVE_KWH = 0.5`
- `DEFAULT_CHARGE_EFFICIENCY = 0.90`
- `DEFAULT_GRID_PHASES = 3`
- `DEFAULT_GRID_VOLTAGE_V = 230`
- `DEFAULT_GRID_CONTINUOUS_PCT = 80`
- `DEFAULT_GRID_HEADROOM_W = 500`
- `DEFAULT_DISCHARGE_BLOCK_MODE = "auto"`

### Safety Features

The integration includes multiple safety mechanisms:

1. **State Persistence**: Original min SOC stored and restored
2. **Error Recovery**: Graceful handling of entity unavailability
3. **Validation**: Target SOC validated before applying
4. **Rate Limiting**: Prevents excessive service calls
5. **Unload Safety**: Automatic reset if integration unloaded during active window
6. **Fallback Mechanisms**: Uses configured defaults if original values unavailable
7. **House Connection Limit**: Charge power capped at what the connection carries for hours
8. **Comprehensive Logging**: All operations logged at appropriate levels

### Forecast Data Parsing

The integration supports multiple Solcast data formats:
- Direct state value (kWh or Wh - auto-detected)
- `forecast` attribute (array of forecast objects)
- `today_forecast` attribute
- `forecast_today` attribute

If forecast data is unavailable, integration uses 0 kWh (resulting in maximum SOC).

### Error Handling

All critical operations include error handling:
- Service calls wrapped in try-except blocks
- Entity state checks before operations
- Graceful degradation when entities unavailable
- State flags reset even if operations fail
- Comprehensive error logging with full exception info

### Developer Checks (CI)

`.github/workflows/ci.yml` runs, on the oldest supported and on the newest Home Assistant
release, plus `hassfest` and the HACS action:
- `pytest` (with the coverage ratchet from `.coveragerc`)
- `mypy custom_components/inverter_charge_night/`
- `pyright`

## Changelog

See `CHANGELOG.md` for the release history; the current version is the `version` field of
`custom_components/inverter_charge_night/manifest.json`.

## Support

For issues, questions, or contributions:

- **GitHub Issues**: [Create an issue](https://github.com/Puma7/InverterChargeNight/issues)
- **Home Assistant Community**: [Forum Discussion](https://community.home-assistant.io/)

## Contributing

For a full local Home Assistant instance with a simulated battery, PV forecasts,
house/EV loads and an optional Kostal REST/Modbus emulator, see
[the Home Assistant laboratory](lab/README.md). It includes a Compose setup,
an experimental Windows development runner and acceptance checks using real
Home Assistant config flows, entities and services.

Issues and pull requests are welcome. The repository runs its checks in CI and they all have to
pass:

```bash
pip install -r requirements-dev.txt
pytest                                        # full suite, 96 % coverage gate
mypy custom_components/inverter_charge_night  # strict
pyright                                       # strict
python scripts/smoke_real_ha.py               # end-to-end against a real HA core
```

`AGENTS.md` describes the conventions this codebase follows, and `plans/README.md` records what
was found, decided and rejected along the way — including the bugs that shaped the safety rules,
which is worth reading before changing anything that writes to an inverter.

## License

[MIT](LICENSE) — do what you like with it, without warranty. Controlling an inverter and a
battery is done at your own risk: check what the integration writes before you let it run
unattended, and read [Electrical safety](#house-connection) first if you have a wallbox on the
same connection.

