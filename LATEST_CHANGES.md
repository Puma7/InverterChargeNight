# Latest Technical Changes

## 0. Trigger Recovery + Guaranteed Min-SOC Enforcement (1.0.2)

**Issue:**
In rare runtime/reload situations, users observed that nightly control did not start reliably at the configured window start (for example 23:00), and min-SOC was not always enforced at window start when battery SOC was already above target.

**Solution:**
Added two reliability hardening measures:
1. **Trigger self-healing**: The integration now verifies that both start/end time triggers exist and automatically re-registers them if they are missing.
2. **Guaranteed min-SOC at window start**: At window start, min-SOC is always enforced to target, independent of whether grid charging needs to be enabled.

**Implementation Details:**
- **New helper**: `_ensure_time_triggers_registered()` validates trigger registration and auto-recovers missing listeners.
- **Recovery points**: Called during setup, config updates, and each coordinator refresh cycle.
- **New helper**: `_ensure_min_soc_target(target_soc)` centralizes min-SOC write logic with tolerance/cooldown/error handling.
- **Window start behavior**: `_on_window_start()` now enforces target min-SOC immediately after target determination.

**File:** `custom_components/inverter_charge_night/__init__.py`

## 1. Resolved Deprecation of `async_track_state_change`

**Issue:**
The standard `async_track_state_change` helper is deprecated and will be removed in Home Assistant 2025.5. Using it caused deprecation warnings in the logs.

**Solution:**
Migrated to `async_track_state_change_event`.

**Implementation Details:**
- **Replaced Import**: `homeassistant.helpers.event.async_track_state_change` -> `async_track_state_change_event`.
- **Updated Callbacks**: The callback signature changed from `(entity_id, old_state, new_state)` to `(event)`.
- **Data Extraction**: Inside the callback, the new state is now accessed via `event.data.get("new_state")`.
- **Applied to**: Battery SOC listener (`_battery_soc_listener`) and Inverter Min SOC listener (`_inverter_min_soc_listener`).

**File:** `custom_components/inverter_charge_night/__init__.py`

## 2. Fixed Startup Blocking Issue

**Issue:**
Home Assistant logged a warning that "Something is blocking Home Assistant from wrapping up the start up phase". This is typically caused by creating long-running tasks using `hass.async_create_task` during the startup phase without informing Home Assistant that they are background tasks.

**Solution:**
Converted the periodic verification loop to a background task and improved cleanup.

**Implementation Details:**
- **Used `async_create_background_task`**: Replaced `hass.async_create_task` with `hass.async_create_background_task` for the `_periodic_verification_loop`. This explicitly marks the task as a background process that shouldn't delay startup/shutdown.
- **Improved Cleanup**: Updated `async_unload_entry` to explicitly stop the periodic verification logic (`_stop_periodic_verification`) and remove the inverter listener (`_remove_inverter_min_soc_listener`) to ensure no orphaned tasks remain when the integration is unloaded.

**File:** `custom_components/inverter_charge_night/__init__.py`

## 3. Fixed Duplicate Warning Messages for Inverter Min SOC Restoration

**Issue:**
The system was logging duplicate WARNING messages when restoring the inverter min SOC to the target value. This occurred because:
- The inverter min SOC listener (`_on_inverter_min_soc_change`) detected a deviation and logged a WARNING, then called `_verify_and_restore_min_soc()`.
- The `_verify_and_restore_min_soc()` function performed the same check again and logged its own WARNING before restoring the value.
- Additionally, concurrent calls from the listener and periodic verification task could trigger duplicate warnings.

**Solution:**
Implemented a two-part fix to eliminate duplicate warnings:
1. **Changed listener log level**: Modified `_on_inverter_min_soc_change` to log at DEBUG level instead of WARNING when detecting deviations. The listener now only detects and delegates restoration, without logging warnings.
2. **Added concurrency guard**: Implemented a `_verifying_min_soc` flag to prevent concurrent execution of `_verify_and_restore_min_soc()`. If verification is already in progress, subsequent calls are skipped with a DEBUG log message.

**Implementation Details:**
- **Listener Change**: Changed `_LOGGER.warning()` to `_LOGGER.debug()` in `_on_inverter_min_soc_change` with updated message indicating it's detecting a deviation and delegating restoration.
- **Concurrency Guard**: Added `self._verifying_min_soc` flag initialized in `__init__()` to track verification state.
- **Guard Logic**: Added check at start of `_verify_and_restore_min_soc()` to skip if already verifying, with flag reset in `finally` block to ensure it's always cleared.
- **Result**: Only `_verify_and_restore_min_soc()` logs the WARNING message when it actually restores the value, eliminating duplicate warnings.

**File:** `custom_components/inverter_charge_night/__init__.py`

## 4. Extracted Auto Efficient Charge Logic

**Issue:**
The Auto Efficient Charge Finder was implemented inside the coordinator, making the class monolithic and harder to maintain.

**Solution:**
Extracted the optimization logic into a dedicated helper class to separate responsibilities and improve readability.

**Implementation Details:**
- **New module**: `custom_components/inverter_charge_night/auto_efficiency.py`
- **New class**: `AutoEfficiencyOptimizer`
- **Coordinator delegates**: Auto-efficiency methods now forward to the helper while preserving behavior.

**Files:**
- `custom_components/inverter_charge_night/auto_efficiency.py`
- `custom_components/inverter_charge_night/__init__.py`