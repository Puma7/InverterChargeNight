"""The Inverter Charge Night integration."""

import asyncio
import logging
import math
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.recorder import DATA_INSTANCE, get_instance
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
    CONF_RUNTIME_STATE,
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
    CONF_PLANNER_MODE,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_PV_CROSSOVER_DELAY_MIN,
    CONF_BRIDGE_RESERVE_KWH,
    CONF_CHARGE_EFFICIENCY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_FEED_IN_PRICE_CT,
    CONF_NIGHT_PRICE_CT,
    CONF_DAY_PRICE_CT,
    DEFAULT_PLANNER_MODE,
    DEFAULT_AVG_HOUSE_LOAD_KW,
    DEFAULT_PV_CROSSOVER_DELAY_MIN,
    DEFAULT_BRIDGE_RESERVE_KWH,
    DEFAULT_CHARGE_EFFICIENCY,
    HOUSE_LOAD_PROFILE_DAYS,
    HOUSE_LOAD_PROFILE_CACHE_S,
    PLANNED_POWER_WRITE_THRESHOLD_W,
    PLANNER_MODE_BRIDGE,
    DEFAULT_END_TIME,
    DEFAULT_MAX_SOC,
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
from .planner import (
    REASON_FALLBACK,
    PlanInput,
    PlanResult,
    plan_target_soc,
    required_charge_power_w,
)
from .util import parse_time_str

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]


type InverterChargeNightConfigEntry = ConfigEntry[InverterChargeNightCoordinator]

# A failed reset at window end is retried after these delays (seconds), then
# every RESET_RETRY_INTERVAL until it succeeds.
RESET_RETRY_DELAYS: tuple[int, ...] = (60, 120, 240)
RESET_RETRY_INTERVAL = 900
SKIP_NEXT_DURATION = timedelta(hours=24)
# A bridge longer than this means the sunrise or the window end is off; the plan
# is still made, but it is worth a warning in the log.
MAX_PLAUSIBLE_BRIDGE_HOURS = 12.0
# Below this much time left in the window the charge setpoint is not written any
# more: the required power goes to infinity and would order the maximum.
MIN_PLAN_HOURS_REMAINING = 1.0 / 60.0  # one minute
# Home Assistant's built-in sun entity; next_rising / next_setting feed the planner
SUN_ENTITY_ID = "sun.sun"


async def async_setup_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> bool:
    """Set up Inverter Charge Night from a config entry."""
    # Test-before-setup: verify critical entities are available
    required_entities = [
        entry.data.get(key)
        for key in (CONF_BATTERY_SOC_ENTITY, CONF_KOSTAL_MIN_SOC_ENTITY, CONF_KOSTAL_GRID_CHARGE_SWITCH)
    ]
    for entity_id in required_entities:
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
    # All required entities are known: clear issues from earlier failed attempts
    for entity_id in required_entities:
        if entity_id:
            ir.async_delete_issue(hass, DOMAIN, f"entity_not_available_{entity_id}")

    coordinator = InverterChargeNightCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    
    entry.runtime_data = coordinator
    
    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Set up time-based triggers
    coordinator.setup_time_triggers()
    # Set up optional backup mode listener
    coordinator._setup_backup_mode_listener()
    # A reset the previous run could not finish is retried from here, not from
    # the constructor: only now is the coordinator reachable and the setup done.
    coordinator.async_start_pending_reset_retry()

    # Add update listener to handle config changes dynamically
    entry.async_on_unload(
        entry.add_update_listener(async_update_entry)
    )
    
    return True


async def async_update_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> None:
    """Handle config entry update."""
    # Safety check: ensure coordinator exists
    coordinator: InverterChargeNightCoordinator | None = getattr(entry, "runtime_data", None)
    if coordinator is None:
        _LOGGER.error("Coordinator not found for entry %s", entry.entry_id)
        return

    if coordinator.config == entry.data:
        # Only entry.options changed: the coordinator persists its runtime state
        # there (see _persist_state), which must not re-register triggers or
        # listeners. Configuration lives in entry.data.
        _LOGGER.debug("Entry options updated without configuration change; nothing to reconfigure")
        return

    _LOGGER.info("Configuration updated, updating triggers and coordinator")

    # Store old window times before update for comparison
    old_start_time = coordinator.config.get(CONF_START_TIME, DEFAULT_START_TIME)
    old_end_time = coordinator.config.get(CONF_END_TIME, DEFAULT_END_TIME)
    
    # The mode owns the window: a window still running in the old mode has to be
    # torn down before the new mode takes over, otherwise the options dialog
    # would leave force discharge on while grid charge is switched back on. The
    # select entity goes through the same method. Done before the config is
    # swapped so the reset still addresses the entities it wrote to.
    await coordinator.async_apply_operation_mode(
        entry.data.get(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE)
    )

    # Update coordinator config reference
    coordinator.config = entry.data
    coordinator.auto_efficient_charge = entry.data.get(CONF_AUTO_EFFICIENT_CHARGE, False)
    coordinator._auto_missing_entities_logged = False
    coordinator._house_load_cache = None  # the meter or the average may have changed
    
    # Update coordinator polling interval
    coordinator.update_interval = timedelta(
        seconds=entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    )
    
    # Update time triggers with new configuration
    try:
        coordinator.update_time_triggers()
        coordinator._setup_backup_mode_listener()
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


async def async_unload_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = entry.runtime_data
        # From here on no timer may be armed any more: an async_call_later
        # scheduled during the unload would fire on a dead coordinator. A failed
        # reset is still persisted, so the next setup retries it.
        coordinator._unloading = True
        # CRITICAL: Reset settings before unloading to prevent leaving inverter in bad state
        if coordinator.is_active:
            _LOGGER.warning("Integration unloading during active window - resetting settings")
            try:
                await coordinator._reset_settings()
            except Exception as e:
                _LOGGER.error("Error resetting settings during unload: %s", e, exc_info=True)
                # Continue with unload even if reset fails - we tried our best
        coordinator.remove_time_triggers()
        coordinator._cancel_window_check()
        coordinator._remove_battery_soc_listener()
        coordinator._remove_inverter_min_soc_listener()
        coordinator._remove_backup_mode_listener()
        await coordinator._stop_periodic_verification()
        coordinator._cancel_skip_next_expiry()
        # A pending reset stays persisted and is retried after the next setup
        coordinator._cancel_reset_retry()

    return unload_ok


def _as_float(value: Any) -> float | None:
    """Return ``value`` as float, or None when it is missing or not usable.

    Non-finite values (``nan``, ``inf``) are rejected as well: they parse fine
    but poison every comparison afterwards - a ``nan`` battery SOC makes
    ``target_reached`` unreachable, so grid charging would never stop.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(number):
        _LOGGER.debug("Ignoring non-finite numeric value %r", value)
        return None
    return number


def _unit_of(state: Any) -> str:
    """Return a state's unit_of_measurement lowercased and stripped, or "" if absent."""
    unit = state.attributes.get("unit_of_measurement") if state else None
    return unit.strip().lower() if isinstance(unit, str) else ""


class InverterChargeNightCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Inverter Charge Night integration."""

    # Class-level defaults: _persist_state and the window guards read these and
    # may run on an instance whose constructor did not (test doubles built with
    # __new__).
    _original_discharge_limit: float | None = None
    # True while _on_window_end tears the window down: nothing may re-arm the
    # listeners or write the window target back to the inverter in between.
    _ending: bool = False
    _switching_mode: bool = False
    # True once async_unload_entry started: no new timers may be armed.
    _unloading: bool = False

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
        self._skip_next_until: datetime | None = None
        self._skip_next_unsub: CALLBACK_TYPE | None = None
        self.calculated_soc: float | None = None
        self.initial_calculated_soc: float | None = None  # Store SOC calculated at window start
        self.minimum_calculated_soc: float | None = None  # Store minimum SOC value (always <= initial)
        self.target_reached = False
        self._time_triggers: list[CALLBACK_TYPE] = []
        self._window_check_task: asyncio.Task[None] | None = None
        self._last_soc_set: float | None = None
        self.override_soc: float | None = None
        # Snow on the modules: the next N windows charge to the user maximum,
        # ignoring the forecast. Counts down at every window end.
        self.snow_nights: int = 0
        self._original_absolute_charge_power: float | None = None
        self._original_ac_charge_power: float | None = None  # W, captured before the finder's first write
        self._pending_reset = False  # window ended but the inverter is not back at its original settings
        self._reset_retry_unsub: CALLBACK_TYPE | None = None
        self._reset_retry_count = 0
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
        # Planner v2 (plan 006)
        self.last_plan: PlanResult | None = None  # result of the last bridge plan this window
        self.planned_charge_power_w: float | None = None  # setpoint for the remaining window
        self._pv_crossover: datetime | None = None
        self._original_discharge_limit: float | None = None  # raw value in the entity's unit
        self._planned_setpoint_written_w: float | None = None
        self._house_load_cache: tuple[datetime, list[float]] | None = None
        self._sun_fallback_logged = False
        self._restore_state()

    # Persistence ------------------------------------------------------------
    #
    # entry.options["runtime_state"] (CONF_RUNTIME_STATE) is the single place
    # for flags that must survive a Home Assistant restart. It lives next to
    # the auto-efficiency history under its own key; neither overwrites the other.

    def _persist_state(self) -> None:
        """Store the flags that must survive a restart in entry.options["runtime_state"]."""
        options = dict(self.entry.options)
        options[CONF_RUNTIME_STATE] = {
            "is_enabled": self.is_enabled,
            "skip_next_until": self._skip_next_until.isoformat() if self._skip_next_until else None,
            "override_soc": self.override_soc,
            "original_min_soc": self.original_min_soc,
            # The window target only matters while a window is active; a stale
            # value must never be taken for a restart during a window.
            "initial_calculated_soc": self.initial_calculated_soc if self.is_active else None,
            "original_ac_charge_power": self._original_ac_charge_power,
            "original_discharge_limit": self._original_discharge_limit,
            "original_absolute_charge_power": self._original_absolute_charge_power,
            "pending_reset": self._pending_reset,
            "snow_nights": self.snow_nights,
        }
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    def _restore_state(self) -> None:
        """Restore the persisted flags; missing or malformed values keep their defaults."""
        raw = self.entry.options.get(CONF_RUNTIME_STATE)
        if not isinstance(raw, dict):
            return
        state: dict[str, Any] = raw
        if isinstance(state.get("is_enabled"), bool):
            self.is_enabled = state["is_enabled"]
        self.override_soc = self._restore_soc(state, "override_soc")
        self.original_min_soc = self._restore_soc(state, "original_min_soc")
        self.initial_calculated_soc = self._restore_soc(state, "initial_calculated_soc")
        self._original_ac_charge_power = _as_float(state.get("original_ac_charge_power"))
        self._original_discharge_limit = _as_float(state.get("original_discharge_limit"))
        self._original_absolute_charge_power = _as_float(
            state.get("original_absolute_charge_power")
        )
        self._pending_reset = state.get("pending_reset") is True
        snow_raw = state.get("snow_nights")
        if isinstance(snow_raw, int) and not isinstance(snow_raw, bool) and snow_raw > 0:
            self.snow_nights = snow_raw

        until_raw = state.get("skip_next_until")
        if isinstance(until_raw, str):
            try:
                until = datetime.fromisoformat(until_raw)
                if until > dt_util.now():
                    self.skip_next = True
                    self._schedule_skip_next_expiry(until)
                else:
                    _LOGGER.info("Persisted skip next expired during downtime")
            except (ValueError, TypeError):
                _LOGGER.warning("Ignoring invalid persisted skip_next_until %r", until_raw)

        _LOGGER.debug("Restored runtime state: %s", state)

    def _restore_soc(self, state: dict[str, Any], key: str) -> float | None:
        """Return a persisted SOC percentage, or None when it is not a usable one.

        Anything outside 0-100 % is dropped: a value like -1 or 1e9 would be
        taken for a target and compared against the battery for the rest of the
        window.
        """
        value = _as_float(state.get(key))
        if value is None:
            return None
        if not 0.0 <= value <= 100.0:
            _LOGGER.warning(
                "Ignoring persisted %s %r - outside the valid range of 0-100 %%",
                key,
                state.get(key),
            )
            return None
        return value

    def async_start_pending_reset_retry(self) -> None:
        """Arm the retry for a reset a previous run could not finish.

        Called by ``async_setup_entry`` once ``entry.runtime_data`` is set. The
        constructor must not arm it: the timer would fire on a coordinator that
        is not reachable yet, and on a setup that may still fail.
        """
        if not self._pending_reset:
            return
        _LOGGER.warning("Inverter settings were not reset before the last shutdown - retrying")
        self._reset_retry_count = 0
        self._schedule_reset_retry()

    def current_target_soc(self) -> float | None:
        """The SOC the inverter should hold right now: snow mode, else manual override, else the plan.

        Snow mode only applies to night charge. A morning discharge window told
        to charge to the user maximum would reach its target instantly and do
        nothing at all, so the counter is ignored (and not counted down) there.
        """
        if self.snow_nights > 0 and not self.is_discharge_mode:
            user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, DEFAULT_MAX_SOC))
            if self.override_soc is not None and self.override_soc != user_max_soc:
                _LOGGER.debug(
                    "Snow mode: ignoring manual override %.1f%%, charging to %.0f%%",
                    self.override_soc,
                    user_max_soc,
                )
            return user_max_soc
        if self.override_soc is not None:
            return self.override_soc
        return self.initial_calculated_soc if self.initial_calculated_soc is not None else self.calculated_soc

    @property
    def is_discharge_mode(self) -> bool:
        """Return True if currently in morning discharge mode."""
        return self.operation_mode == MODE_MORNING_DISCHARGE

    def _is_target_reached(self, current_soc: float, target_soc: float) -> bool:
        """Check if target SOC is reached, respecting operation mode direction."""
        if self.is_discharge_mode:
            return current_soc <= target_soc
        return current_soc >= target_soc

    def _schedule_skip_next_expiry(self, until: datetime | None = None) -> None:
        """Schedule skip_next to expire at ``until`` (default: 24 hours from now).

        The absolute deadline is persisted, so after a restart the timer is
        re-armed for the remaining time only.
        """
        self._cancel_skip_next_expiry()
        now = dt_util.now()
        if until is None:
            until = now + SKIP_NEXT_DURATION
        self._skip_next_until = until

        async def _expire_skip_next(_now: datetime) -> None:
            _LOGGER.info("Skip next expired")
            self.skip_next = False
            self._skip_next_until = None
            self._skip_next_unsub = None
            self._persist_state()
            await self._check_current_window()

        remaining = max(0.0, (until - now).total_seconds())
        self._skip_next_unsub = async_call_later(self.hass, remaining, _expire_skip_next)

    def _cancel_skip_next_expiry(self) -> None:
        """Cancel the skip_next expiry timer."""
        self._skip_next_until = None
        if self._skip_next_unsub:
            self._skip_next_unsub()
            self._skip_next_unsub = None

    def _window_end_datetime(self, now: datetime) -> datetime:
        """Return the end of the window that is running or comes next, at minute resolution.

        The end minute itself still belongs to the window (``_is_time_between``
        is inclusive), so an end time equal to the current minute is today's.

        The result carries ``now``'s own tzinfo, so subtracting the two is
        wall-clock arithmetic and the remaining window stays inside
        [0 h, 24 h) across a DST change as well. What it cannot express is the
        repeated hour of a fall-back, where the end minute occurs twice;
        ``_plan_charge_power`` guards the resulting near-zero remainder.
        """
        _, end = self._window_times()
        end_dt = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        if (end.hour, end.minute) < (now.hour, now.minute):
            end_dt += timedelta(days=1)
        return end_dt

    def _get_active_forecast_entity(self) -> str | None:
        """Return the forecast entity for the solar day the plan is made for.

        The solar day is the calendar day of the window end (plan 006), not the
        time of day the decision is made:
        - Window 00:00-05:59, decision at 23:00 or 02:00 → the window ends
          tomorrow / today → tomorrow's / today's entity
        - Window 22:00-23:30 (not over midnight), decision at 22:30 → the
          window ends today → today's entity, even though it is after noon
        - Morning discharge 06:00-08:00 at 06:00 → today's entity

        Each entity falls back to the other one when it is not configured.
        """
        today_entity = self.config.get(CONF_PV_FORECAST_TODAY_ENTITY)
        tomorrow_entity = self.config.get(CONF_PV_FORECAST_ENTITY)
        now = dt_util.now()
        if self._window_end_datetime(now).date() == now.date():
            return str(today_entity) if today_entity else tomorrow_entity
        return str(tomorrow_entity) if tomorrow_entity else (str(today_entity) if today_entity else None)

    def _parse_forecast_energy(self, entity_id: str | None) -> tuple[float, bool]:
        """Parse forecast energy from an entity, handling multiple Solcast formats.

        The entity state is used when available: unit_of_measurement decides
        Wh/kWh/MWh, an unsupported unit marks the forecast unavailable, and
        without a unit values above 1000 are assumed to be Wh. When the state
        is unknown/unavailable the Solcast ``forecast``, ``today_forecast`` and
        ``forecast_today`` attributes are tried in that order.

        Returns:
            Tuple of (forecast_energy_kwh, forecast_available).
            forecast_energy_kwh is always >= 0.
        """
        if not entity_id:
            return 0.0, False

        state = self.hass.states.get(entity_id)
        if not state:
            return 0.0, False

        # Primary: parse entity state value directly (only when state is available)
        if state.state not in ("unknown", "unavailable", None):
            try:
                value = float(state.state)
                unit = _unit_of(state)  # unit rules are described in the docstring
                if unit in ("wh", "watthour", "watthours"):
                    energy = value / 1000.0
                elif unit in ("kwh", "kilowatthour", "kilowatthours"):
                    energy = value
                elif unit in ("mwh", "megawatthour", "megawatthours"):
                    energy = value * 1000.0
                elif unit:
                    # Not an energy unit we understand (e.g. W, kW): do not guess,
                    # report the forecast as unavailable so the safe fallback applies
                    _LOGGER.warning(
                        "Forecast entity %s reports unsupported unit_of_measurement '%s'; "
                        "treating forecast as unavailable",
                        entity_id,
                        unit,
                    )
                    return 0.0, False
                elif value > 1000:
                    # No unit available: values above 1000 are assumed to be Wh
                    energy = value / 1000.0
                else:
                    energy = value
                return max(0.0, energy), True
            except (ValueError, TypeError):
                # State was not parseable as float — mark as unavailable so
                # callers apply the safe fallback (matching old elif-chain
                # behaviour where a non-numeric state meant forecast_available=False)
                return 0.0, False

        # Attribute-based fallbacks (only reached when state IS unavailable/unknown)
        # This preserves the old elif-chain semantics: attributes are NEVER checked
        # when state.state is a valid (but non-numeric) string.
        if "forecast" in state.attributes:
            forecast_data = state.attributes.get("forecast", [])
            if forecast_data:
                total = 0.0
                skipped_items = 0
                for item in forecast_data:
                    try:
                        total += float(item.get("wh", item.get("pv_power_forecast", 0)) or 0) / 1000.0
                    except (ValueError, TypeError, AttributeError):
                        skipped_items += 1  # Skip malformed or non-dict items instead of crashing
                if skipped_items:
                    _LOGGER.warning(
                        "Skipped %d malformed item(s) in forecast attribute of %s",
                        skipped_items,
                        entity_id,
                    )
                if skipped_items == len(forecast_data):
                    # Nothing usable: report unavailable so the safe fallback applies
                    # instead of treating the forecast as a legitimate 0 kWh
                    return 0.0, False
                return max(0.0, total), True
        elif "today_forecast" in state.attributes:
            try:
                energy = float(state.attributes.get("today_forecast", 0))
                return max(0.0, energy), True
            except (ValueError, TypeError):
                pass
        elif "forecast_today" in state.attributes:
            try:
                energy = float(state.attributes.get("forecast_today", 0))
                return max(0.0, energy), True
            except (ValueError, TypeError):
                pass

        return 0.0, False

    # Planner v2 (plan 006) -------------------------------------------------
    #
    # planner_mode "bridge" replaces the headroom formula with the two-bound
    # plan from planner.py. All Home Assistant reads happen here; the planner
    # itself is pure arithmetic.

    @property
    def _planner_mode_is_bridge(self) -> bool:
        return str(self.config.get(CONF_PLANNER_MODE, DEFAULT_PLANNER_MODE)) == PLANNER_MODE_BRIDGE

    def _current_battery_soc(self) -> float | None:
        """Return the battery SOC in %, or None when the entity has no numeric value."""
        entity_id = self.config.get(CONF_BATTERY_SOC_ENTITY)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            return None
        return _as_float(state.state)

    @staticmethod
    def _align_tz(value: datetime, like: datetime) -> datetime:
        """Return ``value`` with the same tz-awareness as ``like`` (local time)."""
        if like.tzinfo is None:
            return dt_util.as_local(value).replace(tzinfo=None) if value.tzinfo else value
        return dt_util.as_local(value) if value.tzinfo else value.replace(tzinfo=like.tzinfo)

    def _sun_times(self, now: datetime, window_end: datetime) -> tuple[datetime, datetime]:
        """Return (sunrise of the window end's solar day, next sunset) from ``sun.sun``.

        ``next_rising`` is by definition in the future, so for a window that
        ends after that day's sunrise - a morning discharge window, or a night
        window ending after sunrise in spring - it points at *tomorrow's*
        sunrise. Using it would make the planner bridge a whole day. The
        sunrise is therefore shifted back onto the calendar day of
        ``window_end``, the same solar day the forecast entity is chosen for.

        Without a usable sun entity the sunrise is assumed two hours after the
        window end and the sunset ten hours after that; this is logged as a
        warning once per window.
        """
        state = self.hass.states.get(SUN_ENTITY_ID)
        sunrise: datetime | None = None
        sunset: datetime | None = None
        if state is not None:
            rising = dt_util.parse_datetime(str(state.attributes.get("next_rising", "")))
            setting = dt_util.parse_datetime(str(state.attributes.get("next_setting", "")))
            sunrise = self._align_tz(rising, now) if rising else None
            sunset = self._align_tz(setting, now) if setting else None
            if sunrise is not None:
                # At most one day to go back: next_rising is less than 24 h away.
                while sunrise.date() > window_end.date():
                    sunrise -= timedelta(days=1)
        if sunrise is None or sunset is None:
            if sunrise is None:
                sunrise = window_end + timedelta(hours=2)
            if sunset is None:
                sunset = sunrise + timedelta(hours=10)
            if not self._sun_fallback_logged:
                _LOGGER.warning(
                    "%s has no usable next_rising/next_setting; assuming sunrise %s and sunset %s",
                    SUN_ENTITY_ID,
                    sunrise,
                    sunset,
                )
                self._sun_fallback_logged = True
        return sunrise, sunset

    def _prices_ct(self) -> tuple[float, float, float] | None:
        """Return (night, day, feed-in) prices when all three are configured."""
        night = _as_float(self.config.get(CONF_NIGHT_PRICE_CT))
        day = _as_float(self.config.get(CONF_DAY_PRICE_CT))
        feed_in = _as_float(self.config.get(CONF_FEED_IN_PRICE_CT))
        if night is None or day is None or feed_in is None:
            return None
        return night, day, feed_in

    async def _house_load_profile(self) -> list[float]:
        """Return 24 hourly house load values in kW.

        With a consumption meter configured the profile is learned from the
        recorder's hourly statistics of the last 14 days and cached for 15
        minutes; otherwise, or when no statistics are usable, every hour is
        the configured average load.
        """
        avg_kw = float(self.config.get(CONF_AVG_HOUSE_LOAD_KW, DEFAULT_AVG_HOUSE_LOAD_KW))
        flat = [avg_kw] * 24
        entity_id = self.config.get(CONF_HOUSE_LOAD_ENTITY)
        if not entity_id:
            return flat
        now = dt_util.utcnow()
        if self._house_load_cache is not None:
            cached_at, cached = self._house_load_cache
            if (now - cached_at).total_seconds() < HOUSE_LOAD_PROFILE_CACHE_S:
                return cached
        profile = await self._learn_house_load_profile(str(entity_id), avg_kw)
        if profile is None:
            _LOGGER.debug(
                "No usable consumption statistics for %s; using the flat %.2f kW profile",
                entity_id,
                avg_kw,
            )
            profile = flat
        self._house_load_cache = (now, profile)
        return profile

    async def _learn_house_load_profile(self, entity_id: str, avg_kw: float) -> list[float] | None:
        """Average the meter's hourly energy change per hour of the day, or None."""
        if DATA_INSTANCE not in self.hass.data:
            _LOGGER.debug("Recorder is not loaded; cannot learn the house load profile")
            return None
        # Imported here so the integration never depends on the recorder being present
        from homeassistant.components.recorder.statistics import statistics_during_period

        start = (dt_util.utcnow() - timedelta(days=HOUSE_LOAD_PROFILE_DAYS)).replace(
            minute=0, second=0, microsecond=0
        )
        try:
            rows = await get_instance(self.hass).async_add_executor_job(
                lambda: statistics_during_period(
                    self.hass, start, None, {entity_id}, "hour", None, {"change"}
                )
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Reading consumption statistics for %s failed: %s", entity_id, err)
            return None
        unit = _unit_of(self.hass.states.get(entity_id))
        if unit in ("wh", "watthour", "watthours"):
            factor = 0.001
        elif unit in ("mwh", "megawatthour", "megawatthours"):
            factor = 1000.0
        else:
            factor = 1.0  # kWh, or no unit: assume kWh
        sums = [0.0] * 24
        counts = [0] * 24
        for row in rows.get(entity_id, []):
            start_ts = row.get("start")
            change = row.get("change")
            if not isinstance(start_ts, (int, float)) or not isinstance(change, (int, float)):
                continue
            if change < 0:
                continue  # meter reset
            hour = dt_util.as_local(dt_util.utc_from_timestamp(start_ts)).hour
            sums[hour] += float(change) * factor
            counts[hour] += 1
        if not any(counts):
            return None
        return [sums[hour] / counts[hour] if counts[hour] else avg_kw for hour in range(24)]

    async def _plan_target(self, forecast_kwh: float, forecast_available: bool) -> PlanResult | None:
        """Run the bridge planner on the current inputs; None when it cannot plan.

        The result is kept in ``last_plan`` for the sensor attributes. A failure
        (invalid configuration) is logged and the caller uses the headroom formula.
        """
        try:
            capacity = float(self.config.get(CONF_BATTERY_CAPACITY, 10.0))
            now = dt_util.now()
            window_end = self._window_end_datetime(now)
            sunrise, sunset = self._sun_times(now, window_end)
            delay_min = int(float(self.config.get(CONF_PV_CROSSOVER_DELAY_MIN, DEFAULT_PV_CROSSOVER_DELAY_MIN)))
            pv_crossover = sunrise + timedelta(minutes=delay_min)
            if pv_crossover < window_end:
                # The sun is already up when the window ends (morning discharge,
                # or a spring window ending after sunrise): there is nothing to
                # bridge, PV carries the house from the window end onwards.
                _LOGGER.debug(
                    "PV crossover %s is before the window end %s - no bridge needed",
                    pv_crossover,
                    window_end,
                )
                pv_crossover = window_end
            bridge_hours = (pv_crossover - window_end).total_seconds() / 3600.0
            if bridge_hours > MAX_PLAUSIBLE_BRIDGE_HOURS:
                _LOGGER.warning(
                    "Bridging %.1f h from the window end %s to the PV crossover %s - check "
                    "%s and the crossover delay; the target will be very high",
                    bridge_hours,
                    window_end,
                    pv_crossover,
                    SUN_ENTITY_ID,
                )
            current_soc = self._current_battery_soc()
            plan = plan_target_soc(
                PlanInput(
                    capacity_kwh=capacity,
                    current_soc=current_soc if current_soc is not None else 0.0,
                    user_min_soc=float(self.config.get(CONF_USER_MIN_SOC, 8.0)),
                    user_max_soc=float(self.config.get(CONF_USER_MAX_SOC, 100.0)),
                    forecast_kwh_next_day=forecast_kwh,
                    forecast_available=forecast_available,
                    error_margin_pct=float(self.config.get(CONF_FORECAST_ERROR_MARGIN, 10.0)),
                    window_end=window_end,
                    pv_crossover=pv_crossover,
                    sunset=sunset,
                    house_load_kw_profile=await self._house_load_profile(),
                    reserve_kwh=float(self.config.get(CONF_BRIDGE_RESERVE_KWH, DEFAULT_BRIDGE_RESERVE_KWH)),
                    charge_efficiency=float(self.config.get(CONF_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)),
                    prices_ct=self._prices_ct(),
                )
            )
        except (ValueError, TypeError) as err:
            _LOGGER.error("Bridge planner failed, using the headroom formula instead: %s", err)
            return None
        self.last_plan = plan
        self._pv_crossover = pv_crossover
        _LOGGER.info(
            "Bridge plan: target %.1f%% (%s), bridge %.2f kWh until %s, surplus %.2f kWh, "
            "bounds %.1f-%.1f%%",
            plan.target_soc,
            plan.reason,
            plan.bridge_kwh,
            pv_crossover.strftime("%H:%M"),
            plan.surplus_kwh,
            plan.lower_bound_soc,
            plan.upper_bound_soc,
        )
        return plan

    async def _replan_in_window(self, previous_target: float) -> float:
        """Re-run the bridge planner on a poll; the target may only rise.

        Lowering it below what is already charged would give energy away, so
        ``max(previous, plan)`` is kept. A fallback plan (forecast unavailable)
        never moves an existing target.
        """
        forecast_kwh, forecast_available = self._parse_forecast_energy(self._get_active_forecast_entity())
        plan = await self._plan_target(forecast_kwh, forecast_available)
        if plan is None or plan.reason == REASON_FALLBACK or plan.target_soc <= previous_target:
            return previous_target
        _LOGGER.info(
            "Bridge plan raised the window target from %.1f%% to %.1f%% (%s)",
            previous_target,
            plan.target_soc,
            plan.reason,
        )
        self.initial_calculated_soc = plan.target_soc
        # The higher target may not be reached yet; the update re-evaluates it
        self.target_reached = False
        self._persist_state()
        return plan.target_soc

    def _plan_attributes(self) -> dict[str, Any]:
        """Sensor attributes of the last bridge plan; empty in headroom mode."""
        plan = self.last_plan
        if plan is None:
            return {}
        return {
            "plan_reason": plan.reason,
            "bridge_kwh": round(plan.bridge_kwh, 2),
            "surplus_kwh": round(plan.surplus_kwh, 2),
            "lower_bound_soc": plan.lower_bound_soc,
            "upper_bound_soc": plan.upper_bound_soc,
            "pv_crossover": self._pv_crossover.isoformat() if self._pv_crossover else None,
            "planned_charge_power_w": self.planned_charge_power_w,
        }

    async def _plan_charge_power(self, target_soc: float) -> None:
        """Plan the AC charge power for the remaining window (plan 006, step 6).

        ``required`` is the constant power that reaches the target in time. The
        setpoint stays within [min, max] charge power and, when the efficiency
        finder has an optimum and ``required`` is below it, does not exceed the
        optimum. The value is always computed for the ``planned_charge_power``
        sensor; it is written to the AC charge limit only in bridge mode, not
        while the finder owns the limit, and only when it moves by more than
        PLANNED_POWER_WRITE_THRESHOLD_W.
        """
        self.planned_charge_power_w = None
        if self.target_reached:
            self.planned_charge_power_w = 0.0
            return
        current_soc = self._current_battery_soc()
        if current_soc is None:
            return
        now = dt_util.now()
        hours_remaining = max(0.0, (self._window_end_datetime(now) - now).total_seconds() / 3600.0)
        if hours_remaining < MIN_PLAN_HOURS_REMAINING:
            # required_charge_power_w would return infinity and the setpoint
            # would collapse to the maximum in the last minute of the window.
            _LOGGER.debug(
                "Only %.4f h left in the window - not planning a charge power any more",
                hours_remaining,
            )
            return
        try:
            required = required_charge_power_w(
                target_soc,
                current_soc,
                float(self.config.get(CONF_BATTERY_CAPACITY, 10.0)),
                hours_remaining,
                float(self.config.get(CONF_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)),
            )
        except ValueError as err:
            _LOGGER.error("Cannot plan the charge power: %s", err)
            return
        min_w = float(self.config.get(CONF_MIN_CHARGE_POWER_W, 1000))
        max_w = float(self.config.get(CONF_MAX_CHARGE_POWER_W, 10000))
        ceiling = max_w
        best = self.get_auto_efficiency_data().get("best_power_w")
        if isinstance(best, int) and required <= best:
            ceiling = max(min_w, min(max_w, float(best)))
        setpoint = float(round(max(min_w, min(required, ceiling))))
        self.planned_charge_power_w = setpoint
        if not self._planner_mode_is_bridge or self.auto_efficient_charge or self._auto_test_active:
            return
        if self._ac_charge_limit_target() is None:
            return
        written = self._planned_setpoint_written_w
        if written is not None and abs(setpoint - written) <= PLANNED_POWER_WRITE_THRESHOLD_W:
            return
        _LOGGER.info(
            "Planned charge power %.0f W (%.2f kWh missing in %.2f h, required %.0f W)",
            setpoint,
            max(0.0, (target_soc - current_soc) / 100.0 * float(self.config.get(CONF_BATTERY_CAPACITY, 10.0))),
            hours_remaining,
            required,
        )
        await self._set_ac_charge_limit_w(int(setpoint))
        self._planned_setpoint_written_w = setpoint

    # Discharge block (plan 006, step 5) --------------------------------------

    def _discharge_limit_target(self) -> tuple[str, str] | None:
        """Return (entity_id, domain) of the discharge limit entity, or None if unusable."""
        entity_id = self.config.get(CONF_DISCHARGE_LIMIT_ENTITY)
        if not entity_id:
            return None
        domain = str(entity_id).split(".")[0]
        if domain not in ("number", "input_number"):
            _LOGGER.warning("Discharge limit entity %s has unsupported domain %s", entity_id, domain)
            return None
        return str(entity_id), domain

    async def _write_number(self, entity_id: str, domain: str, value: float, what: str) -> bool:
        """Write a raw value to a number entity; report success."""
        try:
            await self.hass.services.async_call(
                domain,
                "set_value",
                {"entity_id": entity_id, "value": value},
            )
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.error("Error writing %s: %s", what, e, exc_info=True)
            return False
        _LOGGER.info("%s to %.3f", what, value)
        return True

    async def _apply_discharge_block(self, announce: bool = False) -> None:
        """Set the discharge power limit to 0 so the house runs from the grid.

        The value found before the first write is remembered (and persisted) so
        the window end can restore it. Never applied in discharge mode or in
        backup mode. ``announce`` logs once that no entity is configured.
        """
        if not self.config.get(CONF_DISCHARGE_LIMIT_ENTITY):
            if announce:
                _LOGGER.info(
                    "No discharge limit entity configured - the battery may discharge into the "
                    "house during the window"
                )
            return
        if self.is_discharge_mode or self._is_backup_active():
            return
        target = self._discharge_limit_target()
        if target is None:
            return
        entity_id, domain = target
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            _LOGGER.warning("Discharge limit entity %s is unavailable - block deferred", entity_id)
            return
        current = _as_float(state.state)
        if self._original_discharge_limit is None:
            if current is None:
                _LOGGER.warning(
                    "Cannot read the current discharge limit from %s - not writing a value "
                    "that could not be restored",
                    entity_id,
                )
                return
            self._original_discharge_limit = current
            _LOGGER.info("Stored original discharge limit: %.3f", current)
            self._persist_state()
        if current == 0:
            _LOGGER.debug("Discharge already blocked (%s is 0)", entity_id)
            return
        await self._write_number(entity_id, domain, 0.0, "Set discharge limit (block)")

    async def _reset_discharge_limit(self) -> bool:
        """Restore the discharge limit captured at the window start.

        Returns True when nothing is left to restore; on failure the original
        value is kept for a retry.
        """
        if self._original_discharge_limit is None:
            return True
        target = self._discharge_limit_target()
        if target is None:
            # Entity no longer configured or unusable: nothing we can restore
            self._original_discharge_limit = None
            return True
        entity_id, domain = target
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            _LOGGER.error("Cannot reset discharge limit - entity %s unavailable", entity_id)
            return False
        if not await self._write_number(
            entity_id, domain, self._original_discharge_limit, "Reset discharge limit"
        ):
            return False
        self._original_discharge_limit = None
        return True

    def _parse_time(self, time_str: str | None, default: str) -> tuple[int, int]:
        """Parse an HH:MM string, falling back to ``default`` (then 00:00) when invalid."""
        parsed = parse_time_str(time_str)
        if parsed is not None:
            return parsed
        _LOGGER.warning("Invalid time format '%s', using default '%s'", time_str, default)
        fallback = parse_time_str(default)
        if fallback is None:
            _LOGGER.error("Default time '%s' is also invalid, using 00:00", default)
            return 0, 0
        return fallback

    def _window_times(self) -> tuple[time, time]:
        """Return the configured (start, end) window times, using defaults when invalid."""
        start_hour, start_minute = self._parse_time(
            str(self.config.get(CONF_START_TIME, DEFAULT_START_TIME)), DEFAULT_START_TIME
        )
        end_hour, end_minute = self._parse_time(
            str(self.config.get(CONF_END_TIME, DEFAULT_END_TIME)), DEFAULT_END_TIME
        )
        return time(start_hour, start_minute), time(end_hour, end_minute)

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

        unit = _unit_of(state)
        if unit in ("kw", "kilowatt", "kilowatts"):
            return value * 1000.0
        if unit in ("w", "watt", "watts"):
            return value
        return value

    def _ac_charge_limit_target(self) -> tuple[str, str] | None:
        """Return (entity_id, domain) of the AC charge limit entity, or None if unusable."""
        entity_id = self.config.get(CONF_CHARGE_POWER_ENTITY)
        if not entity_id:
            return None
        domain = str(entity_id).split(".")[0]
        if domain not in ("number", "input_number"):
            _LOGGER.warning("Charge power entity %s has unsupported domain %s", entity_id, domain)
            return None
        return str(entity_id), domain

    async def _write_ac_charge_limit(self, entity_id: str, domain: str, power_w: float, what: str) -> bool:
        """Write ``power_w`` to the AC charge limit entity in its own unit; report success."""
        value = float(power_w)
        state = self.hass.states.get(entity_id)
        unit = _unit_of(state)
        if unit in ("kw", "kilowatt", "kilowatts"):
            value = value / 1000.0
        try:
            await self.hass.services.async_call(
                domain,
                "set_value",
                {"entity_id": entity_id, "value": value},
            )
        except Exception as e:
            _LOGGER.error("Error writing AC charge limit (%s): %s", what, e, exc_info=True)
            return False
        _LOGGER.info("%s to %.3f (%s)", what, value, unit or "unitless")
        return True

    async def _set_ac_charge_limit_w(self, power_w: int) -> None:
        """Set max AC charge limit if entity is configured.

        The value found on the entity before the first write is remembered so
        that ``_reset_ac_charge_limit`` can restore it; without a readable value
        nothing is written, because a test value must never be left behind.
        """
        target = self._ac_charge_limit_target()
        if target is None:
            return
        entity_id, domain = target
        if self._original_ac_charge_power is None:
            current_w = self._get_power_w(entity_id)
            if current_w is None:
                _LOGGER.warning(
                    "Cannot read the current AC charge limit from %s - not writing a "
                    "test value that could not be restored",
                    entity_id,
                )
                return
            self._original_ac_charge_power = current_w
            _LOGGER.info("Stored original AC charge limit: %.0f W", current_w)
            self._persist_state()
        await self._write_ac_charge_limit(entity_id, domain, power_w, "Set AC charge limit")

    async def _reset_ac_charge_limit(self) -> bool:
        """Restore the AC charge limit captured before the finder's first write.

        Returns True when nothing is left to restore; on failure the original
        value is kept for a retry.
        """
        if self._original_ac_charge_power is None:
            return True
        target = self._ac_charge_limit_target()
        if target is None:
            # Entity no longer configured or unusable: nothing we can restore
            self._original_ac_charge_power = None
            return True
        entity_id, domain = target
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            _LOGGER.error("Cannot reset AC charge limit - entity %s unavailable", entity_id)
            return False
        if not await self._write_ac_charge_limit(
            entity_id, domain, self._original_ac_charge_power, "Reset AC charge limit"
        ):
            return False
        self._original_ac_charge_power = None
        self._planned_setpoint_written_w = None
        return True

    async def _apply_absolute_charge_power_limit(self) -> None:
        """Apply absolute max charge power (AC+DC) during AC charging.

        Like the AC charge limit and the discharge block, the value found on the
        entity before the first write is remembered (and persisted) so the
        window end can restore it. Without a readable value nothing is written -
        a limit that cannot be restored must never be left on the inverter.
        """
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

        domain = entity_id.split(".")[0]
        service = "set_value"
        if domain not in ("number", "input_number"):
            _LOGGER.warning("Absolute charge power entity %s has unsupported domain %s", entity_id, domain)
            return
        if self._original_absolute_charge_power is None:
            state = self.hass.states.get(entity_id)
            current = (
                _as_float(state.state)
                if state and state.state not in ("unknown", "unavailable")
                else None
            )
            if current is None:
                _LOGGER.warning(
                    "Cannot read the current absolute charge power from %s - not writing a "
                    "limit that could not be restored",
                    entity_id,
                )
                return
            self._original_absolute_charge_power = current
            _LOGGER.info("Stored original absolute charge power: %.3f", current)
            self._persist_state()
        value = max_power
        state = self.hass.states.get(entity_id)
        unit = _unit_of(state)
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

    async def _reset_absolute_charge_power(self) -> bool:
        """Restore the absolute charge power captured before the first write.

        Returns True when nothing is left to restore; on failure the original
        value is kept so a retry can still put it back.
        """
        if self._original_absolute_charge_power is None:
            return True
        entity_id = self.config.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY)
        domain = str(entity_id).split(".")[0] if entity_id else ""
        if not entity_id or domain not in ("number", "input_number"):
            # Entity no longer configured or unusable: nothing we can restore
            self._original_absolute_charge_power = None
            return True
        value = self._original_absolute_charge_power
        state = self.hass.states.get(entity_id)
        unit = _unit_of(state)
        try:
            await self.hass.services.async_call(
                domain,
                "set_value",
                {"entity_id": entity_id, "value": value},
            )
        except Exception as e:
            _LOGGER.error("Error resetting absolute charge power: %s", e, exc_info=True)
            return False
        _LOGGER.info("Reset absolute charge power to original value: %.3f (%s)", value, unit or "unitless")
        self._original_absolute_charge_power = None
        return True

    def get_auto_efficiency_data(self) -> dict[str, Any]:
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
        data = self.get_auto_efficiency_data()
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

        data = self.get_auto_efficiency_data()
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
                # Finder switched off during a test: record it and take the
                # test value off the inverter
                self._finalize_auto_test()
                await self._reset_ac_charge_limit()
            return
        if self._is_backup_active():
            # No inverter writes in backup mode; the window end restores the limit
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
            data = self.get_auto_efficiency_data()
            best_power = data.get("best_power_w")
            if isinstance(best_power, int):
                await self._set_ac_charge_limit_w(best_power)
                # The best value is the finder's result and is meant to stay on
                # the inverter, so it is not restored at window end.
                self._original_ac_charge_power = None
                self._persist_state()
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
        """Register window start/end triggers and schedule a check of the current window."""
        # Clear any existing triggers
        self.remove_time_triggers()

        try:
            start, end = self._window_times()

            if start == end:
                # The config flow rejects equal times, but entries created before
                # that validation existed (or an invalid time falling back to its
                # default) can still produce them. Registering triggers would start
                # and end the window in the same minute, so skip them and say why.
                _LOGGER.warning(
                    "Start time and end time are both %s; the charge window will "
                    "never activate. Set different times in the integration options.",
                    start.strftime("%H:%M"),
                )
            else:
                _LOGGER.info(
                    "Setting up charge window: %s - %s",
                    start.strftime("%H:%M"),
                    end.strftime("%H:%M"),
                )
                for handler, trigger_time in (
                    (self._on_window_start, start),
                    (self._on_scheduled_window_end, end),
                ):
                    self._time_triggers.append(
                        async_track_time_change(
                            self.hass,
                            handler,
                            hour=trigger_time.hour,
                            minute=trigger_time.minute,
                            second=0,
                        )
                    )

            # Check if we're already in the active window. The handle is kept so
            # the unload can cancel a check that has not run yet.
            self._cancel_window_check()
            self._window_check_task = self.hass.async_create_task(
                self._check_current_window(),
                name="inverter_charge_night_check_window",
            )

        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Failed to set up time triggers: %s", err, exc_info=True)
            self.remove_time_triggers()  # Clean up any partial setup

    def remove_time_triggers(self) -> None:
        """Remove time-based triggers."""
        for trigger in self._time_triggers:
            trigger()
        self._time_triggers.clear()

    def _cancel_window_check(self) -> None:
        """Cancel a scheduled window check that has not run yet."""
        task = self._window_check_task
        self._window_check_task = None
        if task is not None and not task.done():
            task.cancel()

    def update_time_triggers(self) -> None:
        """Update time triggers when configuration changes."""
        _LOGGER.info("Updating time triggers with new configuration")

        # Store old battery SOC entity to check if it changed
        old_battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)

        # Note: config reference is updated in async_update_entry before calling this
        # So self.config should already be updated, but ensure it's synced
        if self.entry.data != self.config:
            self.config = self.entry.data

        # Re-register triggers for the new times. This also schedules a window
        # check, which starts or ends the window if the new times changed whether
        # we are currently inside it.
        self.setup_time_triggers()

        # If battery SOC entity changed and we're active, update the listener
        new_battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
        if self.is_active and old_battery_soc_entity != new_battery_soc_entity:
            _LOGGER.info("Battery SOC entity changed from %s to %s, updating listener", old_battery_soc_entity, new_battery_soc_entity)
            self._setup_battery_soc_listener()

    async def _check_current_window(self) -> None:
        """Check if we're currently in the active window and adjust state if needed."""
        try:
            if self._ending:
                # A window end is resetting the inverter right now. Re-arming the
                # listeners here would write the window target back afterwards.
                _LOGGER.debug("Window end in progress - skipping the window check")
                return

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
            start_time_obj, end_time_obj = self._window_times()
            in_window = self._is_time_between(now_dt.time(), start_time_obj, end_time_obj)
            
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
                    await self._start_periodic_verification()
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
        # This window owns the inverter now: a reset still pending from the
        # previous window is superseded by this window's end. The original
        # value it kept is not captured again (original_min_soc stays set).
        self._cancel_reset_retry()
        self._reset_retry_count = 0  # this window's end starts a fresh backoff
        self._pending_reset = False

        # Arm the listeners and the verification first. The target calculation
        # no longer waits for the inverter; if the min SOC entity is not
        # available yet, _control_kostal skips the write and the verification
        # applies the target as soon as the entity reports a value.
        self._setup_battery_soc_listener()
        self._setup_inverter_min_soc_listener()
        await self._start_periodic_verification()

        # Calculate and store initial SOC for this charging period
        await self._calculate_initial_soc()

        # Night charge: keep stored PV in the battery while grid energy is cheap
        if not self.is_discharge_mode:
            await self._apply_discharge_block(announce=True)

        await self.async_request_refresh()

    async def _calculate_initial_soc(self) -> None:
        """Calculate and store the initial SOC for this charging period."""
        if self.snow_nights > 0 and not self.is_discharge_mode:
            # Snow on the modules: the forecast is wrong by definition, so it
            # is not read. Charge to the user maximum for this window.
            # Discharge windows are left alone (see current_target_soc).
            user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, DEFAULT_MAX_SOC))
            self.initial_calculated_soc = user_max_soc
            self.minimum_calculated_soc = user_max_soc
            self.calculated_soc = user_max_soc
            _LOGGER.info(
                "Snow mode: charging to %.0f%% (%d night(s) remaining)", user_max_soc, self.snow_nights
            )
            self._persist_state()
            return
        try:
            # Get configuration values
            battery_capacity = float(self.config.get(CONF_BATTERY_CAPACITY, 10.0))
            error_margin = float(self.config.get(CONF_FORECAST_ERROR_MARGIN, 10.0))
            user_min_soc = float(self.config.get(CONF_USER_MIN_SOC, 8.0))
            user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, 100.0))

            # Restart during an active window: the previous run persisted its
            # original min SOC and its target (restored in _restore_state).
            # Continue with that target instead of guessing it from the
            # inverter's live value - which is our own night target, not the
            # original - or recalculating it from a forecast that has moved on.
            if (
                self._pending_reset or self.original_min_soc is not None
            ) and self.initial_calculated_soc is not None:
                preserved_soc = self.initial_calculated_soc
                self.minimum_calculated_soc = preserved_soc
                self.calculated_soc = preserved_soc
                _LOGGER.info(
                    "Restart during active window - continuing with persisted target %.1f%% "
                    "(original min SOC: %s)",
                    preserved_soc,
                    f"{self.original_min_soc:.1f}%" if self.original_min_soc is not None else "not captured",
                )
                return

            # Otherwise, calculate normally from forecast
            pv_forecast_entity = self._get_active_forecast_entity()
            forecast_energy, forecast_available = self._parse_forecast_energy(pv_forecast_entity)

            calculated_soc: float | None
            plan = (
                await self._plan_target(forecast_energy, forecast_available)
                if self._planner_mode_is_bridge
                else None
            )
            if plan is not None:
                calculated_soc = plan.target_soc
            else:
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
            # Persist the target so a restart inside the window continues with it
            self._persist_state()

        except (ValueError, TypeError) as e:
            _LOGGER.error("Error calculating initial SOC: %s", e)
            self.initial_calculated_soc = None

    async def async_apply_operation_mode(self, mode: str) -> None:
        """Switch the operation mode, tearing an active window down first.

        A running window belongs to the mode it was started in: its inverter
        settings are reset and its listeners dropped before the new mode takes
        over. Without that, switching night charge to morning discharge turns
        grid charge on while force discharge is still on. Both ways into a mode
        change - the select entity and the options flow - go through here.
        """
        if mode == self.operation_mode or self._switching_mode:
            # Persisting state re-enters the entry update listener, which can
            # call this again while the teardown below is still awaiting.
            return
        self._switching_mode = True
        try:
            _LOGGER.info("Switching operation mode from %s to %s", self.operation_mode, mode)
            if self.is_active:
                # Same order as the window end: the window is over before the
                # first await, so a concurrent window check or verification
                # cannot re-arm listeners or write the old target back on top
                # of the values the reset restores.
                self.is_active = False
                self._ending = True
                try:
                    self._remove_battery_soc_listener()
                    self._remove_inverter_min_soc_listener()
                    await self._stop_periodic_verification()
                    try:
                        await self._reset_settings()
                    except Exception as e:
                        _LOGGER.error(
                            "Error resetting settings during mode switch: %s", e, exc_info=True
                        )
                finally:
                    self._ending = False
                self.target_reached = False
                self.initial_calculated_soc = None
                self.minimum_calculated_soc = None
                self.override_soc = None
                self.last_plan = None
                self.planned_charge_power_w = None
                self._persist_state()
            self.operation_mode = mode
        finally:
            self._switching_mode = False

    async def _on_scheduled_window_end(self, now: datetime) -> None:
        """Time-trigger entry point: the window reached its configured end time.

        Only an end that arrives here counts as a night that has been used up
        (snow mode); every other caller ends the window early.
        """
        await self._on_window_end(now, scheduled=True)

    async def _on_window_end(self, now: datetime, *, scheduled: bool = False) -> None:
        """Handle window end.

        The end trigger fires every day regardless of state, so without an
        active window there is nothing to end: resetting anyway would write the
        configured default over a min SOC the user set by hand, and arm an
        endless retry chain when the entity happens to be unavailable. A reset
        still pending from a real window is retried by its own timer.
        """
        if not self.is_active:
            _LOGGER.debug("Window end without an active window - nothing to reset")
            return
        mode_label = "Morning discharge" if self.is_discharge_mode else "Night charge"
        _LOGGER.info("%s window ended, resetting settings", mode_label)
        # The window is over from this point on, before the first await: a
        # concurrent window check or listener must not see it as active any more
        # and re-arm what is being torn down here (finding B9).
        self.is_active = False
        self._ending = True
        try:
            # Stop everything that could write the night target back to the
            # inverter before the reset. The verification task is awaited, so a
            # run that has already passed its is_active check cannot finish
            # after the reset.
            self._remove_battery_soc_listener()
            self._remove_inverter_min_soc_listener()
            await self._stop_periodic_verification()
            try:
                await self._reset_settings()
            except Exception as e:
                _LOGGER.error("Error resetting settings at window end: %s", e, exc_info=True)
                # Continue to reset state flags even if reset fails; the reset
                # did not get far enough to mark itself pending.
                self._record_reset_outcome(False)
        finally:
            # First, so a failure in the rest of this block cannot leave the
            # coordinator permanently ending: every window check and every
            # verification would become a no-op for the life of the entry.
            self._ending = False
            # CRITICAL: Always reset state flags, even if reset operation failed
            self._finalize_auto_test()
            self.target_reached = False
            self.initial_calculated_soc = None  # Reset initial SOC for next charging period
            self.minimum_calculated_soc = None  # Reset minimum SOC for next charging period
            self.override_soc = None  # Clear override when window ends
            self.last_plan = None
            self.planned_charge_power_w = None
            self._pv_crossover = None
            self._sun_fallback_logged = False
            # A failed reset is retried until the inverter is back at its
            # original settings; original_min_soc is kept for that (F3).
            # _reset_settings marks and schedules that itself.
            self._persist_state()
            # Only a window that actually ran to its scheduled end uses up a snow
            # night. A skipped, aborted or discharge window does not.
            if scheduled and not self.is_discharge_mode and self.snow_nights > 0:
                self.snow_nights -= 1
                _LOGGER.info("Snow mode: %d night(s) remaining", self.snow_nights)
                self._persist_state()
            await self.async_request_refresh()

    def _schedule_reset_retry(self) -> None:
        """Retry the failed reset after 60 s, 120 s, 240 s, then every 15 minutes."""
        self._cancel_reset_retry()
        if self._reset_retry_count < len(RESET_RETRY_DELAYS):
            delay = RESET_RETRY_DELAYS[self._reset_retry_count]
        else:
            delay = RESET_RETRY_INTERVAL
        self._reset_retry_count += 1

        async def _run_reset_retry(_now: datetime) -> None:
            self._reset_retry_unsub = None
            await self._retry_reset()

        self._reset_retry_unsub = async_call_later(self.hass, delay, _run_reset_retry)
        _LOGGER.warning(
            "Inverter settings are not reset yet - retrying in %d seconds (attempt %d)",
            delay,
            self._reset_retry_count,
        )

    def _record_reset_outcome(self, ok: bool) -> None:
        """Record whether the inverter is back at its original settings.

        Every caller of :meth:`_reset_settings` gets the retry for free this
        way - the window end, a reload, the enable switch and the mode select
        all used to drop the result on the floor and leave grid charging on with
        nothing retrying. While the entry is unloading no timer is armed (it
        would fire on a coordinator that no longer exists), but the flag is
        persisted, so the next setup picks the retry up again.
        """
        self._pending_reset = not ok
        if ok:
            self._reset_retry_count = 0
            self._cancel_reset_retry()
        elif self._unloading:
            _LOGGER.warning(
                "Inverter settings are not reset and the entry is unloading - "
                "retrying after the next setup"
            )
        else:
            self._schedule_reset_retry()
        self._persist_state()

    def _cancel_reset_retry(self) -> None:
        """Cancel a scheduled reset retry."""
        if self._reset_retry_unsub:
            self._reset_retry_unsub()
            self._reset_retry_unsub = None

    async def _retry_reset(self) -> None:
        """Run a deferred reset; keep rescheduling while it fails."""
        if not self._pending_reset:
            return
        if self.is_active:
            # A new window owns the inverter; its end performs the reset
            return
        if self._is_backup_active():
            # Never override backup mode: try again later
            _LOGGER.info("Backup mode active - deferring the reset retry")
            self._schedule_reset_retry()
            return
        # _reset_settings marks the outcome and reschedules a failed retry itself
        try:
            if await self._reset_settings():
                _LOGGER.info("Deferred reset of the inverter settings succeeded")
        except Exception as e:
            _LOGGER.error("Error retrying reset: %s", e, exc_info=True)
            self._record_reset_outcome(False)

    async def _reset_settings(self) -> bool:
        """Reset the inverter to its original settings.

        Returns True when every target was reached: min SOC restored, grid
        charging (and force discharge) off and the charge/discharge limits
        restored. On False the values needed for a retry are kept, the pending
        flag is set and the retry is scheduled - callers do not have to look at
        the result to keep the inverter from staying in the window's state.
        """
        # CRITICAL: Use stored original value if available, otherwise use configured default
        reset_min_soc = self.original_min_soc if self.original_min_soc is not None else float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
        kostal_min_soc_entity = self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY)
        if kostal_min_soc_entity and self.original_min_soc is None:
            # No floor was captured, so this integration never changed it:
            # writing the configured default would clobber a min SOC the user
            # set by hand (reachable by disabling the integration outside a
            # window, or when the entity was unavailable at window start).
            # Switching grid charge and force discharge off below stays safe,
            # as do the limit restores, which carry their own captures.
            _LOGGER.debug(
                "Min SOC restore skipped for %s - no original value was captured",
                kostal_min_soc_entity,
            )
            kostal_min_soc_entity = None
        kostal_grid_charge_switch = self.config.get(CONF_KOSTAL_GRID_CHARGE_SWITCH)

        ok = True

        # Reset min SOC
        if kostal_min_soc_entity:
            try:
                state = self.hass.states.get(kostal_min_soc_entity)
                if state and state.state not in ("unknown", "unavailable"):
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
                else:
                    _LOGGER.error("Cannot reset min SOC - entity state unavailable")
                    ok = False
            except Exception as e:
                _LOGGER.error("Error resetting min SOC: %s", e, exc_info=True)
                ok = False
        elif not self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY):
            # Reaching here with one configured means the guard above skipped the
            # restore, which already logged why.
            _LOGGER.warning("No min SOC entity configured - cannot reset")

        # Turn off grid charge
        if kostal_grid_charge_switch:
            try:
                state = self.hass.states.get(kostal_grid_charge_switch)
                if state and state.state not in ("unknown", "unavailable"):
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
                    ok = False
            except Exception as e:
                _LOGGER.error("Error turning off grid charge: %s", e, exc_info=True)
                ok = False
        else:
            _LOGGER.warning("No grid charge switch configured - cannot reset")

        # Turn off force discharge switch if configured (discharge mode cleanup)
        force_discharge_switch = self.config.get(CONF_FORCE_DISCHARGE_SWITCH)
        if force_discharge_switch:
            try:
                state = self.hass.states.get(force_discharge_switch)
                if not state or state.state in ("unknown", "unavailable"):
                    _LOGGER.error("Cannot reset force discharge - entity state unavailable")
                    ok = False
                elif state.state == "on":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": force_discharge_switch},
                    )
                    _LOGGER.info("Turned off force discharge switch during reset")
            except Exception as e:
                _LOGGER.error("Error turning off force discharge: %s", e, exc_info=True)
                ok = False

        # _last_soc_set is log information only; always forget it so the next
        # window starts from what the inverter actually reports.
        self._last_soc_set = None
        if not await self._reset_ac_charge_limit():
            ok = False
        if not await self._reset_discharge_limit():
            ok = False
        if not await self._reset_absolute_charge_power():
            ok = False
        # Forget the original values only once everything is back in place;
        # a retry needs them otherwise.
        if ok:
            self.original_min_soc = None
            self.override_soc = None  # Clear override when resetting
        # Persist unconditionally: a partial reset already cleared some capture
        # values, and leaving the old ones in the options would resurrect
        # day-old limits after a restart.
        self._record_reset_outcome(ok)
        return ok

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
        # Safety net for a lost end trigger (DST change, stalled event loop, failed
        # trigger setup): the window is bounded by the clock, not only by the trigger.
        # _is_time_between is inclusive at both ends, so a poll in the end minute
        # leaves the window to the trigger.
        start, end = self._window_times()
        if not self._is_time_between(dt_util.now().time(), start, end):
            _LOGGER.warning(
                "Window end was missed (now outside %s-%s); ending window from polling update",
                start,
                end,
            )
            await self._on_window_end(dt_util.now())
            return inactive_data

        try:
            battery_capacity = float(self.config.get(CONF_BATTERY_CAPACITY, 10.0))
            error_margin = float(self.config.get(CONF_FORECAST_ERROR_MARGIN, 10.0))
            user_min_soc = float(self.config.get(CONF_USER_MIN_SOC, 8.0))
            user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, 100.0))
        except (ValueError, TypeError) as e:
            _LOGGER.error("Error parsing configuration values: %s", e)
            return inactive_data
        
        # Use initial SOC calculated at window start, or recalculate if initial failed
        calculated_soc: float | None
        if self.initial_calculated_soc is not None:
            calculated_soc = self.initial_calculated_soc
            _LOGGER.debug("Using stored initial SOC: %.1f%%", calculated_soc)
            if self._planner_mode_is_bridge and not self.is_discharge_mode:
                # Solcast updates overnight: replan on every poll, target only rises
                calculated_soc = await self._replan_in_window(calculated_soc)
        else:
            # Fallback: recalculate if initial calculation failed. The forecast is
            # only read here so the normal path does not parse it on every poll.
            _LOGGER.warning("Initial SOC not available, recalculating as fallback")
            # Uses today's forecast for discharge, tomorrow's for charge
            pv_forecast_entity = self._get_active_forecast_entity()
            forecast_energy, forecast_available = self._parse_forecast_energy(pv_forecast_entity)
            plan = (
                await self._plan_target(forecast_energy, forecast_available)
                if self._planner_mode_is_bridge
                else None
            )
            if plan is not None:
                calculated_soc = plan.target_soc
            else:
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
        
        # The single source of truth for the target: manual override, else the plan.
        current_target = self.current_target_soc()
        target_soc: float = current_target if current_target is not None else calculated_soc
        # minimum_calculated_soc is diagnostic only: the lowest target seen this window.
        if self.minimum_calculated_soc is None or target_soc < self.minimum_calculated_soc:
            self.minimum_calculated_soc = target_soc
            _LOGGER.info(
                "Updated minimum SOC to %.1f%% (%s is lower)",
                target_soc,
                "override" if self.override_soc is not None else "calculated",
            )
        
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
                        self.planned_charge_power_w = 0.0
                        return {
                            "calculated_soc": calculated_soc,
                            "is_active": self.is_active,
                            "target_reached": self.target_reached,
                            "current_soc": current_battery_soc,
                            "operation_mode": self.operation_mode,
                            "skip_next": self.skip_next,
                            **self._plan_attributes(),
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
            # Charge power for the remaining window (written in bridge mode only)
            await self._plan_charge_power(target_soc)
        
        # Check if target is reached (reuse battery_soc_entity from above)
        current_soc = None
        if battery_soc_entity:
            state = self.hass.states.get(battery_soc_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current_soc = float(state.state)
                    check_target = target_soc
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
            **self._plan_attributes(),
        }

    def _capture_original_min_soc(self, current: float | None) -> None:
        """Remember the inverter's min SOC before the first write of a window.

        A value that is already set - captured earlier this window or restored
        from the persisted state after a restart - is never replaced: after a
        restart the live value is our own night target, not the original
        (finding F4). ``current`` is the live value, or None if it is not numeric.
        """
        if self.original_min_soc is not None:
            return
        if current is not None:
            self.original_min_soc = current
            _LOGGER.info("Stored original min SOC: %.1f%%", current)
        else:
            self.original_min_soc = float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
            _LOGGER.warning(
                "Could not read original min SOC, using configured default: %.1f%%",
                self.original_min_soc,
            )
        self._persist_state()

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
                state = self.hass.states.get(kostal_min_soc_entity)
                if not state or state.state in ("unknown", "unavailable"):
                    # Without the entity the floor cannot be set, so charging must
                    # not start either. Nothing has been written yet, so no original
                    # is captured; the periodic verification applies the target as
                    # soon as the entity reports a value (finding F6).
                    _LOGGER.warning(
                        "Min SOC entity %s is unavailable - deferring min SOC and grid "
                        "charge until it reports a value",
                        kostal_min_soc_entity,
                    )
                    should_skip_charging = True
                else:
                    try:
                        min_soc_current_value = float(state.state)
                    except (ValueError, TypeError):
                        pass
                    self._capture_original_min_soc(min_soc_current_value)

                    # Only update if value changed significantly (avoid unnecessary service calls and EEPROM wear)
                    # CRITICAL: Threshold increased to 0.5 to prevent "bricking" inverter memory
                    # The inverter's reported value is authoritative; _last_soc_set is log information only
                    if min_soc_current_value is None or abs(min_soc_current_value - target_soc) > 0.5:
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
            min_soc_set = False
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": kostal_min_soc_entity, "value": target_soc},
                )
                self._last_soc_set = target_soc
                min_soc_set = True
                _LOGGER.info(
                    "Set Kostal min SOC to %.1f%% (was %s)",
                    target_soc,
                    f"{min_soc_current_value:.1f}%" if min_soc_current_value is not None else "unknown",
                )
            except Exception as e:
                # Without the new floor the inverter would charge against the wrong
                # min SOC, so grid charging is not switched on; the next poll retries.
                _LOGGER.error("Error setting min SOC: %s", e, exc_info=True)

            # Immediately send grid charge command (configurable delay) so inverter processes both together
            if min_soc_set and kostal_grid_charge_switch and not should_skip_charging:
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
        # Force discharge needs the floor in place; without the entity it waits
        # for the periodic verification to apply the target (finding F6).
        min_soc_available = True

        if kostal_min_soc_entity:
            try:
                state = self.hass.states.get(kostal_min_soc_entity)
                if not state or state.state in ("unknown", "unavailable"):
                    _LOGGER.warning(
                        "Min SOC entity %s is unavailable - deferring discharge floor and "
                        "force discharge until it reports a value",
                        kostal_min_soc_entity,
                    )
                    min_soc_available = False
                else:
                    min_soc_current_value: float | None = None
                    try:
                        min_soc_current_value = float(state.state)
                    except (ValueError, TypeError):
                        pass
                    self._capture_original_min_soc(min_soc_current_value)

                    if min_soc_current_value is None or abs(min_soc_current_value - target_soc) > 0.5:
                        need_to_set_min_soc = True
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
        if force_discharge_switch and min_soc_available:
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
        # Charging is over for this window: take a running test's value off the inverter
        await self._reset_ac_charge_limit()
    
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
                target_soc = self.current_target_soc()
                
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
                target_soc = self.current_target_soc()
                
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
    
    async def _start_periodic_verification(self) -> None:
        """Start periodic verification task to check inverter min SOC matches our target."""
        await self._stop_periodic_verification()  # Stop any existing task
        
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
    
    async def _stop_periodic_verification(self) -> None:
        """Stop the periodic verification task and wait until it has finished.

        Waiting matters: a verification run that already passed its
        ``is_active`` check could otherwise write the night target back to
        the inverter after the window-end reset (finding F11).
        """
        task = self._verification_task
        self._verification_task = None
        if task is None or task.done():
            return
        task.cancel()
        if task is asyncio.current_task():
            return  # cancelling ourselves; the loop exits on its own
        await asyncio.gather(task, return_exceptions=True)
        _LOGGER.debug("Stopped periodic verification task")
    
    async def _verify_and_restore_min_soc(self) -> None:
        """Verify inverter min SOC matches our target and restore if needed."""
        if not self.is_active or not self.is_enabled or self._ending:
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
            # The discharge block belongs to the window state like the min SOC
            await self._apply_discharge_block()

            target_soc = self.current_target_soc()

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
                # The window may have ended while this run was waiting
                if not self.is_active:
                    return
                # First write of the window (entity was unavailable at window
                # start): remember what to restore before overwriting it
                self._capture_original_min_soc(current_inverter_soc)
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
