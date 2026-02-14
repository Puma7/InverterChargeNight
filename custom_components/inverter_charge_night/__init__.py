"""The Inverter Charge Night integration."""

import asyncio
import logging
import time as time_module
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_UPDATE_INTERVAL,
    CONF_COMMAND_DELAY,
    CONF_BACKUP_MODE_ENTITY,
    CONF_ACTIVE_START_DATE,
    CONF_ACTIVE_END_DATE,
    CONF_CHARGE_POWER_ENTITY,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_START_TIME,
    CONF_END_TIME,
    CONF_USER_MIN_SOC,
    CONF_USER_MAX_SOC,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_PV_FORECAST_ENTITY,
    DEFAULT_END_TIME,
    DEFAULT_SAFE_FALLBACK_SOC,
    DEFAULT_START_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_ACTIVE_START_DATE,
    DEFAULT_ACTIVE_END_DATE,
    AUTO_EFFICIENCY_STEP_W,
    INVERTER_AVAILABILITY_RETRY_INTERVAL_S,
    INVERTER_AVAILABILITY_RETRY_MAX_S,
    MIN_SOC_RESTORE_COOLDOWN_S,
    MIN_SOC_TOLERANCE,
    DOMAIN,
)
from .calculation import calculate_required_soc
from .auto_efficiency import AutoEfficiencyOptimizer

_LOGGER = logging.getLogger(__name__)


def _state_attributes(state: State) -> Mapping[str, Any]:
    """Return state attributes as a typed mapping."""
    attrs = getattr(state, "attributes", {})
    return cast(Mapping[str, Any], attrs)


def _forecast_state_to_kwh(state: State) -> float | None:
    """Parse forecast state value to kWh using unit metadata when available."""
    if state.state in ("unknown", "unavailable", None):
        return None

    try:
        value = float(state.state)
    except (ValueError, TypeError):
        return None

    attrs = _state_attributes(state)
    unit = str(attrs.get("unit_of_measurement", "")).strip().lower()

    # Prefer explicit unit handling, then keep heuristic fallback for unknown units.
    if unit in ("wh", "watt hour", "watt hours"):
        return value / 1000.0
    if unit in ("kwh", "kilowatt hour", "kilowatt hours"):
        return value

    # Backward-compatible fallback when entities do not expose units.
    return value / 1000.0 if value > 1000 else value

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
]


type InverterChargeNightConfigEntry = ConfigEntry[InverterChargeNightCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> bool:
    """Set up Inverter Charge Night from a config entry."""
    # Test-before-setup: verify critical entities are available
    for key in (CONF_BATTERY_SOC_ENTITY, CONF_KOSTAL_MIN_SOC_ENTITY, CONF_KOSTAL_GRID_CHARGE_SWITCH):
        entity_id = entry.data.get(key)
        if entity_id and hass.states.get(entity_id) is None:
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"entity_not_available_{entity_id}",
                is_fixable=False,
                issue_domain=DOMAIN,
                severity=ir.IssueSeverity.ERROR,
                translation_key="entity_not_available",
                translation_placeholders={"entity_id": entity_id},
            )
            raise ConfigEntryNotReady(
                f"Required entity {entity_id} is not yet available"
            )

    coordinator = InverterChargeNightCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    
    entry.runtime_data = coordinator
    
    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Set up time-based triggers
    coordinator.setup_time_triggers()
    coordinator._ensure_time_triggers_registered()
    # Set up optional backup mode listener
    coordinator._setup_backup_mode_listener()
    
    # Add update listener to handle config changes dynamically
    entry.async_on_unload(
        entry.add_update_listener(async_update_entry)
    )
    
    return True


async def async_update_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> None:
    """Handle config entry update."""
    _LOGGER.info("Configuration updated, updating triggers and coordinator")
    
    coordinator: InverterChargeNightCoordinator = entry.runtime_data
    
    # Store old window times before update for comparison
    old_start_time = coordinator.config.get(CONF_START_TIME, DEFAULT_START_TIME)
    old_end_time = coordinator.config.get(CONF_END_TIME, DEFAULT_END_TIME)
    
    # Update coordinator config reference
    coordinator.config = dict(entry.data)
    coordinator.auto_efficient_charge = entry.data.get(CONF_AUTO_EFFICIENT_CHARGE, False)
    coordinator._auto_missing_entities_logged = False
    
    # Update coordinator polling interval
    coordinator.update_interval = timedelta(
        seconds=entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    )
    
    # Update time triggers with new configuration
    try:
        coordinator.update_time_triggers()
    except Exception as e:
        _LOGGER.error("Error updating time triggers: %s", e, exc_info=True)
        # Restore old config if update failed
        coordinator.config = dict(entry.data)  # Still use new data, but log error
        return
    
    # Check if window changed and we need to adjust state
    new_start_time = entry.data.get(CONF_START_TIME, DEFAULT_START_TIME)
    new_end_time = entry.data.get(CONF_END_TIME, DEFAULT_END_TIME)
    
    # If window times changed, check if we need to reset or start
    if old_start_time != new_start_time or old_end_time != new_end_time:
        _LOGGER.info(
            "Window changed from %s-%s to %s-%s, checking current state",
            old_start_time,
            old_end_time,
            new_start_time,
            new_end_time,
        )
        # _check_current_window will handle state adjustment
    
    # Request refresh to recalculate with new settings
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: InverterChargeNightCoordinator = entry.runtime_data
        # CRITICAL: Reset settings before unloading to prevent leaving inverter in bad state
        if coordinator.is_active:
            _LOGGER.warning("Integration unloading during active window - resetting settings")
            try:
                await coordinator._reset_settings()
            except Exception as e:
                _LOGGER.error("Error resetting settings during unload: %s", e, exc_info=True)
                # Continue with unload even if reset fails - we tried our best
        coordinator.remove_time_triggers()
        coordinator._stop_window_check_task()
        coordinator._remove_battery_soc_listener()
        coordinator._remove_inverter_min_soc_listener()
        coordinator._stop_periodic_verification()
    
    return unload_ok


class InverterChargeNightCoordinator(DataUpdateCoordinator):
    """Coordinator for Inverter Charge Night integration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.config: dict[str, Any] = dict(entry.data)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.title}",
            update_interval=timedelta(seconds=self.config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)),
        )
        self.original_min_soc: float | None = None
        self.is_active = False
        self.is_enabled = True  # Integration enabled/disabled via switch
        self.calculated_soc: float | None = None
        self.initial_calculated_soc: float | None = None  # Store SOC calculated at window start
        self.minimum_calculated_soc: float | None = None  # Store minimum SOC value (always <= initial)
        self.target_reached = False
        self._time_triggers: list[Callable[[], None]] = []
        self._last_soc_set: float | None = None  # Track last SOC value set to avoid unnecessary updates
        self._last_soc_set_at: float | None = None  # Monotonic timestamp of last SOC set
        self._window_check_task: asyncio.Task[None] | None = None
        self._time_triggers_bootstrapped = False
        self.override_soc: float | None = None  # Manual override SOC value
        self._original_absolute_charge_power: float | None = None
        self._battery_soc_listener: Callable[[], None] | None = None  # Listener for battery SOC changes
        self._inverter_min_soc_listener: Callable[[], None] | None = None  # Listener for inverter min SOC changes
        self._verification_task: asyncio.Task[None] | None = None  # Periodic verification task
        self._verifying_min_soc = False  # Flag to prevent concurrent verification
        self._backup_mode_listener: Callable[[], None] | None = None  # Listener for backup mode changes
        self.auto_efficient_charge = entry.data.get(CONF_AUTO_EFFICIENT_CHARGE, False)
        self._auto_test_active = False
        self._auto_test_power_w: int | None = None
        self._auto_test_start: datetime | None = None
        self._auto_last_sample_time: datetime | None = None
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0
        self._auto_missing_entities_logged = False
        self._auto_test_start_snapshot: dict[str, float] | None = None
        self._session_active = False
        self._session_start: datetime | None = None
        self._session_start_snapshot: dict[str, float] | None = None
        self._auto_efficiency = AutoEfficiencyOptimizer(self)

    def _parse_time(self, time_str: str | None, default: str) -> tuple[int, int]:
        """Parse time string into (hour, minute) tuple with validation."""
        try:
            if not isinstance(time_str, str) or ":" not in time_str:
                raise ValueError(f"Invalid time format: {time_str}")
                
            hour_str, minute_str = time_str.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
            
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"Time out of range: {time_str}")
                
            return hour, minute
            
        except (ValueError, AttributeError) as err:
            _LOGGER.warning(
                "Invalid time format '%s', using default '%s': %s",
                time_str,
                default,
                err,
            )
            # Prevent infinite recursion - if default is also invalid, use hardcoded fallback
            if time_str == default:
                _LOGGER.error("Default time '%s' is also invalid, using 00:00", default)
                return 0, 0
            return self._parse_time(default, "00:00")

    def _parse_date_optional(self, date_value: str | date | None) -> date | None:
        """Parse optional date value in YYYY-MM-DD format."""
        if not date_value:
            return None
        if isinstance(date_value, date):
            return date_value
        try:
            return date.fromisoformat(date_value)
        except (ValueError, TypeError) as err:
            _LOGGER.warning("Invalid date format '%s' ignored: %s", date_value, err)
            return None

    def _is_within_date_range(self) -> bool:
        """Check if today's date is within the optional active date range."""
        start_date_str = self.config.get(CONF_ACTIVE_START_DATE, DEFAULT_ACTIVE_START_DATE)
        end_date_str = self.config.get(CONF_ACTIVE_END_DATE, DEFAULT_ACTIVE_END_DATE)
        start_date = self._parse_date_optional(start_date_str)
        end_date = self._parse_date_optional(end_date_str)

        if start_date is None and end_date is None:
            return True

        if start_date and end_date and start_date > end_date:
            _LOGGER.warning(
                "Active date range is invalid (%s > %s), ignoring date restriction",
                start_date,
                end_date,
            )
            return True

        today = dt_util.now().date()
        if start_date and today < start_date:
            return False
        if end_date and today > end_date:
            return False
        return True

    def _is_backup_active(self) -> bool:
        """Check if backup mode entity indicates backup/island mode is active."""
        backup_entity = self.config.get(CONF_BACKUP_MODE_ENTITY)
        if not backup_entity:
            return False
        state = self.hass.states.get(backup_entity)
        if not state or state.state in ("unknown", "unavailable", None):
            return False
        state_value = str(state.state).strip().lower()
        if state_value in ("on", "true", "1", "yes", "backup", "active", "island"):
            return True
        if state_value in ("off", "false", "0", "no", "normal", "grid"):
            return False
        return False

    def _get_power_w(self, entity_id: str | None) -> float | None:
        """Return power in W for a given entity, or None if unavailable."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable", None):
            return None
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            return None

        attrs = _state_attributes(state)
        unit = str(attrs.get("unit_of_measurement", "")).lower()
        if unit in ("kw", "kilowatt", "kilowatts"):
            return value * 1000.0
        if unit in ("w", "watt", "watts"):
            return value
        return value

    async def _set_ac_charge_limit_w(self, power_w: int) -> None:
        """Set max AC charge limit if entity is configured."""
        entity_id = self.config.get(CONF_CHARGE_POWER_ENTITY)
        if not entity_id:
            return
        domain = entity_id.split(".")[0]
        service = "set_value"
        if domain not in ("number", "input_number"):
            _LOGGER.warning("Charge power entity %s has unsupported domain %s", entity_id, domain)
            return
        value = float(power_w)
        state = self.hass.states.get(entity_id)
        attrs: Mapping[str, Any] = _state_attributes(state) if state else cast(Mapping[str, Any], {})
        unit = str(attrs.get("unit_of_measurement", "")).lower()
        if unit in ("kw", "kilowatt", "kilowatts"):
            value = value / 1000.0
        try:
            await self.hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id, "value": value},
            )
            _LOGGER.info("Set AC charge limit to %.3f (%s)", value, unit or "unitless")
        except Exception as e:
            _LOGGER.error("Error setting charge power: %s", e, exc_info=True)

    async def _apply_absolute_charge_power_limit(self) -> None:
        """Apply absolute max charge power (AC+DC) during AC charging."""
        entity_id = self.config.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY)
        max_power = self.config.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_W)
        if not entity_id or max_power is None:
            return
        try:
            max_power = float(max_power)
        except (ValueError, TypeError):
            return
        if max_power <= 0:
            return
        if self._original_absolute_charge_power is None:
            state = self.hass.states.get(entity_id)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._original_absolute_charge_power = float(state.state)
                except (ValueError, TypeError):
                    self._original_absolute_charge_power = None

        domain = entity_id.split(".")[0]
        service = "set_value"
        if domain not in ("number", "input_number"):
            _LOGGER.warning("Absolute charge power entity %s has unsupported domain %s", entity_id, domain)
            return
        value = max_power
        state = self.hass.states.get(entity_id)
        attrs: Mapping[str, Any] = _state_attributes(state) if state else cast(Mapping[str, Any], {})
        unit = str(attrs.get("unit_of_measurement", "")).lower()
        if unit in ("kw", "kilowatt", "kilowatts"):
            value = value / 1000.0
        try:
            await self.hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id, "value": value},
            )
            _LOGGER.info("Set absolute charge power to %.3f (%s)", value, unit or "unitless")
        except Exception as e:
            _LOGGER.error("Error setting absolute charge power: %s", e, exc_info=True)

    async def _reset_absolute_charge_power(self) -> None:
        """Reset absolute charge power after AC charging ends."""
        if self._original_absolute_charge_power is None:
            return
        entity_id = self.config.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY)
        if not entity_id:
            return
        domain = entity_id.split(".")[0]
        service = "set_value"
        if domain not in ("number", "input_number"):
            return
        value = self._original_absolute_charge_power
        state = self.hass.states.get(entity_id)
        attrs: Mapping[str, Any] = _state_attributes(state) if state else cast(Mapping[str, Any], {})
        unit = str(attrs.get("unit_of_measurement", "")).lower()
        try:
            await self.hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id, "value": value},
            )
            _LOGGER.info("Reset absolute charge power to original value: %.3f (%s)", value, unit or "unitless")
        except Exception as e:
            _LOGGER.error("Error resetting absolute charge power: %s", e, exc_info=True)
        finally:
            self._original_absolute_charge_power = None

    def _get_auto_efficiency_data(self) -> dict[str, Any]:
        """Load persisted auto efficiency data from entry options."""
        return self._auto_efficiency.get_data()

    def _save_auto_efficiency_data(self, data: dict[str, Any]) -> None:
        """Persist auto efficiency data to entry options."""
        self._auto_efficiency.save_data(data)

    def _round_power_step(self, value_w: float, step_w: int = AUTO_EFFICIENCY_STEP_W) -> int:
        """Round power to nearest step."""
        return self._auto_efficiency.round_power_step(value_w, step_w)

    def _select_next_auto_test_power_w(self) -> int | None:
        """Select next power setpoint for auto efficiency testing."""
        return self._auto_efficiency.select_next_test_power_w()

    def _reset_auto_test_state(self) -> None:
        """Reset current auto test state."""
        self._auto_efficiency.reset_test_state()

    async def _start_auto_test(self, power_w: int) -> None:
        """Start auto efficiency test at given power."""
        await self._auto_efficiency.start_test(power_w)

    def _finalize_auto_test(self) -> None:
        """Finalize auto test and persist efficiency result."""
        self._auto_efficiency.finalize_test()

    def _start_charge_session(self) -> None:
        """Begin tracking a regular charge session."""
        self._auto_efficiency.start_charge_session()

    def _finalize_charge_session(self) -> None:
        """Finalize the regular charge session and persist the result."""
        self._auto_efficiency.finalize_charge_session()

    def _get_session_data(self) -> dict[str, Any]:
        """Load persisted charge session data."""
        return self._auto_efficiency.get_session_data()

    async def _handle_auto_charge(self) -> None:
        """Handle auto efficient charging logic."""
        await self._auto_efficiency.handle_auto_charge()

    def _is_time_between(
        self, check_time: time, start_time: time, end_time: time
    ) -> bool:
        """Check if a time is between two other times, handling overnight ranges."""
        if start_time < end_time:
            return start_time <= check_time <= end_time
        return check_time >= start_time or check_time <= end_time

    def _mark_soc_set(self, target_soc: float) -> None:
        """Record when the inverter min SOC was set."""
        self._last_soc_set = target_soc
        self._last_soc_set_at = time_module.monotonic()

    def _is_within_min_soc_cooldown(self) -> bool:
        """Return True if we're within the min SOC restore cooldown."""
        if self._last_soc_set_at is None:
            return False
        return (time_module.monotonic() - self._last_soc_set_at) < MIN_SOC_RESTORE_COOLDOWN_S

    def _get_window_target_soc(self) -> float | None:
        """Return effective target SOC for the active window."""
        if self.minimum_calculated_soc is not None:
            return float(self.minimum_calculated_soc)
        if self.override_soc is not None:
            return float(self.override_soc)
        if self.initial_calculated_soc is not None:
            return float(self.initial_calculated_soc)
        if self.calculated_soc is not None:
            return float(self.calculated_soc)
        return None

    async def _ensure_min_soc_target(self, target_soc: float) -> bool:
        """Ensure inverter min SOC matches target, independent of charge state."""
        kostal_min_soc_entity = self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY)
        if not kostal_min_soc_entity:
            _LOGGER.warning("No min SOC entity configured - cannot enforce min SOC target")
            return False

        try:
            state = self.hass.states.get(kostal_min_soc_entity)
            current_min_soc: float | None = None
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current_min_soc = float(state.state)
                except (ValueError, TypeError):
                    current_min_soc = None

            if current_min_soc is not None and abs(current_min_soc - target_soc) <= MIN_SOC_TOLERANCE:
                self._last_soc_set = target_soc
                _LOGGER.debug("Min SOC already at target %.1f%%", target_soc)
                return True

            if self._last_soc_set is not None and abs(self._last_soc_set - target_soc) <= MIN_SOC_TOLERANCE:
                if self._is_within_min_soc_cooldown():
                    _LOGGER.debug(
                        "Min SOC target %.1f%% was set recently, skipping duplicate write",
                        target_soc,
                    )
                    return True

            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": kostal_min_soc_entity, "value": target_soc},
            )
            self._mark_soc_set(target_soc)
            _LOGGER.info(
                "Enforced min SOC target %.1f%% (was %s)",
                target_soc,
                f"{current_min_soc:.1f}%" if current_min_soc is not None else "unknown",
            )
            return True
        except Exception as err:
            _LOGGER.error("Failed to enforce min SOC target %.1f%%: %s", target_soc, err, exc_info=True)
            return False

    def _schedule_window_check(self) -> None:
        """Schedule a window state check task, cancelling any previous one."""
        self._stop_window_check_task()

        if hasattr(self.hass, "async_create_background_task"):
            self._window_check_task = self.hass.async_create_background_task(
                self._check_current_window(),
                "inverter_charge_night_window_check",
            )
        else:
            self._window_check_task = self.hass.async_create_task(
                self._check_current_window()
            )

    def _stop_window_check_task(self) -> None:
        """Stop any pending window check task."""
        if self._window_check_task and not self._window_check_task.done():
            self._window_check_task.cancel()
        self._window_check_task = None

    def _ensure_time_triggers_registered(self) -> None:
        """Ensure start/end time triggers are present, recreate if missing."""
        expected_triggers = 2  # window start and window end
        current_triggers = len(self._time_triggers)
        if not self._time_triggers_bootstrapped:
            _LOGGER.debug(
                "Skipping trigger self-heal before initial trigger bootstrap (%d/%d).",
                current_triggers,
                expected_triggers,
            )
            return
        if current_triggers >= expected_triggers:
            return

        _LOGGER.warning(
            "Time triggers missing (%d/%d). Re-registering charge window triggers.",
            current_triggers,
            expected_triggers,
        )
        self.setup_time_triggers()

        recovered_triggers = len(self._time_triggers)
        if recovered_triggers >= expected_triggers:
            _LOGGER.info(
                "Time trigger self-heal successful (%d/%d).",
                recovered_triggers,
                expected_triggers,
            )
        else:
            _LOGGER.error(
                "Time trigger self-heal failed (%d/%d). Check trigger setup/logs.",
                recovered_triggers,
                expected_triggers,
            )

    def setup_time_triggers(self) -> None:
        """Set up time-based triggers for window start/end."""
        # Clear any existing triggers
        self.remove_time_triggers()
        
        # Get and validate times
        start_time_str = str(self.config.get(CONF_START_TIME, DEFAULT_START_TIME))
        end_time_str = str(self.config.get(CONF_END_TIME, DEFAULT_END_TIME))
        
        try:
            # Parse and validate times
            start_hour, start_minute = self._parse_time(start_time_str, DEFAULT_START_TIME)
            end_hour, end_minute = self._parse_time(end_time_str, DEFAULT_END_TIME)
            
            # Create time objects for comparison
            # Log the active window
            _LOGGER.info(
                "Setting up charge window: %02d:%02d - %02d:%02d",
                start_hour,
                start_minute,
                end_hour,
                end_minute,
            )
            
            # Trigger at start time
            self._time_triggers.append(
                async_track_time_change(
                    self.hass,
                    self._on_window_start,
                    hour=start_hour,
                    minute=start_minute,
                    second=0,
                )
            )
            
            # Trigger at end time
            self._time_triggers.append(
                async_track_time_change(
                    self.hass,
                    self._on_window_end,
                    hour=end_hour,
                    minute=end_minute,
                    second=0,
                )
            )
            
            # Check if we're already in the active window
            self._schedule_window_check()
            self._time_triggers_bootstrapped = True
            
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Failed to set up time triggers: %s", err, exc_info=True)
            self.remove_time_triggers()  # Clean up any partial setup

    def remove_time_triggers(self) -> None:
        """Remove time-based triggers."""
        self._stop_window_check_task()
        for trigger in self._time_triggers:
            trigger()
        self._time_triggers.clear()

    def update_time_triggers(self) -> None:
        """Update time triggers when configuration changes."""
        _LOGGER.info("Updating time triggers with new configuration")
        
        # Store old battery SOC entity to check if it changed
        old_battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
        
        # Remove old triggers first
        self.remove_time_triggers()
        
        # Note: config reference is updated in async_update_entry before calling this
        # So self.config should already be updated, but ensure it's synced
        if dict(self.entry.data) != self.config:
            self.config = dict(self.entry.data)
        
        # Set up new triggers with updated times
        self.setup_time_triggers()
        self._ensure_time_triggers_registered()
        
        # If battery SOC entity changed and we're active, update the listener
        new_battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
        if self.is_active and old_battery_soc_entity != new_battery_soc_entity:
            _LOGGER.info("Battery SOC entity changed from %s to %s, updating listener", old_battery_soc_entity, new_battery_soc_entity)
            self._setup_battery_soc_listener()
        
        # Check if we need to adjust current state based on new window
        # This will handle cases where:
        # - We were active but are no longer in new window (will reset)
        # - We were not active but are now in new window (will start)
        # - We were active and still in new window (will continue)
        self._schedule_window_check()

    async def _check_current_window(self) -> None:
        """Check if we're currently in the active window and adjust state if needed."""
        try:
            if self._is_backup_active():
                if self.is_active:
                    _LOGGER.info("Backup mode active - stopping window and resetting settings")
                    await self._on_window_end(dt_util.now())
                else:
                    _LOGGER.info("Backup mode active - skipping window start and inverter control")
                return

            if not self._is_within_date_range():
                if self.is_active:
                    _LOGGER.info("Outside active date range - stopping window")
                    await self._on_window_end(dt_util.now())
                else:
                    _LOGGER.debug("Outside active date range - not starting window")
                return

            now_dt = dt_util.now()
            now = now_dt.time()
            start_time_str = str(self.config.get(CONF_START_TIME, DEFAULT_START_TIME))
            end_time_str = str(self.config.get(CONF_END_TIME, DEFAULT_END_TIME))
            
            # Parse times with validation
            start_hour, start_minute = self._parse_time(start_time_str, DEFAULT_START_TIME)
            end_hour, end_minute = self._parse_time(end_time_str, DEFAULT_END_TIME)
            
            start_time_obj = time(start_hour, start_minute)
            end_time_obj = time(end_hour, end_minute)
            
            in_window = self._is_time_between(now, start_time_obj, end_time_obj)
            
            # Handle state transitions
            if in_window and not self.is_active:
                # We're in the window but not active - start it
                _LOGGER.info("Currently in active window but not active - starting")
                await self._on_window_start(now_dt)
            elif not in_window and self.is_active:
                # We're active but no longer in the window - reset
                _LOGGER.info("No longer in active window but still active - resetting")
                await self._on_window_end(now_dt)
            elif in_window and self.is_active:
                # We're already active and in window - ensure listeners are set up (e.g., after restart)
                if not self._battery_soc_listener:
                    self._setup_battery_soc_listener()
                if not self._inverter_min_soc_listener:
                    self._setup_inverter_min_soc_listener()
                if not self._verification_task or self._verification_task.done():
                    self._start_periodic_verification()
                # Also verify immediately that inverter min SOC matches our target
                await self._verify_and_restore_min_soc()
            # If (not in_window and not is_active) - state is correct
                
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Error checking current window: %s", err, exc_info=True)

    async def _on_window_start(self, now: datetime) -> None:
        """Handle window start."""
        if not self.is_enabled:
            _LOGGER.debug("Window started but integration is disabled")
            return
        if self._is_backup_active():
            _LOGGER.info("Window start ignored - backup mode active")
            return
        if not self._is_within_date_range():
            _LOGGER.info("Window start ignored - outside active date range")
            return
        _LOGGER.info("Night charge window started")
        self.is_active = True
        self.target_reached = False
        
        # Calculate and store initial SOC for this charging period
        await self._calculate_initial_soc()

        # CRITICAL: Always enforce min SOC at window start, even if no charging starts yet.
        # This guarantees overnight reserve behavior for cases where current SOC is above target.
        window_target_soc = self._get_window_target_soc()
        if window_target_soc is not None:
            await self._ensure_min_soc_target(window_target_soc)
        else:
            _LOGGER.warning(
                "Window started but no target SOC available yet - cannot enforce min SOC immediately"
            )
        
        # Set up battery SOC listener for more frequent target checks
        self._setup_battery_soc_listener()
        
        # Set up inverter min SOC listener to detect external changes
        self._setup_inverter_min_soc_listener()
        
        # Start periodic verification task (every 15 minutes)
        self._start_periodic_verification()
        
        # Start tracking charge session for efficiency measurement
        self._start_charge_session()
        
        await self.async_request_refresh()

    async def _calculate_initial_soc(self) -> None:
        """Calculate and store the initial SOC for this charging period."""
        try:
            # Get configuration values
            battery_capacity = float(self.config.get(CONF_BATTERY_CAPACITY, 10.0))
            error_margin = float(self.config.get(CONF_FORECAST_ERROR_MARGIN, 10.0))
            user_min_soc = float(self.config.get(CONF_USER_MIN_SOC, 8.0))
            user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, 100.0))
            default_min_soc = float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
            
            # CRITICAL: Check if we're recovering from a restart during active window
            # If min SOC on inverter is already set to a value different from default,
            # use that value to preserve the target that was active before restart
            # IMPORTANT: Inverter may take 1-2 minutes to load after HA restart, so we retry
            kostal_min_soc_entity = self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY)
            preserved_soc = None
            
            if kostal_min_soc_entity:
                # Retry mechanism: Wait for inverter to become available (max 3 minutes)
                max_retry_time = INVERTER_AVAILABILITY_RETRY_MAX_S
                retry_interval = INVERTER_AVAILABILITY_RETRY_INTERVAL_S
                retry_count = 0
                max_retries = max_retry_time // retry_interval
                
                while retry_count < max_retries:
                    try:
                        state = self.hass.states.get(kostal_min_soc_entity)
                        if state and state.state not in ("unknown", "unavailable"):
                            # Entity is available - check if we can preserve the value
                            try:
                                current_min_soc = float(state.state)
                                # If current value is significantly different from default and within user bounds,
                                # it's likely the target we set before restart - preserve it
                                if (abs(current_min_soc - default_min_soc) > 1.0 and 
                                    user_min_soc <= current_min_soc <= user_max_soc):
                                    preserved_soc = current_min_soc
                                    _LOGGER.info(
                                        "Detected restart during active window - preserving existing min SOC %.1f%% "
                                        "(was set before restart, default is %.1f%%, waited %d seconds)",
                                        preserved_soc,
                                        default_min_soc,
                                        retry_count * retry_interval
                                    )
                                    break  # Found preserved SOC, exit retry loop
                                else:
                                    # Entity is available but value doesn't match criteria (e.g., at default)
                                    # No need to keep retrying - entity is ready, just not preserving this value
                                    if retry_count > 0:
                                        _LOGGER.debug(
                                            "Inverter entity available but min SOC (%.1f%%) doesn't match preservation criteria "
                                            "(default: %.1f%%), will recalculate from forecast",
                                            current_min_soc,
                                            default_min_soc
                                        )
                                    break  # Entity available, exit retry loop (even if not preserving)
                            except (ValueError, TypeError):
                                # Entity available but value invalid - exit retry loop
                                break
                        
                        # If entity is still unavailable and we haven't exceeded max retries, wait and retry
                        if preserved_soc is None and retry_count < max_retries - 1:
                            if retry_count == 0:
                                _LOGGER.info(
                                    "Inverter entity %s not yet available after restart, waiting up to %d seconds...",
                                    kostal_min_soc_entity,
                                    max_retry_time
                                )
                            await asyncio.sleep(retry_interval)
                            retry_count += 1
                        else:
                            break  # Either found SOC or exhausted retries
                    except Exception as e:
                        _LOGGER.debug("Error checking current min SOC for restart recovery (attempt %d): %s", retry_count + 1, e)
                        if retry_count < max_retries - 1:
                            await asyncio.sleep(retry_interval)
                            retry_count += 1
                        else:
                            break
                
                # Log if we couldn't recover the preserved SOC
                if preserved_soc is None and retry_count > 0:
                    _LOGGER.warning(
                        "Inverter entity %s did not become available within %d seconds after restart. "
                        "Will recalculate from forecast instead of preserving previous target.",
                        kostal_min_soc_entity,
                        retry_count * retry_interval
                    )
            
            # If we found a preserved SOC value, use it instead of recalculating
            if preserved_soc is not None:
                self.initial_calculated_soc = preserved_soc
                self.minimum_calculated_soc = preserved_soc  # Initialize minimum with preserved value
                self.calculated_soc = preserved_soc
                _LOGGER.info(
                    "Using preserved SOC from inverter: %.1f%% (restart recovery)",
                    preserved_soc
                )
                return
            
            # Otherwise, calculate normally from forecast
            # Get forecast data
            forecast_energy = 0.0
            forecast_available = False  # Track if forecast entity was actually available
            pv_forecast_entity = self.config.get(CONF_PV_FORECAST_ENTITY)
            if pv_forecast_entity:
                state = self.hass.states.get(pv_forecast_entity)
                if state:
                    # Check if entity state is available (not unknown/unavailable)
                    if state.state not in ("unknown", "unavailable", None):
                        parsed_forecast = _forecast_state_to_kwh(state)
                        if parsed_forecast is not None:
                            forecast_available = True
                            forecast_energy = parsed_forecast
                        else:
                            forecast_available = False
                    else:
                        attrs = _state_attributes(state)
                        if "forecast" in attrs:
                            # Solcast might have forecast in attributes
                            forecast_raw = attrs.get("forecast", [])
                            forecast_items: list[Mapping[str, Any]] = (
                                cast(list[Mapping[str, Any]], forecast_raw)
                                if isinstance(forecast_raw, list)
                                else []
                            )
                            if forecast_items:
                                forecast_available = True
                                # Sum up today's forecast (assuming Wh units)
                                forecast_energy = sum(
                                    float(
                                        item.get(
                                            "wh",
                                            item.get("pv_power_forecast", 0),
                                        )
                                    )
                                    / 1000.0
                                    for item in forecast_items
                                )
                        # Also check for common Solcast attribute names
                        elif "today_forecast" in attrs:
                            try:
                                forecast_energy = float(attrs.get("today_forecast", 0))
                                forecast_available = True
                            except (ValueError, TypeError):
                                pass
                        elif "forecast_today" in attrs:
                            try:
                                forecast_energy = float(attrs.get("forecast_today", 0))
                                forecast_available = True
                            except (ValueError, TypeError):
                                pass
            
            # Calculate required SOC
            calculated_soc = calculate_required_soc(
                forecast_energy,
                battery_capacity,
                error_margin,
                user_min_soc,
                user_max_soc,
            )
            
            # SAFETY: Only use safe fallback if forecast entity was UNAVAILABLE (not if legitimately 0 kWh)
            # This prevents charging to 100% when forecast data is missing, but allows normal calculation
            # when forecast is legitimately 0 kWh (e.g., winter, no sun expected)
            if calculated_soc is None:
                # Calculation failed - use safe fallback
                safe_fallback = max(user_min_soc, min(DEFAULT_SAFE_FALLBACK_SOC, user_max_soc))
                calculated_soc = safe_fallback
                _LOGGER.warning(
                    "SOC calculation failed, using safe fallback: %.1f%% (prevents charging to 100%%)",
                    safe_fallback
                )
            elif not forecast_available and calculated_soc >= user_max_soc - 1.0:
                # Forecast entity UNAVAILABLE (not just 0 kWh) and would charge to max - use safe fallback
                safe_fallback = max(user_min_soc, min(DEFAULT_SAFE_FALLBACK_SOC, user_max_soc))
                calculated_soc = safe_fallback
                _LOGGER.warning(
                    "Forecast entity unavailable would result in %.1f%% target. Using safe fallback: %.1f%% "
                    "(prevents overcharging to 100%%)",
                    user_max_soc,
                    safe_fallback
                )
            
            self.initial_calculated_soc = calculated_soc
            self.minimum_calculated_soc = calculated_soc  # Initialize minimum with initial value
            self.calculated_soc = calculated_soc  # Also update current for display
            _LOGGER.info(
                "Initial SOC calculated for this charging period: %.1f%% (based on %.2f kWh forecast)",
                calculated_soc,
                forecast_energy
            )
                
        except (ValueError, TypeError) as e:
            _LOGGER.error("Error calculating initial SOC: %s", e)
            self.initial_calculated_soc = None

    async def _on_window_end(self, now: datetime) -> None:
        """Handle window end."""
        _LOGGER.info("Night charge window ended, resetting settings")
        try:
            await self._reset_settings()
        except Exception as e:
            _LOGGER.error("Error resetting settings at window end: %s", e, exc_info=True)
            # Continue to reset state flags even if reset fails
        finally:
            # CRITICAL: Always reset state flags, even if reset operation failed
            self._finalize_auto_test()
            self._finalize_charge_session()
            self.is_active = False
            self.target_reached = False
            self.initial_calculated_soc = None  # Reset initial SOC for next charging period
            self.minimum_calculated_soc = None  # Reset minimum SOC for next charging period
            self.override_soc = None  # Clear override when window ends
            self._remove_battery_soc_listener()  # Remove SOC listener
            self._remove_inverter_min_soc_listener()  # Remove inverter min SOC listener
            self._stop_periodic_verification()  # Stop periodic verification
            await self.async_request_refresh()

    async def _reset_settings(self) -> None:
        """Reset Kostal settings to original values."""
        # CRITICAL: Use stored original value if available, otherwise use configured default
        reset_min_soc = self.original_min_soc if self.original_min_soc is not None else float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
        kostal_min_soc_entity = self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY)
        kostal_grid_charge_switch = self.config.get(CONF_KOSTAL_GRID_CHARGE_SWITCH)
        
        reset_success = False
        
        # Reset min SOC
        if kostal_min_soc_entity:
            try:
                state = self.hass.states.get(kostal_min_soc_entity)
                if state:
                    await self.hass.services.async_call(
                        "number",
                        "set_value",
                        {"entity_id": kostal_min_soc_entity, "value": reset_min_soc},
                    )
                    _LOGGER.info(
                        "Reset Kostal min SOC to %.1f%% (original: %s)",
                        reset_min_soc,
                        "stored" if self.original_min_soc is not None else "configured default",
                    )
                    reset_success = True
                else:
                    _LOGGER.error("Cannot reset min SOC - entity state unavailable")
            except Exception as e:
                _LOGGER.error("Error resetting min SOC: %s", e, exc_info=True)
        else:
            _LOGGER.warning("No min SOC entity configured - cannot reset")
        
        # Turn off grid charge
        if kostal_grid_charge_switch:
            try:
                state = self.hass.states.get(kostal_grid_charge_switch)
                if state:
                    if state.state == "on":
                        await self.hass.services.async_call(
                            "switch",
                            "turn_off",
                            {"entity_id": kostal_grid_charge_switch},
                        )
                        _LOGGER.info("Turned off Kostal grid charge switch")
                    else:
                        _LOGGER.debug("Grid charge switch already off")
                else:
                    _LOGGER.error("Cannot reset grid charge - entity state unavailable")
            except Exception as e:
                _LOGGER.error("Error turning off grid charge: %s", e, exc_info=True)
        else:
            _LOGGER.warning("No grid charge switch configured - cannot reset")
        
        # Reset stored original value and tracking after successful reset
        if reset_success:
            self.original_min_soc = None
            self._last_soc_set = None
            self.override_soc = None  # Clear override when resetting
        await self._reset_absolute_charge_power()

    async def _apply_control_logic(
        self,
        target_soc: float,
        calculated_soc: float,
        can_check_soc: bool,
        battery_soc_entity: str | None,
    ) -> float | None:
        """Apply inverter control logic after calculating target SOC."""
        # CRITICAL: Check if battery SOC already exceeds target before controlling
        # If so, turn off charging immediately
        current_soc = None
        if can_check_soc and battery_soc_entity is not None:
            battery_state = self.hass.states.get(battery_soc_entity)
            if battery_state and battery_state.state not in ("unknown", "unavailable"):
                try:
                    current_battery_soc = float(battery_state.state)
                    current_soc = current_battery_soc
                    if current_battery_soc >= target_soc:
                        if not self.target_reached:
                            _LOGGER.info(
                                "Battery SOC (%.1f%%) already exceeds target (%.1f%%) at startup, turning off charging",
                                current_battery_soc,
                                target_soc,
                            )
                            await self._stop_grid_charging()
                            self.target_reached = True
                        # Don't proceed with control if already at target
                        return current_soc
                except (ValueError, TypeError):
                    pass

        # Control Kostal entities if we have a target SOC and can verify battery SOC
        # If battery SOC is unavailable, wait for it to become available before charging
        if not self.target_reached:
            if can_check_soc:
                await self._control_kostal(target_soc)
            else:
                _LOGGER.warning(
                    "Battery SOC entity %s is unavailable - skipping Kostal control to prevent "
                    "unintended charging. Will retry on next update.",
                    battery_soc_entity,
                )

        # Auto efficient charge handling (optional)
        await self._handle_auto_charge()

        # Check if target is reached (use override if set, otherwise calculated)
        if battery_soc_entity:
            state = self.hass.states.get(battery_soc_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current_soc = float(state.state)
                    # Use minimum SOC for target check (always the lowest value we want to maintain)
                    check_target = self.minimum_calculated_soc
                    if check_target is not None and current_soc >= check_target:
                        # SAFETY: Log if we significantly exceed target, but still stop once.
                        if current_soc > check_target + 5.0:
                            _LOGGER.warning(
                                "SAFETY: Battery SOC (%.1f%%) significantly exceeds target (%.1f%%), forcing stop",
                                current_soc,
                                check_target,
                            )
                        if not self.target_reached:
                            _LOGGER.info(
                                "Target SOC reached: %.1f%% >= %.1f%% (target: %s)",
                                current_soc,
                                check_target,
                                "override" if self.override_soc is not None else "calculated",
                            )
                            await self._stop_grid_charging()
                            self.target_reached = True
                except (ValueError, TypeError):
                    pass

        return current_soc

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from entities and update coordinator."""
        # Self-heal trigger registration in case listeners got lost after runtime issues/reload.
        self._ensure_time_triggers_registered()

        if not self.is_enabled or not self.is_active:
            return {
                "calculated_soc": None,
                "is_active": False,
                "target_reached": False,
            }
        if self._is_backup_active():
            _LOGGER.info("Backup mode active - stopping window and resetting settings")
            await self._on_window_end(dt_util.now())
            return {
                "calculated_soc": None,
                "is_active": False,
                "target_reached": False,
            }
        if not self._is_within_date_range():
            _LOGGER.info("Outside active date range during update - resetting settings")
            await self._on_window_end(dt_util.now())
            return {
                "calculated_soc": None,
                "is_active": False,
                "target_reached": False,
            }
        
        # Get forecast data
        pv_forecast_entity = self.config.get(CONF_PV_FORECAST_ENTITY)
        try:
            battery_capacity = float(self.config.get(CONF_BATTERY_CAPACITY, 10.0))
            error_margin = float(self.config.get(CONF_FORECAST_ERROR_MARGIN, 10.0))
            user_min_soc = float(self.config.get(CONF_USER_MIN_SOC, 8.0))
            user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, 100.0))
        except (ValueError, TypeError) as e:
            _LOGGER.error("Error parsing configuration values: %s", e)
            return {
                "calculated_soc": None,
                "is_active": False,
                "target_reached": False,
            }
        
        forecast_energy = 0.0
        forecast_available = False  # Track if forecast entity was actually available
        if pv_forecast_entity:
            state = self.hass.states.get(pv_forecast_entity)
            if state:
                # Check if entity state is available (not unknown/unavailable)
                if state.state not in ("unknown", "unavailable", None):
                    parsed_forecast = _forecast_state_to_kwh(state)
                    if parsed_forecast is not None:
                        forecast_available = True
                        forecast_energy = parsed_forecast
                    else:
                        forecast_available = False
                else:
                    attrs = _state_attributes(state)
                    if "forecast" in attrs:
                        # Solcast might have forecast in attributes
                        forecast_raw = attrs.get("forecast", [])
                        forecast_items: list[Mapping[str, Any]] = (
                            cast(list[Mapping[str, Any]], forecast_raw)
                            if isinstance(forecast_raw, list)
                            else []
                        )
                        if forecast_items:
                            forecast_available = True
                            # Sum up today's forecast (assuming Wh units)
                            forecast_energy = sum(
                                float(
                                    item.get(
                                        "wh",
                                        item.get("pv_power_forecast", 0),
                                    )
                                )
                                / 1000.0
                                for item in forecast_items
                            )
                    # Also check for common Solcast attribute names
                    elif "today_forecast" in attrs:
                        try:
                            forecast_energy = float(attrs.get("today_forecast", 0))
                            forecast_available = True
                        except (ValueError, TypeError):
                            pass
                    elif "forecast_today" in attrs:
                        try:
                            forecast_energy = float(attrs.get("forecast_today", 0))
                            forecast_available = True
                        except (ValueError, TypeError):
                            pass
        
        # Use initial SOC calculated at window start, or recalculate if initial failed
        calculated_soc: float | None
        if self.initial_calculated_soc is not None:
            calculated_soc = self.initial_calculated_soc
            _LOGGER.debug("Using stored initial SOC: %.1f%%", calculated_soc)
        else:
            # Fallback: recalculate if initial calculation failed
            _LOGGER.warning("Initial SOC not available, recalculating as fallback")
            calculated_soc = calculate_required_soc(
                forecast_energy,
                battery_capacity,
                error_margin,
                user_min_soc,
                user_max_soc,
            )
            
            # SAFETY: Only use safe fallback if forecast entity was UNAVAILABLE (not if legitimately 0 kWh)
            # This prevents charging to 100% when forecast data is missing, but allows normal calculation
            # when forecast is legitimately 0 kWh (e.g., winter, no sun expected)
            if calculated_soc is None:
                # Calculation failed - use safe fallback
                safe_fallback = max(user_min_soc, min(DEFAULT_SAFE_FALLBACK_SOC, user_max_soc))
                calculated_soc = safe_fallback
                _LOGGER.warning(
                    "SOC calculation failed, using safe fallback: %.1f%% (prevents charging to 100%%)",
                    safe_fallback
                )
            elif not forecast_available and calculated_soc >= user_max_soc - 1.0:
                # Forecast entity UNAVAILABLE (not just 0 kWh) and would charge to max - use safe fallback
                safe_fallback = max(user_min_soc, min(DEFAULT_SAFE_FALLBACK_SOC, user_max_soc))
                calculated_soc = safe_fallback
                _LOGGER.warning(
                    "Forecast entity unavailable would result in %.1f%% target. Using safe fallback: %.1f%% "
                    "(prevents overcharging to 100%%)",
                    user_max_soc,
                    safe_fallback
                )
            
            # Store the successful fallback calculation
            self.initial_calculated_soc = calculated_soc
            _LOGGER.info("Fallback SOC calculated and stored: %.1f%%", calculated_soc)
        
        # Update display SOC to match calculated SOC
        self.calculated_soc = calculated_soc
        
        # Use override SOC if set, otherwise use calculated SOC
        # CRITICAL: Always use minimum value (initial or override, whichever is lower)
        if self.override_soc is not None:
            # If override is set, use it and update minimum if it's lower
            target_soc = self.override_soc
            if self.minimum_calculated_soc is None or self.override_soc < self.minimum_calculated_soc:
                self.minimum_calculated_soc = self.override_soc
                _LOGGER.info("Updated minimum SOC to %.1f%% (override is lower)", self.override_soc)
        else:
            # Use calculated SOC, but ensure minimum is tracked
            target_soc = calculated_soc
            if self.minimum_calculated_soc is None or calculated_soc < self.minimum_calculated_soc:
                self.minimum_calculated_soc = calculated_soc
                _LOGGER.info("Updated minimum SOC to %.1f%% (calculated is lower)", calculated_soc)
        
        # SAFETY: Before controlling Kostal, verify we can check battery SOC
        # This prevents turning on grid charge switch if battery SOC is unavailable
        # (e.g., after restart when battery entity hasn't loaded yet)
        battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
        can_check_soc = False
        if battery_soc_entity is not None:
            state = self.hass.states.get(battery_soc_entity)
            if state and state.state not in ("unknown", "unavailable"):
                can_check_soc = True
        
        current_soc = await self._apply_control_logic(
            target_soc,
            calculated_soc,
            can_check_soc,
            battery_soc_entity,
        )
        
        return {
            "calculated_soc": calculated_soc,
            "is_active": self.is_active,
            "target_reached": self.target_reached,
            "current_soc": current_soc,
        }

    async def _control_kostal(self, target_soc: float) -> None:
        """Control Kostal entities based on calculated SOC."""
        if self._is_backup_active():
            _LOGGER.info("Backup mode active - skipping Kostal control")
            return
        # CRITICAL: Validate target_soc before applying
        user_min_soc = float(self.config.get(CONF_USER_MIN_SOC, 8.0))
        user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, 100.0))
        
        if not (user_min_soc <= target_soc <= user_max_soc):
            _LOGGER.error(
                "Target SOC %.1f%% is outside allowed range [%.1f%%, %.1f%%] - not applying",
                target_soc,
                user_min_soc,
                user_max_soc,
            )
            return
        
        kostal_min_soc_entity = self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY)
        kostal_grid_charge_switch = self.config.get(CONF_KOSTAL_GRID_CHARGE_SWITCH)
        
        # Check current SOC before starting charging (doesn't delay commands)
        # CRITICAL: If battery SOC is unavailable, we should NOT turn on grid charge
        # to prevent charging when we can't verify the current state (e.g., after restart)
        battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
        current_soc = None
        should_skip_charging = False
        
        if battery_soc_entity:
            state = self.hass.states.get(battery_soc_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current_soc = float(state.state)
                    if current_soc >= target_soc:
                        _LOGGER.info(
                            "Skip charging: currentSOC (%.1f%%) >= targetSOC (%.1f%%)",
                            current_soc,
                            target_soc
                        )
                        should_skip_charging = True
                except (ValueError, TypeError):
                    pass
            else:
                # Battery SOC entity is unavailable - don't turn on grid charge for safety
                _LOGGER.warning(
                    "Battery SOC entity %s is unavailable - skipping grid charge activation "
                    "to prevent unintended charging when current SOC cannot be verified",
                    battery_soc_entity
                )
                should_skip_charging = True  # Safety: don't charge if we can't check SOC
        
        # Track if we need to set min SOC
        need_to_set_min_soc = False
        min_soc_current_value = None
        
        # Prepare min SOC setting (check if needed, store original value)
        if kostal_min_soc_entity:
            try:
                # Store original value if not stored yet
                if self.original_min_soc is None:
                    state = self.hass.states.get(kostal_min_soc_entity)
                    if state and state.state not in ("unknown", "unavailable"):
                        try:
                            self.original_min_soc = float(state.state)
                            
                            # SAFETY: Sanity check the captured value
                            # If we capture a value much higher than default, we might be reading our own
                            # previously set value after a restart. For safety, prefer the default.
                            default_min = float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
                            if self.original_min_soc > default_min + 10.0:
                                _LOGGER.warning(
                                    "Captured original SOC (%.1f%%) is significantly higher than default (%.1f%%). "
                                    "Assuming restart during active window - using default as safe original value.",
                                    self.original_min_soc,
                                    default_min,
                                )
                                self.original_min_soc = default_min
                            else:
                                _LOGGER.info("Stored original min SOC: %.1f%%", self.original_min_soc)
                        except (ValueError, TypeError):
                            # Use configured default if we can't read current value
                            self.original_min_soc = float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
                            _LOGGER.warning(
                                "Could not read original min SOC, using configured default: %.1f%%",
                                self.original_min_soc,
                            )
                    else:
                        # Use configured default if entity unavailable
                        self.original_min_soc = float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
                        _LOGGER.warning(
                            "Min SOC entity unavailable, using configured default: %.1f%%",
                            self.original_min_soc,
                        )
                
                # Check if we need to set min SOC
                state = self.hass.states.get(kostal_min_soc_entity)
                if state and state.state not in ("unknown", "unavailable"):
                    try:
                        min_soc_current_value = float(state.state)
                    except (ValueError, TypeError):
                        pass
                
                # Only update if value changed significantly (avoid unnecessary service calls and EEPROM wear)
                # CRITICAL: Threshold prevents excessive inverter writes
                if min_soc_current_value is None or abs(min_soc_current_value - target_soc) > MIN_SOC_TOLERANCE:
                    # Also check if we just set this value (prevent rapid updates)
                    if self._last_soc_set is None or abs(self._last_soc_set - target_soc) > MIN_SOC_TOLERANCE:
                        need_to_set_min_soc = True
                    elif self._is_within_min_soc_cooldown():
                        _LOGGER.debug("Min SOC already set to %.1f%% recently, skipping update", target_soc)
                        self._last_soc_set = target_soc
                    else:
                        need_to_set_min_soc = True
                else:
                    _LOGGER.debug("Min SOC already at target: %.1f%%", target_soc)
                    self._last_soc_set = target_soc
            except Exception as e:
                _LOGGER.error("Error preparing min SOC: %s", e, exc_info=True)
        
        # CRITICAL: Send commands together to avoid double DC checks
        # If both min SOC and grid charge need to be set, send them with minimal delay (0.1s)
        # so the inverter processes them together and only does DC checks once
        if need_to_set_min_soc and kostal_min_soc_entity:
            # Set min SOC first
            min_soc_set_ok = await self._ensure_min_soc_target(target_soc)
            
            # Immediately send grid charge command (configurable delay) so inverter processes both together
            if min_soc_set_ok and kostal_grid_charge_switch and not should_skip_charging:
                command_delay = float(self.config.get(CONF_COMMAND_DELAY, 0.1))
                await asyncio.sleep(command_delay)  # Configurable delay
                
                try:
                    state = self.hass.states.get(kostal_grid_charge_switch)
                    if state and state.state == "off":
                        await self.hass.services.async_call(
                            "switch",
                            "turn_on",
                            {"entity_id": kostal_grid_charge_switch},
                        )
                        _LOGGER.info("Turned on Kostal grid charge switch (immediately after min SOC)")
                    elif state and state.state == "on":
                        _LOGGER.debug("Grid charge switch already on")
                except Exception as e:
                    _LOGGER.error("Error turning on grid charge: %s", e, exc_info=True)
        elif kostal_grid_charge_switch and not should_skip_charging:
            # Only grid charge needs to be turned on (min SOC already set or not needed)
            try:
                state = self.hass.states.get(kostal_grid_charge_switch)
                if state and state.state == "off":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_on",
                        {"entity_id": kostal_grid_charge_switch},
                    )
                    _LOGGER.info("Turned on Kostal grid charge switch")
                elif state and state.state == "on":
                    _LOGGER.debug("Grid charge switch already on")
            except Exception as e:
                _LOGGER.error("Error turning on grid charge: %s", e, exc_info=True)

        # Apply absolute AC+DC charge limit during AC charging
        if kostal_grid_charge_switch and not should_skip_charging:
            await self._apply_absolute_charge_power_limit()

    async def _stop_grid_charging(self) -> None:
        """Stop grid charging when target is reached."""
        kostal_grid_charge_switch = self.config.get(CONF_KOSTAL_GRID_CHARGE_SWITCH)
        if kostal_grid_charge_switch:
            try:
                state = self.hass.states.get(kostal_grid_charge_switch)
                if state and state.state == "on":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": kostal_grid_charge_switch},
                    )
                    _LOGGER.info("Stopped grid charging - target SOC reached")
                elif state and state.state == "off":
                    _LOGGER.debug("Grid charge already off")
                else:
                    _LOGGER.warning("Cannot stop grid charge - entity state unavailable")
            except Exception as e:
                _LOGGER.error("Error stopping grid charge: %s", e, exc_info=True)
        await self._reset_absolute_charge_power()
        self._finalize_auto_test()
    
    def _setup_battery_soc_listener(self) -> None:
        """Set up a listener for battery SOC changes to check target more frequently."""
        self._remove_battery_soc_listener()  # Remove any existing listener
        
        battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
        if not battery_soc_entity:
            return
        
        async def _on_battery_soc_change(event: Event[EventStateChangedData]) -> None:
            """Handle battery SOC state changes."""
            if not self.is_active or not self.is_enabled or self.target_reached:
                return

            new_state = event.data.get("new_state")
            if not new_state or new_state.state in ("unknown", "unavailable"):
                return
            
            try:
                current_soc = float(new_state.state)
                # Use minimum SOC for target check (always the lowest value we want to maintain)
                target_soc = self.minimum_calculated_soc if self.minimum_calculated_soc is not None else (
                    self.override_soc if self.override_soc is not None else self.calculated_soc
                )
                
                if target_soc is None:
                    return
                
                # Check if target is reached
                if current_soc >= target_soc:
                    # SAFETY: Log if we significantly exceed target.
                    if current_soc > target_soc + 5.0:
                        _LOGGER.warning(
                            "SAFETY: Battery SOC (%.1f%%) significantly exceeds target (%.1f%%) via listener, forcing stop",
                            current_soc,
                            target_soc,
                        )
                    if not self.target_reached:
                        _LOGGER.info(
                            "Target SOC reached via listener: %.1f%% >= %.1f%% (target: %s)",
                            current_soc,
                            target_soc,
                            "override" if self.override_soc is not None else "calculated",
                        )
                        try:
                            await self._stop_grid_charging()
                            self.target_reached = True
                            # Request refresh to update coordinator data
                            await self.async_request_refresh()
                        except Exception as e:
                            _LOGGER.error("Error stopping grid charge in listener: %s", e, exc_info=True)
                            # Still set target_reached to prevent repeated attempts
                            self.target_reached = True
            except (ValueError, TypeError) as e:
                _LOGGER.debug("Error parsing SOC in listener: %s", e)
            except Exception as e:
                _LOGGER.error("Unexpected error in battery SOC listener: %s", e, exc_info=True)
        
        self._battery_soc_listener = async_track_state_change_event(
            self.hass,
            battery_soc_entity,
            _on_battery_soc_change,
        )
        _LOGGER.debug("Set up battery SOC listener for %s", battery_soc_entity)
    
    def _remove_battery_soc_listener(self) -> None:
        """Remove the battery SOC listener."""
        if self._battery_soc_listener:
            self._battery_soc_listener()
            self._battery_soc_listener = None
            _LOGGER.debug("Removed battery SOC listener")
    
    def _setup_inverter_min_soc_listener(self) -> None:
        """Set up a listener for inverter min SOC changes to detect external modifications."""
        self._remove_inverter_min_soc_listener()  # Remove any existing listener
        
        kostal_min_soc_entity = self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY)
        if not kostal_min_soc_entity:
            return
        
        async def _on_inverter_min_soc_change(
            event: Event[EventStateChangedData],
        ) -> None:
            """Handle inverter min SOC state changes."""
            if not self.is_active or not self.is_enabled:
                return

            new_state = event.data.get("new_state")
            if not new_state or new_state.state in ("unknown", "unavailable"):
                return
            
            try:
                current_inverter_soc = float(new_state.state)
                # Get our target SOC (minimum of initial and any override)
                target_soc = self.minimum_calculated_soc if self.minimum_calculated_soc is not None else self.initial_calculated_soc
                
                if target_soc is None:
                    return
                if self._is_within_min_soc_cooldown():
                    _LOGGER.debug("Min SOC recently set - skipping listener-triggered restore")
                    return

                # Check if inverter min SOC doesn't match our target (with tolerance)
                if abs(current_inverter_soc - target_soc) > MIN_SOC_TOLERANCE:
                    _LOGGER.debug(
                        "Inverter min SOC deviation detected via listener (%.1f%% vs %.1f%%), triggering restoration",
                        current_inverter_soc,
                        target_soc
                    )
                    # Restore to our target value - actual checking and restoration happens here
                    await self._verify_and_restore_min_soc()
            except (ValueError, TypeError) as e:
                _LOGGER.debug("Error parsing inverter min SOC in listener: %s", e)
            except Exception as e:
                _LOGGER.error("Unexpected error in inverter min SOC listener: %s", e, exc_info=True)
        
        self._inverter_min_soc_listener = async_track_state_change_event(
            self.hass,
            kostal_min_soc_entity,
            _on_inverter_min_soc_change,
        )
        _LOGGER.debug("Set up inverter min SOC listener for %s", kostal_min_soc_entity)
    
    def _remove_inverter_min_soc_listener(self) -> None:
        """Remove the inverter min SOC listener."""
        if self._inverter_min_soc_listener:
            self._inverter_min_soc_listener()
            self._inverter_min_soc_listener = None
            _LOGGER.debug("Removed inverter min SOC listener")
    
    def _start_periodic_verification(self) -> None:
        """Start periodic verification task to check inverter min SOC matches our target."""
        self._stop_periodic_verification()  # Stop any existing task
        
        async def _periodic_verification_loop() -> None:
            """Periodic verification loop."""
            update_interval = float(self.config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
            while self.is_active and self.is_enabled:
                try:
                    await asyncio.sleep(update_interval)
                    if self.is_active and self.is_enabled:
                        await self._verify_and_restore_min_soc()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    _LOGGER.error("Error in periodic verification: %s", e, exc_info=True)
        
        # Use async_create_background_task to avoid blocking startup/shutdown phases
        # Fallback to async_create_task if async_create_background_task doesn't exist (older HA versions)
        if hasattr(self.hass, 'async_create_background_task'):
            self._verification_task = self.hass.async_create_background_task(
                _periodic_verification_loop(),
                "inverter_charge_night_periodic_verification"
            )
        else:
            # Fallback for older Home Assistant versions
            self._verification_task = self.hass.async_create_task(
                _periodic_verification_loop()
            )
        _LOGGER.debug("Started periodic verification task (interval: %d seconds)", 
                     self.config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    
    def _stop_periodic_verification(self) -> None:
        """Stop the periodic verification task."""
        if self._verification_task and not self._verification_task.done():
            self._verification_task.cancel()
            self._verification_task = None
            _LOGGER.debug("Stopped periodic verification task")
    
    async def _verify_and_restore_min_soc(self) -> None:
        """Verify inverter min SOC matches our target and restore if needed."""
        if not self.is_active or not self.is_enabled:
            return
        if self._is_backup_active():
            _LOGGER.debug("Backup mode active - skipping min SOC verification")
            return
        
        # Prevent concurrent execution (e.g., listener and periodic verification both calling this)
        if self._verifying_min_soc:
            _LOGGER.debug("Verification already in progress, skipping duplicate call")
            return
        
        self._verifying_min_soc = True
        try:
            # Get our target SOC (minimum of initial and any override)
            target_soc = self.minimum_calculated_soc if self.minimum_calculated_soc is not None else self.initial_calculated_soc
            
            if target_soc is None:
                return
            
            kostal_min_soc_entity = self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY)
            if not kostal_min_soc_entity:
                return
            
            state = self.hass.states.get(kostal_min_soc_entity)
            if not state or state.state in ("unknown", "unavailable"):
                _LOGGER.debug("Cannot verify min SOC - entity unavailable")
                return
            
            current_inverter_soc = float(state.state)
            
            # Check if inverter min SOC doesn't match our target (with tolerance)
            if abs(current_inverter_soc - target_soc) > MIN_SOC_TOLERANCE:
                if self._is_within_min_soc_cooldown():
                    _LOGGER.debug("Min SOC recently set - skipping restore")
                    return
                _LOGGER.warning(
                    "Inverter min SOC (%.1f%%) doesn't match our target (%.1f%%), restoring to target",
                    current_inverter_soc,
                    target_soc
                )
                # Restore to our target value
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": kostal_min_soc_entity, "value": target_soc},
                )
                self._mark_soc_set(target_soc)
                _LOGGER.info("Restored inverter min SOC to %.1f%%", target_soc)
            
            # Also check if battery SOC already exceeds target - turn off charging if so
            battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
            if battery_soc_entity:
                battery_state = self.hass.states.get(battery_soc_entity)
                if battery_state and battery_state.state not in ("unknown", "unavailable"):
                    try:
                        current_battery_soc = float(battery_state.state)
                        if current_battery_soc >= target_soc and not self.target_reached:
                            _LOGGER.info(
                                "Battery SOC (%.1f%%) already exceeds target (%.1f%%), turning off charging",
                                current_battery_soc,
                                target_soc
                            )
                            await self._stop_grid_charging()
                            self.target_reached = True
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            _LOGGER.error("Error verifying and restoring min SOC: %s", e, exc_info=True)
        finally:
            self._verifying_min_soc = False

    def _setup_backup_mode_listener(self) -> None:
        """Set up a listener for backup mode changes to re-evaluate window state."""
        self._remove_backup_mode_listener()
        backup_entity = self.config.get(CONF_BACKUP_MODE_ENTITY)
        if not backup_entity:
            return

        async def _on_backup_mode_change(
            event: Event[EventStateChangedData],
        ) -> None:
            if not self.is_enabled:
                return
            _LOGGER.info("Backup mode state changed, re-evaluating window")
            await self._check_current_window()
            await self.async_request_refresh()

        self._backup_mode_listener = async_track_state_change_event(
            self.hass,
            backup_entity,
            _on_backup_mode_change,
        )
        _LOGGER.debug("Set up backup mode listener for %s", backup_entity)

    def _remove_backup_mode_listener(self) -> None:
        """Remove backup mode listener."""
        if self._backup_mode_listener:
            self._backup_mode_listener()
            self._backup_mode_listener = None
            _LOGGER.debug("Removed backup mode listener")
