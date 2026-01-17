# Inverter Behavior Verification and Enhancements

## Overview
This document describes the verification of inverter behavior and the enhancements made to ensure proper operation in all scenarios.

## Switch Activation Logic Verification

The switch activation logic has been verified to work correctly in all scenarios:

### Scenario 1: SOC 40%, Target 30%
- **Current SOC**: 40%
- **Target SOC**: 30%
- **Behavior**: `current_soc >= target_soc` → `40% >= 30%` → **TRUE**
- **Result**: **Skip charging** (correct - battery already above target)

### Scenario 2: SOC 40%, Target 40%
- **Current SOC**: 40%
- **Target SOC**: 40%
- **Behavior**: `current_soc >= target_soc` → `40% >= 40%` → **TRUE**
- **Result**: **Skip charging** (correct - battery already at target)

### Scenario 3: SOC 40%, Target 41%
- **Current SOC**: 40%
- **Target SOC**: 41%
- **Behavior**: `current_soc >= target_soc` → `40% >= 41%` → **FALSE**
- **Result**: **Activate charging** (correct - battery needs to reach target)

## Key Enhancements Implemented

### 1. Minimum SOC Tracking
- **Added**: `minimum_calculated_soc` attribute to track the minimum SOC value
- **Purpose**: Always maintain the lowest target SOC value (initial or override, whichever is lower)
- **Implementation**: 
  - Initialized with `initial_calculated_soc` at window start
  - Updated when override is set if override is lower
  - Used as the target SOC for all operations

### 2. Periodic Verification (15-minute intervals)
- **Added**: `_start_periodic_verification()` method
- **Purpose**: Verify every 15 minutes (or configured interval) that inverter min SOC matches our saved target
- **Behavior**:
  - Checks if inverter min SOC matches our `minimum_calculated_soc` (with 0.5% tolerance)
  - If mismatch detected, restores inverter min SOC to our target value
  - Also checks if battery SOC already exceeds target and turns off charging if so

### 3. Inverter Min SOC Change Listener
- **Added**: `_setup_inverter_min_soc_listener()` method
- **Purpose**: Detect when inverter min SOC is changed externally (by user or system)
- **Behavior**:
  - Monitors inverter min SOC entity for state changes
  - If change detected and doesn't match our target, immediately restores to our target
  - Provides immediate response to external modifications

### 4. Enhanced Target SOC Logic
- **Changed**: Always use `minimum_calculated_soc` as the target (not just `initial_calculated_soc`)
- **Purpose**: Ensure we always maintain the lowest value (initial or override)
- **Implementation**:
  - When override is set, if it's lower than current minimum, update minimum
  - All target checks use minimum value
  - All inverter operations use minimum value

### 5. Startup SOC Check
- **Added**: Check at startup if battery SOC already exceeds target
- **Purpose**: Prevent unnecessary charging if target already reached
- **Behavior**:
  - Before controlling inverter, check if battery SOC >= target
  - If yes, turn off charging switch immediately
  - Set `target_reached` flag to prevent further charging attempts

### 6. Home Assistant Restart Handling
- **Enhanced**: Existing restart recovery logic
- **Behavior**:
  - On restart during active window, preserves inverter min SOC value
  - Sets up listeners and periodic verification if already in active window
  - Immediately verifies and restores min SOC if needed

## Critical Safety Features

### Always Save First Value
- `initial_calculated_soc` is saved at window start and never recalculated
- This ensures we always have the original forecast-based target

### Always Save Minimum Value
- `minimum_calculated_soc` tracks the lowest value (initial or override)
- This ensures we always maintain the most conservative target

### Periodic Verification
- Every 15 minutes (or configured interval), verify inverter min SOC matches our target
- If mismatch, immediately restore to our target value
- Also check if battery SOC exceeds target and turn off charging if so

### Immediate Response to Changes
- Listener detects inverter min SOC changes immediately
- Restores to our target value if changed externally
- Prevents system/user from overriding our calculated target

### Target Already Reached
- If battery SOC already exceeds target at any point:
  - Turn off charging switch immediately
  - Set `target_reached` flag
  - Prevent further charging attempts

## Code Changes Summary

### Files Modified
1. `custom_components/inverter_charge_night/__init__.py`
   - Added `minimum_calculated_soc` attribute
   - Added `_inverter_min_soc_listener` attribute
   - Added `_verification_task` attribute
   - Added `_setup_inverter_min_soc_listener()` method
   - Added `_remove_inverter_min_soc_listener()` method
   - Added `_start_periodic_verification()` method
   - Added `_stop_periodic_verification()` method
   - Added `_verify_and_restore_min_soc()` method
   - Updated `_calculate_initial_soc()` to initialize minimum
   - Updated `_on_window_start()` to set up listeners and verification
   - Updated `_on_window_end()` to clean up listeners and verification
   - Updated `_async_update_data()` to use minimum value and check startup SOC
   - Updated `_check_current_window()` to set up listeners on restart recovery

2. `custom_components/inverter_charge_night/number.py`
   - Updated `async_set_native_value()` to update minimum when override is set

3. `custom_components/inverter_charge_night/switch.py`
   - Updated `async_turn_off()` to clean up listeners and verification

## Testing Scenarios

### Test Case 1: Normal Operation
1. Window starts, calculates 36% target
2. Battery at 30%, activates charging
3. Battery reaches 36%, turns off charging
4. **Expected**: Works correctly ✅

### Test Case 2: Battery Already Above Target
1. Window starts, calculates 30% target
2. Battery already at 40%
3. **Expected**: Skip charging, turn off switch ✅

### Test Case 3: Override Set Lower
1. Window starts, calculates 36% target
2. User sets override to 30%
3. **Expected**: Minimum updated to 30%, uses 30% as target ✅

### Test Case 4: External SOC Change
1. Window active, target is 36%
2. User/system changes inverter min SOC to 100%
3. **Expected**: Detected immediately, restored to 36% ✅

### Test Case 5: Periodic Verification
1. Window active, target is 36%
2. Wait 15 minutes
3. **Expected**: Verification runs, checks inverter min SOC matches 36% ✅

### Test Case 6: Home Assistant Restart
1. Window active, target is 36%
2. Home Assistant restarts
3. **Expected**: Preserves 36% target, sets up listeners, verifies immediately ✅

## Conclusion

All requested features have been implemented:
- ✅ Switch activation logic verified for all scenarios
- ✅ Minimum SOC tracking implemented
- ✅ Periodic verification (15 min or on change) implemented
- ✅ Inverter min SOC mismatch detection and restoration
- ✅ Battery SOC already exceeds target detection and switch-off
- ✅ Home Assistant restart handling enhanced
- ✅ Always save first value (initial_calculated_soc)
- ✅ Always save minimum value (minimum_calculated_soc)

The system now robustly handles all edge cases and ensures the inverter min SOC always matches our calculated target, preventing any external modifications from affecting the charging behavior.

