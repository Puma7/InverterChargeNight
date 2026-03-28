"""The Inverter Charge Night integration."""

import asyncio
import logging
import math
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_OPERATION_MODE,
    CONF_UPDATE_INTERVAL,
    CONF_COMMAND_DELAY,
    CONF_BACKUP_MODE_ENTITY,
    CONF_ACTIVE_START_DATE,
    CONF_ACTIVE_END_DATE,
    CONF_MIN_CHARGE_POWER_W,
    CONF_MAX_CHARGE_POWER_W,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_AUTO_EFFICIENCY_DATA,
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
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_FORCE_DISCHARGE_SWITCH,
    DEFAULT_END_TIME,
    DEFAULT_OPERATION_MODE,
    DEFAULT_SAFE_FALLBACK_SOC,
    DEFAULT_START_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_ACTIVE_START_DATE,
    DEFAULT_ACTIVE_END_DATE,
    MODE_MORNING_DISCHARGE,
    DOMAIN,
)
from .calculation import calculate_required_soc

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Inverter Charge Night from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    coordinator = InverterChargeNightCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Set up time-based triggers
    coordinator.setup_time_triggers()
    # Set up optional backup mode listener
    coordinator._setup_backup_mode_listener()
    
    # Add update listener to handle config changes dynamically
    entry.async_on_unload(
        entry.add_update_listener(async_update_entry)
    )
    
    return True


async def async_update_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry update."""
    _LOGGER.info("Configuration updated, updating triggers and coordinator")
    
    # Safety check: ensure coordinator exists
    if entry.entry_id not in hass.data.get(DOMAIN, {}):
        _LOGGER.error("Coordinator not found for entry %s", entry.entry_id)
        return
    
    coordinator: InverterChargeNightCoordinator = hass.data[DOMAIN][entry.entry_id]
    
    # Store old window times before update for comparison
    old_start_time = coordinator.config.get(CONF_START_TIME, DEFAULT_START_TIME)
    old_end_time = coordinator.config.get(CONF_END_TIME, DEFAULT_END_TIME)
    
    # Update coordinator config reference
    coordinator.config = entry.data
    coordinator.operation_mode = entry.data.get(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE)
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
        coordinator.config = entry.data  # Still use new data, but log error
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


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: InverterChargeNightCoordinator = hass.data[DOMAIN][entry.entry_id]
        # CRITICAL: Reset settings before unloading to prevent leaving inverter in bad state
        if coordinator.is_active:
            _LOGGER.warning("Integration unloading during active window - resetting settings")
            try:
                await coordinator._reset_settings()
            except Exception as e:
                _LOGGER.error("Error resetting settings during unload: %s", e, exc_info=True)
                # Continue with unload even if reset fails - we tried our best
        coordinator.remove_time_triggers()
        coordinator._remove_battery_soc_listener()
        coordinator._remove_inverter_min_soc_listener()
        coordinator._stop_periodic_verification()
        coordinator._cancel_skip_next_expiry()
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok


class InverterChargeNightCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Inverter Charge Night integration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.config = entry.data
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.title}",
            update_interval=timedelta(seconds=self.config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)),
        )
        self.original_min_soc: float | None = None
        self.is_active = False
        self.is_enabled = True  # Integration enabled/disabled via switch
        self.operation_mode: str = entry.data.get(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE)
        self.skip_next = False
        self._skip_next_unsub: CALLBACK_TYPE | None = None
        self.calculated_soc: float | None = None
        self.initial_calculated_soc: float | None = None  # Store SOC calculated at window start
        self.minimum_calculated_soc: float | None = None  # Store minimum SOC value (always <= initial)
        self.target_reached = False
        self._time_triggers: list[CALLBACK_TYPE] = []
        self._last_soc_set: float | None = None
        self.override_soc: float | None = None
        self._original_absolute_charge_power: float | None = None
        self._battery_soc_listener: CALLBACK_TYPE | None = None
        self._inverter_min_soc_listener: CALLBACK_TYPE | None = None
        self._verification_task: asyncio.Task[None] | None = None
        self._verifying_min_soc = False
        self._backup_mode_listener: CALLBACK_TYPE | None = None
        self.auto_efficient_charge = entry.data.get(CONF_AUTO_EFFICIENT_CHARGE, False)
        self._auto_test_active = False
        self._auto_test_power_w: int | None = None
        self._auto_test_start: datetime | None = None
        self._auto_last_sample_time: datetime | None = None
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0
        self._auto_missing_entities_logged = False

    @property
    def is_discharge_mode(self) -> bool:
        """Return True if currently in morning discharge mode."""
        return self.operation_mode == MODE_MORNING_DISCHARGE

    def _is_target_reached(self, current_soc: float, target_soc: float) -> bool:
        """Check if target SOC is reached, respecting operation mode direction."""
        if self.is_discharge_mode:
            return current_soc <= target_soc
        return current_soc >= target_soc

    def _schedule_skip_next_expiry(self) -> None:
        """Schedule skip_next to auto-expire after 24 hours."""
        self._cancel_skip_next_expiry()

        async def _expire_skip_next(_now: datetime) -> None:
            _LOGGER.info("Skip next expired after 24 hours")
            self.skip_next = False
            self._skip_next_unsub = None
            await self._check_current_window()

        self._skip_next_unsub = async_call_later(
            self.hass, 24 * 3600, _expire_skip_next
        )

    def _cancel_skip_next_expiry(self) -> None:
        """Cancel the skip_next expiry timer."""
        if self._skip_next_unsub:
            self._skip_next_unsub()
            self._skip_next_unsub = None

    def _get_active_forecast_entity(self) -> str | None:
        """Return the correct forecast entity based on the time of day.

        The solar day we're planning for depends on when the decision is made:
        - Before noon (00:00-11:59): solar production happens TODAY
          → use pv_forecast_today_entity (falls back to pv_forecast_entity)
        - After noon  (12:00-23:59): solar production happens TOMORROW
          → use pv_forecast_entity (falls back to pv_forecast_today_entity)

        This applies to both operation modes:
        - Night charge at 23:00 → tomorrow's forecast
        - Night charge at 00:01 → today's forecast (yesterday's "tomorrow")
        - Morning discharge at 06:00 → today's forecast
        """
        today_entity = self.config.get(CONF_PV_FORECAST_TODAY_ENTITY)
        tomorrow_entity = self.config.get(CONF_PV_FORECAST_ENTITY)

        now = dt_util.now()
        if now.hour < 12:
            return str(today_entity) if today_entity else tomorrow_entity
        return str(tomorrow_entity) if tomorrow_entity else (str(today_entity) if today_entity else None)

    def _parse_forecast_energy(self, entity_id: str | None) -> tuple[float, bool]:
        """Parse forecast energy from an entity, handling multiple Solcast formats.

        Returns:
            Tuple of (forecast_energy_kwh, forecast_available).
            forecast_energy_kwh is always >= 0.
        """
        if not entity_id:
            return 0.0, False

        state = self.hass.states.get(entity_id)
        if not state:
            return 0.0, False

        # Primary: parse entity state value directly
        if state.state not in ("unknown", "unavailable", None):
            try:
                value = float(state.state)
                # Use unit_of_measurement to decide Wh vs kWh;
                # fall back to heuristic only when unit is missing/ambiguous
                unit = str(state.attributes.get("unit_of_measurement", "")).lower()
                if unit in ("wh", "watthour", "watthours"):
                    energy = value / 1000.0
                elif unit in ("kwh", "kilowatthour", "kilowatthours"):
                    energy = value
                elif value > 200:
                    # Heuristic: values above 200 are likely Wh
                    energy = value / 1000.0
                else:
                    energy = value
                return max(0.0, energy), True
            except (ValueError, TypeError):
                pass  # Fall through to attribute-based parsing

        # Fallback: Solcast-style forecast in attributes
        if "forecast" in state.attributes:
            forecast_data = state.attributes.get("forecast", [])
            if forecast_data:
                total = 0.0
                for item in forecast_data:
                    if not isinstance(item, dict):
                        continue
                    try:
                        total += float(item.get("wh", item.get("pv_power_forecast", 0)) or 0) / 1000.0
                    except (ValueError, TypeError):
                        continue  # Skip malformed items instead of crashing
                return max(0.0, total), True

        for attr_name in ("today_forecast", "forecast_today"):
            if attr_name in state.attributes:
                try:
                    energy = float(state.attributes.get(attr_name, 0))
                    return max(0.0, energy), True
                except (ValueError, TypeError):
                    continue

        return 0.0, False

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

        unit = str(state.attributes.get("unit_of_measurement", "")).lower()
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
        unit = str(state.attributes.get("unit_of_measurement", "")).lower() if state else ""
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
        unit = str(state.attributes.get("unit_of_measurement", "")).lower() if state else ""
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
        unit = str(state.attributes.get("unit_of_measurement", "")).lower() if state else ""
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

    @property
    def auto_efficiency_data(self) -> dict[str, Any]:
        """Return persisted auto efficiency data (public access for sensors)."""
        return self._get_auto_efficiency_data()

    def _get_auto_efficiency_data(self) -> dict[str, Any]:
        """Load persisted auto efficiency data from entry options."""
        data = dict(self.entry.options.get(CONF_AUTO_EFFICIENCY_DATA, {}))
        data.setdefault("history", {})
        return data

    def _save_auto_efficiency_data(self, data: dict[str, Any]) -> None:
        """Persist auto efficiency data to entry options."""
        options = dict(self.entry.options)
        options[CONF_AUTO_EFFICIENCY_DATA] = data
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    def _round_power_step(self, value_w: float, step_w: int = 100) -> int:
        """Round power to nearest step."""
        return int(round(value_w / step_w) * step_w)

    def _select_next_auto_test_power_w(self) -> int | None:
        """Select next power setpoint for auto efficiency testing."""
        min_w = int(self.config.get(CONF_MIN_CHARGE_POWER_W, 1000))
        max_w = int(self.config.get(CONF_MAX_CHARGE_POWER_W, 10000))
        if min_w >= max_w:
            return None

        step_w = 100  # 0.1 kW precision
        data = self._get_auto_efficiency_data()
        history = {int(k): v for k, v in data.get("history", {}).items()}
        range_min = int(data.get("range_min_w", min_w))
        range_max = int(data.get("range_max_w", max_w))
        range_min = max(min_w, range_min)
        range_max = min(max_w, range_max)
        if range_max - range_min < step_w:
            return None

        phi = (math.sqrt(5) - 1) / 2  # golden ratio
        for _ in range(5):
            c = self._round_power_step(range_max - phi * (range_max - range_min), step_w)
            d = self._round_power_step(range_min + phi * (range_max - range_min), step_w)
            c = max(range_min, min(range_max, c))
            d = max(range_min, min(range_max, d))
            if c == d:
                d = min(range_max, c + step_w)
            if c in history and d in history:
                if history[c] <= history[d]:
                    range_max = d
                else:
                    range_min = c
                data["range_min_w"] = range_min
                data["range_max_w"] = range_max
                self._save_auto_efficiency_data(data)
                continue
            if c not in history:
                return c
            if d not in history:
                return d
        return None

    def _reset_auto_test_state(self) -> None:
        """Reset current auto test state."""
        self._auto_test_active = False
        self._auto_test_power_w = None
        self._auto_test_start = None
        self._auto_last_sample_time = None
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0

    async def _start_auto_test(self, power_w: int) -> None:
        """Start auto efficiency test at given power."""
        await self._set_ac_charge_limit_w(power_w)
        self._auto_test_active = True
        self._auto_test_power_w = power_w
        self._auto_test_start = dt_util.now()
        self._auto_last_sample_time = self._auto_test_start
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0
        _LOGGER.info("Auto efficiency test started at %d W", power_w)

    def _accumulate_auto_energy(self) -> None:
        """Accumulate sent/received energy for auto test."""
        if not self._auto_test_active or not self._auto_last_sample_time:
            return
        sent_entity = self.config.get(CONF_CHARGE_POWER_SENT_ENTITY)
        received_entity = self.config.get(CONF_CHARGE_POWER_RECEIVED_ENTITY)
        sent_w = self._get_power_w(sent_entity)
        received_w = self._get_power_w(received_entity)
        if sent_w is None or received_w is None:
            return
        now = dt_util.now()
        delta_h = (now - self._auto_last_sample_time).total_seconds() / 3600.0
        if delta_h <= 0:
            return
        self._auto_energy_sent_wh += sent_w * delta_h
        self._auto_energy_received_wh += received_w * delta_h
        self._auto_last_sample_time = now

    def _finalize_auto_test(self) -> None:
        """Finalize auto test and persist efficiency result."""
        if not self._auto_test_active or not self._auto_test_start or self._auto_test_power_w is None:
            self._reset_auto_test_state()
            return

        duration = (dt_util.now() - self._auto_test_start).total_seconds()
        if duration < 1800 or self._auto_energy_sent_wh <= 0:
            _LOGGER.info("Auto efficiency test discarded (duration < 30 min)")
            self._reset_auto_test_state()
            return

        loss = 1.0 - (self._auto_energy_received_wh / self._auto_energy_sent_wh)
        loss = max(0.0, min(loss, 1.0))

        data = self._get_auto_efficiency_data()
        history = dict(data.get("history", {}))
        history[str(self._auto_test_power_w)] = loss
        data["history"] = history

        best_loss = data.get("best_loss")
        if best_loss is None or loss < best_loss:
            data["best_loss"] = loss
            data["best_power_w"] = self._auto_test_power_w
            _LOGGER.info(
                "New best auto efficiency: %.4f loss at %d W",
                loss,
                self._auto_test_power_w,
            )
        else:
            _LOGGER.info(
                "Auto efficiency recorded: %.4f loss at %d W",
                loss,
                self._auto_test_power_w,
            )

        self._save_auto_efficiency_data(data)
        self._reset_auto_test_state()

    async def _handle_auto_charge(self) -> None:
        """Handle auto efficient charging logic."""
        if not self.auto_efficient_charge:
            if self._auto_test_active:
                self._finalize_auto_test()
            return
        if self._is_backup_active():
            if self._auto_test_active:
                self._reset_auto_test_state()
            return

        charge_entity = self.config.get(CONF_CHARGE_POWER_ENTITY)
        sent_entity = self.config.get(CONF_CHARGE_POWER_SENT_ENTITY)
        received_entity = self.config.get(CONF_CHARGE_POWER_RECEIVED_ENTITY)
        if not charge_entity or not sent_entity or not received_entity:
            if not self._auto_missing_entities_logged:
                _LOGGER.warning(
                    "Auto efficient charge enabled but required entities are missing "
                    "(setpoint, sent, received)."
                )
                self._auto_missing_entities_logged = True
            return

        grid_charge_switch = self.config.get(CONF_KOSTAL_GRID_CHARGE_SWITCH)
        grid_on = False
        if grid_charge_switch:
            state = self.hass.states.get(grid_charge_switch)
            grid_on = state is not None and state.state == "on"

        if not grid_on or self.target_reached:
            if self._auto_test_active:
                self._finalize_auto_test()
            return

        if self._auto_test_active:
            self._accumulate_auto_energy()
            return

        candidate = self._select_next_auto_test_power_w()
        if candidate is None:
            data = self._get_auto_efficiency_data()
            best_power = data.get("best_power_w")
            if isinstance(best_power, int):
                await self._set_ac_charge_limit_w(best_power)
                if self.auto_efficient_charge:
                    self.auto_efficient_charge = False
                    data = dict(self.entry.data)
                    data[CONF_AUTO_EFFICIENT_CHARGE] = False
                    self.hass.config_entries.async_update_entry(self.entry, data=data)
                    _LOGGER.info("Auto efficient charge finder completed, disabling switch")
            return

        await self._start_auto_test(candidate)

    def _is_time_between(
        self, check_time: time, start_time: time, end_time: time
    ) -> bool:
        """Check if a time is between two other times, handling overnight ranges."""
        if start_time == end_time:
            # Identical start/end means zero-length window, never active
            return False
        if start_time < end_time:
            return start_time <= check_time <= end_time
        return check_time >= start_time or check_time <= end_time

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
            self.hass.async_create_task(
                self._check_current_window(),
                name="inverter_charge_night_check_window_setup",
            )
            
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Failed to set up time triggers: %s", err, exc_info=True)
            self.remove_time_triggers()  # Clean up any partial setup

    def remove_time_triggers(self) -> None:
        """Remove time-based triggers."""
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
        if self.entry.data != self.config:
            self.config = self.entry.data
        
        # Set up new triggers with updated times
        self.setup_time_triggers()
        
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
        self.hass.async_create_task(
            self._check_current_window(),
            name="inverter_charge_night_check_window_update",
        )

    async def _check_current_window(self) -> None:
        """Check if we're currently in the active window and adjust state if needed."""
        try:
            if self.skip_next:
                if self.is_active:
                    _LOGGER.info("Skip next active - stopping window")
                    await self._on_window_end(dt_util.now())
                return

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
        if self.skip_next:
            _LOGGER.info("Window start ignored - skip next is active")
            return
        if self._is_backup_active():
            _LOGGER.info("Window start ignored - backup mode active")
            return
        if not self._is_within_date_range():
            _LOGGER.info("Window start ignored - outside active date range")
            return
        mode_label = "Morning discharge" if self.is_discharge_mode else "Night charge"
        _LOGGER.info("%s window started", mode_label)
        self.is_active = True
        self.target_reached = False
        
        # Calculate and store initial SOC for this charging period
        await self._calculate_initial_soc()
        
        # Set up battery SOC listener for more frequent target checks
        self._setup_battery_soc_listener()
        
        # Set up inverter min SOC listener to detect external changes
        self._setup_inverter_min_soc_listener()
        
        # Start periodic verification task (every 15 minutes)
        self._start_periodic_verification()
        
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
                max_retry_time = 180  # 3 minutes in seconds
                retry_interval = 10  # Check every 10 seconds
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
            pv_forecast_entity = self._get_active_forecast_entity()
            forecast_energy, forecast_available = self._parse_forecast_energy(pv_forecast_entity)
            
            calculated_soc: float | None = calculate_required_soc(
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
        mode_label = "Morning discharge" if self.is_discharge_mode else "Night charge"
        _LOGGER.info("%s window ended, resetting settings", mode_label)
        try:
            await self._reset_settings()
        except Exception as e:
            _LOGGER.error("Error resetting settings at window end: %s", e, exc_info=True)
            # Continue to reset state flags even if reset fails
        finally:
            # CRITICAL: Always reset state flags, even if reset operation failed
            self._finalize_auto_test()
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
        
        # Turn off force discharge switch if configured (discharge mode cleanup)
        force_discharge_switch = self.config.get(CONF_FORCE_DISCHARGE_SWITCH)
        if force_discharge_switch:
            try:
                state = self.hass.states.get(force_discharge_switch)
                if state and state.state == "on":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": force_discharge_switch},
                    )
                    _LOGGER.info("Turned off force discharge switch during reset")
            except Exception as e:
                _LOGGER.error("Error turning off force discharge: %s", e, exc_info=True)
        
        # Reset stored original value and tracking after successful reset
        if reset_success:
            self.original_min_soc = None
            self._last_soc_set = None
            self.override_soc = None  # Clear override when resetting
        await self._reset_absolute_charge_power()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from entities and update coordinator."""
        inactive_data: dict[str, Any] = {
            "calculated_soc": None,
            "is_active": False,
            "target_reached": False,
            "operation_mode": self.operation_mode,
            "skip_next": self.skip_next,
        }
        if not self.is_enabled or not self.is_active:
            return inactive_data
        if self._is_backup_active():
            if self.is_active:
                _LOGGER.info("Backup mode active - stopping window and resetting settings")
                await self._on_window_end(dt_util.now())
            else:
                _LOGGER.info("Backup mode active - skipping inverter control")
            return inactive_data
        if not self._is_within_date_range():
            _LOGGER.info("Outside active date range during update - resetting settings")
            await self._on_window_end(dt_util.now())
            return inactive_data
        
        # Get forecast data (uses today's forecast for discharge, tomorrow's for charge)
        pv_forecast_entity = self._get_active_forecast_entity()
        try:
            battery_capacity = float(self.config.get(CONF_BATTERY_CAPACITY, 10.0))
            error_margin = float(self.config.get(CONF_FORECAST_ERROR_MARGIN, 10.0))
            user_min_soc = float(self.config.get(CONF_USER_MIN_SOC, 8.0))
            user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, 100.0))
        except (ValueError, TypeError) as e:
            _LOGGER.error("Error parsing configuration values: %s", e)
            return inactive_data
        
        forecast_energy, forecast_available = self._parse_forecast_energy(pv_forecast_entity)

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
        
        # CRITICAL: Check if target is already reached before controlling
        if can_check_soc and battery_soc_entity is not None:
            battery_state = self.hass.states.get(battery_soc_entity)
            if battery_state and battery_state.state not in ("unknown", "unavailable"):
                try:
                    current_battery_soc = float(battery_state.state)
                    if self._is_target_reached(current_battery_soc, target_soc):
                        if not self.target_reached:
                            direction = "below" if self.is_discharge_mode else "above"
                            _LOGGER.info(
                                "Battery SOC (%.1f%%) already at or %s target (%.1f%%) at startup",
                                current_battery_soc,
                                direction,
                                target_soc,
                            )
                            if self.is_discharge_mode:
                                await self._stop_force_discharge()
                            else:
                                await self._stop_grid_charging()
                            self.target_reached = True
                        return {
                            "calculated_soc": calculated_soc,
                            "is_active": self.is_active,
                            "target_reached": self.target_reached,
                            "current_soc": current_battery_soc,
                            "operation_mode": self.operation_mode,
                            "skip_next": self.skip_next,
                        }
                except (ValueError, TypeError):
                    pass
        
        # Control Kostal entities based on operation mode
        if not self.target_reached:
            if can_check_soc:
                if self.is_discharge_mode:
                    await self._control_discharge(target_soc)
                else:
                    await self._control_kostal(target_soc)
            else:
                _LOGGER.warning(
                    "Battery SOC entity %s is unavailable - skipping control to prevent "
                    "unintended operation. Will retry on next update.",
                    battery_soc_entity,
                )

        # Auto efficient charge handling (only in night charge mode)
        if not self.is_discharge_mode:
            await self._handle_auto_charge()
        
        # Check if target is reached (reuse battery_soc_entity from above)
        current_soc = None
        if battery_soc_entity:
            state = self.hass.states.get(battery_soc_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current_soc = float(state.state)
                    check_target = self.minimum_calculated_soc if self.minimum_calculated_soc is not None else target_soc
                    if self._is_target_reached(current_soc, check_target):
                        if not self.target_reached:
                            _LOGGER.info(
                                "Target SOC reached: %.1f%% %s %.1f%% (mode: %s, target: %s)",
                                current_soc,
                                "<=" if self.is_discharge_mode else ">=",
                                check_target,
                                self.operation_mode,
                                "override" if self.override_soc is not None else "calculated",
                            )
                            if self.is_discharge_mode:
                                await self._stop_force_discharge()
                            else:
                                await self._stop_grid_charging()
                            self.target_reached = True
                except (ValueError, TypeError):
                    pass
        
        return {
            "calculated_soc": calculated_soc,
            "is_active": self.is_active,
            "target_reached": self.target_reached,
            "current_soc": current_soc,
            "operation_mode": self.operation_mode,
            "skip_next": self.skip_next,
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
                # CRITICAL: Threshold increased to 0.5 to prevent "bricking" inverter memory
                if min_soc_current_value is None or abs(min_soc_current_value - target_soc) > 0.5:
                    # Also check if we just set this value (prevent rapid updates)
                    if self._last_soc_set is None or abs(self._last_soc_set - target_soc) > 0.5:
                        need_to_set_min_soc = True
                    else:
                        _LOGGER.debug("Min SOC already set to %.1f%% recently, skipping update", target_soc)
                        self._last_soc_set = target_soc
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
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": kostal_min_soc_entity, "value": target_soc},
            )
            self._last_soc_set = target_soc
            _LOGGER.info(
                "Set Kostal min SOC to %.1f%% (was %s)",
                target_soc,
                f"{min_soc_current_value:.1f}%" if min_soc_current_value is not None else "unknown",
            )
            
            # Immediately send grid charge command (configurable delay) so inverter processes both together
            if kostal_grid_charge_switch and not should_skip_charging:
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

    async def _control_discharge(self, target_soc: float) -> None:
        """Control Kostal entities for morning discharge mode.

        Sets min SOC to the discharge target (floor), ensures grid charging is off,
        and activates the force-discharge switch (if configured) to push battery
        energy into the grid.
        """
        if self._is_backup_active():
            _LOGGER.info("Backup mode active - skipping discharge control")
            return

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
        force_discharge_switch = self.config.get(CONF_FORCE_DISCHARGE_SWITCH)

        battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
        should_skip = False

        if battery_soc_entity:
            state = self.hass.states.get(battery_soc_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current_soc = float(state.state)
                    if current_soc <= target_soc:
                        _LOGGER.info(
                            "Skip discharge: current SOC (%.1f%%) <= target (%.1f%%)",
                            current_soc,
                            target_soc,
                        )
                        should_skip = True
                except (ValueError, TypeError):
                    pass
            else:
                _LOGGER.warning(
                    "Battery SOC entity %s is unavailable - skipping discharge control",
                    battery_soc_entity,
                )
                should_skip = True

        if should_skip:
            # Target already reached — turn OFF force discharge if it was on
            if force_discharge_switch:
                try:
                    state = self.hass.states.get(force_discharge_switch)
                    if state and state.state == "on":
                        await self.hass.services.async_call(
                            "switch", "turn_off",
                            {"entity_id": force_discharge_switch},
                        )
                        _LOGGER.info("Turned off force discharge - target already reached")
                except Exception as e:
                    _LOGGER.error("Error turning off force discharge: %s", e, exc_info=True)
            return

        need_to_set_min_soc = False

        if kostal_min_soc_entity:
            try:
                if self.original_min_soc is None:
                    state = self.hass.states.get(kostal_min_soc_entity)
                    if state and state.state not in ("unknown", "unavailable"):
                        try:
                            self.original_min_soc = float(state.state)
                            _LOGGER.info("Stored original min SOC: %.1f%%", self.original_min_soc)
                        except (ValueError, TypeError):
                            self.original_min_soc = float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
                            _LOGGER.warning(
                                "Could not read original min SOC, using configured default: %.1f%%",
                                self.original_min_soc,
                            )
                    else:
                        self.original_min_soc = float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
                        _LOGGER.warning(
                            "Min SOC entity unavailable, using configured default: %.1f%%",
                            self.original_min_soc,
                        )

                state = self.hass.states.get(kostal_min_soc_entity)
                min_soc_current_value: float | None = None
                if state and state.state not in ("unknown", "unavailable"):
                    try:
                        min_soc_current_value = float(state.state)
                    except (ValueError, TypeError):
                        pass

                if min_soc_current_value is None or abs(min_soc_current_value - target_soc) > 0.5:
                    if self._last_soc_set is None or abs(self._last_soc_set - target_soc) > 0.5:
                        need_to_set_min_soc = True
                    else:
                        self._last_soc_set = target_soc
                else:
                    self._last_soc_set = target_soc
            except Exception as e:
                _LOGGER.error("Error preparing min SOC for discharge: %s", e, exc_info=True)

        if need_to_set_min_soc and kostal_min_soc_entity:
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": kostal_min_soc_entity, "value": target_soc},
            )
            self._last_soc_set = target_soc
            _LOGGER.info("Set min SOC to %.1f%% for discharge (floor)", target_soc)

        # Ensure grid charging is OFF during discharge
        if kostal_grid_charge_switch:
            try:
                state = self.hass.states.get(kostal_grid_charge_switch)
                if state and state.state == "on":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": kostal_grid_charge_switch},
                    )
                    _LOGGER.info("Turned off grid charge for discharge mode")
            except Exception as e:
                _LOGGER.error("Error turning off grid charge in discharge mode: %s", e, exc_info=True)

        # Activate force-discharge switch to push battery energy to grid
        if force_discharge_switch:
            try:
                state = self.hass.states.get(force_discharge_switch)
                if state and state.state == "off":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_on",
                        {"entity_id": force_discharge_switch},
                    )
                    _LOGGER.info("Turned on force discharge switch")
                elif state and state.state == "on":
                    _LOGGER.debug("Force discharge switch already on")
            except Exception as e:
                _LOGGER.error("Error turning on force discharge: %s", e, exc_info=True)

    async def _stop_force_discharge(self) -> None:
        """Turn off force discharge switch when discharge target is reached."""
        force_discharge_switch = self.config.get(CONF_FORCE_DISCHARGE_SWITCH)
        if force_discharge_switch:
            try:
                state = self.hass.states.get(force_discharge_switch)
                if state and state.state == "on":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": force_discharge_switch},
                    )
                    _LOGGER.info("Stopped force discharge - target SOC reached")
            except Exception as e:
                _LOGGER.error("Error stopping force discharge: %s", e, exc_info=True)

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
                
                if self._is_target_reached(current_soc, target_soc):
                    if not self.target_reached:
                        _LOGGER.info(
                            "Target SOC reached via listener: %.1f%% %s %.1f%% (mode: %s, target: %s)",
                            current_soc,
                            "<=" if self.is_discharge_mode else ">=",
                            target_soc,
                            self.operation_mode,
                            "override" if self.override_soc is not None else "calculated",
                        )
                        try:
                            if self.is_discharge_mode:
                                await self._stop_force_discharge()
                            else:
                                await self._stop_grid_charging()
                            self.target_reached = True
                            await self.async_request_refresh()
                        except Exception as e:
                            _LOGGER.error("Error stopping control in listener: %s", e, exc_info=True)
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
                
                # Check if inverter min SOC doesn't match our target (with tolerance)
                if abs(current_inverter_soc - target_soc) > 0.5:
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
        
        self._verification_task = self.hass.async_create_background_task(
            _periodic_verification_loop(),
            "inverter_charge_night_periodic_verification",
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
            if abs(current_inverter_soc - target_soc) > 0.5:
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
                self._last_soc_set = target_soc
                _LOGGER.info("Restored inverter min SOC to %.1f%%", target_soc)
            
            battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
            if battery_soc_entity:
                battery_state = self.hass.states.get(battery_soc_entity)
                if battery_state and battery_state.state not in ("unknown", "unavailable"):
                    try:
                        current_battery_soc = float(battery_state.state)
                        if self._is_target_reached(current_battery_soc, target_soc) and not self.target_reached:
                            _LOGGER.info(
                                "Battery SOC (%.1f%%) already at target (%.1f%%, mode: %s)",
                                current_battery_soc,
                                target_soc,
                                self.operation_mode,
                            )
                            if self.is_discharge_mode:
                                await self._stop_force_discharge()
                            else:
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
