# Inverter Charge Night - Technical Documentation

**Purpose**: Comprehensive technical documentation optimized for AI code analysis and understanding. This document enables AI systems to understand the entire codebase structure, data flows, algorithms, and implementation details without requiring full source code analysis.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Component Structure](#component-structure)
3. [Data Flow & State Management](#data-flow--state-management)
4. [Core Algorithms](#core-algorithms)
5. [Configuration Schema](#configuration-schema)
6. [Safety Mechanisms](#safety-mechanisms)
7. [Error Handling Patterns](#error-handling-patterns)
8. [Edge Cases & Special Scenarios](#edge-cases--special-scenarios)
9. [API & Interface Details](#api--interface-details)
10. [Constants & Defaults](#constants--defaults)
11. [State Transitions](#state-transitions)
12. [Critical Implementation Details](#critical-implementation-details)

---

## Architecture Overview

### System Type
Home Assistant Custom Integration using Coordinator Pattern

### Core Pattern
- **Coordinator**: `InverterChargeNightCoordinator` extends `DataUpdateCoordinator`
- **Platforms**: Sensor, Switch, Binary Sensor, Number
- **Update Model**: Periodic (configurable) + Event-driven (time triggers + state listeners)

### Key Responsibilities
1. Calculate optimal battery SOC based on PV forecast
2. Control Kostal inverter (min SOC + grid charge switch)
3. Monitor battery SOC and stop charging at target
4. Restore original settings at window end
5. Handle manual overrides, date-range constraints, and edge cases

### Dependencies
- Home Assistant Core (2024.x+)
- Kostal Integration (provides entities)
- Solcast Integration (provides forecast data)
- Python 3.10+ (type hints: `float | None`)

---

## Component Structure

### File: `__init__.py` (Main Coordinator)
**Purpose**: Core integration logic, coordinator, and lifecycle management

**Key Classes**:
- `InverterChargeNightCoordinator`: Main coordinator class

**Key Methods**:
- `async_setup_entry()`: Integration setup entry point
- `async_update_entry()`: Handle config updates
- `async_unload_entry()`: Cleanup on unload
- `_on_window_start()`: Window start handler
- `_on_window_end()`: Window end handler
- `_async_update_data()`: Periodic update (every 15 min)
- `_control_kostal()`: Control inverter entities
- `_stop_grid_charging()`: Stop charging when target reached
- `_reset_settings()`: Restore original settings
- `_setup_battery_soc_listener()`: Real-time SOC monitoring
- `_remove_battery_soc_listener()`: Cleanup listener
- `_setup_inverter_min_soc_listener()`: Monitor external min SOC changes
- `_remove_inverter_min_soc_listener()`: Cleanup inverter listener
- `_start_periodic_verification()`: Background verification task
- `_stop_periodic_verification()`: Stop verification task
- `_verify_and_restore_min_soc()`: Verify and restore inverter min SOC
- `_calculate_initial_soc()`: Calculate SOC at window start
- `_check_current_window()`: Verify window state on config change

**State Variables** (Coordinator):
- `original_min_soc: float | None` - Stored original min SOC value
- `is_active: bool` - Currently in active window
- `is_enabled: bool` - Integration enabled via switch
- `calculated_soc: float | None` - Current calculated SOC
- `initial_calculated_soc: float | None` - SOC calculated at window start
- `minimum_calculated_soc: float | None` - Lowest target SOC during window
- `target_reached: bool` - Target SOC reached flag
- `override_soc: float | None` - Manual override value
- `_last_soc_set: float | None` - Last SOC value set (rate limiting)
- `_battery_soc_listener` - State change listener reference
- `_inverter_min_soc_listener` - Listener for external min SOC changes
- `_verification_task` - Periodic verification background task
- `_verifying_min_soc: bool` - Guard against concurrent verification
- `_time_triggers: list` - Time-based trigger references

**Critical Logic**:
- Commands sent with configured delay: Min SOC → delay → Grid Charge (prevents double DC checks)
- Override takes precedence but minimum target enforced via `minimum_calculated_soc`
- Dual target checking: Periodic + Real-time listener (immediate)
- Optional active date range limits operation

### File: `calculation.py`
**Purpose**: SOC calculation algorithm

**Key Function**:
- `calculate_required_soc(forecast_energy, battery_capacity, error_margin, user_min_soc, user_max_soc) -> float | None`

**Algorithm**:
1. Validate inputs (capacity > 0, SOC range valid, error_margin 0-100)
2. Apply error margin: `forecast_with_margin = forecast_energy * (1 + error_margin / 100.0)`
3. Calculate remaining: `remaining_capacity = battery_capacity - forecast_with_margin`
4. If remaining < 0: Use `user_min_soc`
5. Else: `calculated_soc = (remaining_capacity / battery_capacity) * 100.0`
6. Clamp: `max(user_min_soc, min(calculated_soc, user_max_soc))`
7. Round to 1 decimal: `round(calculated_soc, 1)`

**Edge Cases**:
- Forecast exceeds capacity → Returns `user_min_soc`
- Forecast is 0 → Returns `user_max_soc` (or clamped)
- Invalid inputs → Returns `None`

### File: `config_flow.py`
**Purpose**: Configuration UI and validation

**Key Classes**:
- `InverterChargeNightConfigFlow`: Initial configuration
- `OptionsFlowHandler`: Options/update flow

**Validation Rules**:
- Time format: `HH:MM` (0-23 hours, 0-59 minutes)
- SOC values: 0.0 - 100.0
- `user_max_soc > user_min_soc` (enforced)
- Battery capacity: > 0
- Entities: Must exist in entity registry or state registry
- Optional dates: `YYYY-MM-DD` or empty

**Schema Fields**:
- `name`: Integration name (string)
- `kostal_min_soc_entity`: Number entity selector
- `kostal_grid_charge_switch`: Switch entity selector
- `pv_forecast_entity`: Entity selector (any domain)
- `battery_soc_entity`: Sensor entity selector
- `battery_capacity`: Float (kWh)
- `start_time`: String (HH:MM)
- `end_time`: String (HH:MM)
- `user_min_soc`: Float (0-100)
- `user_max_soc`: Float (0-100)
- `forecast_error_margin`: Float (0-100, percentage)
- `default_min_soc`: Float (0-100)
- `update_interval`: Int (60-3600 seconds)
- `command_delay`: Float (0.0-5.0 seconds)
- `active_start_date`: Optional date (YYYY-MM-DD)
- `active_end_date`: Optional date (YYYY-MM-DD)

### File: `sensor.py`
**Purpose**: Calculated SOC sensor entity

**Entity Class**: `CalculatedSOCSensor`
**Entity ID Pattern**: `{entry_id}_calculated_soc`
**State**: `calculated_soc` value (float | None)
**Attributes**:
- `is_active`: bool
- `target_reached`: bool
- `current_soc`: float | None

### File: `sensor.py` (Best Charge Power)
**Purpose**: Best charge power found by Auto Efficient Charge Finder
**Entity Class**: `BestChargePowerSensor`
**Entity ID Pattern**: `{entry_id}_best_charge_power`
**State**: Best power in W (float | None)

### File: `switch.py`
**Purpose**: Enable/disable integration switch

**Entity Class**: `InverterChargeNightSwitch`
**Entity ID Pattern**: `{entry_id}_enabled`
**State**: `coordinator.is_enabled` (bool)
**Actions**:
- `async_turn_on()`: Enable integration, request refresh
- `async_turn_off()`: Disable integration, reset settings, clear override, remove listener

### File: `switch.py` (Auto Efficient Charge Finder)
**Purpose**: Enable/disable auto efficiency search
**Entity Class**: `AutoEfficientChargeSwitch`
**Entity ID Pattern**: `{entry_id}_auto_efficient_charge`
**State**: `coordinator.auto_efficient_charge` (bool)
**Behavior**: Auto disables after optimum is found

### File: `number.py`
**Purpose**: Manual SOC override

**Entity Class**: `MinSOCOverrideNumber`
**Entity ID Pattern**: `{entry_id}_min_soc_override`
**Range**: 0.0 - 100.0
**Step**: 1
**State**: Override value or calculated SOC
**Actions**:
- `async_set_native_value(value)`: Sets override, stores in coordinator, resets `target_reached`, applies if active

**State Logic**:
- If coordinator override is cleared, internal override is cleared too
- Otherwise return override value, else `coordinator.data.get("calculated_soc")`

### File: `binary_sensor.py`
**Purpose**: Active window indicator

**Entity Class**: `ActiveWindowBinarySensor`
**Entity ID Pattern**: `{entry_id}_active`
**State**: `coordinator.data.get("is_active", False)`

### File: `const.py`
**Purpose**: Constants and configuration keys

**See**: [Constants & Defaults](#constants--defaults) section

---

## Data Flow & State Management

### Update Cycle (Periodic)
1. `_async_update_data()` called at configured interval (default: 15 minutes / 900 seconds, configurable: 60-3600 seconds)
2. Check `is_enabled`, `is_active`, and active date range → Return early/reset if outside
3. Read forecast data (multiple formats supported)
4. Use `initial_calculated_soc` if available, else recalculate
5. Determine target and update `minimum_calculated_soc` if needed
6. Verify battery SOC availability before controlling inverter
7. Call `_control_kostal(target_soc)` if target exists and not reached
8. Check current SOC against target
9. If reached: Call `_stop_grid_charging()`, set `target_reached = True`
10. Return data dict for entities

### Real-Time Monitoring (Listener)
1. `_setup_battery_soc_listener()` called at window start
2. Listener callback receives `event` via `async_track_state_change_event`
3. Check: `is_active`, `is_enabled`, `target_reached` → Return early if false
4. Parse new SOC value
5. Determine target: `minimum_calculated_soc` (or override/calculated fallback)
6. If `current_soc >= target_soc`: Stop charging, set `target_reached`
7. Safety check: If `current_soc > target_soc + 5.0`: Force stop
8. Inverter min SOC listener triggers verification if external changes detected

### Window Start Flow
1. `_on_window_start()` triggered at start time
2. Check `is_enabled` and active date range → Return if not allowed
3. Set `is_active = True`, `target_reached = False`
4. Call `_calculate_initial_soc()` → Store in `initial_calculated_soc`
5. Call `_setup_battery_soc_listener()`
6. Call `_setup_inverter_min_soc_listener()` + start periodic verification
7. Request refresh → Triggers `_async_update_data()`

### Window End Flow
1. `_on_window_end()` triggered at end time
2. Try: Call `_reset_settings()`
3. Finally (always executes):
   - Set `is_active = False`
   - Set `target_reached = False`
   - Clear `initial_calculated_soc`
   - Clear `minimum_calculated_soc`
   - Clear `override_soc`
   - Remove battery SOC listener
   - Remove inverter min SOC listener
   - Stop periodic verification task
   - Request refresh

### Control Flow (`_control_kostal`)
1. Validate `target_soc` against `[user_min_soc, user_max_soc]` → Return if invalid
2. Check current SOC → Set `should_skip_charging = True` if already at target
3. If battery SOC entity is unavailable → `should_skip_charging = True` (safety)
3. Store original min SOC if not stored (with sanity check)
4. Determine if min SOC needs update (threshold: 0.5%)
5. If both min SOC and grid charge needed:
   - Set min SOC
   - Wait configured delay (`asyncio.sleep(command_delay)`, default 0.1s, configurable 0.0-5.0s)
   - Turn on grid charge
6. Else if only grid charge needed: Turn on grid charge
7. All operations wrapped in try-except with logging

### Reset Flow (`_reset_settings`)
1. Determine reset value: `original_min_soc` if available, else `default_min_soc`
2. Reset min SOC entity (if entity exists and state available)
3. Turn off grid charge switch (if entity exists and state is "on")
4. If reset successful: Clear `original_min_soc`, `_last_soc_set`, `override_soc`

---

## Core Algorithms

### SOC Calculation Formula
```
forecast_with_margin = forecast_energy × (1 + error_margin / 100.0)
remaining_capacity = battery_capacity - forecast_with_margin

IF remaining_capacity < 0:
    calculated_soc = user_min_soc
ELSE:
    calculated_soc = (remaining_capacity / battery_capacity) × 100.0

final_soc = max(user_min_soc, min(calculated_soc, user_max_soc))
return round(final_soc, 1)
```

### Target SOC Selection
```
IF override_soc is not None:
    target_soc = override_soc
    minimum_calculated_soc = min(minimum_calculated_soc, override_soc)
ELSE:
    target_soc = calculated_soc
    minimum_calculated_soc = min(minimum_calculated_soc, calculated_soc)
```

### Time Window Check
```
IF start_time < end_time:
    in_window = (start_time <= current_time <= end_time)
ELSE:  # Overnight window (e.g., 22:00-06:00)
    in_window = (current_time >= start_time OR current_time <= end_time)
```

### Active Date Range Check
```
IF no start_date AND no end_date:
    allowed = True
ELSE IF start_date > end_date:
    allowed = True  # invalid range ignored
ELSE:
    allowed = (start_date <= today <= end_date)
```

### Rate Limiting (Min SOC Updates)
```
IF current_value is None OR abs(current_value - target_soc) > 0.5:
    IF _last_soc_set is None OR abs(_last_soc_set - target_soc) > 0.5:
        # Update allowed
        set_value(target_soc)
        _last_soc_set = target_soc
```

### Original SOC Sanity Check
```
IF original_min_soc > (default_min_soc + 10.0):
    # Suspiciously high - likely leftover from restart
    original_min_soc = default_min_soc
```

---

## Configuration Schema

### Configuration Keys (stored in `entry.data`)
- `kostal_min_soc_entity`: str (entity ID)
- `kostal_grid_charge_switch`: str (entity ID)
- `pv_forecast_entity`: str (entity ID)
- `battery_soc_entity`: str (entity ID)
- `battery_capacity`: float (kWh, must be > 0)
- `start_time`: str (HH:MM format)
- `end_time`: str (HH:MM format)
- `user_min_soc`: float (0-100)
- `user_max_soc`: float (0-100, must be > user_min_soc)
- `forecast_error_margin`: float (0-100, percentage)
- `default_min_soc`: float (0-100)
- `update_interval`: int (60-3600 seconds, default: 900)
- `command_delay`: float (0.0-5.0 seconds, default: 0.1)
- `active_start_date`: str (YYYY-MM-DD, optional)
- `active_end_date`: str (YYYY-MM-DD, optional)
- `backup_mode_entity`: str (entity ID, optional)
- `absolute_max_charge_power_w`: int (W, optional)
- `absolute_max_charge_power_entity`: str (entity ID, optional)
- `min_charge_power_w`: int (W)
- `max_charge_power_w`: int (W)
- `charge_power_entity`: str (entity ID, optional)
- `charge_power_sent_entity`: str (entity ID, optional)
- `charge_power_received_entity`: str (entity ID, optional)
- `auto_efficient_charge`: bool

### Entity Requirements
- **Kostal Min SOC**: `number` domain entity
- **Kostal Grid Charge**: `switch` domain entity
- **PV Forecast**: Any domain (supports multiple formats)
- **Battery SOC**: `sensor` domain entity

### Optional Active Date Range
- **Purpose**: Restrict operation to a specific date window (e.g., season).
- **Behavior**:
  - If today is outside the range, the integration will not start the window.
  - If it is already active and the date range ends, it will stop and reset.
- **Fields**:
  - `active_start_date`: start date (YYYY-MM-DD) or empty
  - `active_end_date`: end date (YYYY-MM-DD) or empty

### Optional Backup Mode Entity
- **Purpose**: Skip min SOC/grid-charge control when inverter is in backup/island mode.
- **Behavior**:
  - If backup mode is active, the integration will not start the window.
  - If backup mode becomes active during a window, control actions are skipped.
- **Fields**:
  - `backup_mode_entity`: entity indicating backup/island mode

### Auto Efficient Charge (Optional)
- **Purpose**: Find the most efficient charge power (lowest loss) between user min/max.
- **Inputs**:
  - `absolute_max_charge_power_w` + `absolute_max_charge_power_entity` (AC+DC limit)
  - `min_charge_power_w`, `max_charge_power_w`
  - `charge_power_entity` (max AC charge limit)
  - `charge_power_sent_entity` (power sent to battery)
  - `charge_power_received_entity` (power received by battery)
- **Behavior**:
  - Tests candidate power points until a 0.1 kW optimum is found
  - Requires at least 30 minutes of charging per test (shorter runs are discarded)
  - Stores best result persistently and reuses it
  - Restores original absolute max (AC+DC) after grid charging ends

#### Example Sequence (5–15 kW)
Assume `min_charge_power_w=5000`, `max_charge_power_w=15000` (step 100 W).
Each test must run ≥30 minutes to be recorded.
```
Range [5.0, 15.0] kW:
Candidate C = 15 - φ*(15-5) ≈ 8.82 kW
Candidate D = 5 + φ*(15-5) ≈ 11.18 kW

Test 8.8 kW → loss 6.2%
Test 11.2 kW → loss 5.4%  (better)
→ New range [8.8, 15.0] kW

Next C/D inside [8.8, 15.0]:
Test 10.6 kW → loss 5.0%  (better)
→ New range [10.6, 15.0] kW

Continue until range width < 0.1 kW
Best point stored and applied automatically
```

---

## Safety Mechanisms

### 1. Overcharging Protection
- **Primary**: Stops at exact target SOC (`current_soc >= target_soc`)
- **Emergency**: Forces stop if exceeds by 5% (`current_soc > target_soc + 5.0`)
- **Dual Monitoring**: Periodic (`update_interval` seconds) + Real-time listener (immediate)

### 2. EEPROM Wear Protection
- **Threshold**: 0.5% change required before updating min SOC
- **Rate Limiting**: Tracks `_last_soc_set` to prevent rapid updates
- **State Check**: Verifies current value before setting

### 3. Restart Loop Protection
- **Sanity Check**: If captured `original_min_soc > default_min_soc + 10.0`, use default
- **Prevents**: Capturing high SOC as "original" after HA restart during active window

### 4. State Persistence
- **Original Storage**: `original_min_soc` stored when first setting value
- **Fallback**: Uses `default_min_soc` if original unavailable
- **Reset Guarantee**: Always attempts reset, even if original lost

### 5. Resource Cleanup
- **Listener Removal**: Removed on window end, disable, unload, before new setup
- **Trigger Cleanup**: Time triggers removed on unload
- **State Reset**: Flags reset even if operations fail

### 6. Inverter Min SOC Verification
- **Periodic Check**: Background task verifies inverter min SOC matches target
- **Listener Check**: Detects external changes and triggers restore
- **Guard**: `_verifying_min_soc` prevents concurrent verification

### 7. Safe Fallback SOC
- **Trigger**: When forecast entity is unavailable or calculation fails
- **Value**: 50% (configurable via `DEFAULT_SAFE_FALLBACK_SOC`)
- **Purpose**: Prevents charging to 100% when forecast data is missing
- **Location**: `_calculate_initial_soc()`, `_async_update_data()`
- **Clamping**: Respects `user_min_soc` and `user_max_soc` bounds

### 8. Validation Layers
- **Config Flow**: Validates all inputs before saving
- **Calculation**: Validates inputs before calculation
- **Runtime**: Validates target SOC before applying to inverter

### 9. Error Recovery
- **Service Calls**: Wrapped in try-except
- **Entity Checks**: Verifies availability before operations
- **Graceful Degradation**: Continues operation if some entities unavailable
- **State Flags**: Always reset even if operations fail

### 10. Command Sequencing
- **Min SOC First**: Set min SOC before grid charge
- **0.1s Delay (Configurable)**: Prevents inverter double DC checks
- **Together Processing**: Inverter processes both commands together

### 11. Battery SOC Availability Check
- **Location**: `_async_update_data()`, `_control_kostal()`
- **Behavior**: Verifies battery SOC entity is available before turning on grid charge
- **Safety**: If battery SOC unavailable (e.g., after restart), skips grid charge activation
- **Prevents**: Unintended charging when current SOC cannot be verified
- **Rationale**: After restart, battery entity may take time to load - we shouldn't charge until we can verify current state

### 12. Active Date Range Enforcement
- **Location**: `_check_current_window()`, `_on_window_start()`, `_async_update_data()`
- **Behavior**: Prevents start outside date range and stops/resets if range expires mid-window

---

## Error Handling Patterns

### Service Call Pattern
```python
try:
    state = self.hass.states.get(entity_id)
    if state and state.state == desired_state:
        await self.hass.services.async_call(domain, service, params)
        _LOGGER.info("Operation successful")
    elif state and state.state == already_desired:
        _LOGGER.debug("Already in desired state")
    else:
        _LOGGER.warning("Entity unavailable")
except Exception as e:
    _LOGGER.error("Error: %s", e, exc_info=True)
```

### Float Conversion Pattern
```python
try:
    value = float(state.state)
except (ValueError, TypeError):
    # Handle gracefully, log if needed
    pass
```

### State Check Pattern
```python
if state and state.state not in ("unknown", "unavailable"):
    # Proceed with operation
else:
    # Handle unavailable state
```

### Reset Pattern (Always Execute)
```python
try:
    await self._reset_settings()
except Exception as e:
    _LOGGER.error("Error: %s", e, exc_info=True)
finally:
    # CRITICAL: Always reset flags
    self.is_active = False
    self.target_reached = False
    # ... other cleanup
```

---

## Edge Cases & Special Scenarios

### Forecast Data Unavailable
- **Behavior**: Distinguishes between entity unavailable vs legitimately 0 kWh
- **Unavailable Entity**: Uses safe fallback SOC (50% default) instead of calculating to 100%
- **Legitimate 0 kWh**: Uses normal calculation (allows charging to 100% if forecast is legitimately 0)
- **Location**: `_async_update_data()`, `_calculate_initial_soc()`
- **Rationale**: Prevents overcharging to 100% when forecast entity is missing, but allows normal operation when forecast is legitimately 0 kWh (e.g., winter, no sun expected)

### Forecast Exceeds Battery Capacity
- **Behavior**: Returns `user_min_soc`
- **Location**: `calculation.py` → `calculate_required_soc()`

### Battery SOC Unavailable
- **Periodic**: Skips Kostal control if battery SOC unavailable (prevents unintended charging)
- **Listener**: Returns early, no action
- **Control**: **SAFETY**: Sets `should_skip_charging = True` if battery SOC unavailable (prevents grid charge switch activation)
- **Rationale**: After restart, if battery SOC entity hasn't loaded yet, we shouldn't turn on grid charge to prevent charging when we can't verify current state

### Backup / Island Mode Active
- **Behavior**: Integration skips inverter control and verification while backup mode is active
- **Start Guard**: Window will not start in backup mode
- **Recovery**: When backup mode ends, normal window logic resumes

### Auto Efficient Charge Active
- **Behavior**: Sets charge power to test points and records loss ratio
- **Result**: Stores best power in entry options for reuse

### Entity Unavailable During Reset
- **Behavior**: Logs error, continues with other operations
- **State Flags**: Always reset (in `finally` block)

### Integration Unload During Active Window
- **Behavior**: Calls `_reset_settings()` before unload
- **Location**: `async_unload_entry()`
- **Safety**: Prevents leaving inverter in high SOC state

### Power Loss / HA Offline During Window
- **Behavior**: While HA is offline, no commands can be sent; inverter keeps last state
- **Recovery**: On startup, window check + verification restore target and stop charging if already reached
- **Limitation**: Reset at exact end time cannot occur if HA has no power

### HA Restart During Active Window
- **Behavior**: `_check_current_window()` detects state mismatch, calls `_on_window_start()`
- **Restart Recovery**: `_calculate_initial_soc()` checks current min SOC on inverter with retry mechanism
  - **Retry Logic**: Waits up to 3 minutes (180 seconds) for inverter entity to become available
  - **Retry Interval**: Checks every 10 seconds
  - **Preservation**: If min SOC is set to value different from default (>1% difference) and within user bounds
  - **Result**: Uses that value as `initial_calculated_soc` instead of recalculating
  - **Fallback**: If inverter doesn't become available within timeout, recalculates from forecast
- **Original SOC**: Sanity check prevents using high SOC as original
- **Recovery**: Preserves target if found within timeout, otherwise recalculates from forecast

### Config Update During Active Window
- **Behavior**: Updates triggers, checks current window state
- **Listener**: Updated if battery SOC entity changed
- **State**: Adjusted if window times changed

### Override Set But Calculated SOC is None
- **Behavior**: Uses override value (override takes precedence)
- **Location**: Target selection logic in `_async_update_data()` and listener

### Target Already Reached at Window Start
- **Behavior**: `_control_kostal()` checks current SOC, skips charging
- **Location**: `_control_kostal()` → `should_skip_charging` flag

### Multiple Rapid SOC Changes
- **Behavior**: `target_reached` flag prevents duplicate stop attempts
- **Location**: Both periodic and listener check flag before stopping

### Time Format Invalid
- **Behavior**: Falls back to default, prevents infinite recursion
- **Location**: `_parse_time()` with guard clause

### User Max SOC <= User Min SOC
- **Behavior**: Rejected in config flow validation
- **Location**: `config_flow.py` → validation

---

## API & Interface Details

### Coordinator Methods (Public/Internal)

**Public (called by Home Assistant)**:
- `async_setup_entry()`: Setup entry point
- `async_update_entry()`: Config update handler
- `async_unload_entry()`: Cleanup handler

**Internal (called by coordinator/entities)**:
- `_on_window_start()`: Window start handler
- `_on_window_end()`: Window end handler
- `_async_update_data()`: Periodic update
- `_control_kostal(target_soc)`: Control inverter
- `_stop_grid_charging()`: Stop charging
- `_reset_settings()`: Restore original settings
- `_calculate_initial_soc()`: Calculate at window start
- `_setup_battery_soc_listener()`: Setup real-time monitoring
- `_remove_battery_soc_listener()`: Remove listener
- `_setup_inverter_min_soc_listener()`: Setup inverter min SOC monitoring
- `_remove_inverter_min_soc_listener()`: Remove inverter listener
- `_start_periodic_verification()`: Start verification loop
- `_stop_periodic_verification()`: Stop verification loop
- `_verify_and_restore_min_soc()`: Verify/restore inverter min SOC
- `_check_current_window()`: Verify window state

### Entity Methods

**Switch** (`InverterChargeNightSwitch`):
- `async_turn_on()`: Enable integration
- `async_turn_off()`: Disable integration, reset settings

**Number** (`MinSOCOverrideNumber`):
- `async_set_native_value(value)`: Set override value

### Service Calls Used
- `number.set_value`: Set Kostal min SOC
- `switch.turn_on`: Enable grid charge
- `switch.turn_off`: Disable grid charge

### Event Listeners
- `async_track_time_change`: Window start/end triggers
- `async_track_state_change_event`: Battery SOC and inverter min SOC monitoring

---

## Constants & Defaults

### Domain
- `DOMAIN = "inverter_charge_night"`

### Default Values
- `DEFAULT_MIN_SOC = 8.0`
- `DEFAULT_MAX_SOC = 100.0`
- `DEFAULT_START_TIME = "00:00"`
- `DEFAULT_END_TIME = "05:59"`
- `DEFAULT_FORECAST_ERROR_MARGIN = 10.0` (10%)
- `DEFAULT_UPDATE_INTERVAL = 900` (15 minutes in seconds, configurable: 60-3600)
- `DEFAULT_COMMAND_DELAY = 0.1` (0.1 seconds, configurable: 0.0-5.0)
- `DEFAULT_SAFE_FALLBACK_SOC = 50.0` (Safe fallback when forecast unavailable, prevents charging to 100%)
- `DEFAULT_ACTIVE_START_DATE = ""` (optional YYYY-MM-DD)
- `DEFAULT_ACTIVE_END_DATE = ""` (optional YYYY-MM-DD)

### Configuration Keys
- `CONF_KOSTAL_MIN_SOC_ENTITY = "kostal_min_soc_entity"`
- `CONF_KOSTAL_GRID_CHARGE_SWITCH = "kostal_grid_charge_switch"`
- `CONF_PV_FORECAST_ENTITY = "pv_forecast_entity"`
- `CONF_BATTERY_SOC_ENTITY = "battery_soc_entity"`
- `CONF_BATTERY_CAPACITY = "battery_capacity"`
- `CONF_START_TIME = "start_time"`
- `CONF_END_TIME = "end_time"`
- `CONF_USER_MIN_SOC = "user_min_soc"`
- `CONF_USER_MAX_SOC = "user_max_soc"`
- `CONF_FORECAST_ERROR_MARGIN = "forecast_error_margin"`
- `CONF_DEFAULT_MIN_SOC = "default_min_soc"`
- `CONF_UPDATE_INTERVAL = "update_interval"`
- `CONF_COMMAND_DELAY = "command_delay"`
- `CONF_ACTIVE_START_DATE = "active_start_date"`
- `CONF_ACTIVE_END_DATE = "active_end_date"`
- `CONF_BACKUP_MODE_ENTITY = "backup_mode_entity"`
- `CONF_MIN_CHARGE_POWER_W = "min_charge_power_w"`
- `CONF_MAX_CHARGE_POWER_W = "max_charge_power_w"`
- `CONF_CHARGE_POWER_ENTITY = "charge_power_entity"`
- `CONF_CHARGE_POWER_SENT_ENTITY = "charge_power_sent_entity"`
- `CONF_CHARGE_POWER_RECEIVED_ENTITY = "charge_power_received_entity"`
- `CONF_AUTO_EFFICIENT_CHARGE = "auto_efficient_charge"`

### Attributes
- `ATTR_CALCULATED_SOC = "calculated_soc"`
- `ATTR_ORIGINAL_MIN_SOC = "original_min_soc"`
- `ATTR_IS_ACTIVE = "is_active"`
- `ATTR_TARGET_SOC = "target_soc"`
- `ATTR_CURRENT_SOC = "current_soc"`

### Thresholds
- **SOC Update Threshold**: 0.5% (prevents EEPROM wear)
- **Safety Overcharge Buffer**: 5.0% (emergency stop)
- **Command Delay**: Configurable (default 0.1 seconds, range 0.0-5.0) - delay between min SOC and grid charge commands
- **Update Interval**: Configurable (default 900 seconds / 15 minutes, range 60-3600) - periodic refresh interval
- **Original SOC Sanity Check**: +10.0% above default

---

## State Transitions

### Integration States

**is_enabled** (bool):
- `False` → `True`: Enable switch turned on → Request refresh
- `True` → `False`: Enable switch turned off → Reset settings, clear override, remove listener

**is_active** (bool):
- `False` → `True`: Window start → Calculate initial SOC, setup listener, request refresh
- `True` → `False`: Window end OR disable → Reset settings, clear state, remove listener

**target_reached** (bool):
- `False` → `True`: Current SOC >= target → Stop charging
- `True` → `False`: Override changed OR window start → Reset flag

**override_soc** (float | None):
- `None` → `value`: Override set → Store in coordinator, reset `target_reached`
- `value` → `None`: Window end OR disable OR reset → Clear override

### Window State Machine
```
[Standby] --window_start--> [Active] --window_end--> [Standby]
    |                           |
    |                           +--disable--> [Disabled]
    |                           |
    +--disable--> [Disabled]    +--unload--> [Unloaded]
```

### Charging State Machine
```
[Idle] --target_set--> [Charging] --target_reached--> [Stopped]
    |                       |                              |
    |                       +--override_change--> [Idle]   |
    |                       |                              |
    +--window_end--> [Reset] <--window_end--+-------------+
```

---

## Critical Implementation Details

### Command Sequencing (Configurable Delay)
**Location**: `_control_kostal()` method
**Purpose**: Prevent inverter double DC checks
**Implementation**:
```python
if need_to_set_min_soc and kostal_min_soc_entity:
    await self.hass.services.async_call("number", "set_value", ...)
    if kostal_grid_charge_switch and not should_skip_charging:
        command_delay = float(self.config.get(CONF_COMMAND_DELAY, 0.1))
        await asyncio.sleep(command_delay)  # Configurable delay
        await self.hass.services.async_call("switch", "turn_on", ...)
```
**Rationale**: Inverter processes min SOC change, then immediately receives grid charge command. Delay is configurable (default 0.1s, range 0.0-5.0s) and should be within 2-second window before inverter goes to off mode.

### Override Precedence
**Location**: Multiple locations (periodic update, listener, control)
**Logic**: `target_soc = override_soc if override_soc is not None else calculated_soc`
**Impact**: Override always takes precedence when set, even if calculated_soc is None

### Initial SOC Storage
**Location**: `_calculate_initial_soc()` called at window start
**Purpose**: Store SOC calculated at window start, use throughout window
**Rationale**: Prevents recalculation from changing target mid-charge
**Storage**: `initial_calculated_soc` → Used in `_async_update_data()` if available
**Restart Recovery**: 
- Checks current min SOC on inverter before calculating
- **Retry Mechanism**: Waits up to 3 minutes (checks every 10s) for inverter to become available
- If min SOC is set to value >1% different from default and within user bounds
- Uses that value as `initial_calculated_soc` to preserve target from before restart
- Prevents recalculating to 100% when forecast unavailable after restart
- Handles case where inverter takes 1-2 minutes longer to load than Home Assistant

### Listener Setup/Cleanup
**Setup**: Called in `_on_window_start()`, after `_calculate_initial_soc()`
**Cleanup**: Called in:
- `_on_window_end()` (finally block)
- `async_unload_entry()`
- `async_turn_off()` (switch disable)
- `_setup_battery_soc_listener()` (before new setup)

**Safety**: Always removes existing listener before setting up new one

### Original SOC Storage
**Location**: `_control_kostal()` when `original_min_soc is None`
**Timing**: First time setting min SOC during active window
**Sanity Check**: If captured value > default + 10%, use default instead
**Storage**: `self.original_min_soc`
**Usage**: Reset value in `_reset_settings()`

### Forecast Data Parsing
**Supported Formats**:
1. Direct state value (kWh or Wh - auto-detected if > 1000)
2. `forecast` attribute (array of dicts with `wh` or `pv_power_forecast`)
3. `today_forecast` attribute (float)
4. `forecast_today` attribute (float)

**Fallback**: If all fail, uses 0.0 kWh

### Time Window Logic
**Overnight Support**: Handles windows like 22:00-06:00
**Implementation**: `_is_time_between()` checks if start < end, else uses OR logic
**Validation**: `_parse_time()` with infinite recursion guard

### Rate Limiting Details
**Min SOC Updates**: Only if change > 0.5%
**Tracking**: `_last_soc_set` stores last value
**Check**: Both current value AND last set value checked
**Purpose**: Prevent EEPROM wear on inverter

### Error Logging Levels
- **DEBUG**: Detailed operation info, state checks
- **INFO**: Important events (window start/end, SOC reached, commands sent)
- **WARNING**: Recoverable issues (unavailable entities, sanity checks)
- **ERROR**: Failures with `exc_info=True` for stack traces

---

## Data Structures

### Coordinator Data Dict (returned by `_async_update_data()`)
```python
{
    "calculated_soc": float | None,
    "is_active": bool,
    "target_reached": bool,
    "current_soc": float | None
}
```

### Entity State Patterns
- **Sensor**: `native_value = calculated_soc`, attributes = data dict
- **Switch**: `is_on = coordinator.is_enabled`
- **Binary Sensor**: `is_on = coordinator.data.get("is_active", False)`
- **Number**: `native_value = override_value or calculated_soc`

---

## Platform Setup Order

1. `async_setup_entry()`: Create coordinator, first refresh
2. `async_forward_entry_setups()`: Setup all platforms
3. `setup_time_triggers()`: Setup window triggers
4. `_check_current_window()`: Verify current state

---

## Integration Lifecycle

### Setup
1. Coordinator created
2. First refresh (may trigger window start if in window)
3. Platforms setup
4. Time triggers setup
5. Current window check

### Active Window
1. Time trigger fires → `_on_window_start()`
2. Calculate initial SOC
3. Setup listener
4. Request refresh → `_async_update_data()`
5. Control Kostal entities
6. Monitor SOC (periodic + listener)
7. Stop when target reached

### Window End
1. Time trigger fires → `_on_window_end()`
2. Reset settings
3. Cleanup state
4. Remove listener
5. Request refresh

### Unload
1. Check if active → Reset if needed
2. Remove time triggers
3. Remove listener
4. Remove from hass.data

---

## Key Design Decisions

1. **Initial SOC Storage**: Store at window start to prevent mid-charge target changes
2. **0.1s Delay**: Balance between command synchronization and inverter processing
3. **Dual Monitoring**: Periodic (reliable) + Listener (fast response)
4. **Override Precedence**: Manual override always wins for user control
5. **State Flag Reset**: Always reset in finally blocks for guaranteed cleanup
6. **Sanity Check**: Prevent restart loop by checking original SOC value
7. **Rate Limiting**: 0.5% threshold balances responsiveness and hardware protection
8. **Fallback Values**: Always have defaults to ensure reset always happens

---

## Testing Considerations

### Critical Test Scenarios
1. Window start/end transitions
2. Override set/cleared during active window
3. Integration disable during active window
4. HA restart during active window (with inverter delay)
5. Entity unavailability
6. Forecast data unavailable
7. Target already reached at start
8. Config update during active window
9. Integration unload during active window
10. Rapid SOC changes near target
11. Inverter takes longer to load than Home Assistant after restart
12. Active date range boundaries (start/end date)
13. HA offline during active window (power loss)

### Edge Cases to Test
- Forecast exceeds capacity
- Forecast is 0
- Invalid time formats
- User max <= user min (should reject)
- Battery SOC unavailable
- Kostal entities unavailable
- Service call failures
- Listener callback errors

---

## Performance Characteristics

- **Update Interval**: Configurable (default 15 minutes / 900 seconds)
- **Listener**: Event-driven (only during active window)
- **Service Calls**: Minimal (only when values change > 0.5%)
- **CPU Usage**: Negligible
- **Memory**: Minimal (coordinator + entity objects)

---

## Compatibility Notes

- **Home Assistant**: 2024.x and later
- **Python**: 3.10+ (uses `float | None` type hints)
- **Kostal Integration**: Any version providing required entities
- **Solcast Integration**: Multiple versions supported (flexible parsing)

---

## Future Extension Points

1. **Multiple Inverters**: Currently single instance design
2. **Forecast Providers**: Currently Solcast-focused (but flexible)
3. **Advanced Scheduling**: Currently simple time window

---

**Document Version**: 1.0
**Last Updated**: 2026-01-16
**Purpose**: AI-optimized technical documentation for code analysis

