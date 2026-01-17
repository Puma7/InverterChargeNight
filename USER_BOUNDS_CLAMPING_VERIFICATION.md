# User Bounds Clamping Verification

## Question
If we calculate 30% but the user has set minimum SOC to 36% and maximum to 100%, does the calculation automatically clamp to 36%?

## Answer: YES ✅

The calculation function already clamps values to user-defined bounds.

## How It Works

### 1. Calculation Clamping (calculation.py, line 85)
```python
calculated_soc = max(user_min_soc, min(calculated_soc, user_max_soc))
```

**Example:**
- User sets: `user_min_soc = 36%`, `user_max_soc = 100%`
- Calculation returns: `30%`
- After clamping: `max(36, min(30, 100)) = max(36, 30) = 36%` ✅

### 2. Override Clamping (number.py, line 67)
**NEW:** Override values are now also clamped to user bounds.

```python
clamped_value = max(user_min_soc, min(value, user_max_soc))
```

**Example:**
- User sets: `user_min_soc = 36%`, `user_max_soc = 100%`
- User sets override to: `30%`
- After clamping: `max(36, min(30, 100)) = 36%` ✅
- Warning logged: "Override value 30.0% clamped to 36.0% to respect user bounds [36.0%, 100.0%]"

**Another Example:**
- User sets: `user_min_soc = 36%`, `user_max_soc = 100%`
- User sets override to: `40%`
- After clamping: `max(36, min(40, 100)) = 40%` ✅ (within bounds, no clamping needed)

## Complete Flow Example

### Scenario: User min = 36%, max = 100%

1. **Calculation returns 30%:**
   - Calculation clamps: `30% → 36%` ✅
   - `initial_calculated_soc = 36%`
   - `minimum_calculated_soc = 36%`
   - Target used: `36%`

2. **User sets override to 40%:**
   - Override clamps: `40% → 40%` (within bounds) ✅
   - `override_soc = 40%`
   - `minimum_calculated_soc = 36%` (stays at minimum)
   - Target used: `40%` (override takes precedence)

3. **User sets override to 30%:**
   - Override clamps: `30% → 36%` ✅
   - Warning logged about clamping
   - `override_soc = 36%` (clamped value)
   - `minimum_calculated_soc = 36%`
   - Target used: `36%`

## Code Locations

1. **Calculation clamping:** `calculation.py` line 85
   ```python
   calculated_soc = max(user_min_soc, min(calculated_soc, user_max_soc))
   ```

2. **Override clamping:** `number.py` lines 63-79
   ```python
   user_min_soc = float(self.coordinator.config.get(CONF_USER_MIN_SOC, 8.0))
   user_max_soc = float(self.coordinator.config.get(CONF_USER_MAX_SOC, 100.0))
   clamped_value = max(user_min_soc, min(value, user_max_soc))
   ```

3. **Validation in control:** `__init__.py` lines 868-875
   ```python
   if not (user_min_soc <= target_soc <= user_max_soc):
       _LOGGER.error("Target SOC outside allowed range - not applying")
       return
   ```

## Summary

✅ **Calculation always respects user bounds** - If calculated value is below `user_min_soc` or above `user_max_soc`, it is automatically clamped.

✅ **Override now also respects user bounds** - If override value is outside bounds, it is automatically clamped and a warning is logged.

✅ **Both use the same clamping formula** - `max(user_min_soc, min(value, user_max_soc))`

## Test Cases

| Calculated | User Min | User Max | Result | Notes |
|------------|----------|----------|--------|-------|
| 30% | 36% | 100% | **36%** | Clamped to minimum |
| 50% | 36% | 100% | **50%** | Within bounds |
| 110% | 36% | 100% | **100%** | Clamped to maximum |
| 30% (override) | 36% | 100% | **36%** | Override clamped to minimum |
| 40% (override) | 36% | 100% | **40%** | Override within bounds |

