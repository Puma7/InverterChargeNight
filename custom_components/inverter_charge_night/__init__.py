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
    DEFAULT_COMMAND_DELAY,
    BACKUP_ACTIVE_STATES,
    BACKUP_INACTIVE_STATES,
    CONF_BACKUP_MODE_ENTITY,
    CONF_BACKUP_MODE_STATES,
    CONF_ACTIVE_START_DATE,
    CONF_ACTIVE_END_DATE,
    CONF_MIN_CHARGE_POWER_W,
    CONF_MAX_CHARGE_POWER_W,
    CONF_CHARGE_POWER_ENTITY,
    AUTO_TEST_MAX_ATTEMPTS,
    AUTO_TEST_MAX_PLAUSIBLE_LOSS,
    AUTO_TEST_MIN_DURATION_S,
    AUTO_TEST_MIN_ENERGY_WH,
    AUTO_TEST_MIN_FOLLOW_RATIO,
    AUTO_TEST_SETTLE_S,
    AUTO_TEST_STATE_FINISHED,
    AUTO_TEST_STATE_IDLE,
    AUTO_TEST_STATE_MEASURING,
    AUTO_TEST_STATE_SETTLING,
    AUTO_TEST_STATE_WAITING,
    CONF_CHARGE_ENERGY_RECEIVED_ENTITY,
    CONF_CHARGE_ENERGY_SENT_ENTITY,
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
    CONF_DISCHARGE_BLOCK_SWITCH,
    CONF_DISCHARGE_BLOCK_MODE,
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
    DEFAULT_DISCHARGE_BLOCK_MODE,
    DISCHARGE_BLOCK_OFF,
    DISCHARGE_BLOCK_VIA_SWITCH,
    DISCHARGE_BLOCK_VIA_LIMIT,
    DISCHARGE_BLOCK_VIA_MIN_SOC,
    MODE_MORNING_DISCHARGE,
    DOMAIN,
    AUTO_EFFICIENCY_KEYS,
    ENERGY_UNITS_KWH,
    ENERGY_UNITS_MWH,
    ENERGY_UNITS_WH,
    LEGACY_HOUSE_LOAD_ENERGY_ENTITY,
    LEGACY_UNUSED_DATA_KEYS,
    LEGACY_UNUSED_OPTION_KEYS,
    # House connection limit (plan 008)
    CONF_GRID_IMPORT_ENTITY,
    CONF_MAIN_FUSE_A,
    CONF_GRID_PHASES,
    CONF_GRID_VOLTAGE_V,
    CONF_GRID_CONTINUOUS_PCT,
    CONF_GRID_MAX_CONTINUOUS_W,
    CONF_GRID_HEADROOM_W,
    DEFAULT_GRID_PHASES,
    DEFAULT_GRID_VOLTAGE_V,
    DEFAULT_GRID_CONTINUOUS_PCT,
    DEFAULT_GRID_HEADROOM_W,
    DEFAULT_MIN_CHARGE_POWER_W,
    DEFAULT_MAX_CHARGE_POWER_W,
    GRID_LIMIT_STALE_AFTER_S,
    GRID_LIMIT_MIN_WRITE_INTERVAL_S,
)
from .calculation import calculate_required_soc
from .planner import (
    REASON_FALLBACK,
    PlanInput,
    PlanResult,
    allowed_charge_power_w,
    grid_budget_w,
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


def _clear_stale_entity_issues(hass: HomeAssistant) -> None:
    """Drop "entity not available" issues that nobody can act on any more.

    The issue is not fixable from the repairs page, so one raised for an entity
    that has since been renamed - or replaced when the inverter integration
    changed - would stay red for ever, with no way for the user to clear it.
    An issue is dropped once its entity exists again, or once no configuration
    entry of this integration names it any more.
    """
    registry = ir.async_get(hass)
    configured: set[str] = set()
    for other in hass.config_entries.async_entries(DOMAIN):
        configured.update(
            str(value)
            for value in {**other.data, **other.options}.values()
            if isinstance(value, str) and "." in value
        )
    prefix = "entity_not_available_"
    for (domain, issue_id) in list(registry.issues):
        if domain != DOMAIN or not issue_id.startswith(prefix):
            continue
        entity_id = issue_id[len(prefix):]
        if entity_id not in configured or hass.states.get(entity_id) is not None:
            _LOGGER.debug("Clearing the stale repair issue for %s", entity_id)
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def _migrate_entry_data(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> None:
    """Carry settings of earlier versions over, and drop what nobody reads.

    Two things accumulate in a config entry over the life of an integration:
    settings whose key has been renamed, and data written by a version that no
    longer exists. The second kind is not harmless - one installation carried
    500 dead measurement records, a hundred kilobytes that Home Assistant
    loads, writes back and stores with every single change.
    """
    data = dict(entry.data)
    options = dict(entry.options)
    changed = False

    legacy_load_meter = data.pop(LEGACY_HOUSE_LOAD_ENERGY_ENTITY, None)
    if legacy_load_meter and not data.get(CONF_HOUSE_LOAD_ENTITY):
        # Same quantity under a new name: a cumulative kWh meter of the house.
        data[CONF_HOUSE_LOAD_ENTITY] = legacy_load_meter
        _LOGGER.info(
            "Carried the house consumption meter %s over from an earlier version",
            legacy_load_meter,
        )
    changed = changed or legacy_load_meter is not None

    for key in LEGACY_UNUSED_DATA_KEYS:
        if key in data:
            value = data.pop(key)
            changed = True
            if value:
                _LOGGER.info(
                    "The setting %r from an earlier version is no longer used. Its entity "
                    "%s is a good choice for the efficiency search's energy meters in the "
                    "options",
                    key,
                    value,
                )

    for key in LEGACY_UNUSED_OPTION_KEYS:
        if key in options:
            options.pop(key)
            changed = True

    efficiency = options.get(CONF_AUTO_EFFICIENCY_DATA)
    if isinstance(efficiency, dict):
        kept = {k: v for k, v in efficiency.items() if k in AUTO_EFFICIENCY_KEYS}
        if kept != efficiency:
            dropped = sorted(set(efficiency) - set(kept))
            _LOGGER.info(
                "Dropping efficiency data of an earlier version from the configuration: %s",
                ", ".join(dropped),
            )
            options[CONF_AUTO_EFFICIENCY_DATA] = kept
            changed = True

    if changed:
        hass.config_entries.async_update_entry(entry, data=data, options=options)


async def async_setup_entry(hass: HomeAssistant, entry: InverterChargeNightConfigEntry) -> bool:
    """Set up Inverter Charge Night from a config entry."""
    _migrate_entry_data(hass, entry)
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
    # All required entities are known: clear issues from earlier failed attempts,
    # including ones left behind by entities that are no longer configured.
    _clear_stale_entity_issues(hass)

    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.review_discharge_block_risk()
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
    # Captured before the swap below: afterwards it is the new value, and the
    # listener that watches the old entity would never be re-armed.
    old_battery_soc_entity = coordinator.config.get(CONF_BATTERY_SOC_ENTITY)
    
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
    coordinator.review_discharge_block_risk()
    coordinator.auto_efficient_charge = entry.data.get(CONF_AUTO_EFFICIENT_CHARGE, False)
    coordinator._auto_missing_entities_logged = False
    coordinator._house_load_cache = None  # the meter or the average may have changed
    
    # Update coordinator polling interval
    coordinator.update_interval = timedelta(
        seconds=entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    )
    
    # Update time triggers with new configuration
    try:
        coordinator.update_time_triggers(previous_soc_entity=old_battery_soc_entity)
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
        coordinator._remove_grid_import_listener()
        coordinator._remove_auto_sample_listener()
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


def _as_float_strict(value: Any) -> float:
    """Like :func:`_as_float`, but raises instead of returning None.

    For the places that already sit inside ``except (ValueError, TypeError)``
    and treat an unusable reading as "no reading": raising keeps that handling
    and makes ``nan`` take the same path as ``"unavailable"``, rather than
    silently poisoning a comparison that decides whether to stop charging.
    """
    number = _as_float(value)
    if number is None:
        raise ValueError(f"not a usable number: {value!r}")
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
    # Plan 009: the raised min SOC floor of the running window, the state the
    # discharge block switch had before this window turned it on, and the way
    # already announced in the log for this window.
    _window_floor_soc: float | None = None
    _window_started_at: datetime | None = None
    _original_discharge_block: bool | None = None
    _discharge_block_logged: str | None = None
    # True while _on_window_end tears the window down: nothing may re-arm the
    # listeners or write the window target back to the inverter in between.
    _ending: bool = False
    _switching_mode: bool = False
    # True once async_unload_entry started: no new timers may be armed.
    _unloading: bool = False
    # House connection limit (plan 008): read by the sensor and by the teardown
    # paths, which may run on an instance whose constructor did not.
    _grid_limit_listener: CALLBACK_TYPE | None = None
    _grid_limit_last_write: datetime | None = None
    _grid_budget_cache_w: float | None = None
    _grid_import_w: float | None = None
    _grid_other_load_w: float | None = None
    _grid_allowed_w: float | None = None
    _grid_limited: bool = False
    _grid_stale_logged: bool = False
    _grid_own_draw_logged: bool = False
    _backup_unreadable_logged: bool = False
    _auto_range_warned: bool = False
    _grid_plan_request_w: float | None = None

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
        # The measurement proper starts only after the inverter has settled on
        # the new setpoint; everything before that is ramp, not steady state.
        self._auto_measure_start: datetime | None = None
        self._auto_last_sent_w: float | None = None
        self._auto_last_received_w: float | None = None
        self._auto_meter_start: tuple[float, float] | None = None
        self._auto_sample_listener: CALLBACK_TYPE | None = None
        self._auto_last_result: dict[str, Any] | None = None
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
        # House connection limit (plan 008)
        self._grid_limit_listener: CALLBACK_TYPE | None = None
        self._grid_limit_last_write: datetime | None = None
        self._grid_budget_cache_w: float | None = None
        self._grid_import_w: float | None = None
        self._grid_other_load_w: float | None = None
        self._grid_allowed_w: float | None = None
        self._grid_limited = False
        self._grid_stale_logged = False
        self._grid_own_draw_logged = False
        self._backup_unreadable_logged = False
        self._auto_range_warned = False
        self._backup_unknown_logged: set[str] = set()
        self._grid_plan_request_w = None
        # Discharge block (plan 009). The floor is the highest battery SOC seen
        # this window; it only ever rises, so a jittering measurement cannot
        # produce a write. None outside a window.
        self._window_floor_soc: float | None = None
        # When the running window started. Persisted so a restart can tell a
        # target that belongs to this window from one left behind by a window
        # whose end Home Assistant was not running for.
        self._window_started_at: datetime | None = None
        self._original_discharge_block: bool | None = None
        self._discharge_block_logged: str | None = None
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
            # The raised floor belongs to the running window only; a stale one
            # would block the battery for good after a restart (plan 009).
            "window_floor_soc": self._window_floor_soc if self.is_active else None,
            "window_started_at": (
                self._window_started_at.isoformat()
                if self.is_active and self._window_started_at is not None
                else None
            ),
            "original_discharge_block": self._original_discharge_block,
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
        # The floor belongs to the window that also captured the inverter's
        # original floor. Without that original no window was running when Home
        # Assistant stopped, so a stored floor is stale: carrying it into the
        # next window would charge the battery to a level nobody planned.
        restored_floor = self._restore_soc(state, "window_floor_soc")
        self._window_floor_soc = restored_floor if self.original_min_soc is not None else None
        if restored_floor is not None and self._window_floor_soc is None:
            _LOGGER.info(
                "Discarding a stored discharge-block floor of %.1f%%: its window is over",
                restored_floor,
            )
        started_raw = state.get("window_started_at")
        if isinstance(started_raw, str):
            try:
                self._window_started_at = datetime.fromisoformat(started_raw)
            except ValueError:
                self._window_started_at = None
        block_raw = state.get("original_discharge_block")
        if isinstance(block_raw, bool):
            self._original_discharge_block = block_raw
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

    def inverter_floor_soc(self, target_soc: float | None = None) -> float | None:
        """The value written to the inverter's min SOC entity (plan 009).

        Charge target and written floor are two different things. They are equal
        unless the discharge block runs over the min SOC: a battery does not
        discharge below its min SOC, so raising the floor to the charge level the
        window started at keeps stored PV in the battery while grid energy is
        cheap. The floor only ever rises within a window and always stays inside
        ``[user_min_soc, user_max_soc]``.

        Everything that compares or restores the *written* min SOC must use this,
        not :meth:`current_target_soc` - otherwise the verification writes the
        raised floor straight back down. "Target reached" keeps using the charge
        target.
        """
        if target_soc is None:
            target_soc = self.current_target_soc()
        if target_soc is None:
            return None
        if not self.is_active or self._ending:
            return target_soc
        if self._discharge_block_method() != DISCHARGE_BLOCK_VIA_MIN_SOC:
            return target_soc
        floor = self._window_floor_soc
        if floor is None or floor <= target_soc:
            return target_soc
        user_min_soc = float(self.config.get(CONF_USER_MIN_SOC, 8.0))
        user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, DEFAULT_MAX_SOC))
        if not user_min_soc <= target_soc <= user_max_soc:
            # The target itself is out of bounds; _control_kostal refuses it and
            # logs why. Raising it here would paper over that.
            return target_soc
        return max(user_min_soc, min(user_max_soc, floor))

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
            if self._unloading or not self._is_the_live_coordinator():
                # Armed in the constructor, so it survives a setup that failed
                # afterwards. Writing the options of an entry that never loaded
                # helps nobody.
                _LOGGER.debug("Skip next expired on a coordinator that is no longer in use")
                return
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

    def _window_length_s(self) -> float:
        """Length of the configured window in seconds; 24 h when it is degenerate."""
        start, end = self._window_times()
        start_minutes = start.hour * 60 + start.minute
        end_minutes = end.hour * 60 + end.minute
        span = (end_minutes - start_minutes) % (24 * 60)
        return float(span * 60) if span else 24 * 3600.0

    def _window_end_datetime(self, now: datetime) -> datetime:
        """Return the end of the window that is running or comes next, at minute resolution.

        The end minute itself still belongs to the window (``_is_time_between``
        is inclusive), so an end time equal to the current minute is today's.

        The result carries ``now``'s own tzinfo, so subtracting the two gives
        the time that will really pass - one hour less across a spring-forward,
        one hour more across a fall-back - which is what the charge power has
        to be planned against. What it cannot express is the repeated hour of a
        fall-back, where the end minute occurs twice; ``_plan_charge_power``
        guards the resulting near-zero remainder.
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
            value = _as_float(state.state)
            if value is not None:
                unit = _unit_of(state)  # unit rules are described in the docstring
                if unit in ENERGY_UNITS_WH:
                    energy = value / 1000.0
                elif unit in ENERGY_UNITS_KWH:
                    energy = value
                elif unit in ENERGY_UNITS_MWH:
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
            # Not a usable number (text, nan, inf): report the forecast as
            # unavailable so callers apply the safe fallback rather than
            # planning a target from a value that poisons every comparison.
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
        sensor; it is written to the AC charge limit in bridge mode and, since
        plan 008, whenever a house connection limit is configured - a protection
        must not depend on the planner mode. It is never written while the
        finder owns the limit, and only when it moves by more than
        PLANNED_POWER_WRITE_THRESHOLD_W. The house connection limit is applied
        after the calculation and before the write; that order is what keeps the
        setpoint from ever exceeding what the connection can carry.
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
        # What the battery would draw without the house connection limit. Kept
        # so the sensor can say whether the limit is actually holding it back.
        self._grid_plan_request_w = setpoint
        # Plan, then limit, then write - never the other way round (plan 008).
        setpoint = self._grid_limited_setpoint(setpoint)
        self.planned_charge_power_w = setpoint
        if self.auto_efficient_charge or self._auto_test_active:
            return
        if not self._planner_mode_is_bridge and not self._grid_limit_configured():
            # The planner only owns the setpoint in bridge mode, but the house
            # connection limit is a protection and must not depend on the mode.
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
        if await self._set_ac_charge_limit_w(int(setpoint)):
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

    def _discharge_block_method(self) -> str | None:
        """Which of the three ways blocks the discharge this window (plan 009).

        Checked against the configuration in this order: a switch of the
        inverter, the discharge power limit, and finally the min SOC - which
        needs no vendor feature at all. ``None`` means the battery is left free
        to discharge: the mode is off, or this is a morning discharge window,
        where a block would be pointless.
        """
        if self.is_discharge_mode:
            return None
        mode = str(self.config.get(CONF_DISCHARGE_BLOCK_MODE, DEFAULT_DISCHARGE_BLOCK_MODE))
        if mode == DISCHARGE_BLOCK_OFF:
            return None
        if self.config.get(CONF_DISCHARGE_BLOCK_SWITCH):
            return DISCHARGE_BLOCK_VIA_SWITCH
        if self.config.get(CONF_DISCHARGE_LIMIT_ENTITY):
            return DISCHARGE_BLOCK_VIA_LIMIT
        return DISCHARGE_BLOCK_VIA_MIN_SOC

    def discharge_block_state(self) -> str:
        """The way in use, for the ``discharge_block`` sensor attribute."""
        return self._discharge_block_method() or DISCHARGE_BLOCK_OFF

    def _update_window_floor(self) -> None:
        """Raise the window's min SOC floor to the current battery SOC.

        Monotone on purpose: if the charge level falls anyway (the block did not
        take), the floor stays up and the inverter recharges from the grid -
        which is exactly right inside the window, where grid energy is cheap.

        The floor does not follow our own grid charging. It would otherwise feed
        back on itself: the floor is written to the min SOC, the inverter buys
        energy up to it, overshoots it a little, the floor follows the new charge
        level, and the battery ratchets to the user maximum night after night -
        past the planned target, at full price, and with no room left for the
        next day's PV.
        """
        if not self.is_active or self._ending:
            return
        if self._discharge_block_method() != DISCHARGE_BLOCK_VIA_MIN_SOC:
            return
        current = self._current_battery_soc()
        if current is None:
            return
        previous = self._window_floor_soc
        if previous is not None and current <= previous:
            return
        if previous is not None and self._grid_charging_is_on():
            # Rising while we are buying: that is our own charge, not something
            # the block has to protect. The target already keeps the floor up.
            return
        self._window_floor_soc = current
        _LOGGER.info(
            "Discharge block: min SOC floor raised to %.1f%% (was %s)",
            current,
            f"{previous:.1f}%" if previous is not None else "unset",
        )
        self._persist_state()

    def _grid_charging_is_on(self) -> bool:
        """True while the inverter is charging the battery from the grid on our order."""
        switch = self.config.get(CONF_KOSTAL_GRID_CHARGE_SWITCH)
        if not switch:
            # Without the switch the integration cannot tell; the charge target
            # is the honest assumption while the target is not reached yet.
            return not self.target_reached
        state = self.hass.states.get(str(switch))
        return state is not None and state.state == "on"

    def review_discharge_block_risk(self) -> None:
        """Warn when blocking the discharge could leave the house dark.

        Raising the min SOC is the fallback that works on every inverter, and
        it has one failure mode that is not the integration's to fix: during a
        power cut the house runs on the battery, and a battery does not
        discharge below its min SOC. Without an entity that reports island
        operation, this integration cannot know to get out of the way - so it
        says so where the user will see it, once, rather than in a log line
        nobody reads at three in the morning.
        """
        issue_id = f"discharge_block_without_backup_{self.entry.entry_id}"
        at_risk = self._discharge_block_method() == DISCHARGE_BLOCK_VIA_MIN_SOC and not (
            self.config.get(CONF_BACKUP_MODE_ENTITY)
        )
        if not at_risk:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="discharge_block_without_backup",
            translation_placeholders={"name": str(self.entry.title)},
        )

    def release_window_floor_to(self, target_soc: float | None) -> None:
        """Let the discharge-block floor follow a target the user lowered.

        The floor only ever rises on its own - that is what keeps the charge
        the window bought. But when the user lowers the target by hand, they
        are asking for the opposite, and a floor left above it would hold the
        battery blocked until the window ends with no way to tell why.
        """
        if target_soc is None or self._window_floor_soc is None:
            return
        if self._window_floor_soc <= target_soc:
            return
        _LOGGER.info(
            "Lowering the discharge-block floor from %.1f%% to %.1f%%: the target was "
            "lowered by hand",
            self._window_floor_soc,
            target_soc,
        )
        self._window_floor_soc = target_soc
        self._persist_state()

    def _discharge_block_switch_target(self) -> tuple[str, str] | None:
        """Return (entity_id, domain) of the discharge block switch, or None."""
        entity_id = self.config.get(CONF_DISCHARGE_BLOCK_SWITCH)
        if not entity_id:
            return None
        domain = str(entity_id).split(".")[0]
        if domain not in ("switch", "input_boolean"):
            _LOGGER.warning(
                "Discharge block switch %s has unsupported domain %s", entity_id, domain
            )
            return None
        return str(entity_id), domain

    async def _apply_discharge_block_switch(self) -> None:
        """Turn the inverter's discharge block switch on, remembering its state."""
        target = self._discharge_block_switch_target()
        if target is None:
            return
        entity_id, domain = target
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            _LOGGER.warning("Discharge block switch %s is unavailable - block deferred", entity_id)
            return
        was_on = state.state == "on"
        if self._original_discharge_block is None:
            self._original_discharge_block = was_on
            _LOGGER.info("Stored original discharge block switch state: %s", state.state)
            self._persist_state()
        if was_on:
            _LOGGER.debug("Discharge already blocked (%s is on)", entity_id)
            return
        try:
            await self.hass.services.async_call(domain, "turn_on", {"entity_id": entity_id})
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.error("Error blocking discharge via %s: %s", entity_id, e, exc_info=True)
            return
        _LOGGER.info("Blocked battery discharge via %s", entity_id)

    async def _reset_discharge_block_switch(self) -> bool:
        """Put the discharge block switch back to the state captured at window start."""
        if self._original_discharge_block is None:
            return True
        target = self._discharge_block_switch_target()
        if target is None:
            # Entity no longer configured or unusable: nothing we can restore
            self._original_discharge_block = None
            return True
        entity_id, domain = target
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            _LOGGER.error("Cannot reset discharge block - entity %s unavailable", entity_id)
            return False
        service = "turn_on" if self._original_discharge_block else "turn_off"
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id})
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.error("Error resetting discharge block switch: %s", e, exc_info=True)
            return False
        _LOGGER.info("Reset discharge block switch %s to %s", entity_id, service)
        self._original_discharge_block = None
        return True

    async def _write_raised_min_soc_floor(self) -> None:
        """Write the floor to the min SOC entity when it sits above the target.

        Only then: with floor == charge target nothing about the existing
        control flow changes, and _control_kostal writes it on the next update
        as it always did.
        """
        target_soc = self.current_target_soc()
        floor_soc = self.inverter_floor_soc(target_soc)
        if target_soc is None or floor_soc is None or floor_soc <= target_soc:
            return
        entity_id = self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY)
        if not entity_id:
            return
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Cannot raise the min SOC floor yet - entity unavailable")
            return
        current = _as_float(state.state)
        self._capture_original_min_soc(current)
        if current is not None and abs(current - floor_soc) <= 0.5:
            self._last_soc_set = floor_soc
            return
        try:
            await self.hass.services.async_call(
                "number", "set_value", {"entity_id": entity_id, "value": floor_soc}
            )
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.error("Error raising the min SOC floor: %s", e, exc_info=True)
            return
        self._last_soc_set = floor_soc
        _LOGGER.info(
            "Discharge block: raised inverter min SOC to %.1f%% (charge target %.1f%%)",
            floor_soc,
            target_soc,
        )

    async def _apply_discharge_block(self, announce: bool = False) -> None:
        """Keep the battery from running the house while the window is open.

        Three ways, chosen by :meth:`_discharge_block_method`: the inverter's
        own switch, the discharge power limit set to 0, or - on an inverter that
        offers neither - raising the min SOC floor, which every battery honours.
        Never applied in discharge mode or in backup mode. ``announce`` logs the
        chosen way once per window.
        """
        if self.is_discharge_mode or self._is_backup_active():
            return
        method = self._discharge_block_method()
        if method is None:
            if announce:
                _LOGGER.info(
                    "Discharge block is off - the battery may discharge into the house "
                    "during the window"
                )
            return
        if announce and self._discharge_block_logged != method:
            _LOGGER.info("Blocking battery discharge for this window via: %s", method)
            if (
                method == DISCHARGE_BLOCK_VIA_MIN_SOC
                and self.config.get(CONF_DISCHARGE_BLOCK_MODE) is None
            ):
                # An entry configured before plan 009 never chose this; say so
                # once, because the inverter now shows a min SOC above the
                # charge target until the window ends.
                _LOGGER.info(
                    "No discharge block was configured, so the min SOC is raised to the "
                    "charge level for the window and reset at its end. The inverter "
                    "therefore shows a higher min SOC than the charge target while the "
                    "window runs. Set the discharge block to 'off' in the options to keep "
                    "the previous behaviour"
                )
            self._discharge_block_logged = method
        if method == DISCHARGE_BLOCK_VIA_SWITCH:
            await self._apply_discharge_block_switch()
            return
        if method == DISCHARGE_BLOCK_VIA_MIN_SOC:
            # The floor is written by _control_kostal and _verify_and_restore_min_soc,
            # which both read it back through inverter_floor_soc().
            self._update_window_floor()
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

    def _backup_mode_states(self) -> frozenset[str]:
        """The states the user declared as "backup mode", lowercased."""
        raw = self.config.get(CONF_BACKUP_MODE_STATES)
        if not raw:
            return frozenset()
        return frozenset(
            part.strip().lower() for part in str(raw).split(",") if part.strip()
        )

    def _is_backup_active(self) -> bool:
        """True while the house runs on the battery instead of the grid.

        Backup (island) operation is the one state in which this integration
        must keep its hands off the inverter entirely. There is no grid to
        charge from, and - far more important - a raised min SOC would stop
        the battery from supplying the house: the lights go out in a power
        cut, which is precisely when they must not.

        The state is read from whatever entity the user configured. A switch
        or binary sensor answers on/off; a plain sensor can say anything at
        all, so the states that mean backup can be declared in the
        configuration (Kostal reports ``ESB`` for Ersatzstrombetrieb). Without
        that declaration the words that unambiguously mean island operation
        are recognised, and anything else is reported once in the log rather
        than guessed at.
        """
        backup_entity = self.config.get(CONF_BACKUP_MODE_ENTITY)
        if not backup_entity:
            return False
        state = self.hass.states.get(backup_entity)
        if not state or state.state in ("unknown", "unavailable", None):
            if not self._backup_unreadable_logged:
                _LOGGER.warning(
                    "Backup mode entity %s is unavailable - the integration cannot tell "
                    "whether the house is running on the battery",
                    backup_entity,
                )
                self._backup_unreadable_logged = True
            return False
        self._backup_unreadable_logged = False
        value = str(state.state).strip().lower()

        declared = self._backup_mode_states()
        if declared:
            # The user named the exact states; nothing else counts.
            return value in declared

        if value in BACKUP_ACTIVE_STATES:
            return True
        if value in BACKUP_INACTIVE_STATES:
            return False

        domain = str(backup_entity).split(".")[0]
        if domain in ("switch", "binary_sensor", "input_boolean"):
            # A binary entity has no third state, so whatever this is, it is
            # not "off". Erring towards backup only costs a night of charging.
            _LOGGER.warning(
                "Backup mode entity %s reports the unexpected state %r - treating it as "
                "backup mode and leaving the inverter alone",
                backup_entity,
                state.state,
            )
            return True

        if value not in self._backup_unknown_logged:
            # A sensor with free-form states: guessing either way is wrong.
            # Saying so once, with the remedy, beats silently blocking the
            # integration or silently ignoring an island.
            self._backup_unknown_logged.add(value)
            _LOGGER.warning(
                "Backup mode entity %s reports %r, which the integration does not "
                "recognise as backup or grid operation. If this state means the house "
                "runs on the battery, add it to 'Backup mode states' in the options "
                "(comma separated)",
                backup_entity,
                state.state,
            )
        return False

    def _get_power_w(self, entity_id: str | None) -> float | None:
        """Return power in W for a given entity, or None if unavailable."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable", None):
            return None
        value = _as_float(state.state)
        if value is None:
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
        """Write ``power_w`` to the AC charge limit entity in its own unit; report success.

        An entity without a unit is the dangerous case: writing 5000 to a
        number that counts kilowatts asks for 5 MW, and an inverter that clamps
        that to its maximum turns a protective limit into full power. The
        number entity's own ``max`` attribute settles it - a charge limit whose
        maximum is below 1000 counts kilowatts - and when even that is missing,
        a value the entity cannot accept is not written at all.
        """
        value = float(power_w)
        state = self.hass.states.get(entity_id)
        unit = _unit_of(state)
        if unit in ("kw", "kilowatt", "kilowatts"):
            value = value / 1000.0
        elif unit not in ("w", "watt", "watts"):
            entity_max = _as_float(getattr(state, "attributes", {}).get("max")) if state else None
            if entity_max is not None and 0 < entity_max < 1000:
                value = value / 1000.0
                _LOGGER.debug(
                    "%s reports no unit but a maximum of %.0f - writing kilowatts",
                    entity_id,
                    entity_max,
                )
            elif entity_max is not None and value > entity_max:
                _LOGGER.error(
                    "Not writing %.0f to %s: the entity accepts at most %.0f and reports no "
                    "unit, so the scale is unknown. Set a unit of measurement (W or kW) on it",
                    value,
                    entity_id,
                    entity_max,
                )
                return False
            elif entity_max is None:
                _LOGGER.warning(
                    "%s reports neither a unit nor a maximum - writing %.0f as watts",
                    entity_id,
                    value,
                )
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

    async def _set_ac_charge_limit_w(self, power_w: int) -> bool:
        """Set max AC charge limit if entity is configured.

        The value found on the entity before the first write is remembered so
        that ``_reset_ac_charge_limit`` can restore it; without a readable value
        nothing is written, because a test value must never be left behind.

        This is the single place where a charge setpoint reaches the inverter,
        so the house connection limit is applied here as well (plan 008): not
        even the efficiency finder may order more than the connection carries.

        Returns True only when the value actually reached the inverter. The
        callers record what they believe stands there, and every later
        comparison is "write only downwards from that" - so believing a failed
        write would suppress the retry of a protective limit.
        """
        target = self._ac_charge_limit_target()
        if target is None:
            return False
        entity_id, domain = target
        limited_w = int(self._grid_limited_setpoint(float(power_w)))
        if limited_w < power_w:
            _LOGGER.info(
                "House connection limit: writing %d W instead of the requested %d W",
                limited_w,
                power_w,
            )
            power_w = limited_w
        if self._original_ac_charge_power is None:
            current_w = self._get_power_w(entity_id)
            if current_w is None:
                _LOGGER.warning(
                    "Cannot read the current AC charge limit from %s - not writing a "
                    "test value that could not be restored",
                    entity_id,
                )
                return False
            self._original_ac_charge_power = current_w
            _LOGGER.info("Stored original AC charge limit: %.0f W", current_w)
            self._persist_state()
        if not await self._write_ac_charge_limit(
            entity_id, domain, power_w, "Set AC charge limit"
        ):
            return False
        self._grid_limit_last_write = dt_util.now()
        return True

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
            self._planned_setpoint_written_w = None
            self._grid_plan_request_w = None
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
        self._grid_plan_request_w = None
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
        """Load persisted auto efficiency data from entry options.

        Samples are dropped when the configured charge power range has changed
        since they were taken: the search narrows a range, and a range that
        moved makes the stored bounds - and with them the conclusions drawn
        from the old samples - meaningless.
        """
        data = dict(self.entry.options.get(CONF_AUTO_EFFICIENCY_DATA, {}))
        data.setdefault("history", {})
        bounds = data.get("bounds_w")
        current = [
            int(self.config.get(CONF_MIN_CHARGE_POWER_W, 1000)),
            int(self.config.get(CONF_MAX_CHARGE_POWER_W, 10000)),
        ]
        if isinstance(bounds, list) and [int(b) for b in bounds] != current:
            _LOGGER.info(
                "Charge power range changed from %s to %s - starting the efficiency search over",
                bounds,
                current,
            )
            data = {"history": {}, "bounds_w": current}
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
        """The next charge power to measure, or None when the search is over.

        Golden-section search over [range_min, range_max]. None means one thing
        only: there is nothing left to learn, because the interval is down to
        one step. A point that cannot be measured - the battery is full before
        the measurement completes, or the inverter never followed the setpoint -
        narrows the interval like a measured one instead of ending the search,
        which would otherwise stop with large unexplored stretches and keep a
        far-from-optimal "best" for good.
        """
        min_w = int(self.config.get(CONF_MIN_CHARGE_POWER_W, 1000))
        max_w = int(self.config.get(CONF_MAX_CHARGE_POWER_W, 10000))
        if min_w >= max_w:
            if not self._auto_range_warned:
                _LOGGER.warning(
                    "The efficiency search has nothing to search: minimum charge power "
                    "(%d W) is not below the maximum (%d W)",
                    min_w,
                    max_w,
                )
                self._auto_range_warned = True
            return None

        step_w = 100  # 0.1 kW precision
        data = self.get_auto_efficiency_data()
        history = {int(k): v for k, v in data.get("history", {}).items()}
        failed = {int(k): int(v) for k, v in dict(data.get("failed", {})).items()}
        # A power that could not be measured repeatedly counts as done, so the
        # search moves on instead of asking for the same sample every night.
        unmeasurable = {p for p, n in failed.items() if n >= AUTO_TEST_MAX_ATTEMPTS}
        range_min = int(data.get("range_min_w", min_w))
        range_max = int(data.get("range_max_w", max_w))
        range_min = max(min_w, range_min)
        range_max = min(max_w, range_max)
        if range_max - range_min < step_w:
            return None

        phi = (math.sqrt(5) - 1) / 2  # golden ratio
        # Each round removes at least one step from the interval, so this
        # terminates; the bound is only a guard against a mistake in the
        # arithmetic above.
        for _ in range((max_w - min_w) // step_w + 2):
            if range_max - range_min < step_w:
                return None
            c = self._round_power_step(range_max - phi * (range_max - range_min), step_w)
            d = self._round_power_step(range_min + phi * (range_max - range_min), step_w)
            c = max(range_min, min(range_max, c))
            d = max(range_min, min(range_max, d))
            if c == d:
                d = min(range_max, c + step_w)

            def _narrow(new_min: int, new_max: int) -> bool:
                """Store the new interval; False when it did not actually shrink."""
                if (new_min, new_max) == (
                    int(data.get("range_min_w", min_w)),
                    int(data.get("range_max_w", max_w)),
                ):
                    return False
                data["range_min_w"] = new_min
                data["range_max_w"] = new_max
                self._save_auto_efficiency_data(data)
                return True

            if c in unmeasurable:
                # Not measurable here, and it will not become measurable on
                # another night: take it out of the interval and carry on.
                range_min = min(c + step_w, range_max)
                if not _narrow(range_min, range_max):
                    return None
                continue
            if d in unmeasurable:
                range_max = max(d - step_w, range_min)
                if not _narrow(range_min, range_max):
                    return None
                continue
            if c in history and d in history:
                if history[c] <= history[d]:
                    range_max = d
                else:
                    range_min = c
                if not _narrow(range_min, range_max):
                    # The two points have converged on the interval bounds:
                    # there is no third point between them left to ask for.
                    return None
                continue
            if c not in history:
                return c
            return d
        return None

    def _reset_auto_test_state(self) -> None:
        """Reset current auto test state."""
        self._remove_auto_sample_listener()
        self._auto_test_active = False
        self._auto_test_power_w = None
        self._auto_test_start = None
        self._auto_measure_start = None
        self._auto_last_sample_time = None
        self._auto_last_sent_w = None
        self._auto_last_received_w = None
        self._auto_meter_start = None
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0

    async def _limit_the_efficiency_finder(self) -> None:
        """Hold the finder's setpoint inside what the connection carries.

        A running test is abandoned rather than corrected: its energy counters
        were collected at the test power, and a sample that continues at a
        different power would be recorded against a label the battery never
        drew. The finder picks the value up again on a later night.
        """
        if not self._grid_limit_configured():
            return
        if self._auto_test_active and self._auto_test_power_w is not None:
            reference = float(self._auto_test_power_w)
        else:
            written = self._planned_setpoint_written_w
            reference = (
                written
                if written is not None
                else float(self.config.get(CONF_MAX_CHARGE_POWER_W, DEFAULT_MAX_CHARGE_POWER_W))
            )
        allowed = self._grid_limited_setpoint(reference)
        if allowed >= reference - PLANNED_POWER_WRITE_THRESHOLD_W:
            return
        if self._grid_write_is_debounced():
            return
        if self._auto_test_active:
            _LOGGER.info(
                "House connection limit: abandoning the efficiency test at %s W, the "
                "connection carries only %d W",
                self._auto_test_power_w,
                int(allowed),
            )
            self._reset_auto_test_state()
        if await self._set_ac_charge_limit_w(int(allowed)):
            self._planned_setpoint_written_w = allowed

    async def _start_auto_test(self, power_w: int) -> None:
        """Start auto efficiency test at given power.

        A test the connection cannot carry is not started at all: the written
        setpoint would be the limited one while the result was filed under the
        requested power, which would poison the efficiency history for every
        later night.
        """
        if self._grid_limit_configured():
            allowed = self._grid_limited_setpoint(float(power_w))
            if allowed < power_w - PLANNED_POWER_WRITE_THRESHOLD_W:
                written = self._planned_setpoint_written_w
                if written is not None and abs(written - allowed) <= PLANNED_POWER_WRITE_THRESHOLD_W:
                    # Already capped at this value: writing it again every poll
                    # is a Modbus write and a log line for nothing.
                    return
                _LOGGER.info(
                    "Not testing the efficiency at %d W: the house connection carries "
                    "only %d W right now",
                    power_w,
                    int(allowed),
                )
                if await self._set_ac_charge_limit_w(int(allowed)):
                    self._planned_setpoint_written_w = allowed
                return
        await self._set_ac_charge_limit_w(power_w)
        self._auto_test_active = True
        self._auto_test_power_w = power_w
        self._auto_test_start = dt_util.now()
        self._auto_measure_start = None
        self._auto_last_sample_time = None
        self._auto_last_sent_w = None
        self._auto_last_received_w = None
        self._auto_meter_start = None
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0
        self._setup_auto_sample_listener()
        _LOGGER.info(
            "Efficiency test started at %d W (%d s to settle, then at least %d s and %.1f kWh)",
            power_w,
            AUTO_TEST_SETTLE_S,
            AUTO_TEST_MIN_DURATION_S,
            AUTO_TEST_MIN_ENERGY_WH / 1000.0,
        )

    @property
    def auto_test_state(self) -> str:
        """What the efficiency finder is doing, for the sensor."""
        if self._auto_test_active:
            if self._auto_measure_start is None:
                return AUTO_TEST_STATE_SETTLING
            return AUTO_TEST_STATE_MEASURING
        if not self.auto_efficient_charge:
            data = self.get_auto_efficiency_data()
            if data.get("best_power_w") is not None:
                return AUTO_TEST_STATE_FINISHED
            return AUTO_TEST_STATE_IDLE
        if self.is_active:
            return AUTO_TEST_STATE_WAITING
        return AUTO_TEST_STATE_IDLE

    def auto_test_attributes(self) -> dict[str, Any]:
        """Everything needed to judge the search from the frontend."""
        data = self.get_auto_efficiency_data()
        history = {k: round(float(v) * 100.0, 2) for k, v in dict(data.get("history", {})).items()}
        attributes: dict[str, Any] = {
            "test_power_w": self._auto_test_power_w,
            "best_power_w": data.get("best_power_w"),
            "best_loss_pct": (
                round(float(data["best_loss"]) * 100.0, 2) if data.get("best_loss") is not None else None
            ),
            "loss_by_power_pct": history,
            "search_range_w": [data.get("range_min_w"), data.get("range_max_w")],
            "measured_energy_kwh": round(self._auto_energy_sent_wh / 1000.0, 3),
            "last_result": self._auto_last_result,
            "measurement_source": "energy meters" if self._energy_meter_entities() else "power sensors",
        }
        if self._auto_measure_start is not None:
            attributes["measuring_for_min"] = round(
                (dt_util.now() - self._auto_measure_start).total_seconds() / 60.0, 1
            )
        return attributes

    def _energy_meter_entities(self) -> tuple[str, str] | None:
        """The pair of kWh meters, when both are configured.

        Meters beat power sensors for this measurement: the difference between
        two readings is the energy that actually flowed, with no assumption
        about what the power did in between.
        """
        sent = self.config.get(CONF_CHARGE_ENERGY_SENT_ENTITY)
        received = self.config.get(CONF_CHARGE_ENERGY_RECEIVED_ENTITY)
        if sent and received:
            return str(sent), str(received)
        return None

    def _read_energy_wh(self, entity_id: str) -> float | None:
        """Read a cumulative energy meter in Wh, understanding kWh, Wh and MWh."""
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable", None):
            return None
        value = _as_float(state.state)
        if value is None:
            return None
        unit = _unit_of(state)
        if unit in ENERGY_UNITS_KWH:
            return value * 1000.0
        if unit in ENERGY_UNITS_WH:
            return value
        if unit in ENERGY_UNITS_MWH:
            return value * 1_000_000.0
        # No fallback for a missing unit here, unlike the forecast parser: a
        # meter reading is a running total, so guessing its scale would put the
        # measurement out by a factor of a thousand without anything looking odd.
        _LOGGER.debug("Energy meter %s reports the unusable unit %r", entity_id, unit or "(none)")
        return None

    def _begin_auto_measurement(self, now: datetime) -> None:
        """Start counting: the inverter has had its settling time."""
        self._auto_measure_start = now
        self._auto_last_sample_time = now
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0
        self._auto_last_sent_w = None
        self._auto_last_received_w = None
        meters = self._energy_meter_entities()
        if meters is not None:
            start_sent = self._read_energy_wh(meters[0])
            start_received = self._read_energy_wh(meters[1])
            if start_sent is not None and start_received is not None:
                self._auto_meter_start = (start_sent, start_received)
        _LOGGER.debug(
            "Efficiency test at %s W settled, measuring from now (%s)",
            self._auto_test_power_w,
            "energy meters" if self._auto_meter_start else "power sensors",
        )

    def _accumulate_auto_energy(self) -> None:
        """Integrate the charge power into energy, trapezoidally.

        Called on every reading of the power sensor, not only on the polling
        interval: a rectangle over fifteen minutes, valued at whatever the
        sensor happened to show at its end, is not a measurement of anything.
        Samples before the settling time are dropped, because the ramp to the
        new setpoint belongs to no power in particular.
        """
        if not self._auto_test_active or self._auto_test_start is None:
            return
        now = dt_util.now()
        if self._auto_measure_start is None:
            if (now - self._auto_test_start).total_seconds() < AUTO_TEST_SETTLE_S:
                return
            self._begin_auto_measurement(now)
            return
        sent_entity = self.config.get(CONF_CHARGE_POWER_SENT_ENTITY)
        received_entity = self.config.get(CONF_CHARGE_POWER_RECEIVED_ENTITY)
        sent_w = self._get_power_w(sent_entity)
        received_w = self._get_power_w(received_entity)
        if sent_w is None or received_w is None:
            return
        last_time = self._auto_last_sample_time
        if last_time is None:
            self._auto_last_sample_time = now
            self._auto_last_sent_w = sent_w
            self._auto_last_received_w = received_w
            return
        delta_h = (now - last_time).total_seconds() / 3600.0
        if delta_h <= 0:
            return
        previous_sent = self._auto_last_sent_w
        previous_received = self._auto_last_received_w
        mean_sent = sent_w if previous_sent is None else (previous_sent + sent_w) / 2.0
        mean_received = (
            received_w if previous_received is None else (previous_received + received_w) / 2.0
        )
        self._auto_energy_sent_wh += mean_sent * delta_h
        self._auto_energy_received_wh += mean_received * delta_h
        self._auto_last_sample_time = now
        self._auto_last_sent_w = sent_w
        self._auto_last_received_w = received_w

    def _setup_auto_sample_listener(self) -> None:
        """Sample the charge power whenever the sensor reports, not every poll."""
        self._remove_auto_sample_listener()
        sent_entity = self.config.get(CONF_CHARGE_POWER_SENT_ENTITY)
        received_entity = self.config.get(CONF_CHARGE_POWER_RECEIVED_ENTITY)
        if not sent_entity or not received_entity:
            return

        async def _on_charge_power_change(event: Event[EventStateChangedData]) -> None:
            try:
                self._accumulate_auto_energy()
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected error while sampling the charge power: %s", e)

        self._auto_sample_listener = async_track_state_change_event(
            self.hass,
            [str(sent_entity), str(received_entity)],
            _on_charge_power_change,
        )

    def _remove_auto_sample_listener(self) -> None:
        """Remove the charge power sampling listener."""
        if self._auto_sample_listener:
            self._auto_sample_listener()
            self._auto_sample_listener = None

    def _auto_measurement_is_complete(self) -> bool:
        """True once the running measurement has enough time and energy behind it."""
        if not self._auto_test_active or self._auto_measure_start is None:
            return False
        duration = (dt_util.now() - self._auto_measure_start).total_seconds()
        if duration < AUTO_TEST_MIN_DURATION_S:
            return False
        measured = self._measured_energy_wh()
        if measured is None:
            return False
        return measured[0] >= AUTO_TEST_MIN_ENERGY_WH

    def _measured_energy_wh(self) -> tuple[float, float] | None:
        """The energy of the current measurement as (sent, received) in Wh.

        From the kWh meters when they are configured, otherwise from the
        integrated power. A meter that has been reset (or replaced) during the
        measurement reads lower than at the start; that is not a measurement.
        """
        meters = self._energy_meter_entities()
        start = self._auto_meter_start
        if meters is not None and start is not None:
            end_sent = self._read_energy_wh(meters[0])
            end_received = self._read_energy_wh(meters[1])
            if end_sent is None or end_received is None:
                return None
            sent = end_sent - start[0]
            received = end_received - start[1]
            if sent < 0 or received < 0:
                _LOGGER.info("Efficiency test discarded: an energy meter went backwards")
                return None
            return sent, received
        return self._auto_energy_sent_wh, self._auto_energy_received_wh

    def _discard_auto_test(self, reason: str, *args: Any) -> None:
        """Drop the running measurement and say why, once, in plain words."""
        power = self._auto_test_power_w
        self._auto_last_result = {
            "power_w": power,
            "loss": None,
            "discarded": reason % args if args else reason,
        }
        _LOGGER.info(
            "Efficiency test at %s W discarded: " + reason,
            power,
            *args,
        )
        self._reset_auto_test_state()

    def _record_failed_attempt(self, power_w: int, too_short: bool) -> None:
        """Remember that this power could not be measured.

        A power the battery cannot absorb for long enough - because it is full
        before the measurement is over - is not going to become measurable on
        the next night either. After a few attempts the search stops offering
        it, instead of asking for the same impossible sample for ever.
        """
        data = self.get_auto_efficiency_data()
        failed = {int(k): int(v) for k, v in dict(data.get("failed", {})).items()}
        attempts = failed.get(power_w, 0) + 1
        failed[power_w] = attempts
        data["failed"] = {str(k): v for k, v in failed.items()}
        if too_short and attempts >= AUTO_TEST_MAX_ATTEMPTS:
            # Too high for the energy the window has left: lower the ceiling.
            range_max = int(data.get("range_max_w", power_w))
            new_max = max(int(self.config.get(CONF_MIN_CHARGE_POWER_W, 1000)), power_w - 100)
            if new_max < range_max:
                data["range_max_w"] = new_max
                _LOGGER.info(
                    "Efficiency search: %d W could not be measured %d times - the battery is "
                    "full before a measurement completes, so the search now stops at %d W",
                    power_w,
                    attempts,
                    new_max,
                )
        self._save_auto_efficiency_data(data)

    def _finalize_auto_test(self) -> None:
        """Turn the running measurement into a sample, or discard it.

        A sample is only worth keeping if it was really measured at the power
        it is filed under: long enough, with enough energy, with the inverter
        actually following the setpoint, and with a loss that a charger can
        physically have. Everything else would steer every later night wrong.
        """
        if not self._auto_test_active or self._auto_test_start is None or self._auto_test_power_w is None:
            self._reset_auto_test_state()
            return
        power_w = self._auto_test_power_w
        if self._auto_measure_start is None:
            self._discard_auto_test("it never got past the %d s settling time", AUTO_TEST_SETTLE_S)
            return

        duration = (dt_util.now() - self._auto_measure_start).total_seconds()
        measured = self._measured_energy_wh()
        if measured is None:
            self._reset_auto_test_state()
            return
        sent_wh, received_wh = measured
        if duration < AUTO_TEST_MIN_DURATION_S or sent_wh < AUTO_TEST_MIN_ENERGY_WH:
            self._discard_auto_test(
                "only %.0f s and %.2f kWh (needs %d s and %.1f kWh)",
                duration,
                sent_wh / 1000.0,
                AUTO_TEST_MIN_DURATION_S,
                AUTO_TEST_MIN_ENERGY_WH / 1000.0,
            )
            self._record_failed_attempt(power_w, too_short=True)
            return

        average_w = sent_wh / (duration / 3600.0)
        if average_w < AUTO_TEST_MIN_FOLLOW_RATIO * power_w:
            # The inverter charged at something else - a full battery, the BMS
            # tapering near the top, or another limit. Filing this under the
            # test power would compare two different things.
            self._discard_auto_test(
                "the inverter drew %.0f W on average, not the %d W it was set to",
                average_w,
                power_w,
            )
            self._record_failed_attempt(power_w, too_short=False)
            return

        loss = 1.0 - (received_wh / sent_wh)
        if loss < 0 or loss > AUTO_TEST_MAX_PLAUSIBLE_LOSS:
            # Negative means the battery received more than was sent, which no
            # charger does: the two sensors measure the same side, or the wrong
            # ones are configured. Recording it would make this power win the
            # search for ever, because the loss is clamped at zero.
            self._discard_auto_test(
                "a loss of %.1f %% is not physical - check that 'sent' is the AC side and "
                "'received' the battery side",
                loss * 100.0,
            )
            return

        data = self.get_auto_efficiency_data()
        history = dict(data.get("history", {}))
        history[str(power_w)] = loss
        data["history"] = history
        data["bounds_w"] = [
            int(self.config.get(CONF_MIN_CHARGE_POWER_W, 1000)),
            int(self.config.get(CONF_MAX_CHARGE_POWER_W, 10000)),
        ]

        best_loss = data.get("best_loss")
        if best_loss is None or loss < best_loss:
            data["best_loss"] = loss
            data["best_power_w"] = power_w
            _LOGGER.info(
                "New best charge power: %.2f %% loss at %d W (%.2f kWh in, %.2f kWh into the "
                "battery, over %.0f min)",
                loss * 100.0,
                power_w,
                sent_wh / 1000.0,
                received_wh / 1000.0,
                duration / 60.0,
            )
        else:
            _LOGGER.info(
                "Charge power measured: %.2f %% loss at %d W (best so far %.2f %% at %s W)",
                loss * 100.0,
                power_w,
                float(best_loss) * 100.0,
                data.get("best_power_w"),
            )

        self._auto_last_result = {
            "power_w": power_w,
            "loss": round(loss, 4),
            "energy_sent_kwh": round(sent_wh / 1000.0, 3),
            "energy_received_kwh": round(received_wh / 1000.0, 3),
            "minutes": round(duration / 60.0, 1),
            "source": "meters" if self._auto_meter_start else "power sensors",
        }
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
            if not self._auto_measurement_is_complete():
                return
            # Enough time and energy at this power: record it and spend the
            # rest of the window on the next candidate. A search that took one
            # sample per night needed a week and a half of nights; this way it
            # usually finishes inside one window.
            self._finalize_auto_test()

        candidate = self._select_next_auto_test_power_w()
        if candidate is None:
            data = self.get_auto_efficiency_data()
            best_power = data.get("best_power_w")
            if isinstance(best_power, int):
                # Written like every other setpoint, which means the house
                # connection limit may cap it. That cap belongs to this moment,
                # not to the result, so the user's own value stays on record and
                # the window end restores it. What carries the result forward is
                # best_power_w: the planner uses it as its ceiling from now on.
                await self._set_ac_charge_limit_w(best_power)
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

            if self._parse_date_optional(
                self.config.get(CONF_ACTIVE_START_DATE, DEFAULT_ACTIVE_START_DATE)
            ) or self._parse_date_optional(
                self.config.get(CONF_ACTIVE_END_DATE, DEFAULT_ACTIVE_END_DATE)
            ):
                # The active date range is a calendar-day bound, so it changes at
                # midnight - in the middle of an overnight window. Without a
                # check there, the first night of the range loses everything
                # after midnight (the start trigger fired while still outside
                # the range), and the last night runs on up to a whole polling
                # interval past the end date.
                self._time_triggers.append(
                    async_track_time_change(
                        self.hass,
                        self._on_date_boundary,
                        hour=0,
                        minute=0,
                        second=5,
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

    def update_time_triggers(self, previous_soc_entity: str | None = None) -> None:
        """Update time triggers when configuration changes.

        ``previous_soc_entity`` is the battery SOC entity as it was *before* the
        caller swapped in the new configuration. Reading it from ``self.config``
        here cannot work: by the time this runs, that is already the new value,
        so the comparison below would never find a change and a running window
        would keep listening to an entity nobody configures any more.
        """
        _LOGGER.info("Updating time triggers with new configuration")

        old_battery_soc_entity = (
            previous_soc_entity
            if previous_soc_entity is not None
            else self.config.get(CONF_BATTERY_SOC_ENTITY)
        )

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
            elif not in_window and not self.is_active and self._inverter_still_holds_our_settings():
                # Home Assistant was not running when the window ended: nothing
                # reset the inverter, so grid charging is still on and the min
                # SOC still carries the night's floor. Without this the battery
                # would be bought full from the grid in daylight, every day,
                # until somebody notices.
                _LOGGER.warning(
                    "Settings from an earlier window are still on the inverter (Home Assistant "
                    "was not running at the window end) - resetting them now"
                )
                await self._reset_settings()
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
        if self._window_started_at is not None and self.initial_calculated_soc is not None:
            previous = self._window_started_at
            if (now - previous).total_seconds() > self._window_length_s() + 3600:
                # The persisted target belongs to a window that is long over -
                # usually one whose end Home Assistant was not running for.
                # Planning starts fresh instead of buying last night's target.
                _LOGGER.info(
                    "Discarding the target of %.1f%% from the window that started %s: "
                    "it is not this window",
                    self.initial_calculated_soc,
                    previous.isoformat(timespec="minutes"),
                )
                self.initial_calculated_soc = None
                self.minimum_calculated_soc = None
        self._window_started_at = now
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
        self._setup_grid_import_listener()
        await self._start_periodic_verification()

        # Calculate and store initial SOC for this charging period
        await self._calculate_initial_soc()

        # Night charge: keep stored PV in the battery while grid energy is cheap
        if not self.is_discharge_mode:
            await self._apply_discharge_block(announce=True)
            # A floor above the charge target has to reach the inverter now:
            # with the battery already above the target the update below returns
            # early ("target reached") and _control_kostal never runs - which is
            # exactly the case this block exists for.
            await self._write_raised_min_soc_floor()

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
                    self._remove_grid_import_listener()
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

    async def async_disable(self) -> None:
        """Switch the integration off and hand the inverter back.

        The same teardown as a window end, minus the bookkeeping that belongs
        to a night that actually ran: no snow night is used up, and no reset is
        scheduled for later, because the user is switching this off now.
        Doing it in one place is the point - the switch entity used to repeat
        part of this list and left the efficiency sampler running, so its
        listener kept integrating into a measurement nobody would ever finish.
        """
        if not self.is_enabled:
            return
        _LOGGER.info("Disabling Inverter Charge Night")
        self.is_enabled = False
        self.is_active = False
        self._ending = True
        try:
            self._remove_battery_soc_listener()
            self._remove_inverter_min_soc_listener()
            self._remove_grid_import_listener()
            self._reset_auto_test_state()
            await self._stop_periodic_verification()
            try:
                await self._reset_settings()
            except Exception as e:
                _LOGGER.error("Error resetting settings while disabling: %s", e, exc_info=True)
        finally:
            self._ending = False
        self.target_reached = False
        self.override_soc = None
        self.initial_calculated_soc = None
        self.minimum_calculated_soc = None
        self.last_plan = None
        self.planned_charge_power_w = None
        self._planned_setpoint_written_w = None
        self._grid_plan_request_w = None
        self._window_floor_soc = None
        self._window_started_at = None
        self._discharge_block_logged = None
        self._persist_state()

    def _is_the_live_coordinator(self) -> bool:
        """False once this instance is not the entry's coordinator any more.

        A timer armed in the constructor outlives a setup that failed after it
        (``ConfigEntryNotReady`` retries for as long as the entity is missing),
        and would then write the options of an entry that is not loaded.
        """
        return getattr(self.entry, "runtime_data", None) is self

    async def _on_date_boundary(self, now: datetime) -> None:
        """Re-evaluate the window when the calendar day - and the range - changes."""
        if not self.is_enabled:
            return
        _LOGGER.debug("Date boundary reached, re-evaluating the active date range")
        await self._check_current_window()

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
            self._remove_grid_import_listener()
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
            # A raised floor must never outlive its window: the battery would be
            # blocked for good. _reset_settings puts original_min_soc back on the
            # inverter; this drops the floor that produced the raised value.
            self._window_floor_soc = None
            self._window_started_at = None
            self._discharge_block_logged = None
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

    def _inverter_still_holds_our_settings(self) -> bool:
        """True when a value captured for a window has not been restored yet.

        Every ``original_*`` field is captured before the integration changes
        the corresponding setting and cleared once it has been restored, so any
        of them being set outside a window means the inverter is still carrying
        what a window put there.
        """
        return any(
            value is not None
            for value in (
                self.original_min_soc,
                self._original_ac_charge_power,
                self._original_discharge_limit,
                self._original_discharge_block,
                self._original_absolute_charge_power,
            )
        )

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
        if not await self._reset_discharge_block_switch():
            ok = False
        if not await self._reset_absolute_charge_power():
            ok = False
        # Forget the original values only once everything is back in place;
        # a retry needs them otherwise.
        if ok:
            self.original_min_soc = None
            self.override_soc = None  # Clear override when resetting
            # The inverter is back at its original floor, so the raised one is
            # gone too - it must never survive into another window (plan 009).
            self._window_floor_soc = None
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
            # The window did run to its end time - only the trigger did not
            # fire. Counted as a scheduled end, so a snow night is used up
            # instead of charging to the maximum again the next night.
            await self._on_window_end(dt_util.now(), scheduled=True)
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
        # The discharge-block floor follows the charge level (plan 009)
        self._update_window_floor()
        
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
        # A reading the integration cannot compare is no reading. "unavailable"
        # and a nan that parses fine but poisons every comparison have to take
        # the same path, or grid charging would run on a target that can never
        # count as reached.
        can_check_soc = battery_soc_entity is not None and self._current_battery_soc() is not None
        
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
                    current_soc = _as_float_strict(state.state)
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
                    current_soc = _as_float_strict(state.state)
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
        
        # What goes on the min SOC entity: the charge target, or the raised
        # discharge-block floor (plan 009), already clamped into the user bounds.
        floor_soc = self.inverter_floor_soc(target_soc)
        if floor_soc is None:
            floor_soc = target_soc

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
                    min_soc_current_value = _as_float(state.state)
                    self._capture_original_min_soc(min_soc_current_value)

                    # Only update if value changed significantly (avoid unnecessary service calls and EEPROM wear)
                    # CRITICAL: Threshold increased to 0.5 to prevent "bricking" inverter memory
                    # The inverter's reported value is authoritative; _last_soc_set is log information only
                    if min_soc_current_value is None or abs(min_soc_current_value - floor_soc) > 0.5:
                        need_to_set_min_soc = True
                    else:
                        _LOGGER.debug("Min SOC already at target: %.1f%%", floor_soc)
                        self._last_soc_set = floor_soc
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
                    {"entity_id": kostal_min_soc_entity, "value": floor_soc},
                )
                self._last_soc_set = floor_soc
                min_soc_set = True
                _LOGGER.info(
                    "Set Kostal min SOC to %.1f%% (was %s)",
                    floor_soc,
                    f"{min_soc_current_value:.1f}%" if min_soc_current_value is not None else "unknown",
                )
            except Exception as e:
                # Without the new floor the inverter would charge against the wrong
                # min SOC, so grid charging is not switched on; the next poll retries.
                _LOGGER.error("Error setting min SOC: %s", e, exc_info=True)

            # Immediately send grid charge command (configurable delay) so inverter processes both together
            if min_soc_set and kostal_grid_charge_switch and not should_skip_charging:
                delay = _as_float(self.config.get(CONF_COMMAND_DELAY))
                # A configured 0 is a valid answer ("no delay"), so it must not
                # fall through to the default the way a falsy check would.
                command_delay = max(0.0, delay) if delay is not None else DEFAULT_COMMAND_DELAY
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
                    current_soc = _as_float_strict(state.state)
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
                    min_soc_current_value: float | None = _as_float(state.state)
                    self._capture_original_min_soc(min_soc_current_value)

                    if min_soc_current_value is None or abs(min_soc_current_value - target_soc) > 0.5:
                        need_to_set_min_soc = True
                    else:
                        self._last_soc_set = target_soc
            except Exception as e:
                _LOGGER.error("Error preparing min SOC for discharge: %s", e, exc_info=True)

        if need_to_set_min_soc and kostal_min_soc_entity:
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": kostal_min_soc_entity, "value": target_soc},
                )
            except Exception as e:
                # Every other call in this method is wrapped; this one was not,
                # so a failed write took the two blocks below with it - and one
                # of them is what switches grid charging off. Forcing a
                # discharge against an unknown floor is not safe either, so the
                # floor counts as unavailable from here on.
                _LOGGER.error("Error setting min SOC for discharge: %s", e, exc_info=True)
                min_soc_available = False
            else:
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
        if self.is_active and self._grid_budget() is not None:
            # The house connection limit stays on the inverter until the window
            # ends. Reaching the charge target does not end the window: the
            # raised min SOC floor can still make the inverter buy power, and
            # the rest of the house (wallboxes) may still be running. The
            # window-end reset restores the user's own value.
            _LOGGER.debug(
                "Target reached, but the house connection limit stays on the inverter "
                "until the window ends"
            )
            await self._enforce_grid_limit_now()
        else:
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
                # The written value is the floor, not the charge target (plan 009)
                floor_soc = self.inverter_floor_soc()
                
                if floor_soc is None:
                    return
                
                # Check if inverter min SOC doesn't match the floor (with tolerance)
                if abs(current_inverter_soc - floor_soc) > 0.5:
                    _LOGGER.debug(
                        "Inverter min SOC deviation detected via listener (%.1f%% vs %.1f%%), triggering restoration",
                        current_inverter_soc,
                        floor_soc
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
            while self.is_active and self.is_enabled:
                try:
                    # Read each round: a changed update interval takes effect
                    # in the running window, not only in the next one.
                    configured = _as_float(self.config.get(CONF_UPDATE_INTERVAL))
                    update_interval = (
                        max(0.0, configured) if configured is not None else DEFAULT_UPDATE_INTERVAL
                    )
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
    
    async def _verify_ac_charge_limit(self) -> None:
        """Check that the charge limit we wrote is the one the inverter holds.

        A service call that returns without raising is not proof of anything:
        an inverter integration can accept the call and drop it - for instance
        when the inverter is not in external-control mode, or when another
        feature owns the same register and has written its own value since.
        Silently believing our limit is in force is exactly the failure the
        house connection protection must not have, so the value is read back
        and rewritten when it has drifted.
        """
        written = self._planned_setpoint_written_w
        if written is None or self._is_backup_active():
            return
        target = self._ac_charge_limit_target()
        if target is None:
            return
        actual_w = self._get_power_w(target[0])
        if actual_w is None:
            return
        if abs(actual_w - written) <= PLANNED_POWER_WRITE_THRESHOLD_W:
            return
        if actual_w < written:
            # Someone or something is charging less than we asked for. That is
            # never a danger, so it is noted and left alone.
            _LOGGER.debug(
                "AC charge limit is %.0f W, below the %.0f W we wrote - leaving it",
                actual_w,
                written,
            )
            return
        _LOGGER.warning(
            "The inverter holds an AC charge limit of %.0f W, not the %.0f W this "
            "integration wrote - writing it again. If this repeats, the inverter is not "
            "accepting external control, and the house connection limit cannot protect it",
            actual_w,
            written,
        )
        await self._set_ac_charge_limit_w(int(written))

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
            await self._verify_ac_charge_limit()

            # Two different values: the charge target decides "target reached",
            # the floor is what the min SOC entity has to hold (plan 009).
            target_soc = self.current_target_soc()

            if target_soc is None:
                return
            
            floor_soc = self.inverter_floor_soc(target_soc)
            if floor_soc is None:
                return

            kostal_min_soc_entity = self.config.get(CONF_KOSTAL_MIN_SOC_ENTITY)
            if not kostal_min_soc_entity:
                return
            
            state = self.hass.states.get(kostal_min_soc_entity)
            if not state or state.state in ("unknown", "unavailable"):
                _LOGGER.debug("Cannot verify min SOC - entity unavailable")
                return
            
            current_inverter_soc = _as_float(state.state)
            if current_inverter_soc is None:
                _LOGGER.debug("Cannot verify min SOC - %s is not a usable number", kostal_min_soc_entity)
                return
            
            # Check if inverter min SOC doesn't match the floor (with tolerance)
            if abs(current_inverter_soc - floor_soc) > 0.5:
                _LOGGER.warning(
                    "Inverter min SOC (%.1f%%) doesn't match our target (%.1f%%), restoring to target",
                    current_inverter_soc,
                    floor_soc
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
                    {"entity_id": kostal_min_soc_entity, "value": floor_soc},
                )
                self._last_soc_set = floor_soc
                _LOGGER.info("Restored inverter min SOC to %.1f%%", floor_soc)
            
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

    # House connection limit (plan 008) --------------------------------------
    #
    # During the cheap-tariff window the big loads run at the same time: two
    # wallboxes are 33 kW on their own, and a 63 A three-phase connection is
    # only about 43 kW. Six hours at that level heats the meter terminals and
    # the fuse contacts, so the battery - the only load this integration
    # controls - has to give way to the rest of the house.
    #
    # This is a safety feature, not an optimisation: it applies in both planner
    # modes, and every uncertainty (no reading, a stale reading, an unparseable
    # unit) charges *less*, never more. It never engages outside a window,
    # because outside a window the integration controls nothing.

    def _grid_budget(self) -> float | None:
        """The configured continuous grid budget in W, or None when unconfigured."""
        phases_raw = _as_float(self.config.get(CONF_GRID_PHASES, DEFAULT_GRID_PHASES))
        voltage = _as_float(self.config.get(CONF_GRID_VOLTAGE_V, DEFAULT_GRID_VOLTAGE_V))
        pct = _as_float(self.config.get(CONF_GRID_CONTINUOUS_PCT, DEFAULT_GRID_CONTINUOUS_PCT))
        budget = grid_budget_w(
            _as_float(self.config.get(CONF_MAIN_FUSE_A)),
            int(phases_raw) if phases_raw is not None else DEFAULT_GRID_PHASES,
            voltage if voltage is not None else float(DEFAULT_GRID_VOLTAGE_V),
            pct if pct is not None else float(DEFAULT_GRID_CONTINUOUS_PCT),
            _as_float(self.config.get(CONF_GRID_MAX_CONTINUOUS_W)),
        )
        self._grid_budget_cache_w = budget
        return budget

    def _grid_limit_configured(self) -> bool:
        """True when a budget *and* a grid import entity are set.

        Without the import entity there is nothing to measure against, and the
        documented behaviour is then exactly as before: no limiting at all.
        """
        return bool(self.config.get(CONF_GRID_IMPORT_ENTITY)) and self._grid_budget() is not None

    @staticmethod
    def _state_age_s(state: Any, now: datetime) -> float | None:
        """Seconds since the state last reported, or None when not determinable."""
        for attribute in ("last_reported", "last_updated", "last_changed"):
            stamp = getattr(state, attribute, None)
            if isinstance(stamp, datetime):
                try:
                    return (now - stamp).total_seconds()
                except (TypeError, ValueError):
                    return None
        return None

    def _read_grid_import_w(self, entity_id: str) -> float | None:
        """Current grid import in W, or None when it is missing, stale or odd.

        Stricter than :meth:`_get_power_w` on purpose: a unit this integration
        does not understand (kVA, A, MW) must not be read as watts, because
        mistaking MW for W would remove the limit entirely.
        """
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable", None):
            self._grid_import_w = None
            return None
        value = _as_float(state.state)
        if value is None:
            self._grid_import_w = None
            return None
        unit = _unit_of(state)
        if unit in ("kw", "kilowatt", "kilowatts"):
            value *= 1000.0
        elif unit not in ("w", "watt", "watts"):
            # No unit is not trusted either: a template sensor reporting kW
            # without one would read 33 as 33 W and lift the limit entirely.
            _LOGGER.debug(
                "Grid import %s reports the unusable unit %r - set W or kW on the sensor",
                entity_id,
                unit or "(none)",
            )
            self._grid_import_w = None
            return None
        age_s = self._state_age_s(state, dt_util.now())
        if age_s is not None and age_s > GRID_LIMIT_STALE_AFTER_S:
            _LOGGER.debug("Grid import %s is %.0f s old - treating it as unknown", entity_id, age_s)
            self._grid_import_w = None
            return None
        # Feeding in is not "negative import": treated as a number it would
        # make the foreign load look negative and hand the battery more than
        # the connection has.
        value = max(0.0, value)
        self._grid_import_w = value
        return value

    def _own_charge_draw_w(self) -> float:
        """What the battery itself is drawing right now, in W.

        The written setpoint is only a request: during the ramp-up, with an
        inverter that undercuts it, or after a manual change it can be well
        above the real draw, and every watt overstated here is a watt of other
        load that goes unnoticed. When the measured charge power is configured
        and fresh, the smaller of the two is used - that never overstates our
        own share, so the limit errs towards charging less. Without that
        measurement the setpoint stands in for the draw only while a charge is
        still being ordered: once the target is reached the limit caps a battery
        that draws nothing, and subtracting it would hide that much house load.
        """
        written = self._planned_setpoint_written_w
        measured: float | None = None
        sent_entity = self.config.get(CONF_CHARGE_POWER_SENT_ENTITY)
        if sent_entity:
            state = self.hass.states.get(str(sent_entity))
            age_s = self._state_age_s(state, dt_util.now()) if state else None
            if age_s is None or age_s <= GRID_LIMIT_STALE_AFTER_S:
                value = self._get_power_w(sent_entity)
                if value is not None and value >= 0:
                    measured = value
        if measured is None:
            if self.target_reached:
                return 0.0
            return float(written or 0.0)
        if written is None:
            return measured
        return min(measured, float(written))

    async def _enforce_grid_limit_now(self) -> None:
        """Keep the written charge limit within what the connection carries.

        Used once charging itself is over but the window is not: the raised min
        SOC floor can still make the inverter buy power, so the limit must stay
        current rather than being handed back to the user's own value. Writes
        only downwards, and only when the entity and a budget are configured.
        """
        if not self.is_active or self._ending or self._unloading:
            return
        if self._is_backup_active():
            # Off the grid there is nothing to limit, and backup mode owns the
            # inverter until it ends.
            return
        if not self.config.get(CONF_GRID_IMPORT_ENTITY) or self._grid_budget() is None:
            return
        reference = self._planned_setpoint_written_w
        if reference is None:
            reference = float(self.config.get(CONF_MAX_CHARGE_POWER_W, DEFAULT_MAX_CHARGE_POWER_W))
        allowed = self._grid_limited_setpoint(reference)
        if allowed >= reference - PLANNED_POWER_WRITE_THRESHOLD_W:
            return
        if self._grid_write_is_debounced():
            return
        _LOGGER.info(
            "House connection limit: holding the charge limit at %d W after the target was reached",
            int(allowed),
        )
        if not await self._set_ac_charge_limit_w(int(allowed)):
            # The limit did not reach the inverter. Leave the reference alone so
            # the next reading tries again instead of assuming it is capped.
            return
        # What stands on the inverter is the reference for the next reading, so
        # an unchanged load does not write the same value over and over.
        self._planned_setpoint_written_w = allowed
        self.planned_charge_power_w = allowed

    def _grid_write_is_debounced(self) -> bool:
        """True while the last charge-limit write is too recent to follow up.

        A power sensor reports every few seconds; without this the inverter
        would be written to on every reading for as long as the limit engages.
        """
        last_write = self._grid_limit_last_write
        if last_write is None:
            return False
        if (dt_util.now() - last_write).total_seconds() >= GRID_LIMIT_MIN_WRITE_INTERVAL_S:
            return False
        _LOGGER.debug("House connection limit changed again within the debounce - waiting")
        return True

    def _grid_limited_setpoint(self, planned_w: float) -> float:
        """Cap ``planned_w`` at what the house connection can still carry.

        Returns ``planned_w`` unchanged when no window is active or nothing is
        configured. Otherwise the result is never above ``planned_w``: this
        method only ever takes power away.

        ``self._grid_limited`` answers "is the connection holding the battery
        below what it would otherwise draw". It is measured against the last
        unlimited plan, not against this call's ``planned_w``, so that a caller
        passing an already limited value (which is what holding the cap looks
        like) does not make the flag drop back to False.
        """
        if not self.is_active:
            # Outside a window the integration controls nothing, so there is
            # nothing to hold back either.
            return planned_w
        entity_id = self.config.get(CONF_GRID_IMPORT_ENTITY)
        budget_w = self._grid_budget()
        if not entity_id or budget_w is None:
            self._grid_allowed_w = None
            self._grid_other_load_w = None
            self._grid_limited = False
            return planned_w

        min_w = float(self.config.get(CONF_MIN_CHARGE_POWER_W, DEFAULT_MIN_CHARGE_POWER_W))
        import_w = self._read_grid_import_w(str(entity_id))
        if import_w is None:
            # Blind: the connection may already be at its limit. Fall back to
            # the minimum charge power instead of carrying on at full power.
            if not self._grid_stale_logged:
                _LOGGER.warning(
                    "Grid import %s is unavailable or stale - limiting the charge power to "
                    "%.0f W until it reports again",
                    entity_id,
                    min_w,
                )
                self._grid_stale_logged = True
            self._grid_allowed_w = None
            self._grid_other_load_w = None
            self._grid_limited = True
            return min(planned_w, min_w)

        self._grid_stale_logged = False
        headroom = _as_float(self.config.get(CONF_GRID_HEADROOM_W, DEFAULT_GRID_HEADROOM_W))
        own_w = min(self._own_charge_draw_w(), max(0.0, import_w))
        if not self.config.get(CONF_CHARGE_POWER_SENT_ENTITY) and not self._grid_own_draw_logged:
            # Without a measurement the written setpoint stands in for the draw.
            # It is an upper bound, so a battery that does not follow it makes
            # the foreign load look smaller than it is.
            _LOGGER.info(
                "House connection limit is working from the written setpoint: configure the "
                "charge power sensor to measure what the battery really draws"
            )
            self._grid_own_draw_logged = True
        allowed = allowed_charge_power_w(
            budget_w,
            import_w,
            own_w,
            headroom if headroom is not None else float(DEFAULT_GRID_HEADROOM_W),
        )
        self._grid_allowed_w = allowed
        self._grid_other_load_w = max(0.0, import_w - max(0.0, own_w))
        request = self._grid_plan_request_w
        if request is None:
            request = float(self.config.get(CONF_MAX_CHARGE_POWER_W, DEFAULT_MAX_CHARGE_POWER_W))
        self._grid_limited = allowed < request - PLANNED_POWER_WRITE_THRESHOLD_W
        limited = min(planned_w, allowed)
        if limited < min_w and limited < planned_w:
            # Below the inverter's own minimum the setpoint is meaningless; the
            # connection simply has no room for the battery right now.
            _LOGGER.info(
                "House connection has no room for the battery: %.0f W other load leaves "
                "%.0f W of a %.0f W budget",
                self._grid_other_load_w,
                allowed,
                budget_w,
            )
            limited = 0.0
        return limited

    @property
    def grid_charge_headroom_w(self) -> float | None:
        """What the connection still allows the battery, for the sensor."""
        if not self.is_active or not self._grid_limit_configured():
            return None
        return self._grid_allowed_w

    def grid_limit_attributes(self) -> dict[str, Any]:
        """Attributes of the house connection limit for the sensor."""
        return {
            "budget_w": self._grid_budget_cache_w,
            "grid_import_w": self._grid_import_w,
            "other_load_w": self._grid_other_load_w,
            "limited": self._grid_limited,
        }

    def _setup_grid_import_listener(self) -> None:
        """Watch the grid import so a rising house load is answered between polls."""
        self._remove_grid_import_listener()
        self._grid_stale_logged = False
        self._grid_own_draw_logged = False
        self._grid_limit_last_write = None
        if self.is_discharge_mode:
            # A discharge window feeds the house from the battery; the battery
            # is not drawing from the connection then.
            return
        entity_id = self.config.get(CONF_GRID_IMPORT_ENTITY)
        if not entity_id or self._grid_budget() is None:
            return

        async def _on_grid_import_change(event: Event[EventStateChangedData]) -> None:
            """Throttle the battery when the rest of the house needs the connection."""
            try:
                await self._react_to_grid_import()
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected error in grid import listener: %s", e, exc_info=True)

        self._grid_limit_listener = async_track_state_change_event(
            self.hass,
            str(entity_id),
            _on_grid_import_change,
        )
        _LOGGER.debug("Set up grid import listener for %s", entity_id)

    def _remove_grid_import_listener(self) -> None:
        """Remove the grid import listener."""
        if self._grid_limit_listener:
            self._grid_limit_listener()
            self._grid_limit_listener = None
            _LOGGER.debug("Removed grid import listener")

    async def _react_to_grid_import(self) -> None:
        """Lower the written setpoint immediately when the connection gets tight.

        Downwards only. A load peak that ends is picked up by the next regular
        poll, so a momentary dip cannot make the setpoint jump back up - and a
        mistake here can therefore only ever reduce the charge power.
        """
        if not self.is_active or not self.is_enabled or self._ending or self._unloading:
            return
        if self.is_discharge_mode:
            return
        if self._is_backup_active():
            # Off the grid there is no import to limit, and backup mode owns the
            # inverter until it ends.
            return
        if self.target_reached:
            # The window is not over: the raised floor can still draw power, so
            # the limit is kept current instead of being abandoned here.
            await self._enforce_grid_limit_now()
            return
        if self.auto_efficient_charge or self._auto_test_active:
            # The finder owns the setpoint, but not the connection: a load that
            # appears after its write has to reach the inverter too.
            await self._limit_the_efficiency_finder()
            return
        written = self._planned_setpoint_written_w
        if written is None:
            # Nothing of ours stands on the inverter, so there is nothing to
            # lower - the next plan will be capped before it is written.
            return
        planned = self.planned_charge_power_w
        if planned is None:
            # The planner produced nothing this round (battery SOC unreadable,
            # almost no window left, a bad value). The connection limit must not
            # stop with it: what stands on the inverter is the reference, and
            # this path only ever writes downwards from it.
            planned = written
        limited = self._grid_limited_setpoint(planned)
        if limited > written - PLANNED_POWER_WRITE_THRESHOLD_W:
            return
        if self._grid_write_is_debounced():
            return
        _LOGGER.info(
            "House connection limit: lowering the charge power from %.0f W to %.0f W",
            written,
            limited,
        )
        if not await self._set_ac_charge_limit_w(int(limited)):
            return
        self._planned_setpoint_written_w = limited
        self.planned_charge_power_w = limited
