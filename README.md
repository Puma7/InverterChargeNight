# Inverter Charge Night - Home Assistant Custom Integration

A Home Assistant custom integration that intelligently calculates and sets the optimal battery state of charge (SOC) for overnight grid charging based on PV forecast data. The integration automatically controls your Kostal inverter during a configurable time window to ensure you have enough battery capacity to store the next day's solar production.

## Latest Release

- Current version: the `version` field of `custom_components/inverter_charge_night/manifest.json`
- Changelog: `CHANGELOG.md`

## Features

- **Two planner modes**: `Headroom` keeps room in the battery for tomorrow's PV forecast; `Bridge` additionally covers the house load from the window end until the PV output exceeds it. See [Planner Modes](#planner-modes).
- **Two operation modes**: `Night Charge` charges the battery from the grid during the window; `Morning Discharge` empties it towards the grid before sunrise. Switchable at runtime with `select.inverter_charge_night_operation_mode`.
- **Time-Based Control**: Automatically activates during a configurable time window (e.g., midnight to 6 AM); the window may span midnight.
- **Kostal Integration**: Directly controls Kostal inverter min SOC and grid charging switch.
- **Forecast-Based**: Uses Solcast PV forecast data, picking today's or tomorrow's forecast entity depending on the time of day.
- **Smart Charging**: Stops charging when the target SOC is reached, and plans the AC charge power needed for the rest of the window (`sensor.inverter_charge_night_planned_charge_power`).
- **Discharge Block**: Keeps the battery from running the house during the window, so stored PV is still there when energy is expensive. Uses the inverter's discharge lock switch, else the discharge power limit, else - on an inverter that offers neither - the min SOC, which every battery honours. See [Discharge in the Window](#discharge-in-the-window).
- **Automatic Reset**: Restores the original min SOC, the grid charge switch, the AC charge limit, the discharge limit and the discharge block switch at the end of the window; a failed reset is retried after 1, 2 and 4 minutes, then every 15 minutes, until it works.
- **Survives a restart**: The runtime state (active window, override, snow nights, captured original values) is persisted, so a restart inside a window continues where it left off.
- **Manual overrides**: A target SOC override, a `Snow nights` counter that charges the next N nights to the maximum, and a `Skip Next` switch that skips one cycle for 24 hours.
- **Efficiency finder**: Searches for the most efficient AC charge limit and disables itself once it has an answer.
- **Full UI configuration**: Four-step wizard for setup, reconfigure and options, plus diagnostics and a repair issue when a required entity is missing.

## Requirements

- Home Assistant 2025.2.0 or later (the floor declared in `hacs.json`; CI tests that floor and the newest release)
- Kostal inverter integration installed and configured
- Solcast integration installed and configured (or another PV forecast source)
- Battery SOC sensor available in Home Assistant
- For the Bridge planner: the built-in `sun` integration, and optionally a cumulative house consumption meter (kWh) with recorder statistics

## Quality Scale (Gold)

The declared status per rule lives in `custom_components/inverter_charge_night/quality_scale.yaml`;
the Bronze and Silver rules are met and the Gold rules are met except
`exception_translations`. Evidence:

- UI setup, reconfigure and options flows via `config_flow` (`unique_config_entry`, `reconfiguration_flow`)
- `runtime_data` instead of `hass.data`, and `PARALLEL_UPDATES` on every platform
- Startup entity check that raises `ConfigEntryNotReady` and files a repair issue (`test_before_setup`, `repair_issues`)
- Icon translations in `icons.json` instead of hardcoded icons
- Diagnostics with every entity id redacted (`diagnostics.py`)
- Strict typing: `mypy --strict` and `pyright` in strict mode over the whole package
- Tests covering setup/unload, the control loop, the planner and the flows (`tests/`)

## Troubleshooting

**Integration does not start or stops immediately**
- Check that all required entities exist and are available. If the min SOC entity, the grid
  charge switch or the battery SOC sensor is unknown at startup, setup is retried and a repair
  issue appears under **Settings → System → Repairs** naming the missing entity.
- Verify the time window and optional date range settings.

**Settings need to be changed**
- **Settings → Devices & Services → Inverter Charge Night → Configure** opens the same four-step
  wizard as the initial setup and saves into the config entry.
- The three-dot menu of the entry offers **Reconfigure** for the same fields. Both ways accept a
  new min SOC entity, so a replaced inverter or a renamed entity can be pointed at without losing
  the entry -- only an entity that another entry already drives is refused, because two
  controllers would push the same inverter towards opposite targets.

**Battery SOC entity is unavailable**
- The integration will skip grid charging for safety until the SOC entity is available again.

**Auto Efficient Charge Finder does not run**
- Ensure the AC limit entity and both power sensors are configured.
- Each test requires at least 30 minutes of continuous charging to be recorded.

**Download diagnostics**
- Go to **Settings → Devices & Services → Inverter Charge Night → Download diagnostics**.

## Installation

### Method 1: Manual Installation (Recommended)

1. **Navigate to your Home Assistant configuration directory**
   - If using Home Assistant OS/Supervised: `/config/custom_components/`
   - If using Home Assistant Core: `~/.homeassistant/custom_components/`

2. **Create the integration directory**
   ```bash
   mkdir -p custom_components/inverter_charge_night
   ```

3. **Copy all files** from this repository into the `custom_components/inverter_charge_night/` directory:
   ```
   custom_components/inverter_charge_night/
   ├── __init__.py
   ├── manifest.json
   ├── config_flow.py
   ├── const.py
   ├── calculation.py
   ├── planner.py
   ├── util.py
   ├── entity.py
   ├── diagnostics.py
   ├── sensor.py
   ├── switch.py
   ├── select.py
   ├── number.py
   ├── binary_sensor.py
   ├── icons.json
   ├── icon.svg
   ├── quality_scale.yaml
   ├── strings.json
   └── translations/en.json
   ```

4. **Restart Home Assistant**
   - Go to **Settings** → **System** → **Hardware**
   - Click the three dots menu (⋮) → **Restart Home Assistant**

### Method 2: Using HACS (Home Assistant Community Store)

1. **Install HACS** if you haven't already (see [HACS documentation](https://hacs.xyz/docs/setup/download))

2. **Add this repository to HACS**:
   - Go to **HACS** → **Integrations**
   - Click the three dots menu (⋮) → **Custom repositories**
   - Add repository URL: `https://github.com/Puma7/InverterChargeNight`
   - Category: **Integration**
   - Click **Add**

3. **Install the integration**:
   - Search for "Inverter Charge Night" in HACS
   - Click **Download**
   - Restart Home Assistant

## Configuration

### Step 1: Find Your Entity IDs

Before configuring, you need to identify the following entities in Home Assistant:

1. **Kostal Min SOC Number Entity**: 
   - Look for a `number` entity from your Kostal integration (e.g., `number.kostal_plenticore_min_soc`)
   - This entity controls the minimum state of charge

2. **Kostal Grid Charge Switch**:
   - Look for a `switch` entity that enables/disables grid charging (e.g., `switch.kostal_plenticore_grid_charge`)
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

2. **Walk through the four wizard steps**. The same four steps are used for the initial setup,
   for **Reconfigure** and for **Configure** (options), so every setting can be changed later.

   **Step 1 -- Entities**
   - **Name**: Name for this integration instance
   - **Operation Mode**: `Night Charge` (charge from the grid overnight) or `Morning Discharge`
     (discharge before sunrise to make room for solar)
   - **Kostal Min SOC Number Entity**: The number entity that controls min SOC. It also identifies
     this instance, so each inverter can only be configured once.
   - **Kostal Grid Charge Switch**: The switch that enables/disables grid charging
   - **PV Forecast Entity (tomorrow)**: Forecast for the next day, used when the window runs
     before midnight
   - **PV Forecast Today Entity** (optional): Today's forecast, used when the window runs after
     midnight; falls back to the entity above when unset
   - **Battery SOC Sensor**: The sensor showing the current SOC in percent
   - **Battery Capacity**: Total capacity in kWh (e.g., `35.8`)

   **Step 2 -- Time & SOC**
   - **Start Time** / **End Time**: The window; it may span midnight (e.g., `22:00` to `05:59`)
   - **Minimum SOC** / **Maximum SOC**: Hard bounds; every target is clamped into this range
   - **Default Min SOC to restore at end time**: Used when the original value could not be read
     (typically `8.0` for Kostal)
   - **Forecast Error Margin**: Safety buffer added to the forecast before the calculation
   - **Planner Mode**: `Headroom` (default) or `Bridge`, see [Planner Modes](#planner-modes)

   **Step 3 -- Charge Power**
   - **Min Charge Power** / **Max Charge Power**: The band the planner and the efficiency finder
     may use (W)
   - **Battery Charge Max (AC+DC)** and its **Entity** (optional): An absolute charge power limit
     applied during AC charging and reset afterwards. Both belong together: setting one without
     the other is rejected.
   - **Battery Max AC Charge Limit Entity** (optional): The number entity the planned charge
     power and the efficiency finder write to
   - **Charge Power Sent / Received** (optional): The two power sensors the efficiency finder
     compares to measure the charging loss
   - **Auto Efficient Charge Finder**: Enables the search; requires the AC limit entity and both
     power sensors
   - **Force Discharge Switch** (optional): Switch that forces discharge to the grid, used by
     `Morning Discharge`
   - **Discharge Power Limit Entity** (optional): Number entity set to 0 at the window start so
     the house runs from the grid; the previous value is restored at the window end

   **Step 4 -- Advanced**
   - **Update Interval**: How often the coordinator refreshes (default `900` s)
   - **Command Delay**: Delay between the min SOC and the grid charge command (default `0.1` s)
   - **Active Start Date** / **Active End Date** (optional): Restrict the integration to a season
   - **Backup Mode Entity** (optional): While it is active, nothing is written to the inverter
   - **House Consumption Energy Meter** (optional): Cumulative kWh meter; the Bridge planner
     learns an hourly load profile from the last 14 days of recorder statistics
   - **Average House Load**: Fallback load in kW when no meter is configured (default `0.5`)
   - **PV Crossover Delay**: Minutes after sunrise until PV output exceeds the house load
     (default `90`)
   - **Bridge Reserve**: Safety reserve in kWh added to the bridge energy (default `0.5`)
   - **Charge Efficiency**: Grid-to-battery efficiency used to plan the charge power
     (default `0.9`)
   - **Night / Day / Feed-in Price** (optional): Tariffs in ct/kWh. Set all three or none; the
     Bridge planner uses them to decide a conflict between bridging and PV headroom.

3. **Submit the configuration**
   - Review all settings
   - Click **Submit**

### Step 3: Verify Installation

After configuration, you should see these ten entities:

- **`sensor.inverter_charge_night_calculated_soc`** - The target SOC the planner calculated
  - Unit: `%`
  - Attributes: `is_active`, `target_reached`, `current_soc`, `operation_mode`, `skip_next`,
    `snow_nights`, `inverter_floor_soc`, `discharge_block`, and in Bridge mode `plan_reason`,
    `bridge_kwh`, `surplus_kwh`, `lower_bound_soc`, `upper_bound_soc`, `pv_crossover`
  - `inverter_floor_soc` is what is written to the inverter's min SOC entity. It is higher than
    the target while the discharge block runs over the min SOC (see
    [Discharge in the Window](#discharge-in-the-window)); `discharge_block` names the way in use
    (`switch` / `limit` / `min_soc` / `off`)

- **`sensor.inverter_charge_night_best_charge_power`** - Best AC charge power found
  - Unit: `W`
  - The last stored optimum of the Auto Efficient Charge Finder

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

- **`switch.inverter_charge_night_auto_efficient_charge_finder`** - Auto Efficient Charge Finder
  - Searches for the most efficient AC charge limit and turns itself off when done

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

## Use Cases

- **Winter / low PV forecast**: Use higher target SOC to ensure enough overnight capacity.
- **Summer / high PV forecast**: Use lower target SOC to create more daytime storage headroom.
- **Backup/Island mode**: Disable integration when backup mode is active to avoid AC charging.
- **Efficiency tuning**: Enable Auto Efficient Charge Finder for a few nights to find best AC limit.
- **Snow on the modules**: Set `Snow nights` to the number of nights the panels will stay
  covered; each of those nights charges to the maximum and the counter drops by one.
- **One-off exception (EV charging, guests)**: Turn on `Skip Next` to skip the coming window;
  it expires by itself after 24 hours.
- **House load after the window**: Switch the planner to `Bridge` so the battery also carries
  the house until the PV output takes over in the morning.

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

**Auto Finder card**
```yaml
type: entities
title: Auto Efficient Charge
entities:
  - switch.inverter_charge_night_auto_efficient_charge_finder
  - sensor.inverter_charge_night_best_charge_power
```

## Entity List (End-User)

- **`sensor.inverter_charge_night_calculated_soc`** - The target SOC the planner calculated
  - Unit: `%`
  - Attributes: `is_active`, `target_reached`, `current_soc`, `operation_mode`, `skip_next`,
    `snow_nights`, `inverter_floor_soc`, `discharge_block`, and in Bridge mode `plan_reason`,
    `bridge_kwh`, `surplus_kwh`, `lower_bound_soc`, `upper_bound_soc`, `pv_crossover`
  - `inverter_floor_soc` is what is written to the inverter's min SOC entity. It is higher than
    the target while the discharge block runs over the min SOC (see
    [Discharge in the Window](#discharge-in-the-window)); `discharge_block` names the way in use
    (`switch` / `limit` / `min_soc` / `off`)

- **`sensor.inverter_charge_night_best_charge_power`** - Best AC charge power found
  - Unit: `W`
  - The last stored optimum of the Auto Efficient Charge Finder

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

- **`switch.inverter_charge_night_auto_efficient_charge_finder`** - Auto Efficient Charge Finder
  - Searches for the most efficient AC charge limit and turns itself off when done

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

## How It Works

### Planner Modes

The planner decides the target SOC for the window. Which formula it uses is set by **Planner
Mode** in step 2 of the wizard; both clamp the result into the configured minimum and maximum
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

The chosen bound and its inputs are exposed as attributes of
`sensor.inverter_charge_night_calculated_soc` (`plan_reason`, `bridge_kwh`, `surplus_kwh`,
`lower_bound_soc`, `upper_bound_soc`, `pv_crossover`).

#### Target precedence

Snow nights beat everything, then the manual override, then the planner:

1. `snow_nights > 0` -> the configured maximum SOC
2. a manual override set on `number.inverter_charge_night_min_soc_override`
3. the SOC the planner calculated at the window start (re-planned during the window, but in
   Night Charge mode the target never drops below what was already reached)

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
### Discharge in the Window

Inside the cheap window the house should run from the grid, not from the battery. A kWh taken
out of the battery at night is a kWh that has to be bought at the day tariff tomorrow, so
discharging during the window trades expensive energy for cheap energy the wrong way round.

There are three ways to stop it, and the integration picks the first one your configuration
offers:

1. **Block Discharge Switch** - a switch of the inverter that locks the battery discharge
   (Kostal calls it "battery discharge lock"). Cleanest way, but not every manufacturer has one.
2. **Discharge Power Limit Entity** - a number entity for the discharge power, set to 0 for the
   window. Also vendor-specific.
3. **Min SOC** - the fallback that always works. **No inverter guarantees either of the two
   entities above, but every battery honours its min SOC**: a battery does not discharge below
   it, and the min SOC entity is the one this integration controls anyway. Raising it to the
   charge level the window started at leaves the stored energy where it is.

**Discharge Block** (step 3 of the wizard) chooses between `Automatic` (the order above,
default) and `Off` (the battery may discharge, as it did before this feature).

#### Why the inverter may show a higher min SOC than the charge target

With the min SOC fallback in use, the charge target and the value written to the inverter are
**two different numbers**, on purpose:

| Value | Meaning |
|---|---|
| charge target (`sensor.inverter_charge_night_calculated_soc`) | how far the battery is charged from the grid |
| inverter floor (attribute `inverter_floor_soc`) | what is written to the min SOC entity |

Example: the plan wants 45 %, the battery starts the window at 70 %. The integration writes
**70 %** to the min SOC entity so the 70 % stay put, while grid charging still stops at the
45 % target (it is already reached). The inverter therefore displays a min SOC of 70 % during
the window -- **this is intended**, and the value captured before the window is written back at
the window end, like every other setting the integration touches.

The floor only ever rises within a window, never falls (a jittering measurement must not cause
writes), always stays inside your minimum and maximum SOC, survives a Home Assistant restart,
and is cleared at the window end. The `sensor.inverter_charge_night_calculated_soc` attributes
`inverter_floor_soc` and `discharge_block` (`switch` / `limit` / `min_soc` / `off`) say which
way is in use and what the floor currently is.

Morning Discharge windows are never blocked -- there the point is to empty the battery.

### Operation Flow

**At Window Start:**

1. The forecast entity for the time of day is read (today's before noon, tomorrow's after)
2. The planner derives the target SOC in the configured planner mode
3. The current Kostal min SOC, the AC charge limit and the discharge limit are stored so they
   can be restored later, and the values are persisted so they also survive a restart
4. In Night Charge mode the discharge block is applied so the house runs from the grid and
   the energy just bought stays in the battery: the block switch is turned on, or the discharge
   limit is set to 0, or the min SOC floor is raised to the current charge level (see
   "Discharge in the Window")

**During Active Window (e.g., 00:00 - 05:59):**

1. Target SOC is validated against the user limits
2. Kostal min SOC is set to the inverter floor -- the charge target, or the raised
   discharge-block floor if that is higher (only when it differs from the current value)
3. Kostal grid charging is switched on (only when it is off), after the configured command delay
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

**In Morning Discharge mode** the same window drives the battery *down* to the target instead:
the min SOC acts as a floor, grid charging is kept off, and the optional force discharge switch
is turned on until the target is reached.

**At Window End (e.g., 05:59):**

1. Kostal min SOC is reset to the stored original (or the configured default if it is unknown)
2. Kostal grid charging is switched off, as is the force discharge switch
3. The AC charge limit, the absolute charge power limit, the discharge limit and the
   discharge block switch are restored, and the raised min SOC floor is dropped
4. State flags (is_active, target_reached), the manual override and the stored originals are
   cleared, and `snow_nights` counts down by one if this was a night-charge window that
   reached its configured end time
5. A reset that failed (e.g. an unavailable entity) is retried after 1, 2 and 4 minutes, then
   every 15 minutes, until it works

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

## Troubleshooting

### Integration Not Appearing

- **Check file structure**: Ensure all files are in `custom_components/inverter_charge_night/`
- **Check manifest.json**: Verify it's valid JSON
- **Restart Home Assistant**: A restart is required after installation
- **Check logs**: Look for errors in **Settings** → **System** → **Logs**

### Entities Not Found

- **Verify entity IDs**: Use **Settings** → **Devices & Services** → **Entities** to find correct entity IDs
- **Check entity domains**: 
  - Kostal min SOC should be a `number` entity
  - Grid charge should be a `switch` entity
  - Battery SOC should be a `sensor` entity

### Calculation Not Working

- **Check forecast entity**: Verify the Solcast entity is providing data in kWh
- **Check battery capacity**: Ensure it's entered correctly in kWh (not Wh)
- **Check logs**: Look for calculation errors in the logs

### Charging Not Starting

- **Check time window**: Verify current time is within the configured window
- **Check switch state**: Ensure the integration switch is enabled
- **Check Kostal entities**: Verify Kostal entities are accessible and not in unavailable state
- **Check logs**: Look for control errors in the logs

### SOC Not Resetting

- **Check end time**: Verify the end time is configured correctly
- **Check logs**: Look for reset errors at end time
- **Manual reset**: You can manually set the Kostal min SOC if needed

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
- All Kostal settings are reset to original values
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
- Kostal entities are controlled if needed
- Battery SOC is checked for target reached condition

## Support

For issues, questions, or contributions:

- **GitHub Issues**: [Create an issue](https://github.com/Puma7/InverterChargeNight/issues)
- **Home Assistant Community**: [Forum Discussion](https://community.home-assistant.io/)

## License

This integration is provided as-is for personal use.

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
├── __init__.py              # Integration setup and the coordinator (control loop)
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
- `operation_mode` - `night_charge` or `morning_discharge`
- `kostal_min_soc_entity` - Kostal min SOC number entity ID (also the entry's unique id)
- `kostal_grid_charge_switch` - Kostal grid charge switch entity ID
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
- `discharge_limit_entity` - Optional number entity blocked to 0 during the window
- `grid_import_entity` - Optional sensor with the current grid import in W or kW; without it
  the charge power is not limited against the house connection
- `main_fuse_a` - Main fuse of the house connection, per phase
- `grid_phases` - 1 or 3
- `grid_voltage_v` - Phase voltage (230 V in Germany)
- `grid_continuous_pct` - Share of the nominal power allowed as continuous load
- `grid_max_continuous_w` - The budget in W directly; takes precedence over the fuse size
- `grid_headroom_w` - Safety margin kept below the budget
- `discharge_block_switch` - Optional switch of the inverter that locks the battery discharge
- `discharge_block_mode` - `auto` (default: switch, else power limit, else min SOC) or `off`

Step 4 -- advanced:
- `update_interval` - Coordinator refresh interval in seconds (default 900)
- `command_delay` - Delay between min SOC and grid charge commands in seconds
- `active_start_date` / `active_end_date` - Optional seasonal restriction (YYYY-MM-DD)
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
