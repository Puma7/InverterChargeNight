"""The Inverter Charge Night coordinator.

The coordinator holds all runtime logic: it reads the configured entities,
plans the night, writes the charge setpoint and cleans up at window end. It
lives in its own module so that ``__init__.py`` stays a thin setup shim.
"""

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
    async_track_time_change,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.recorder import DATA_INSTANCE, get_instance
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util

from .pv_yield import PVYieldMonitor

from .const import (
    CONF_AUTO_PV_SHORTFALL,
    CONF_PV_ENERGY_TODAY_ENTITY,
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
    CONF_ACTIVE_RANGE_YEARLY,
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
    CONF_MIN_SOC_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_HIGH_PRICE_END,
    CONF_HIGH_PRICE_MARGIN_PCT,
    CONF_HIGH_PRICE_START,
    CONF_PV_FORECAST_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_PRICE_SURCHARGE_CT,
    CONF_PRICE_SURCHARGE_WINDOW_CT,
    CONF_PRICE_UNIT,
    CONF_CURTAILMENT_FEED_IN_ENTITY,
    CONF_CURTAILMENT_LIMIT_W,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_PLANNER_MODE,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_PV_CROSSOVER_DELAY_MIN,
    CONF_BRIDGE_RESERVE_KWH,
    CONF_CHARGE_EFFICIENCY,
    CONF_DISCHARGE_EFFICIENCY,
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
    CURTAILMENT_PEAK_CACHE_S,
    CURTAILMENT_PEAK_DAYS,
    HOUSE_LOAD_PROFILE_CACHE_S,
    PLANNED_POWER_WRITE_THRESHOLD_W,
    PLANNER_MODE_BRIDGE,
    PRICE_UNIT_AUTO,
    DEFAULT_END_TIME,
    DEFAULT_MAX_SOC,
    DEFAULT_OPERATION_MODE,
    DEFAULT_SAFE_FALLBACK_SOC,
    DEFAULT_START_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_ACTIVE_START_DATE,
    DEFAULT_ACTIVE_END_DATE,
    DEFAULT_ACTIVE_RANGE_YEARLY,
    DEFAULT_DISCHARGE_BLOCK_MODE,
    DEFAULT_DISCHARGE_EFFICIENCY,
    DISCHARGE_BLOCK_OFF,
    DISCHARGE_BLOCK_VIA_MIN_SOC,
    MODE_MORNING_DISCHARGE,
    DOMAIN,
    ENERGY_UNITS_KWH,
    ENERGY_UNITS_MWH,
    ENERGY_UNITS_WH,
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
    ADHOC_REASON_EVENING_RESCUE,
    ADHOC_REASON_SERVICE,
    EFFICIENCY_BAND_MAX_SPAN,
    EFFICIENCY_BAND_MIN_SAMPLES,
    EFFICIENCY_BAND_WIDTH_PCT,
    EVENING_RESCUE_CHARGE_LEAD_H,
    DEFAULT_HIGH_PRICE_MARGIN_PCT,
    DEFAULT_PRICE_SURCHARGE_CT,
    DEFAULT_PRICE_UNIT,
    DEFAULT_GRID_CONTINUOUS_PCT,
    DEFAULT_GRID_HEADROOM_W,
    DEFAULT_MIN_CHARGE_POWER_W,
    DEFAULT_MAX_CHARGE_POWER_W,
    GRID_LIMIT_STALE_AFTER_S,
    GRID_LIMIT_MIN_WRITE_INTERVAL_S,
)
from .calculation import calculate_required_soc
from .prices import PriceSeries, mean_price_ct, parse_price_series
from .planner import (
    REASON_CONFLICT_BRIDGE_WINS,
    REASON_CONFLICT_HEADROOM_WINS,
    REASON_FALLBACK,
    CurtailmentOutlook,
    EveningOutlook,
    PlanInput,
    PlanResult,
    allowed_charge_power_w,
    curtailment_outlook,
    evening_outlook,
    evening_reserve_soc,
    grid_budget_w,
    plan_target_soc,
    required_charge_power_w,
)
from .util import add_elapsed, elapsed_seconds, parse_time_str, scheduled_window_bounds
from .communication import CommunicationState

_LOGGER = logging.getLogger(__name__)

# Every entity a window writes to. A window has to end on the entities it
# wrote to, so an options change that replaces one of them ends it first.
_WINDOW_WRITTEN_ENTITY_KEYS = (
    CONF_MIN_SOC_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_DISCHARGE_BLOCK_SWITCH,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
)

type InverterChargeNightConfigEntry = ConfigEntry[InverterChargeNightCoordinator]

# A failed reset at window end is retried after these delays (seconds), then
# RESET_RETRY_SLOW_ATTEMPTS more times every RESET_RETRY_INTERVAL. After that
# the integration stops writing and files a repair issue: on 20 October 2026 an
# unbounded ladder rewrote the inverter's min SOC every 15 minutes for hours.
RESET_RETRY_DELAYS: tuple[int, ...] = (60, 120, 240)
RESET_RETRY_INTERVAL = 900
RESET_RETRY_SLOW_ATTEMPTS = 2
# A protective charge limit only matters while the battery can draw from the
# connection. With grid charging switched off and the battery this many points
# above the min SOC floor, it cannot: on 20 October 2026 a short meter outage
# had the integration write 0 W at 03:10 although nothing was charging, and the
# value it captured before that write fed the reset loop at the window end.
IDLE_BATTERY_SOC_MARGIN = 5.0
# A battery SOC this recent still answers that question while the entity briefly
# reports nothing (the same inverter hiccup also blanks the SOC); a battery
# cannot fall by the margin above in that time.
IDLE_SOC_MAX_AGE_S = 300
# The verification rewrites a charge limit the inverter does not hold at most
# this many times per window. That night the Kostal reported its own 22 kW
# again after every 0 W write, and the integration wrote it anew every 15
# minutes until the window ended.
AC_LIMIT_MAX_REWRITES = 2
# An unconfirmed grid-charge stop on reaching the target is retried from the
# regular poll, sending turn_off at most this many more times per window; the
# window-end reset, with its own bounded ladder, takes it from there.
GRID_STOP_MAX_RETRIES = 2
# KORE polls the charge limit with its slow registers, about every 30 s: a
# recheck after switching grid charging on sees a reading made after it
SWITCH_ON_RECHECK_S = 45
# How long a write KORE reports as unconfirmed waits for its fresh reading.
# The write itself makes KORE's next poll (5 s) read the register.
LIMIT_FEEDBACK_WAIT_S = 12
# The Kostal hands battery control back to its own management unless the
# Modbus values are written again within its timeout (30-60 s; 20/21 October:
# 1000 W written at 23:01, about 8 kW charging at 23:12). KORE and evcc write
# them cyclically; so does a window that can draw from the grid, at this interval.
# Sleep plus the write itself must stay inside the shortest timeout, 30 s: a
# third of it is left for the write and the event loop (Codex on #38).
KOSTAL_MODBUS_TIMEOUT_MIN_S = 30
CHARGE_LIMIT_KEEPALIVE_S = 20
# Only controls whose value lapses are kept alive: a persistent setting written
# every 20 s all night is traffic and wear for nothing (Codex on #38)
EXPIRING_LIMIT_PLATFORMS = frozenset({"kostal_kore"})
# A lapse the reading still shows this long after the first keepalive saw it,
# writes acknowledged or not, is verified: two of KORE's slow polls and more
KEEPALIVE_LAPSE_GRACE_S = 75
SKIP_NEXT_DURATION = timedelta(hours=24)
# A bridge longer than this means the sunrise or the window end is off; the plan
# is still made, but it is worth a warning in the log.
MAX_PLAUSIBLE_BRIDGE_HOURS = 12.0
# Below this much time left in the window the charge setpoint is not written any
# more: the required power goes to infinity and would order the maximum.
MIN_PLAN_HOURS_REMAINING = 1.0 / 60.0  # one minute
# Home Assistant's built-in sun entity; next_rising / next_setting feed the planner
SUN_ENTITY_ID = "sun.sun"


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


def _within_entity_range(state: Any, value: float, entity_id: str, what: str) -> float:
    """Clamp a value about to be restored to what the number entity accepts.

    A captured original is whatever the device reported, and a device may
    report more than its own control accepts: the Kostal charge limit read
    22063 W through an entity that takes 0 - 12540 W. Home Assistant refuses
    such a write, so a restore of the raw value can never succeed - and a
    restore that can never succeed used to be retried for ever. The entity's
    maximum is the largest limit it can express, which for a power limit means
    "unlimited", so that is what is restored.
    """
    attributes = getattr(state, "attributes", None) or {}
    low = _as_float(attributes.get("min"))
    high = _as_float(attributes.get("max"))
    clamped = value
    if high is not None and clamped > high:
        clamped = high
    if low is not None and clamped < low:
        clamped = low
    if clamped != value:
        _LOGGER.warning(
            "%s: the captured value %.3f lies outside what %s accepts (%s - %s); "
            "restoring %.3f instead",
            what,
            value,
            entity_id,
            "?" if low is None else f"{low:g}",
            "?" if high is None else f"{high:g}",
            clamped,
        )
    return clamped


class InverterChargeNightCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Inverter Charge Night integration."""

    # Class-level defaults: _persist_state and the window guards read these and
    # may run on an instance whose constructor did not (test doubles built with
    # __new__).
    _original_discharge_limit: float | None = None
    # Explicit reservations may keep a raised min SOC floor, the state the
    # discharge block switch had before this window turned it on, and the way
    # already announced in the log for this window.
    _window_floor_soc: float | None = None
    _window_started_at: datetime | None = None
    _last_scheduled_end: datetime | None = None
    _communication: CommunicationState | None = None
    _communication_texts: dict[str, str] | None = None
    _original_discharge_block: bool | None = None
    _discharge_block_logged: str | None = None
    # True while _on_window_end tears the window down: nothing may re-arm the
    # listeners or write the window target back to the inverter in between.
    _ending: bool = False
    _switching_mode: bool = False
    # Set while the options update tears a window down; see async_update_entry
    _reconfiguring: bool = False
    _reset_abandoned: bool = False
    _ac_limit_rewrites: int = 0
    _ac_limit_not_held: bool = False
    _ac_limit_window: str | None = None
    _grid_switch_suspended: bool = False
    _grid_stop_pending: bool = False
    _grid_stop_retries: int = 0
    _last_trusted_soc: tuple[float, datetime] | None = None
    _last_trusted_min_soc: tuple[float, datetime] | None = None
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
        # Set by the reset_inverter action: until this moment the running window
        # is not taken up again. Without it the action would be a no-op - the min
        # SOC listener writes the window's floor back within milliseconds, and
        # the window check restarts the window on the next update.
        self._hands_off_until: datetime | None = None
        # The end date of the expired range the repair issue currently names,
        # so the registry is only written when that changes.
        self._expired_range_issue: date | None = None
        # The last reason a price entity was refused, so the warning is written
        # once rather than every fifteen minutes.
        self._price_refusal_logged: str | None = None
        # Where the prices behind the last plan's conflict decision came from:
        # "entity" or "static", and None when the last plan decided no conflict
        # or decided it without prices. Set only by _plan_target and cleared
        # with last_plan, so it always describes the plan the sensor shows.
        self._conflict_prices_source: str | None = None
        # An ad-hoc window (plan 013): a window like any other - same capture,
        # restore, retry ladder and interlocks - whose start came from a call
        # rather than from the clock. None means no ad-hoc window is running.
        self._adhoc_until: datetime | None = None
        self._adhoc_reason: str | None = None
        self._adhoc_target_soc: float | None = None
        self._adhoc_allow_grid_charge = False
        # The evening rescue: 0 nothing, 1 holding what is there, 2 also buying.
        # Only ever raised within a day, so a poll cannot flap between stages.
        self._rescue_stage = 0
        # Buying for the evening costs money now and pays it back later, so it
        # waits for the user to switch it on. Blocking the discharge does not.
        self.evening_rescue_charge = False
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
        self._pv_yield_notice: tuple[str, str] | None = None
        self._pv_yield = PVYieldMonitor()
        self.snow_nights: int = 0
        self._original_absolute_charge_power: float | None = None
        # What we last wrote there ourselves. Without it a fresh capture
        # cannot tell the user's value from our own - see the guard in
        # _apply_absolute_charge_power_limit.
        self._absolute_charge_power_written_w: float | None = None
        self._original_ac_charge_power: float | None = None  # W, captured before the finder's first write
        self._pending_reset = False  # window ended but the inverter is not back at its original settings
        # The bounded retry ladder ran out: nothing is written automatically any
        # more until a window starts, a manual reset runs or a reset succeeds.
        self._reset_abandoned = False
        # Rewrites of a charge limit the inverter did not hold, this window;
        # bounded by AC_LIMIT_MAX_REWRITES, then a repair issue instead.
        self._ac_limit_rewrites = 0
        self._ac_limit_not_held = False
        # Which window spent them: a restart inside it resumes, any other starts over
        self._ac_limit_window: str | None = None
        # The charge limit did not hold with grid charging switched on: the
        # switch stays off for the rest of the window, the floor still charges
        self._grid_switch_suspended = False
        # Reaching the target asked for a grid-charge stop that was not confirmed off
        self._grid_stop_pending = False
        self._grid_stop_retries = 0
        self._last_trusted_soc: tuple[float, datetime] | None = None
        # The inverter's own min SOC as last reported, for the same question
        self._last_trusted_min_soc: tuple[float, datetime] | None = None
        self._reset_retry_unsub: CALLBACK_TYPE | None = None
        self._switch_on_recheck_unsub: CALLBACK_TYPE | None = None
        self._reset_retry_count = 0
        self._battery_soc_listener: CALLBACK_TYPE | None = None
        self._inverter_min_soc_listener: CALLBACK_TYPE | None = None
        self._verification_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._keepalive_lapse_since: datetime | None = None
        self._verifying_min_soc = False
        self._min_soc_write_lock = asyncio.Lock()
        self._ac_charge_limit_write_lock = asyncio.Lock()
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
        self._auto_test_limit_confirmed = False
        self._auto_last_sample_time: datetime | None = None
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0
        # The state of charge the running measurement started at, so its loss
        # can be filed under the part of the battery it was measured in.
        self._auto_start_soc: float | None = None
        self._auto_missing_entities_logged = False
        self._auto_wait_reason: str | None = "not_evaluated"
        # Planner v2 (plan 006)
        self.last_plan: PlanResult | None = None  # result of the last bridge plan this window
        # The evening outlook belongs to the day, not to a window, so it is
        # kept here rather than in the coordinator data, which is empty while
        # no window runs - which is exactly when this matters (plan 013).
        self.last_evening_outlook: EveningOutlook | None = None
        self.last_curtailment_outlook: CurtailmentOutlook | None = None
        # Built once per poll: the attributes need the recorder, and an
        # entity's attribute property cannot await anything.
        self.last_curtailment_attributes: dict[str, Any] = {}
        self.planned_charge_power_w: float | None = None  # setpoint for the remaining window
        # Whether that setpoint is also ordered from the inverter. The plan is
        # worth showing in every mode, but a number that looks like a command
        # and is not would be the worse kind of wrong (see the sensor).
        self.planned_power_is_applied = False
        # The last plan tried to write its limit and the write failed - not
        # the same as a plan that writes nothing on purpose
        self._plan_limit_write_failed = False
        self._pv_crossover: datetime | None = None
        self._original_discharge_limit: float | None = None  # raw value in the entity's unit
        self._planned_setpoint_written_w: float | None = None
        self._house_load_cache: tuple[datetime, list[float]] | None = None
        self._feed_in_peak_cache: tuple[datetime, list[float]] | None = None
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
        self._auto_search_deferred = False
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
        self._last_scheduled_end: datetime | None = None
        self._original_discharge_block: bool | None = None
        self._discharge_block_logged: str | None = None
        self._restore_state()
        saved = self.entry.options.get(CONF_AUTO_EFFICIENCY_DATA)
        if isinstance(saved, dict) and saved and "measurement_signature" not in saved:
            # Older versions did not bind measurements to a sensor/hardware
            # configuration. Their provenance cannot be established safely.
            self._save_auto_efficiency_data({"history": {}})

    def enable_communication_monitoring(self) -> None:
        """Enable user-facing incident reporting after real HA entry setup."""
        raw = self.entry.options.get(CONF_RUNTIME_STATE, {})
        self._communication = CommunicationState(raw.get("communication") if isinstance(raw, dict) else None)

    async def async_load_communication_translations(self) -> None:
        from homeassistant.helpers.translation import async_get_translations
        translations = await async_get_translations(self.hass, self.hass.config.language, "issues", {DOMAIN})
        prefix = f"component.{DOMAIN}.issues.communication_"
        self._communication_texts = {
            key: translations[prefix + key + ".description"]
            for key in ("stopped", "unconfirmed", "interrupted", "recovered")
        }
        self._communication_texts.update({
            "title_interrupted": translations[prefix + "interrupted.title"],
            "title_recovered": translations[prefix + "recovered.title"],
        })
        pv_prefix = f"component.{DOMAIN}.issues.pv_yield_shortfall."
        self._pv_yield_notice = (translations[pv_prefix + "title"], translations[pv_prefix + "description"])


    def setup_communication_listener(self) -> None:
        """Wake the coordinator on loss/recovery instead of waiting for its poll."""
        for unsubscribe in getattr(self, "_communication_unsubscribers", []):
            unsubscribe()
        self._communication_unsubscribers: list[CALLBACK_TYPE] = []
        entities = [self.config[key] for key in (
            CONF_BATTERY_SOC_ENTITY, CONF_MIN_SOC_ENTITY, CONF_GRID_CHARGE_SWITCH,
            CONF_CHARGE_POWER_ENTITY, CONF_GRID_IMPORT_ENTITY, CONF_BACKUP_MODE_ENTITY,
            CONF_FORCE_DISCHARGE_SWITCH,
        ) if self.config.get(key)]
        numeric = {self.config[key] for key in (
            CONF_BATTERY_SOC_ENTITY, CONF_MIN_SOC_ENTITY,
            CONF_CHARGE_POWER_ENTITY, CONF_GRID_IMPORT_ENTITY,
        ) if self.config.get(key)}

        def unreadable(state: Any) -> bool:
            if state is None or state.state in ("unknown", "unavailable"):
                return True
            if state.entity_id in numeric:
                try:
                    return not math.isfinite(float(state.state))
                except ValueError:
                    return True
            return False

        async def changed(event: Event[EventStateChangedData]) -> None:
            before, after = event.data.get("old_state"), event.data.get("new_state")
            if unreadable(before) or unreadable(after):
                await self.async_request_refresh()

        self._communication_unsubscribers.append(async_track_state_change_event(self.hass, entities, changed))

        async def check_staleness(now: datetime) -> None:
            # A silent sensor does not emit an unavailable state. Check the
            # existing meter freshness policy even with a long planning poll.
            if self._auto_test_active:
                self._accumulate_auto_energy()
            # A pending reset is not a reason: its retry timer owns the next
            # attempt, and a refresh every 15 s only persisted state for nothing.
            # Nor is its "write_unconfirmed" in the health monitor (CodeRabbit on #36).
            if self._auto_measurement_is_complete() or self._communication_faults() or (
                self._communication is not None
                and any(fault != "write_unconfirmed" for fault in self._communication.faults)
            ):
                await self.async_request_refresh()

        self._communication_unsubscribers.append(async_track_time_interval(
            self.hass, check_staleness, timedelta(seconds=15),
        ))
        # Register a single unload callback; reconfiguration replaces its handles.
        if not getattr(self, "_communication_unload_registered", False):
            self.entry.async_on_unload(self._unsubscribe_communication)
            self._communication_unload_registered = True

    def _unsubscribe_communication(self) -> None:
        for unsubscribe in self._communication_unsubscribers:
            unsubscribe()
        self._communication_unsubscribers.clear()

    def communication_diagnostics(self) -> dict[str, Any]:
        """Return a local snapshot without reading the inverter or exposing IDs."""
        return self._communication.snapshot() if self._communication is not None else {"state": "starting"}

    def _communication_faults(self) -> list[str]:
        faults: list[str] = []
        for role, key in (("battery_soc", CONF_BATTERY_SOC_ENTITY),
                          ("min_soc", CONF_MIN_SOC_ENTITY),
                          ("grid_switch", CONF_GRID_CHARGE_SWITCH),
                          ("charge_limit", CONF_CHARGE_POWER_ENTITY),
                          ("discharge_switch", CONF_FORCE_DISCHARGE_SWITCH),
                          ("island_contact", CONF_BACKUP_MODE_ENTITY)):
            entity_id = self.config.get(key)
            state = self.hass.states.get(entity_id) if entity_id else None
            if entity_id and (state is None or state.state in ("unknown", "unavailable")):
                faults.append(role)
            elif state is not None:
                if role in ("grid_switch", "discharge_switch") and state.state not in ("on", "off"):
                    faults.append(role)
                if role in ("min_soc", "charge_limit"):
                    try:
                        value = float(state.state)
                        if not math.isfinite(value) or value < 0 or (role == "min_soc" and value > 100):
                            faults.append(role)
                    except ValueError:
                        faults.append(role)
                if role == "island_contact" and not self._backup_mode_states() and state.state.strip().lower() not in BACKUP_ACTIVE_STATES | BACKUP_INACTIVE_STATES:
                    faults.append(role)
        if self.config.get(CONF_BATTERY_SOC_ENTITY) and self._current_battery_soc() is None:
            faults.append("battery_soc")
        meter = self.config.get(CONF_GRID_IMPORT_ENTITY)
        if meter and self._read_grid_import_w(str(meter)) is None:
            faults.append("grid_meter")
        return sorted(set(faults))

    def _update_communication_health(self) -> list[str]:
        """Retain one notification across outages and subsequent recovery."""
        health = self._communication
        if health is None:
            return []
        faults = self._communication_faults()
        if self._pending_reset or self._reset_abandoned:
            # Giving up on the restore is not a recovery: the notification
            # keeps saying the stop is unconfirmed until it really is.
            faults.append("write_unconfirmed")
        switch_id = self.config.get(CONF_GRID_CHARGE_SWITCH)
        switch = self.hass.states.get(switch_id) if switch_id else None
        stopped = bool(switch is not None and switch.state == "off") if faults else None
        force_id = self.config.get(CONF_FORCE_DISCHARGE_SWITCH)
        if faults and force_id:
            force = self.hass.states.get(force_id)
            stopped = stopped and force is not None and force.state == "off"
        announce = health.observe(faults, dt_util.now(), stopped)
        if announce:
            from homeassistant.components import persistent_notification
            persistent_notification.async_create(
                self.hass, health.message(self._communication_texts),
                title=(self._communication_texts["title_interrupted" if faults else "title_recovered"]
                       if self._communication_texts else "Inverter Charge Night: " + ("Kommunikation gestört" if faults else "Verbindung wiederhergestellt")),
                notification_id=f"inverter_charge_night_{self.entry.entry_id}_communication",
            )
            _LOGGER.warning("Communication status: %s; HA grid-charge-off feedback: %s", faults or "recovered", stopped)
        if announce or faults:
            self._persist_state()
        return [fault for fault in faults if fault != "write_unconfirmed"]

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
            **({"communication": self._communication.snapshot()} if self._communication is not None else {}),
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
            **({"last_scheduled_end": self._last_scheduled_end.isoformat()} if self._last_scheduled_end else {}),
            "original_discharge_block": self._original_discharge_block,
            "original_absolute_charge_power": self._original_absolute_charge_power,
            "pending_reset": self._pending_reset,
            "reset_abandoned": self._reset_abandoned,
            "reset_attempts": self._reset_retry_count,
            # Bounded per window, a restart inside the window included (Codex on #36)
            "ac_limit_rewrites": self._ac_limit_rewrites,
            "ac_limit_not_held": self._ac_limit_not_held,
            "ac_limit_window": self._ac_limit_window,
            "grid_switch_suspended": self._grid_switch_suspended,
            "hands_off_until": (
                self._hands_off_until.isoformat() if self._hands_off_until else None
            ),
            # A restart in the middle of an ad-hoc window must still end it at
            # the right moment - for the evening rescue that end is the release
            # of the discharge block, and a battery whose release a restart
            # swallowed stays blocked until the next regular window end.
            "adhoc_until": self._adhoc_until.isoformat() if self._adhoc_until else None,
            "adhoc_reason": self._adhoc_reason,
            "adhoc_target_soc": self._adhoc_target_soc,
            "adhoc_allow_grid_charge": self._adhoc_allow_grid_charge,
            "evening_rescue_charge": self.evening_rescue_charge,
            "snow_nights": self.snow_nights,
            "pv_yield": self._pv_yield.snapshot(),
        }
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    def _restore_state(self) -> None:
        """Restore the persisted flags; missing or malformed values keep their defaults."""
        raw = self.entry.options.get(CONF_RUNTIME_STATE)
        if not isinstance(raw, dict):
            return
        state: dict[str, Any] = raw
        self._pv_yield = PVYieldMonitor(state.get("pv_yield"))
        if isinstance(state.get("is_enabled"), bool):
            self.is_enabled = state["is_enabled"]
        if isinstance(state.get("evening_rescue_charge"), bool):
            self.evening_rescue_charge = state["evening_rescue_charge"]
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
        completed_raw = state.get("last_scheduled_end")
        if isinstance(completed_raw, str):
            try:
                self._last_scheduled_end = datetime.fromisoformat(completed_raw)
            except ValueError:
                self._last_scheduled_end = None
        block_raw = state.get("original_discharge_block")
        if isinstance(block_raw, bool):
            self._original_discharge_block = block_raw
        self._original_absolute_charge_power = _as_float(
            state.get("original_absolute_charge_power")
        )
        self._pending_reset = state.get("pending_reset") is True
        self._reset_abandoned = state.get("reset_abandoned") is True
        attempts = state.get("reset_attempts")
        if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts > 0:
            self._reset_retry_count = attempts
        rewrites = state.get("ac_limit_rewrites")
        if isinstance(rewrites, int) and not isinstance(rewrites, bool) and rewrites > 0:
            self._ac_limit_rewrites = rewrites
        self._ac_limit_not_held = state.get("ac_limit_not_held") is True
        window_raw = state.get("ac_limit_window")
        self._ac_limit_window = window_raw if isinstance(window_raw, str) else None
        self._grid_switch_suspended = state.get("grid_switch_suspended") is True
        snow_raw = state.get("snow_nights")
        if isinstance(snow_raw, int) and not isinstance(snow_raw, bool) and snow_raw > 0:
            self.snow_nights = snow_raw

        adhoc_raw = state.get("adhoc_until")
        if isinstance(adhoc_raw, str):
            try:
                adhoc_until = datetime.fromisoformat(adhoc_raw)
            except (ValueError, TypeError):
                _LOGGER.warning("Ignoring invalid persisted adhoc_until %r", adhoc_raw)
            else:
                now_local = dt_util.now()
                # A naive persisted value is not reachable through the actions
                # (they all take a duration and add it to an aware now), but an
                # unguarded comparison here raises inside __init__ - and that
                # means the integration does not load at all.
                if elapsed_seconds(self._align_tz(adhoc_until, now_local), now_local) > 0:
                    adhoc_until = self._align_tz(adhoc_until, now_local)
                    self._adhoc_until = adhoc_until
                    self._adhoc_reason = (
                        state["adhoc_reason"] if isinstance(state.get("adhoc_reason"), str) else None
                    )
                    self._adhoc_target_soc = self._restore_soc(state, "adhoc_target_soc")
                    self._adhoc_allow_grid_charge = state.get("adhoc_allow_grid_charge") is True
                    _LOGGER.info(
                        "Restored the ad-hoc window (%s) running until %s",
                        self._adhoc_reason,
                        adhoc_until.isoformat(timespec="minutes"),
                    )
                else:
                    # It is over; the window end below resets the inverter, and
                    # a pending reset was persisted with it.
                    _LOGGER.info("The persisted ad-hoc window ended during downtime")

        hands_off_raw = state.get("hands_off_until")
        if isinstance(hands_off_raw, str):
            try:
                hands_off = datetime.fromisoformat(hands_off_raw)
            except (ValueError, TypeError):
                _LOGGER.warning("Ignoring invalid persisted hands_off_until %r", hands_off_raw)
            else:
                # A deadline in the past is simply over; no timer is needed
                # either way, because the window check looks at the clock.
                if hands_off > dt_util.now():
                    self._hands_off_until = hands_off

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
        # The attempts already spent stay spent (restored with the state).
        self._schedule_reset_retry()

    def _automatic_pv_shortfall_active(self) -> bool:
        return bool(self.config.get(CONF_AUTO_PV_SHORTFALL)) and self._adhoc_until is None and self._pv_yield.active_for(
            self._window_end_datetime(dt_util.now()).date()
        )

    def pv_yield_attributes(self) -> dict[str, Any]:
        return {**self._pv_yield.snapshot(),
                "enabled": bool(self.config.get(CONF_AUTO_PV_SHORTFALL)),
                "automatic_full_charge": self._automatic_pv_shortfall_active()}

    def _observe_pv_yield(self) -> None:
        if not self.config.get(CONF_AUTO_PV_SHORTFALL) or self.is_discharge_mode:
            return
        now = dt_util.now()
        sun = self.hass.states.get(SUN_ENTITY_ID)
        if sun is None or not all(dt_util.parse_datetime(str(sun.attributes.get(key, ""))) for key in ("next_rising", "next_setting")):
            return
        sunrise, sunset = self._sun_times(now, now)
        sunset -= timedelta(days=(sunset.date() - now.date()).days)
        forecast, available = self._parse_forecast_energy(self.config.get(CONF_PV_FORECAST_TODAY_ENTITY))
        entity = self.config.get(CONF_PV_ENERGY_TODAY_ENTITY)
        state = self.hass.states.get(entity) if entity else None
        age = self._state_age_s(state, now)
        energy = self._read_energy_wh(str(entity)) if state is not None else None
        actual = energy / 1000 if energy is not None and age is not None and 0 <= age <= 1800 else None
        before = self._pv_yield.snapshot()
        incident = self._pv_yield.observe(now, sunrise, sunset, forecast if available else None, actual)
        if before != self._pv_yield.snapshot():
            self._persist_state()
        if incident:
            ir.async_create_issue(
                self.hass, DOMAIN, f"pv_yield_shortfall_{self.entry.entry_id}",
                is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="pv_yield_shortfall",
                translation_placeholders={"name": self.entry.title,
                    "expected": str(self._pv_yield.expected_kwh),
                    "actual": str(self._pv_yield.actual_kwh)},
            )
            if self._pv_yield_notice is not None:
                from homeassistant.components import persistent_notification
                title, description = self._pv_yield_notice
                persistent_notification.async_create(
                    self.hass, description.format(name=self.entry.title,
                        expected=self._pv_yield.expected_kwh, actual=self._pv_yield.actual_kwh),
                    title=title, notification_id=f"icn_pv_yield_{self.entry.entry_id}",
                )
        elif self._pv_yield.status == "normal":
            ir.async_delete_issue(self.hass, DOMAIN, f"pv_yield_shortfall_{self.entry.entry_id}")



    def current_target_soc(self) -> float | None:
        """The SOC the inverter should hold right now: snow mode, else manual override, else the plan.

        Snow mode only applies to night charge. A morning discharge window told
        to charge to the user maximum would reach its target instantly and do
        nothing at all, so the counter is ignored (and not counted down) there.
        """
        if (self.snow_nights > 0 or self._automatic_pv_shortfall_active()) and not self.is_discharge_mode:
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

    def _representable_min_soc(self, value: float, *, round_up: bool = True) -> float | None:
        """Select a representable floor without exceeding device/user bounds."""
        entity_id = self.config.get(CONF_MIN_SOC_ENTITY)
        state = self.hass.states.get(entity_id) if entity_id else None
        attributes: Any = state.attributes if state is not None else {}
        if not isinstance(attributes, Mapping):
            return value
        step = _as_float(attributes.get("step"))
        if step is None or step <= 0:
            return value
        lower = _as_float(attributes.get("min")) or 0.0
        upper = _as_float(attributes.get("max"))
        upper = min(upper if upper is not None else 100.0,
                    float(self.config.get(CONF_USER_MAX_SOC, DEFAULT_MAX_SOC)))
        if round_up:
            result = lower + math.ceil((max(value, lower) - lower) / step - 1e-9) * step
        else:
            result = lower + math.floor((min(value, upper) - lower) / step + 1e-9) * step
        user_min = float(self.config.get(CONF_USER_MIN_SOC, 8.0))
        if result > upper + 1e-9 or result < max(lower, user_min) - 1e-9:
            _LOGGER.error("No representable min SOC for %.3f%% within the configured bounds", value)
            return None
        return round(result, 9)

    def _charging_floor_soc(self, target_soc: float) -> float | None:
        """Keep semantic reserve decisions separate from actuator rounding."""
        floor = self.inverter_floor_soc(target_soc)
        if floor is None or self.is_discharge_mode:
            return floor
        soc = self._current_battery_soc()
        if soc is None:
            return None
        # Only an actual charging request may round upwards. A hold must not
        # create a new grid-energy demand just to reach the next input step.
        return self._representable_min_soc(floor, round_up=(soc < target_soc and not self.target_reached))

    def inverter_floor_soc(self, target_soc: float | None = None) -> float | None:
        """The minimum SOC written to the inverter.

        Scheduled nights use the planned charge target, so energy above it can
        supply the house. Only an explicit ad-hoc reservation may preserve a
        higher initial/rising SOC. Target completion always uses the charge
        target, while verification and restoration use this written floor.
        """
        if target_soc is None:
            target_soc = self.current_target_soc()
        if target_soc is None:
            return None
        if not self.is_active or self._ending:
            return target_soc
        if self._adhoc_until is None or self._discharge_block_method() != DISCHARGE_BLOCK_VIA_MIN_SOC:
            return target_soc
        floor = self._window_floor_soc
        if floor is None or floor <= target_soc:
            return target_soc
        user_min_soc = float(self.config.get(CONF_USER_MIN_SOC, 8.0))
        user_max_soc = float(self.config.get(CONF_USER_MAX_SOC, DEFAULT_MAX_SOC))
        if not user_min_soc <= target_soc <= user_max_soc:
            # The target itself is out of bounds; _control_charge refuses it and
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
            until = add_elapsed(now, SKIP_NEXT_DURATION)
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

        remaining = max(0.0, elapsed_seconds(until, now))
        self._skip_next_unsub = async_call_later(self.hass, remaining, _expire_skip_next)

    def clear_hands_off(self) -> None:
        """Let the integration drive the running window again.

        The ``reset_inverter`` action keeps it off the inverter until the
        window's end time; switching the integration back on is the user saying
        otherwise. The caller persists.
        """
        if self._hands_off_until is not None:
            _LOGGER.info("Driving the window again - the inverter is no longer handed back")
            self._hands_off_until = None

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

    def _high_price_window(self, after: datetime) -> tuple[datetime, datetime] | None:
        """The next high-price period that starts at or after ``after``.

        ``None`` when none is configured. The period may cross midnight, the
        same way the charge window may; its end is then on the following day.
        Everything the battery is meant to carry through it has to be in there
        by its start, which is why the planner is given the pair and not a
        duration.
        """
        start = parse_time_str(self.config.get(CONF_HIGH_PRICE_START))
        end = parse_time_str(self.config.get(CONF_HIGH_PRICE_END))
        if start is None or end is None or start == end:
            return None
        start_dt = after.replace(hour=start[0], minute=start[1], second=0, microsecond=0)
        if start_dt < after:
            start_dt += timedelta(days=1)
        end_dt = start_dt.replace(hour=end[0], minute=end[1])
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        return start_dt, end_dt

    def _price_series(self) -> PriceSeries | None:
        """Read the configured price entity, or None with a reason logged once.

        Every refusal falls back to the static price fields, which is to say
        to the behaviour of an installation with no price entity at all. That
        is the whole contract: a price that cannot be trusted must never
        produce a worse decision than having no price.
        """
        entity_id = self.config.get(CONF_PRICE_ENTITY)
        if not entity_id:
            return None
        state = self.hass.states.get(str(entity_id))
        if not state or state.state in ("unknown", "unavailable", None):
            return None
        unit_override = str(self.config.get(CONF_PRICE_UNIT, DEFAULT_PRICE_UNIT))
        series, reason = parse_price_series(
            state.attributes,
            entity_unit=_unit_of(state),
            unit_override=None if unit_override == PRICE_UNIT_AUTO else unit_override,
            surcharge_ct=float(
                self.config.get(CONF_PRICE_SURCHARGE_CT, DEFAULT_PRICE_SURCHARGE_CT)
            ),
            tz=dt_util.DEFAULT_TIME_ZONE,
            now=dt_util.now(),
        )
        if series is None:
            if self._price_refusal_logged != reason:
                _LOGGER.warning(
                    "Cannot use the price entity %s (%s) - the configured prices are used "
                    "instead. Attributes seen: %s",
                    entity_id,
                    reason,
                    ", ".join(sorted(state.attributes)) or "(none)",
                )
                self._price_refusal_logged = reason
            return None
        if self._price_refusal_logged is not None:
            _LOGGER.info("The price entity %s is usable again", entity_id)
            self._price_refusal_logged = None
        return series

    def _priced_window_bounds(self, now: datetime) -> tuple[datetime, datetime]:
        """The configured window the price question is about: (start, end).

        Derived from the *end*, not from the start. ``_window_start_datetime``
        answers "when does the next one begin", which during a running window
        that crosses midnight is tomorrow's - so at 02:00 in a 23:00 to 05:00
        window it priced the night that has not happened yet. With day-ahead
        data that stretch is usually unpublished, ``mean_price_ct`` refuses it,
        and the reserve was then held whatever the prices said.

        Taking ``end - length`` gives the window that is running when one is,
        and the next one otherwise - which is also the window whose ``end`` the
        planner is given in the same ``PlanInput``. The ad-hoc override is
        deliberately not used: that deadline is not a tariff window.
        """
        return scheduled_window_bounds(now, *self._window_times()) or (now, now)

    def _window_price_ct(self, series: PriceSeries | None, now: datetime) -> float | None:
        """The mean price over the charge window, with the window's own grid fee.

        A reduced tariff applies to those hours only, so the day surcharge that
        every interval carries is swapped for the window one here.
        """
        if series is None:
            return None
        start, end = self._priced_window_bounds(now)
        mean = mean_price_ct(series, start, end)
        if mean is None:
            return None
        window_surcharge = self.config.get(CONF_PRICE_SURCHARGE_WINDOW_CT)
        if window_surcharge is None:
            return mean
        day = float(self.config.get(CONF_PRICE_SURCHARGE_CT, DEFAULT_PRICE_SURCHARGE_CT))
        return mean - day + float(window_surcharge)

    def price_snapshot(self) -> dict[str, Any]:
        """What was parsed and what it says, for the sensor and diagnostics."""
        series = self._price_series()
        now = dt_util.now()
        zone = self._high_price_window(now)
        # The series is always aware; the clock here need not be (the test
        # suite runs on a naive one deliberately), and comparing the two
        # raises rather than answering.
        reading_now = self._align_tz(now, series.intervals[0].start) if series else now
        current = (
            next(
                (i.ct_per_kwh for i in series.intervals if i.start <= reading_now < i.end),
                None,
            )
            if series
            else None
        )
        horizon = series.horizon_end() if series else None
        return {
            "current_ct": round(current, 2) if current is not None else None,
            "source_attribute": series.source_attribute if series else None,
            "value_key": series.value_key if series else None,
            "unit_resolved": series.unit if series else None,
            "surcharge_ct": series.surcharge_ct if series else None,
            "intervals": len(series.intervals) if series else 0,
            "horizon_end": horizon.isoformat() if horizon else None,
            "window_ct": self._window_price_ct(series, now),
            "evening_ct": mean_price_ct(series, zone[0], zone[1]) if (series and zone) else None,
            "conflict_prices_source": self._conflict_prices_source,
            "reason": self._price_refusal_logged,
        }

    def next_high_price_window(self) -> tuple[datetime, datetime] | None:
        """The next high-price period from now, for the sensor."""
        return self._high_price_window(dt_util.now())

    def _window_start_datetime(self, now: datetime) -> datetime:
        """The start of the configured window that is running or comes next.

        The counterpart of :meth:`_window_end_datetime`, and deliberately
        without its ad-hoc override: this is about the window the clock owns.
        """
        start, _ = self._window_times()
        start_dt = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
        if (start.hour, start.minute) < (now.hour, now.minute):
            start_dt += timedelta(days=1)
        return start_dt

    def _window_end_datetime(self, now: datetime) -> datetime:
        """Return the end of the window that is running or comes next, at minute resolution.

        Queries during the end minute retain today's deadline for planning.
        Permission to charge is separate and ends at the exact boundary.

        Boundaries use the first occurrence of an ambiguous wall time. The
        planner subtracts them in UTC; same-zone datetime subtraction alone
        would ignore the offset change.
        """
        if self._adhoc_until is not None and elapsed_seconds(self._adhoc_until, now) > 0:
            # An ad-hoc window ends when it was told to, not when the configured
            # one would. Everything that asks "how long has this window left" -
            # the charge power plan above all - has to get that answer.
            #
            # Only while it is still ahead of us. A deadline left behind in the
            # past would answer every planner question with a window that ended
            # long ago, which is worse than having no ad-hoc window at all.
            return self._adhoc_until
        bounds = scheduled_window_bounds(now, *self._window_times())
        return bounds[1] if bounds is not None else now

    def _scheduled_window_is_open(self, now: datetime) -> bool:
        """Membership by real instants; a repeated wall hour cannot reopen it."""
        bounds = scheduled_window_bounds(now, *self._window_times())
        if bounds is None:
            return False
        if self._last_scheduled_end is not None and elapsed_seconds(
            bounds[1], self._align_tz(self._last_scheduled_end, now)
        ) <= 0:
            return False  # A clock rollback must not reopen a completed window.
        return elapsed_seconds(now, bounds[0]) >= 0 and elapsed_seconds(bounds[1], now) > 0

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
        value = _as_float(state.state)
        if value is None or not 0 <= value <= 100:
            return None
        seen = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
        if isinstance(seen, datetime):
            self._last_trusted_soc = (value, seen)
        return value

    @staticmethod
    def _trusted_reading(state: Any) -> tuple[float, datetime] | None:
        """A percentage with the time its entity reported it, or None."""
        if state is None:
            return None
        value = _as_float(state.state)
        seen = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
        if value is None or not 0 <= value <= 100 or not isinstance(seen, datetime):
            return None
        return value, seen

    def _observe_min_soc(self, state: Any) -> None:
        """Remember the inverter's min SOC; a later blank state does not erase it."""
        reading = self._trusted_reading(state)
        if reading is not None:
            self._last_trusted_min_soc = reading

    def _battery_idle_on_grid(self) -> bool:
        """True when the battery provably cannot draw from the connection right now.

        It can do so in two ways: grid charging, and the inverter topping the
        battery up to its min SOC. Neither is possible once the target is
        reached, grid charging is switched off and the battery is well above
        every floor this window wrote - and then a protective charge limit
        protects nothing, while writing it captures a value that has to be
        restored later. Anything unknown answers False and keeps the limit.
        """
        if not self.target_reached:
            return False
        switch_id = self.config.get(CONF_GRID_CHARGE_SWITCH)
        switch = self.hass.states.get(str(switch_id)) if switch_id else None
        if switch is None or switch.state != "off":
            return False
        # The last trusted reading with the time its entity reported it: a SOC
        # that stopped updating proves nothing about the battery now, whether
        # the entity went blank or kept its old number (Codex on #36).
        self._current_battery_soc()
        if self._last_trusted_soc is None:
            return False
        soc, seen = self._last_trusted_soc
        if not 0 <= elapsed_seconds(dt_util.now(), seen) <= IDLE_SOC_MAX_AGE_S:
            return False
        floors: list[float] = []
        planned = self.inverter_floor_soc()
        if planned is not None:
            floors.append(planned)
        # The floor the inverter actually holds has to be known as well: with
        # its entity blank or stale, a manual or foreign floor above the plan
        # could make it draw (Codex on #36). Same freshness rule as the SOC.
        min_soc_id = self.config.get(CONF_MIN_SOC_ENTITY)
        if not min_soc_id:
            return False
        self._observe_min_soc(self.hass.states.get(str(min_soc_id)))
        if self._last_trusted_min_soc is None:
            return False
        held, held_seen = self._last_trusted_min_soc
        if not 0 <= elapsed_seconds(dt_util.now(), held_seen) <= IDLE_SOC_MAX_AGE_S:
            return False
        floors.append(held)
        return soc >= max(floors) + IDLE_BATTERY_SOC_MARGIN

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

    def _conflict_prices(
        self,
        window_ct: float | None,
        series: PriceSeries | None,
        now: datetime,
        window_end: datetime,
        pv_crossover: datetime,
    ) -> tuple[tuple[float, float, float] | None, str | None]:
        """(night, day, feed-in) for the planner's conflict branch, and their source.

        Returns ``(triple, source)`` with source ``"entity"``, ``"static"`` or
        None. It writes nothing: this runs for every caller of
        ``_build_plan_input`` - the curtailment outlook on every poll, the
        read-only ``plan_target_soc`` action - and a source kept on the
        coordinator would report whichever of them ran last rather than the plan
        that actually decided. ``_plan_target`` stores it, next to ``last_plan``.

        The branch weighs ``day - night`` against ``night - feed_in``, so what
        it needs is the night the window buys in and the day the bridge would
        otherwise be bought at: the window's mean price (``window_ct``, computed
        once by the caller and shared with the evening-reserve gate so the two
        can never disagree), and the mean over ``[window_end, pv_crossover)``.

        Night and day come from the price entity **as a pair or not at all**.
        The decision turns on their difference, and a difference between two
        numbers taken from two different sources is partly a difference between
        the sources. So when the entity cannot cover both stretches, both fall
        back to the fixed fields.

        The pair also has to describe **one** window. ``window_ct`` is always
        the configured window, but during an ad-hoc window ``window_end`` is the
        ad-hoc deadline, and a day stretch starting there would be priced
        against the night of a different window. Then the fixed fields decide -
        also when the ad-hoc deadline happens to equal the configured end.

        Feed-in stays a fixed field: no price source publishes a feed-in tariff.
        Without it the triple is None, exactly as before, and the bridge wins.

        The bridge is empty in the morning-discharge mode, where the crossover
        is clamped to the window end because the sun is already up. An empty
        stretch has no mean, so that mode always uses the fixed fields.

        Known approximation: the day price is the mean over the whole bridge,
        while the energy the headroom bound leaves uncovered is the *tail* of
        the bridge, the last hours before the crossover. Pricing the tail needs
        the size of the conflict, which only the planner knows - see plans/012.
        """
        feed_in = _as_float(self.config.get(CONF_FEED_IN_PRICE_CT))
        if feed_in is None:
            return None, None

        if (
            series is not None
            and window_ct is not None
            and pv_crossover > window_end
            # Asked directly, not inferred from the end times: an ad-hoc window
            # can end exactly where the configured one does and still buy in
            # hours the configured window's mean does not price.
            and self._adhoc_until is None
        ):
            _, priced_end = self._priced_window_bounds(now)
            if window_end == priced_end:
                day_ct = mean_price_ct(series, window_end, pv_crossover)
                if day_ct is not None:
                    return (window_ct, day_ct, feed_in), "entity"

        night = _as_float(self.config.get(CONF_NIGHT_PRICE_CT))
        day = _as_float(self.config.get(CONF_DAY_PRICE_CT))
        if night is None or day is None:
            return None, None
        return (night, day, feed_in), "static"

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

    async def _feed_in_peaks_by_hour(self) -> list[float] | None:
        """The highest feed-in power seen in each hour of the day, in W, or None.

        The reality check for the curtailment model. A feed-in curve that sits
        flat on the same number for hours is a curve against a cap, and where it
        sits is where the cap is - which is the one thing the model cannot tell
        you, because it is the number the model was given.

        Not the same call as the house load profile: that one asks for
        ``change``, which only exists for a total/total_increasing meter. A
        power sensor is a ``measurement``, so it has ``max`` and no ``change``
        at all, and asking for both in one call gets neither.
        """
        entity_id = self.config.get(CONF_CURTAILMENT_FEED_IN_ENTITY)
        if not entity_id:
            return None
        now = dt_util.utcnow()
        if self._feed_in_peak_cache is not None:
            cached_at, cached = self._feed_in_peak_cache
            if (now - cached_at).total_seconds() < CURTAILMENT_PEAK_CACHE_S:
                return cached
        peaks = await self._learn_feed_in_peaks(str(entity_id))
        if peaks is not None:
            self._feed_in_peak_cache = (now, peaks)
        return peaks

    async def _learn_feed_in_peaks(self, entity_id: str) -> list[float] | None:
        """Read the hourly maxima of a feed-in power sensor over 14 days."""
        if DATA_INSTANCE not in self.hass.data:
            _LOGGER.debug("Recorder is not loaded; cannot read the feed-in peaks")
            return None
        # Imported here so the integration never depends on the recorder being present
        from homeassistant.components.recorder.statistics import statistics_during_period

        start = (dt_util.utcnow() - timedelta(days=CURTAILMENT_PEAK_DAYS)).replace(
            minute=0, second=0, microsecond=0
        )
        try:
            rows = await get_instance(self.hass).async_add_executor_job(
                lambda: statistics_during_period(
                    self.hass, start, None, {entity_id}, "hour", None, {"max"}
                )
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Reading feed-in statistics for %s failed: %s", entity_id, err)
            return None
        unit = _unit_of(self.hass.states.get(entity_id))
        factor = 1000.0 if unit in ("kw", "kilowatt", "kilowatts") else 1.0
        peaks = [0.0] * 24
        seen = False
        for row in rows.get(entity_id, []):
            start_ts = row.get("start")
            value = row.get("max")
            if not isinstance(start_ts, (int, float)) or not isinstance(value, (int, float)):
                continue
            hour = dt_util.as_local(dt_util.utc_from_timestamp(start_ts)).hour
            # A negative reading is this meter pointing the other way; the peak
            # of an export sensor is never below zero.
            peaks[hour] = max(peaks[hour], float(value) * factor)
            seen = True
        return peaks if seen else None

    async def _build_plan_input(
        self, forecast_kwh: float, forecast_available: bool
    ) -> tuple[PlanInput, datetime, str | None]:
        """Gather every state the planner needs.

        Returns the input, the PV crossover, and where the conflict prices came
        from (``"entity"``, ``"static"`` or None) - returned rather than stored,
        so that callers which only want a projection cannot overwrite what the
        last real plan decided on.

        Split out of :meth:`_plan_target` so that a caller can run the planner
        without the side effects - the ``plan_target_soc`` action answers with a
        plan and must not move the window's target while it does so.
        """
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
        # Plan 012 stage 3: what the window and the period actually cost, when
        # a price entity says. Both None without one, and the reserve is then
        # held exactly as before.
        series = self._price_series()
        window_ct = self._window_price_ct(series, now)
        prices_ct, prices_source = self._conflict_prices(
            window_ct, series, now, window_end, pv_crossover
        )
        high_price_window = self._high_price_window(window_end)
        return (
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
                discharge_efficiency=float(
                    self.config.get(CONF_DISCHARGE_EFFICIENCY, DEFAULT_DISCHARGE_EFFICIENCY)
                ),
                prices_ct=prices_ct,
                # Measured from the window end: the evening the battery has to
                # reach is the one on the solar day this window is planning for.
                high_price_window=high_price_window,
                reserve_margin_pct=float(
                    self.config.get(CONF_HIGH_PRICE_MARGIN_PCT, DEFAULT_HIGH_PRICE_MARGIN_PCT)
                ),
                window_price_ct=window_ct,
                evening_price_ct=(
                    mean_price_ct(series, high_price_window[0], high_price_window[1])
                    if high_price_window
                    else None
                ),
            ),
            pv_crossover,
            prices_source,
        )

    async def _evening_reserve_floor(
        self, forecast_kwh: float, forecast_available: bool
    ) -> float | None:
        """The SOC a morning discharge must not go below, or None.

        Morning discharge empties the battery into the morning peak. What the
        evening's high-price period will need must not be sold there - the same
        reserve the night charge buys, seen from the other direction. What
        today's sun is forecast to deliver is subtracted first, so a summer day
        leaves the discharge untouched.
        """
        if not self.is_discharge_mode or self._high_price_window(dt_util.now()) is None:
            return None
        try:
            plan_input, _, _ = await self._build_plan_input(forecast_kwh, forecast_available)
            return evening_reserve_soc(plan_input)
        except (ValueError, TypeError) as err:
            _LOGGER.error("Could not work out the evening reserve: %s", err)
            return None

    async def _floor_discharge_at_the_evening_reserve(
        self, calculated_soc: float, forecast_kwh: float, forecast_available: bool
    ) -> float:
        """Raise a discharge target that would sell the evening's energy."""
        floor = await self._evening_reserve_floor(forecast_kwh, forecast_available)
        if floor is None or floor <= calculated_soc:
            return calculated_soc
        _LOGGER.info(
            "Morning discharge target raised from %.1f%% to %.1f%% - the high-price "
            "period this evening needs that much in the battery",
            calculated_soc,
            floor,
        )
        return floor

    async def async_evening_outlook(self) -> EveningOutlook | None:
        """Will the battery still carry the high-price period this evening?

        The night plan buys for the evening in advance; this is the question
        the day *after* a forecast that was too good - snow on the panels, say,
        with nobody having set the snow nights. By mid-afternoon it is already
        decidable, and there is still time to do something about it.

        ``None`` when there is no high-price period configured, without a
        forecast entity for *today*, when the battery cannot be read, or when
        the configuration makes the arithmetic impossible. None of those are
        reasons to act.
        """
        now = dt_util.now()
        zone = self._high_price_window(now)
        if zone is None:
            return None
        if self.is_active and self._adhoc_reason != ADHOC_REASON_EVENING_RESCUE:
            # A window owns the inverter and plans for the evening itself. The
            # rescue's own window is the exception: it has to keep seeing the
            # shortfall, or holding would lock out the buying that follows it.
            return None
        if self._window_start_datetime(now) < zone[0]:
            # A charge window runs before the period starts - once the period
            # of the day has begun, the next one is on the other side of a
            # night this projection knows nothing about. Carrying the house
            # across it would invent a shortfall every evening.
            _LOGGER.debug(
                "The next high-price period at %s is behind a charge window - no outlook",
                zone[0].isoformat(timespec="minutes"),
            )
            return None
        today_entity = self.config.get(CONF_PV_FORECAST_TODAY_ENTITY)
        if not today_entity:
            # The tomorrow entity is no stand-in here: in the afternoon it is
            # tomorrow. A sunny tomorrow hid today's shortfall, a dull one
            # invented one, and stage one acts on it. Same rule as the
            # curtailment outlook - no answer rather than the wrong day's.
            return None
        current_soc = self._current_battery_soc()
        if current_soc is None:
            return None
        sunrise, sunset = self._sun_times(now, now)
        forecast_kwh, forecast_available = self._parse_forecast_energy(today_entity)
        try:
            return evening_outlook(
                now=now,
                capacity_kwh=float(self.config.get(CONF_BATTERY_CAPACITY, 10.0)),
                current_soc=current_soc,
                user_min_soc=float(self.config.get(CONF_USER_MIN_SOC, 8.0)),
                user_max_soc=float(self.config.get(CONF_USER_MAX_SOC, 100.0)),
                house_load_kw_profile=await self._house_load_profile(),
                zone_start=zone[0],
                zone_end=zone[1],
                margin_pct=float(
                    self.config.get(CONF_HIGH_PRICE_MARGIN_PCT, DEFAULT_HIGH_PRICE_MARGIN_PCT)
                ),
                charge_efficiency=float(
                    self.config.get(CONF_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)
                ),
                discharge_efficiency=float(
                    self.config.get(CONF_DISCHARGE_EFFICIENCY, DEFAULT_DISCHARGE_EFFICIENCY)
                ),
                sunrise=sunrise,
                sunset=sunset,
                forecast_kwh_today=forecast_kwh,
                forecast_available=forecast_available,
                forecast_error_margin_pct=float(self.config.get(CONF_FORECAST_ERROR_MARGIN, 10.0)),
            )
        except (ValueError, TypeError) as err:
            _LOGGER.error("Could not work out the evening outlook: %s", err)
            return None

    async def async_curtailment_outlook(self) -> CurtailmentOutlook | None:
        """How much of today's PV a permanent feed-in cap will throw away.

        Display only. Nothing is written from this in 3.7.0: the model and the
        observation have to be held against each other for a season first, and
        the sensor's ``model_vs_envelope_pct`` is what does the holding.

        ``None`` without a cap configured, and - deliberately - ``None`` without
        an explicit *today* forecast entity. ``_get_active_forecast_entity``
        falls back to tomorrow's entity when today's is unset, which for a
        morning decision is quietly the wrong day. Better no answer than an
        answer about the wrong day.
        """
        limit_w = self.config.get(CONF_CURTAILMENT_LIMIT_W)
        today_entity = self.config.get(CONF_PV_FORECAST_TODAY_ENTITY)
        if not limit_w or not today_entity:
            return None
        now = dt_util.now()
        sunrise, sunset = self._sun_times(now, now)
        forecast_kwh, forecast_available = self._parse_forecast_energy(today_entity)
        if not forecast_available:
            return None
        reserve_floor = 0.0
        try:
            plan_input, _, _ = await self._build_plan_input(forecast_kwh, forecast_available)
            reserve_floor = evening_reserve_soc(plan_input)
        except (ValueError, TypeError) as err:
            # No reserve is a weaker floor, not a wrong one: the user minimum
            # still holds, and the sensor writes nothing either way.
            _LOGGER.debug("No evening reserve for the curtailment outlook: %s", err)
        try:
            return curtailment_outlook(
                capacity_kwh=float(self.config.get(CONF_BATTERY_CAPACITY, 10.0)),
                user_min_soc=float(self.config.get(CONF_USER_MIN_SOC, 8.0)),
                user_max_soc=float(self.config.get(CONF_USER_MAX_SOC, 100.0)),
                house_load_kw_profile=await self._house_load_profile(),
                sunrise=sunrise,
                sunset=sunset,
                forecast_kwh_today=forecast_kwh,
                forecast_available=forecast_available,
                error_margin_pct=float(self.config.get(CONF_FORECAST_ERROR_MARGIN, 10.0)),
                limit_w=float(limit_w),
                charge_efficiency=float(
                    self.config.get(CONF_CHARGE_EFFICIENCY, DEFAULT_CHARGE_EFFICIENCY)
                ),
                evening_reserve_soc_pct=reserve_floor,
            )
        except (ValueError, TypeError) as err:
            _LOGGER.error("Could not work out the curtailment outlook: %s", err)
            return None

    async def curtailment_attributes(self) -> dict[str, Any]:
        """What the curtailment sensor shows beside its state.

        The measured half is the point of the whole release: the model says how
        much should be spilling, the recorder says how much actually was, and
        the ratio between them is the number that decides whether throttling is
        worth building at all.
        """
        outlook = self.last_curtailment_outlook
        peaks = await self._feed_in_peaks_by_hour()
        limit_w = self.config.get(CONF_CURTAILMENT_LIMIT_W)
        attrs: dict[str, Any] = {
            "limit_w": float(limit_w) if limit_w else None,
            # Plan 014 stage 2 writes the cap on this entity and nothing else
            # can cap DC-side charging. Saying so is better than a feature that
            # silently does nothing.
            "control_entity_configured": bool(
                self.config.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY)
            ),
            "forecast_today_configured": bool(self.config.get(CONF_PV_FORECAST_TODAY_ENTITY)),
        }
        if outlook is None:
            return attrs
        attrs.update(
            {
                "binding_start": (
                    outlook.binding_start.isoformat() if outlook.binding_start else None
                ),
                "binding_end": (
                    outlook.binding_end.isoformat() if outlook.binding_end else None
                ),
                "room_needed_kwh": round(outlook.room_needed_kwh, 2),
                "morning_target_soc": outlook.morning_target_soc,
                "clamped_by": outlook.clamped_by,
                "forecast_available": outlook.forecast_available,
            }
        )
        if peaks is not None:
            # Each entry is the highest that hour of the day ever reached in 14
            # days, so treating it as if it held for the whole hour, and summing
            # across the day, gives an *upper envelope*: a day at least this bad
            # may never actually have happened. It is deliberately not called a
            # measurement of any one day.
            envelope_kwh = (
                sum(max(0.0, peak - float(limit_w or 0.0)) for peak in peaks) / 1000.0
            )
            attrs["measured_peak_w_by_hour"] = {
                str(hour): round(peak) for hour, peak in enumerate(peaks) if peak > 0
            }
            attrs["measured_peak_w"] = round(max(peaks))
            attrs["measured_hours_at_the_cap"] = sum(
                1 for peak in peaks if limit_w and peak >= float(limit_w) * 0.98
            )
            attrs["measured_overflow_kwh_envelope"] = round(envelope_kwh, 2)
            if envelope_kwh > 0:
                # Today's model against that envelope. Comparable only in one
                # direction, and that is the direction that matters: the
                # envelope cannot be exceeded by any real day, so a model above
                # 100 % is claiming an overflow the inverter has never once come
                # close to, and is wrong. Below it says nothing on its own.
                attrs["model_vs_envelope_pct"] = round(
                    outlook.overflow_kwh / envelope_kwh * 100.0, 1
                )
        return attrs

    async def preview_plan(self) -> tuple[PlanResult, PlanInput] | None:
        """Run the planner on the current inputs and change nothing.

        For the ``plan_target_soc`` action: an automation may ask what the
        planner would do at any time, including outside a window, without the
        answer becoming the window's target.
        """
        forecast_kwh, forecast_available = self._parse_forecast_energy(
            self._get_active_forecast_entity()
        )
        try:
            plan_input, _, _ = await self._build_plan_input(forecast_kwh, forecast_available)
            return plan_target_soc(plan_input), plan_input
        except (ValueError, TypeError) as err:
            _LOGGER.error("Bridge planner failed: %s", err)
            return None

    async def _plan_target(self, forecast_kwh: float, forecast_available: bool) -> PlanResult | None:
        """Run the bridge planner on the current inputs; None when it cannot plan.

        The result is kept in ``last_plan`` for the sensor attributes. A failure
        (invalid configuration) is logged and the caller uses the headroom formula.
        """
        try:
            plan_input, pv_crossover, prices_source = await self._build_plan_input(
                forecast_kwh, forecast_available
            )
            plan = plan_target_soc(plan_input)
        except (ValueError, TypeError) as err:
            _LOGGER.error("Bridge planner failed, using the headroom formula instead: %s", err)
            return None
        self.last_plan = plan
        # Only a conflict is decided on these prices. Reporting a source for a
        # plan whose bounds never conflicted would name a decision that was not
        # taken; None then says so, and so does None for a conflict decided
        # without any prices at all.
        self._conflict_prices_source = (
            prices_source
            if plan.reason in (REASON_CONFLICT_BRIDGE_WINS, REASON_CONFLICT_HEADROOM_WINS)
            else None
        )
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
            # Plan 011: what the high-price period is expected to draw, and how
            # much of that this window has to buy because the sun will not.
            "evening_reserve_kwh": round(plan.evening_reserve_kwh, 2),
            "evening_shortfall_kwh": round(plan.evening_shortfall_kwh, 2),
            "evening_reserve_dropped": plan.evening_reserve_dropped,
            "planned_charge_power_w": self.planned_charge_power_w,
        }

    async def _plan_charge_power(self, target_soc: float) -> None:
        """Prefer the measured optimum, then apply the connection budget.

        Without an optimum, spread the required energy across the window. A
        higher required power may override the optimum to meet the deadline,
        but never the configured maximum or the available house connection.
        Only an actively measuring finder owns the limit instead of this plan.
        """
        self.planned_charge_power_w = None
        self.planned_power_is_applied = False
        if self.target_reached:
            self.planned_charge_power_w = 0.0
            return
        current_soc = self._current_battery_soc()
        if current_soc is None:
            return
        now = dt_util.now()
        hours_remaining = max(0.0, elapsed_seconds(self._window_end_datetime(now), now) / 3600.0)
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
        target = self._ac_charge_limit_target()
        control_max_w = self._control_max_w(target[0]) if target is not None else None
        if control_max_w is not None:
            # Plan what the control can take, so the record, the sensor and the
            # "already written" comparison all hold the value it really took
            # (Codex on #37)
            max_w = min(max_w, float(int(control_max_w)))
        # Per band where one has been searched, battery-wide otherwise (plan 015)
        best = self.best_charge_power_w(current_soc)
        # Prefer the measured optimum, finishing early rather than stretching
        # an inefficient trickle across the whole window. Only a deadline that
        # needs more power overrides it; the connection budget always wins.
        preferred = max(required, float(best)) if best is not None else required
        setpoint = float(round(max(min_w, min(preferred, max_w))))
        # What the battery would draw without the house connection limit. Kept
        # so the sensor can say whether the limit is actually holding it back.
        self._grid_plan_request_w = setpoint
        # Plan, then limit, then write - never the other way round (plan 008).
        setpoint = self._grid_limited_setpoint(setpoint)
        self.planned_charge_power_w = setpoint
        if self._auto_test_active:
            return
        if best is None and not self._planner_mode_is_bridge and not self._grid_limit_configured():
            # The planner only owns the setpoint in bridge mode, but the house
            # connection limit is a protection and must not depend on the mode.
            return
        if self._ac_charge_limit_target() is None:
            # Bridge mode without a charge limit entity: the plan is computed and
            # shown, but there is nothing to write it to.
            return
        written = self._planned_setpoint_written_w
        if written is not None and abs(setpoint - written) <= PLANNED_POWER_WRITE_THRESHOLD_W:
            # Near enough to what already stands on the inverter: that value is
            # in force, so the plan is applied without another write.
            self.planned_power_is_applied = True
            return
        _LOGGER.info(
            "Planned charge power %.0f W (%.2f kWh missing in %.2f h, required %.0f W)",
            setpoint,
            max(0.0, (target_soc - current_soc) / 100.0 * float(self.config.get(CONF_BATTERY_CAPACITY, 10.0))),
            hours_remaining,
            required,
        )
        # Only a write that reached the inverter counts. _set_ac_charge_limit_w
        # returns False when the service call failed or when the current limit
        # could not be read - and then the number on the sensor is a plan, not
        # an order, which is the whole point of the attribute.
        if await self._set_ac_charge_limit_w(int(setpoint)):
            self._planned_setpoint_written_w = setpoint
            self.planned_power_is_applied = True
        elif not self._ac_limit_not_held:
            # Refused on purpose once the inverter was found not to hold the
            # limit this window: the floor charges as before, not never
            self._plan_limit_write_failed = True

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
                blocking=True,
            )
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.error("Error writing %s: %s", what, e, exc_info=True)
            return False
        _LOGGER.info("%s to %.3f", what, value)
        return True

    def _discharge_block_method(self) -> str | None:
        """Return the floor mechanism in use: the min SOC, or nothing.

        Every window holds the battery through the min SOC only. A scheduled
        night writes its planned target; an explicit reservation (the evening
        rescue, ``block_discharge``) may raise the floor to the level it holds.
        A discharge block switch or a zero discharge limit is never written any
        more: either stops the battery from serving the house at all, and on a
        Kostal each write is an external battery-control command. Their
        entities stay configurable only so that a value an older
        version captured is still given back. ``off`` turns the reservation's
        raised floor off. Morning discharge never applies a discharge block.
        """
        if self.is_discharge_mode:
            return None
        if self._adhoc_until is None:
            # A scheduled night only preserves the planned minimum. Existing
            # energy above that floor must remain available to the house.
            return DISCHARGE_BLOCK_VIA_MIN_SOC
        mode = str(self.config.get(CONF_DISCHARGE_BLOCK_MODE, DEFAULT_DISCHARGE_BLOCK_MODE))
        if mode == DISCHARGE_BLOCK_OFF:
            return None
        return DISCHARGE_BLOCK_VIA_MIN_SOC

    def discharge_block_state(self) -> str:
        """The way in use, for the ``discharge_block`` sensor attribute."""
        return self._discharge_block_method() or DISCHARGE_BLOCK_OFF

    def _update_window_floor(self) -> None:
        """Preserve initial/rising energy only for an explicit reservation.

        The reservation floor never follows our own grid charging, which could
        otherwise ratchet the battery beyond its charge target. Scheduled
        nights never raise their floor to the current battery SOC.
        """
        if not self.is_active or self._ending or self._adhoc_until is None:
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
        switch = self.config.get(CONF_GRID_CHARGE_SWITCH)
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

    async def _reset_discharge_block_switch(self) -> bool:
        """Put the discharge block switch back to the state captured at window start."""
        original = self._original_discharge_block
        if original is None:
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
        service = "turn_on" if original else "turn_off"
        if await self._already_confirmed(entity_id, state, "on" if original else "off"):
            if self._original_discharge_block != original:
                return False
            self._original_discharge_block = None
            return True
        before_value, before_reported = state.state, state.last_reported
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id}, blocking=True)
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.error("Error resetting discharge block switch: %s", e, exc_info=True)
            return False
        if not await self._confirm_discharge_restore(
            entity_id, before_value, before_reported,
            "on" if original else "off",
        ):
            _LOGGER.error("Discharge block restore has no fresh matching feedback: %s", entity_id)
            return False
        _LOGGER.info("Reset discharge block switch %s to %s", entity_id, service)
        if self._original_discharge_block != original:
            return False
        self._original_discharge_block = None
        return True

    async def _write_raised_min_soc_floor(self) -> None:
        """Apply the floor immediately when the charge target is already met.

        The normal update returns early in this case, so it cannot be left to
        _control_charge. A scheduled night writes its planned target, whereas
        an explicit reservation may hold a higher SOC. Below the charge target,
        _control_charge still owns the write and its charging safety checks.
        """
        target_soc = self.current_target_soc()
        floor_soc = self.inverter_floor_soc(target_soc)
        if target_soc is None or floor_soc is None:
            return
        if floor_soc <= target_soc:
            method = self._discharge_block_method()
            holds_by_min_soc = method == DISCHARGE_BLOCK_VIA_MIN_SOC or (
                method is None and self._adhoc_until is not None
            )
            if not holds_by_min_soc:
                return
            current_soc = self._current_battery_soc()
            if current_soc is None or not self._is_target_reached(current_soc, target_soc):
                return
        entity_id = self.config.get(CONF_MIN_SOC_ENTITY)
        if not entity_id:
            return
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            _LOGGER.debug("Cannot raise the min SOC floor yet - entity unavailable")
            return
        current = _as_float(state.state)
        floor_soc = self._charging_floor_soc(target_soc)
        if floor_soc is None:
            return
        self._capture_original_min_soc(current)
        if current is not None and abs(current - floor_soc) <= 0.5:
            self._last_soc_set = floor_soc
            return
        window_started_at = self._window_started_at
        try:
            async with self._min_soc_write_lock:
                if (not self.is_active or self._ending or self._unloading
                        or self._window_started_at != window_started_at):
                    return
                await self.hass.services.async_call(
                    "number", "set_value", {"entity_id": entity_id, "value": floor_soc},
                    blocking=True,
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
        """Hold the battery through the min SOC, releasing a legacy block once.

        No window enables the block switch or zeroes the discharge limit (see
        _discharge_block_method). Older versions did, and a value they captured
        is given back once, at the window start (``announce``) - not on every
        verification, where a device that ignores it would be written to every
        15 minutes. Whatever is still owed then goes back at the window end.
        Backup operation and morning discharge do not apply blocks.
        """
        if self.is_discharge_mode or self._is_backup_active():
            return
        if announce:
            # Restore only values that release an ICN-owned block; never turn a
            # pre-existing block on or write a zero limit here.
            before = (self._original_discharge_block, self._original_discharge_limit)
            if self._original_discharge_block is False:
                await self._reset_discharge_block_switch()
            if self._original_discharge_limit is not None and self._original_discharge_limit > 0:
                await self._reset_discharge_limit()
            if before != (self._original_discharge_block, self._original_discharge_limit):
                self._persist_state()
        if self._adhoc_until is None:
            if self._window_floor_soc is not None:
                self._window_floor_soc = None
                self._persist_state()
            if announce:
                _LOGGER.info("Night window uses only the target min SOC; stored energy above it may supply the house")
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
            self._discharge_block_logged = method
        # The floor is written by _control_charge and _verify_and_restore_min_soc,
        # which both read it back through inverter_floor_soc().
        self._update_window_floor()

    async def _reset_discharge_limit(self) -> bool:
        """Restore the discharge limit captured at the window start.

        Returns True when nothing is left to restore; on failure the original
        value is kept for a retry.
        """
        original = self._original_discharge_limit
        if original is None:
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
        value = _within_entity_range(state, original, entity_id, "Reset discharge limit")
        # Clamped down to the maximum, a reading above it is restored too (Codex on #36)
        clamped_down = value < original
        if await self._already_confirmed(entity_id, state, value, at_least=clamped_down):
            if self._original_discharge_limit != original:
                return False
            self._original_discharge_limit = None
            return True
        before_value, before_reported = state.state, state.last_reported
        if not await self._write_number(
            entity_id, domain, value, "Reset discharge limit"
        ):
            return False
        if not await self._confirm_discharge_restore(
            entity_id, before_value, before_reported, value, at_least=clamped_down,
        ):
            _LOGGER.error("Discharge limit restore has no fresh matching feedback: %s", entity_id)
            return False
        if self._original_discharge_limit != original:
            return False
        self._original_discharge_limit = None
        return True

    async def _already_confirmed(
        self, entity_id: str, state: Any, expected: str | float, tolerance: float = 1e-6,
        *, at_least: bool = False,
    ) -> bool:
        """True when the entity already shows ``expected`` on a fresh observation.

        A restore then needs no write. Every write is a command to the inverter,
        and a restore rewritten although the value already stood there is what
        the retry ladder did every 15 minutes. A cached value is not trusted on
        its own: the owning integration is asked for a new reading first, and
        only a fresh one that still matches counts.
        """
        def matches(current: Any) -> bool:
            if current is None:
                return False
            if isinstance(expected, str):
                return bool(current.state == expected)
            value = _as_float(current.state)
            if value is None:
                return False
            if at_least:
                # Clamped to the entity maximum: "no limit", whatever larger
                # native value the device goes on reporting.
                return value >= expected - tolerance
            return abs(value - expected) <= tolerance

        if not matches(state):
            return False
        before_value, before_reported = state.state, getattr(state, "last_reported", None)
        try:
            await self.hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True,
            )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Cannot refresh %s before its restore - writing it", entity_id)
            return False
        feedback = self.hass.states.get(entity_id)
        fresh = feedback is not None and (
            feedback is not state
            or feedback.state != before_value
            or getattr(feedback, "last_reported", None) != before_reported
        )
        if fresh and matches(feedback):
            _LOGGER.debug("%s already shows its original value - nothing to write", entity_id)
            return True
        return False

    async def _confirm_discharge_restore(
        self, entity_id: str, before_value: str, before_reported: datetime | None,
        expected: str | float, *, at_least: bool = False,
    ) -> bool:
        """Retain captures until a fresh observation confirms their restoration.

        ``at_least`` is for a restore clamped to the entity maximum, which means
        "no limit": a device may go on reporting its own larger maximum, and any
        fresh reading at or above the written value counts then.
        """
        def confirmed() -> bool:
            feedback = self.hass.states.get(entity_id)
            if feedback is None:
                return False
            if isinstance(expected, str):
                matches = feedback.state == expected
            else:
                value = _as_float(feedback.state)
                matches = value is not None and (
                    value >= expected - 1e-6 if at_least else math.isclose(
                        value, expected, rel_tol=1e-6, abs_tol=1e-6,
                    )
                )
            return matches and (
                feedback.state != before_value or feedback.last_reported != before_reported
            )

        if confirmed():
            return True
        try:
            await self.hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True,
            )
        except Exception:
            _LOGGER.exception("Cannot refresh discharge restore feedback: %s", entity_id)
            return False
        return confirmed()

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
        """Is today inside the optional active date range?

        The range means one of two things, and the difference is the
        "repeat every year" setting:

        * **Yearly** - only month and day count, so 11-01 to 03-31 is every
          winter. A range whose start falls after its end crosses the new year
          and is matched the same way a window crossing midnight is: inside
          when today is on or after the start *or* on or before the end.
        * **Absolute** - the years count too, and the range is over when its
          end has passed. That is a silent stop, so it raises a repair issue
          instead of leaving the user to wonder why nothing charges.

        A range that crosses the new year is always yearly, whatever the
        setting says: read absolutely it would be start > end, which cannot
        contain any day at all. It used to be logged as invalid and then
        dropped entirely, which turned "every winter" into "all year round".
        """
        start_date = self._parse_date_optional(
            self.config.get(CONF_ACTIVE_START_DATE, DEFAULT_ACTIVE_START_DATE)
        )
        end_date = self._parse_date_optional(
            self.config.get(CONF_ACTIVE_END_DATE, DEFAULT_ACTIVE_END_DATE)
        )

        if start_date is None and end_date is None:
            self._update_expired_range_issue(None)
            return True

        today = dt_util.now().date()
        wraps = (
            start_date is not None
            and end_date is not None
            and (start_date.month, start_date.day) > (end_date.month, end_date.day)
        )
        yearly = wraps or bool(
            self.config.get(CONF_ACTIVE_RANGE_YEARLY, DEFAULT_ACTIVE_RANGE_YEARLY)
        )

        if yearly:
            self._update_expired_range_issue(None)
            return self._is_within_yearly_range(today, start_date, end_date, wraps)

        if start_date is not None and end_date is not None and start_date > end_date:
            # Not a season crossing the new year (that is handled above) but the
            # same year the wrong way round, e.g. 2026-06-01 to 2026-05-31.
            _LOGGER.warning(
                "Active date range is invalid (%s > %s), ignoring date restriction",
                start_date,
                end_date,
            )
            self._update_expired_range_issue(None)
            return True

        self._update_expired_range_issue(end_date if end_date and today > end_date else None)
        if start_date and today < start_date:
            return False
        if end_date and today > end_date:
            return False
        return True

    @staticmethod
    def _is_within_yearly_range(
        today: date, start_date: date | None, end_date: date | None, wraps: bool
    ) -> bool:
        """Match month and day only, so the range comes back every year.

        Comparing (month, day) tuples rather than constructing dates keeps a
        29 February boundary usable in a year that does not have one.
        """
        now_md = (today.month, today.day)
        start_md = (start_date.month, start_date.day) if start_date else None
        end_md = (end_date.month, end_date.day) if end_date else None
        if wraps and start_md is not None and end_md is not None:
            return now_md >= start_md or now_md <= end_md
        if start_md is not None and now_md < start_md:
            return False
        if end_md is not None and now_md > end_md:
            return False
        return True

    def _update_expired_range_issue(self, ended_on: date | None) -> None:
        """Say out loud that an absolute date range has run out.

        Without this the integration simply stops working one morning and
        nothing anywhere says why - the log line it used to write is gone by
        the time anyone looks.

        The date range is checked on every poll and on every window trigger,
        so the registry is only touched when something actually changed.
        """
        if ended_on == self._expired_range_issue:
            return
        issue_id = f"active_range_expired_{self.entry.entry_id}"
        self._expired_range_issue = ended_on
        if ended_on is None:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="active_range_expired",
            translation_placeholders={
                "name": str(self.entry.title),
                "end_date": ended_on.isoformat(),
            },
        )

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

        A configured contact that is unreadable or unrecognised also inhibits
        control: uncertainty never grants permission to draw from the grid.
        Explicit manufacturer state mappings remain a trusted configuration.

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
            return True
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
        return True

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

    def _get_ac_charge_limit_w(self, entity_id: str | None) -> float | None:
        """Read the charge control with the same unit inference used for writes."""
        value = self._get_power_w(entity_id)
        if value is None or not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if not _unit_of(state):
            entity_max = _as_float(getattr(state, "attributes", {}).get("max"))
            if entity_max is not None and 0 < entity_max < 1000:
                return value * 1000.0
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

    async def _write_ac_charge_limit(
        self, entity_id: str, domain: str, power_w: float, what: str, *, restore: bool = False,
        keepalive: bool = False,
    ) -> bool:
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
        if unit and unit not in ("kw", "kilowatt", "kilowatts", "w", "watt", "watts"):
            _LOGGER.error("Refusing a charge-power command to %s with incompatible unit %s", entity_id, unit)
            return False
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
            elif entity_max is not None and value > entity_max and not restore:
                # A restore is exempt: it was read from this very entity in the
                # same scale, and it is clamped to the maximum below.
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
        if restore:
            value = _within_entity_range(state, value, entity_id, what)
        before_value = state.state if state is not None else None
        try:
            await self.hass.services.async_call(
                domain,
                "set_value",
                {"entity_id": entity_id, "value": value},
                blocking=True,
            )
        except Exception as e:
            if keepalive:
                # The same value once more: KORE's exact readback says nothing
                # new, and the verification judges what the inverter holds
                _LOGGER.debug("%s to %.3f: %s", what, value, e)
                return False
            if (not restore and not isinstance(e, ServiceValidationError)
                    and await self._limit_reads_back(entity_id, before_value, power_w)):
                # KORE compares its readback exactly, and the Kostal answers a
                # written 1000 W with 997.9 to 999.9 W. Treating that as a failed
                # write paused grid charging, and the next poll switched it on
                # again - every 15 minutes, all night (20 October 2026).
                _LOGGER.info(
                    "%s to %.3f (%s): %s, but a fresh reading is within %d W of it",
                    what, value, unit or "unitless", e, PLANNED_POWER_WRITE_THRESHOLD_W,
                )
                return True
            _LOGGER.error("Error writing AC charge limit (%s): %s", what, e, exc_info=True)
            return False
        if keepalive:
            _LOGGER.debug("%s to %.3f (%s)", what, value, unit or "unitless")
        else:
            _LOGGER.info("%s to %.3f (%s)", what, value, unit or "unitless")
        return True

    async def _keep_charge_limit_alive(self) -> None:
        """Write the charge limit in force again, so the Kostal does not drop it.

        Only while this window holds a limit it wrote and the battery could
        draw from the grid. Nothing is captured, recorded or paused here: the
        verification and its bounded rewrites judge what the inverter holds.
        """
        if (not self.is_active or self._ending or self._unloading or self.is_discharge_mode
                or self._ac_limit_not_held or self._original_ac_charge_power is None):
            return
        target = self._ac_charge_limit_target()
        written = (
            float(self._auto_test_power_w)
            if self._auto_test_active and self._auto_test_power_w is not None
            else self._planned_setpoint_written_w
        )
        if target is None or written is None or self._is_backup_active():
            return
        if self._battery_idle_on_grid():
            return
        actual = self._get_ac_charge_limit_w(target[0])
        if actual is not None and actual < written - PLANNED_POWER_WRITE_THRESHOLD_W:
            # Lowered by someone else - the user, evcc. The verification leaves
            # a lower limit alone, and so does the keepalive (CodeRabbit on #38)
            return
        lapsed_s: float | None = None
        if actual is not None and actual > written + PLANNED_POWER_WRITE_THRESHOLD_W:
            now = dt_util.now()
            if self._keepalive_lapse_since is None:
                self._keepalive_lapse_since = now
            lapsed_s = elapsed_seconds(now, self._keepalive_lapse_since)
        else:
            self._keepalive_lapse_since = None
        if self._ac_charge_limit_write_lock.locked():
            return  # a writer is at it right now
        async with self._ac_charge_limit_write_lock:
            if not self.is_active or self._ending or self._unloading:
                return
            delivered = await self._write_ac_charge_limit(
                target[0], target[1], written, "Keep AC charge limit", keepalive=True,
            )
        if delivered:
            if lapsed_s is not None and lapsed_s >= KEEPALIVE_LAPSE_GRACE_S:
                # Acknowledged, but the reading has not come back for minutes:
                # an adapter can accept a write the inverter drops (Codex on #38)
                _LOGGER.warning(
                    "The AC charge limit still reads %.0f W, %.0f s after its keepalive of "
                    "%.0f W began - verifying it now", actual, lapsed_s, written,
                )
                await self._verify_ac_charge_limit()
            return
        actual = self._get_ac_charge_limit_w(target[0])
        if actual is None or abs(actual - written) <= PLANNED_POWER_WRITE_THRESHOLD_W:
            # KORE's exact readback of the Kostal's float: the limit holds
            return
        # Not delivered, and the inverter no longer reads the limit: it drops it
        # within a minute, the verification would come up to 15 minutes later
        # (Codex on #39). It judges the reading and acts, with its bounded rewrites.
        _LOGGER.warning(
            "Keeping the AC charge limit at %.0f W failed and the inverter reads %.0f W - "
            "verifying it now", written, actual,
        )
        await self._verify_ac_charge_limit()

    def _charge_limit_expires(self) -> bool:
        """Whether the charge-limit control lapses unless written again."""
        target = self._ac_charge_limit_target()
        registry = self.hass.data.get(er.DATA_REGISTRY)
        if target is None or registry is None:
            return False
        entry = registry.async_get(target[0])
        return entry is not None and entry.platform in EXPIRING_LIMIT_PLATFORMS

    async def _limit_reads_back(self, entity_id: str, before_value: str | None, power_w: float) -> bool:
        """True when the reading moved to within 100 W of ``power_w`` after the write.

        KORE reads the charge limit with its slow registers; the write makes its
        next poll include it, so the new reading may take a few seconds. Every
        poll in between publishes the retained old value with a new timestamp,
        so only a changed value is evidence. A value written again over itself
        cannot be confirmed this way and counts as unconfirmed - the safe side
        for the check after switching grid charging on.
        """
        def fresh() -> bool:
            feedback = self.hass.states.get(entity_id)
            return feedback is not None and feedback.state != before_value

        if not fresh():
            try:
                await self.hass.services.async_call(
                    "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True,
                )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Cannot refresh the AC charge limit feedback: %s", entity_id)
                return False
            for _ in range(LIMIT_FEEDBACK_WAIT_S):
                if fresh() or self._unloading:
                    break
                await asyncio.sleep(1)
        reading = self._get_ac_charge_limit_w(entity_id)
        return (fresh() and reading is not None
                and abs(reading - float(power_w)) <= PLANNED_POWER_WRITE_THRESHOLD_W)

    async def _set_ac_charge_limit_w(self, power_w: int) -> bool:
        """Serialize actuator writes and keep verification out of an in-flight command."""
        was_active, window_started_at = self.is_active, self._window_started_at
        async with self._ac_charge_limit_write_lock:
            if self._unloading or (was_active and (
                not self.is_active or self._ending or self._window_started_at != window_started_at
            )):
                return False
            return await self._set_ac_charge_limit_w_locked(power_w)

    async def _set_ac_charge_limit_w_locked(self, power_w: int) -> bool:
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
        if self._ac_limit_not_held:
            # The repair issue says writing has stopped for this window, and
            # every writer has to honour that, not only the verification
            # (Codex on #36). Grid charging stays paused instead.
            _LOGGER.debug("Not writing %d W to %s: the inverter does not hold it", power_w, entity_id)
            if self.is_active and self._grid_limit_configured():
                await self._pause_grid_charge("the inverter does not hold the protective charge limit")
            return False
        limited_w = int(self._grid_limited_setpoint(float(power_w)))
        if limited_w < power_w:
            _LOGGER.info(
                "House connection limit: writing %d W instead of the requested %d W",
                limited_w,
                power_w,
            )
            power_w = limited_w
        control_max_w = self._control_max_w(entity_id)
        if control_max_w is not None and power_w > control_max_w:
            # A limit above what the control accepts is refused by Home
            # Assistant, and the refused write left grid charging paused. The
            # control's maximum is lower and protects at least as well; it is
            # what gets recorded as written (Codex on #37).
            _LOGGER.debug(
                "Writing %d W instead of %d W: %s accepts at most that",
                int(control_max_w), power_w, entity_id,
            )
            power_w = int(control_max_w)
        if self._original_ac_charge_power is None:
            current_w = self._get_ac_charge_limit_w(entity_id)
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
            # A forced charge reads above the limit; a Modbus timeout does not,
            # and must not cost an inverter that needs the switch its night
            # (CodeRabbit on #39)
            # Above anything written here, too: a failed lowering write leaves
            # the earlier, higher limit in the reading (Codex on #39)
            reading = self._get_ac_charge_limit_w(entity_id)
            reference = max(float(power_w), self._planned_setpoint_written_w or 0.0)
            if reading is not None and reading > reference + PLANNED_POWER_WRITE_THRESHOLD_W:
                self._suspend_grid_switch_if_on()
            if self.is_active and (self._grid_switch_suspended or self._grid_limit_configured()):
                await self._pause_grid_charge("protective charge-limit write failed")
            return False
        self._grid_limit_last_write = dt_util.now()
        self._planned_setpoint_written_w = float(power_w)
        self._plan_limit_write_failed = False
        if power_w == 0 and self.is_active and self._grid_limit_configured():
            await self._pause_grid_charge("no confirmed connection headroom")
        return True

    def _control_max_w(self, entity_id: str) -> float | None:
        """The charge control's maximum in W, when its scale is known.

        A unit, or a unitless maximum below 1000 (kilowatts, as the write path
        infers). A unitless control with a larger maximum has no known scale,
        and the write path refuses a value above it rather than guess.
        """
        state = self.hass.states.get(entity_id)
        maximum = _as_float((getattr(state, "attributes", None) or {}).get("max")) if state else None
        if maximum is None or maximum <= 0:
            return None
        if not _unit_of(state) and maximum >= 1000:
            return None
        return self._entity_value_as_w(state, maximum)

    async def _reset_ac_charge_limit(self) -> bool:
        """Restore only after an already-started limit command has finished."""
        async with self._ac_charge_limit_write_lock:
            return await self._reset_ac_charge_limit_locked()

    async def _reset_ac_charge_limit_locked(self) -> bool:
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
        original = self._original_ac_charge_power
        # Already back (the Kostal drops an external 1038 limit by itself, or an
        # earlier restore's feedback came late): confirm, do not write again.
        if not await self._ac_limit_already_restored(entity_id, state, original):
            state = self.hass.states.get(entity_id) or state
            before_value, before_reported = state.state, getattr(state, "last_reported", None)
            if not await self._write_ac_charge_limit(
                entity_id, domain, original, "Reset AC charge limit", restore=True,
            ):
                return False
            if not await self._confirm_ac_limit_restore(
                entity_id, state, before_value, before_reported, original,
            ):
                _LOGGER.error("AC charge limit restore has no fresh matching feedback: %s", entity_id)
                return False
        if self._original_ac_charge_power != original:
            return False
        self._original_ac_charge_power = None
        self._planned_setpoint_written_w = None
        self._grid_plan_request_w = None
        return True

    async def _confirm_ac_limit_restore(
        self, entity_id: str, state: Any, before_value: str, before_reported: Any, original_w: float,
    ) -> bool:
        """Keep the capture until a fresh reading shows the limit is back (Codex on #36).

        An accepted service call is not a restored limit: a device that ignores
        it keeps the window's lower limit in force. A restore clamped to the
        entity maximum means "no limit", and a device may go on reporting its
        own larger maximum - the Kostal read 22063 W through a 12540 W control -
        so any reading at or above the target counts there.
        """
        def confirmed() -> bool:
            feedback = self.hass.states.get(entity_id)
            if feedback is None:
                return False
            fresh = (
                feedback is not state
                or feedback.state != before_value
                or getattr(feedback, "last_reported", None) != before_reported
            )
            return fresh and self._ac_limit_reads_restored(entity_id, state, original_w)

        if confirmed():
            return True
        try:
            await self.hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True,
            )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Cannot refresh AC charge limit restore feedback: %s", entity_id)
            return False
        return confirmed()

    def _ac_limit_reads_restored(self, entity_id: str, state: Any, original_w: float) -> bool:
        """True when the charge limit reads as the restored original, within 100 W.

        The target is the original as ``_within_entity_range`` clamps it, with
        both entity bounds (Codex on #36). Clamped down to the maximum it means
        "no limit", and a device may go on reporting its own larger maximum -
        the Kostal read 22063 W through a 12540 W control - so any reading at
        or above the target counts there.
        """
        attributes = getattr(state, "attributes", None) or {}
        low = _as_float(attributes.get("min"))
        high = _as_float(attributes.get("max"))
        high_w = self._entity_value_as_w(state, high) if high is not None else None
        low_w = self._entity_value_as_w(state, low) if low is not None else None
        target_w = original_w
        if high_w is not None and target_w > high_w:
            target_w = high_w
        if low_w is not None and target_w < low_w:
            target_w = low_w
        open_ended = high_w is not None and original_w > high_w and target_w == high_w
        value_w = self._get_ac_charge_limit_w(entity_id)
        if value_w is None or value_w < target_w - PLANNED_POWER_WRITE_THRESHOLD_W:
            return False
        return open_ended or value_w <= target_w + PLANNED_POWER_WRITE_THRESHOLD_W

    async def _ac_limit_already_restored(self, entity_id: str, state: Any, original_w: float) -> bool:
        """True when a fresh reading already shows the original - then nothing is written.

        An earlier restore may have landed with its feedback arriving only
        after that attempt gave up, or the inverter may have dropped the
        window's limit by itself. Rewriting it on every retry is what this
        release removes (Codex on #36). A cached value alone is not trusted.
        """
        if not self._ac_limit_reads_restored(entity_id, state, original_w):
            return False
        before_value, before_reported = state.state, getattr(state, "last_reported", None)
        try:
            await self.hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True,
            )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Cannot refresh %s before its restore - writing it", entity_id)
            return False
        feedback = self.hass.states.get(entity_id)
        fresh = feedback is not None and (
            feedback is not state
            or feedback.state != before_value
            or getattr(feedback, "last_reported", None) != before_reported
        )
        if fresh and self._ac_limit_reads_restored(entity_id, feedback, original_w):
            _LOGGER.info("AC charge limit %s already reads its original value - nothing to write", entity_id)
            return True
        return False

    def _entity_value_as_w(self, state: Any, value: float) -> float:
        """An entity-unit value of the charge limit in W, with the write path's scale."""
        unit = _unit_of(state)
        if unit in ("kw", "kilowatt", "kilowatts"):
            return value * 1000.0
        if not unit:
            entity_max = _as_float((getattr(state, "attributes", None) or {}).get("max"))
            if entity_max is not None and 0 < entity_max < 1000:
                return value * 1000.0
        return value

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
        state = self.hass.states.get(entity_id)
        unit = _unit_of(state)
        if unit and unit not in ("kw", "kilowatt", "kilowatts", "w", "watt", "watts"):
            _LOGGER.error("Refusing an absolute charge limit with incompatible unit %s", unit)
            return
        scale = 1000.0 if unit in ("kw", "kilowatt", "kilowatts") else 1.0

        if self._original_absolute_charge_power is None:
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
            written = self._absolute_charge_power_written_w
            if (
                written is not None
                and abs(current * scale - written) <= PLANNED_POWER_WRITE_THRESHOLD_W
            ):
                # This reading is our own limit, not the user's setting. Adopting
                # it would make the window-end restore put our value back as if
                # it were theirs, and the real one would be gone for good. Better
                # to leave the limit off than to lose what it was.
                _LOGGER.warning(
                    "%s still reads back our own limit (%.3f) - not capturing it as the "
                    "original value",
                    entity_id,
                    current,
                )
                return
            self._original_absolute_charge_power = current
            _LOGGER.info("Stored original absolute charge power: %.3f", current)
            self._persist_state()
        value = max_power / scale
        try:
            await self.hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id, "value": value},
                blocking=True,
            )
            self._absolute_charge_power_written_w = max_power
            _LOGGER.info("Set absolute charge power to %.3f (%s)", value, unit or "unitless")
        except Exception as e:
            _LOGGER.error("Error setting absolute charge power: %s", e, exc_info=True)

    async def _reset_absolute_charge_power(self, *, forget: bool = True) -> bool:
        """Restore the absolute charge power captured before the first write.

        Returns True when nothing is left to restore; on failure the original
        value is kept so a retry can still put it back.

        ``forget=False`` restores the value but keeps remembering it. That is
        for the restore that happens *while the window is still open* - the one
        on reaching the charge target. The service call is not blocking, and on
        an inverter whose limit register falls back to factory on its own the
        entity can still be reading our value moments later. A later charge pass
        would then capture that as "the user's setting" and the real one would be
        lost at the window end. Keeping it costs one redundant write; dropping it
        costs the setting.
        """
        if self._original_absolute_charge_power is None:
            return True
        entity_id = self.config.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY)
        domain = str(entity_id).split(".")[0] if entity_id else ""
        if not entity_id or domain not in ("number", "input_number"):
            # Entity no longer configured or unusable: nothing we can restore
            self._original_absolute_charge_power = None
            self._absolute_charge_power_written_w = None
            return True
        state = self.hass.states.get(entity_id)
        value = _within_entity_range(
            state, self._original_absolute_charge_power, str(entity_id), "Reset absolute charge power"
        )
        unit = _unit_of(state)
        # Clamped down to the maximum: a device reading above it is restored
        clamped_down = value < self._original_absolute_charge_power
        if state is not None and await self._already_confirmed(
            str(entity_id), state, value, at_least=clamped_down,
        ):
            # Already back - an earlier write landed late (Codex on #36)
            pass
        elif not await self._write_absolute_restore(str(entity_id), domain, state, value, forget, clamped_down):
            return False
        _LOGGER.info("Reset absolute charge power to original value: %.3f (%s)", value, unit or "unitless")
        self._absolute_charge_power_written_w = None
        if forget:
            self._original_absolute_charge_power = None
            self._persist_state()
        return True

    async def _write_absolute_restore(
        self, entity_id: str, domain: str, state: Any, value: float, forget: bool, clamped_down: bool,
    ) -> bool:
        """Write the absolute charge power back; with ``forget``, also confirm it."""
        # Read before the write: the entity may update this very object in place
        before_value = state.state if state else ""
        before_reported = getattr(state, "last_reported", None)
        try:
            await self.hass.services.async_call(
                domain,
                "set_value",
                {"entity_id": entity_id, "value": value},
                blocking=True,
            )
        except Exception as e:
            _LOGGER.error("Error resetting absolute charge power: %s", e, exc_info=True)
            return False
        if forget and not await self._confirm_discharge_restore(
            entity_id, before_value, before_reported, value, at_least=clamped_down,
        ):
            # Same rule as the other restores (Codex on #36): an accepted call is
            # not a restored limit, and the capture is what a retry needs.
            _LOGGER.error("Absolute charge power restore has no fresh matching feedback: %s", entity_id)
            return False
        return True

    def _efficiency_signature(self) -> str:
        """Bind measured losses to this measurement path without publishing IDs."""
        keys = (
            CONF_CHARGE_POWER_ENTITY, CONF_CHARGE_POWER_SENT_ENTITY,
            CONF_CHARGE_POWER_RECEIVED_ENTITY, CONF_CHARGE_ENERGY_SENT_ENTITY,
            CONF_CHARGE_ENERGY_RECEIVED_ENTITY, CONF_BATTERY_SOC_ENTITY,
            CONF_BATTERY_CAPACITY,
        )
        payload = [2, *(self.config.get(key) for key in keys)]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _efficiency_window_key(self) -> str:
        """Failures apply to this charging opportunity, never to the loss curve."""
        return self._window_end_datetime(dt_util.now()).isoformat()

    def get_auto_efficiency_data(self) -> dict[str, Any]:
        """Load persisted auto efficiency data from entry options.

        Samples are dropped when the configured charge power range has changed
        since they were taken: the search narrows a range, and a range that
        moved makes the stored bounds - and with them the conclusions drawn
        from the old samples - meaningless.
        """
        data: dict[str, Any] = dict(self.entry.options.get(CONF_AUTO_EFFICIENCY_DATA, {}))
        data.setdefault("history", {})
        bounds = data.get("bounds_w")
        current = [
            int(self.config.get(CONF_MIN_CHARGE_POWER_W, 1000)),
            int(self.config.get(CONF_MAX_CHARGE_POWER_W, 10000)),
        ]
        signature = data.get("measurement_signature")
        changed = signature is not None and signature != self._efficiency_signature()
        if changed or (isinstance(bounds, list) and [int(b) for b in bounds] != current):
            _LOGGER.info(
                "Charge power range changed from %s to %s - starting the efficiency search over",
                bounds,
                current,
            )
            data = dict[str, Any](history={}, bounds_w=current)
        data["measurement_signature"] = self._efficiency_signature()
        if data.get("failed_window") != self._efficiency_window_key():
            data.pop("failed", None)
        return data

    def _save_auto_efficiency_data(self, data: dict[str, Any]) -> None:
        """Persist auto efficiency data to entry options."""
        options = dict(self.entry.options)
        data["measurement_signature"] = self._efficiency_signature()
        options[CONF_AUTO_EFFICIENCY_DATA] = data
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    def _round_power_step(self, value_w: float, step_w: int = 100) -> int:
        """Round power to nearest step."""
        return int(round(value_w / step_w) * step_w)

    def _select_next_auto_test_power_w(self) -> int | None:
        """Golden-section search using valid measurements only.

        Transient failures defer the search until another charging window.
        They never prove that neighbouring, unmeasured powers are inefficient.
        """
        self._auto_search_deferred = False
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
        # Retries are bounded per window; failure never counts as a loss sample.
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

            if c in unmeasurable or d in unmeasurable:
                # A full/cold battery, BMS taper or a busy connection says
                # nothing about losses at neighbouring powers. Never narrow
                # the search using a failed measurement. Retry next window.
                for point in (c, d):
                    if point not in history and point not in unmeasurable:
                        return point
                self._auto_search_deferred = True
                return None
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
        self._auto_test_limit_confirmed = False
        self._auto_measure_start = None
        self._auto_last_sample_time = None
        self._auto_last_sent_w = None
        self._auto_last_received_w = None
        self._auto_meter_start = None
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0
        self._auto_start_soc = None

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
        if allowed > 0 and self._grid_write_is_debounced():
            return
        if self._auto_test_active:
            _LOGGER.info(
                "House connection limit: abandoning the efficiency test at %s W, the "
                "connection carries only %d W",
                self._auto_test_power_w,
                int(allowed),
            )
            self._reset_auto_test_state()
        # The setter records what the control took, clamped to its maximum
        await self._set_ac_charge_limit_w(int(allowed))

    def _efficiency_now(self) -> datetime:
        """Measurement wall clock; durations are converted to UTC elapsed time."""
        return dt_util.now()

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
                self._set_auto_wait_reason("connection_headroom")
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
                await self._set_ac_charge_limit_w(int(allowed))  # records what it wrote
                return
        target = self._ac_charge_limit_target()
        control_max_w = self._control_max_w(target[0]) if target is not None else None
        if control_max_w is not None and power_w > control_max_w + PLANNED_POWER_WRITE_THRESHOLD_W:
            # The control would write its maximum while the sample was filed under
            # this power - the same poisoning as a connection-limited test.
            self._set_auto_wait_reason("charge_limit_above_control_maximum")
            return
        if not await self._set_ac_charge_limit_w(power_w):
            # The battery would charge at whatever stands on the inverter -
            # usually the previous test's higher value, which the follow check
            # does not catch - and the sample would be filed under this power.
            # The next poll offers the candidate again.
            _LOGGER.debug("Efficiency test at %d W not started: the setpoint did not arrive", power_w)
            self._set_auto_wait_reason("charge_limit_write_failed")
            return
        self._auto_wait_reason = None
        self._auto_test_active = True
        self._auto_test_power_w = power_w
        self._auto_test_start = self._efficiency_now()
        self._auto_test_limit_confirmed = False
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
            "waiting_reason": self._auto_wait_reason if self.auto_test_state == AUTO_TEST_STATE_WAITING else None,
            "test_power_w": self._auto_test_power_w,
            "best_power_w": data.get("best_power_w"),
            "best_loss_pct": (
                round(float(data["best_loss"]) * 100.0, 2) if data.get("best_loss") is not None else None
            ),
            "loss_by_power_pct": history,
            "search_range_w": [data.get("range_min_w"), data.get("range_max_w")],
            "measured_energy_kwh": round(self._auto_energy_sent_wh / 1000.0, 3),
            "last_result": self._auto_last_result,
            "deferred_until_next_window": self._auto_search_deferred,
            "measurement_source": "energy meters" if self._energy_meter_entities() else "power sensors",
            # Plan 015: what has been measured where on the battery, and which
            # of those bands is far enough along to overrule the overall best.
            "loss_by_band_pct": {
                key: {
                    "best_power_w": band.get("best_power_w"),
                    "best_loss_pct": (
                        round(float(band["best_loss"]) * 100.0, 2)
                        if band.get("best_loss") is not None
                        else None
                    ),
                    "measured_powers": len(band.get("history") or {}),
                    "in_use": len(band.get("history") or {}) >= EFFICIENCY_BAND_MIN_SAMPLES,
                }
                for key, band in sorted(
                    (dict(data.get("bands") or {})).items(), key=lambda item: int(item[0])
                )
                if isinstance(band, dict)
            },
            "band_width_pct": EFFICIENCY_BAND_WIDTH_PCT,
        }
        if self._auto_measure_start is not None:
            attributes["measuring_for_min"] = round(
                elapsed_seconds(self._efficiency_now(), self._auto_measure_start) / 60.0, 1
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

    def _auto_limit_matches_feedback(self) -> bool:
        """A measurement belongs only to its currently observed setpoint."""
        actual_w = self._get_ac_charge_limit_w(self.config.get(CONF_CHARGE_POWER_ENTITY))
        requested_w = self._auto_test_power_w
        return (actual_w is not None and requested_w is not None
                and abs(actual_w - requested_w) <= PLANNED_POWER_WRITE_THRESHOLD_W)

    def _begin_auto_measurement(self, now: datetime) -> None:
        """Start counting: the inverter has had its settling time."""
        if not self._auto_limit_matches_feedback():
            self._discard_auto_test("charge limit has no matching feedback after settling")
            return
        self._auto_test_limit_confirmed = True
        self._auto_measure_start = now
        # Where on the battery this measurement is taken. A loss measured from
        # a third full is not the loss measured near the top, and until now
        # every sample was filed as if it were (plan 015).
        self._auto_start_soc = self._current_battery_soc()
        self._auto_last_sample_time = now
        self._auto_energy_sent_wh = 0.0
        self._auto_energy_received_wh = 0.0
        self._auto_last_sent_w = self._get_power_w(self.config.get(CONF_CHARGE_POWER_SENT_ENTITY))
        self._auto_last_received_w = self._get_power_w(self.config.get(CONF_CHARGE_POWER_RECEIVED_ENTITY))
        if self._auto_last_sent_w is None or self._auto_last_received_w is None:
            self._discard_auto_test("power telemetry is unavailable at measurement start")
            return
        meters = self._energy_meter_entities()
        if meters is not None:
            start_sent = self._read_energy_wh(meters[0])
            start_received = self._read_energy_wh(meters[1])
            if start_sent is None or start_received is None:
                self._discard_auto_test("configured energy counters are unavailable")
                return
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
        sun = self.hass.states.get(SUN_ENTITY_ID)
        if sun is None or sun.state != "below_horizon":
            self._discard_auto_test("daylight or unknown sun state can contaminate the AC/DC comparison")
            return
        now = self._efficiency_now()
        if self._auto_measure_start is None:
            if elapsed_seconds(now, self._auto_test_start) < AUTO_TEST_SETTLE_S:
                return
            self._begin_auto_measurement(now)
            return
        if not self._auto_test_limit_confirmed or not self._auto_limit_matches_feedback():
            self._discard_auto_test("charge limit changed or became unavailable during measurement")
            return
        sent_entity = self.config.get(CONF_CHARGE_POWER_SENT_ENTITY)
        received_entity = self.config.get(CONF_CHARGE_POWER_RECEIVED_ENTITY)
        sent_w = self._get_power_w(sent_entity)
        received_w = self._get_power_w(received_entity)
        for entity in (sent_entity, received_entity):
            state = self.hass.states.get(entity) if entity else None
            age = self._state_age_s(state, dt_util.now())
            if age is not None and (age < -5 or age > 60):
                self._discard_auto_test("power telemetry is stale; retrying after recovery")
                return
        if sent_w is None or received_w is None:
            self._discard_auto_test("power telemetry is unavailable; retrying after recovery")
            return
        if self._auto_meter_start is not None and self._measured_energy_wh() is None:
            self._discard_auto_test("energy counters were reset or became unavailable")
            return
        last_time = self._auto_last_sample_time
        if last_time is None:
            self._auto_last_sample_time = now
            self._auto_last_sent_w = sent_w
            self._auto_last_received_w = received_w
            return
        delta_h = elapsed_seconds(now, last_time) / 3600.0
        if delta_h < 0 or (delta_h > 60 / 3600 and self._auto_meter_start is None):
            self._discard_auto_test("measurement clock or sample continuity was interrupted")
            return
        if delta_h == 0:
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
        duration = elapsed_seconds(self._efficiency_now(), self._auto_measure_start)
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
        """Bound retries within this window without modifying the loss curve."""
        data = self.get_auto_efficiency_data()
        failed = {int(k): int(v) for k, v in dict(data.get("failed", {})).items()}
        attempts = failed.get(power_w, 0) + 1
        failed[power_w] = attempts
        data["failed"] = {str(k): v for k, v in failed.items()}
        data["failed_window"] = self._efficiency_window_key()
        self._save_auto_efficiency_data(data)

    @staticmethod
    def _efficiency_band(soc: float) -> str:
        """The band key a state of charge falls into, e.g. 60.0 -> "60".

        100 % belongs to the top band rather than one of its own: a battery is
        only briefly full, and a band that can only ever hold the last moments
        of a charge would never gather enough samples to be used.
        """
        clamped = max(0.0, min(100.0, soc))
        index = min(
            int(clamped // EFFICIENCY_BAND_WIDTH_PCT),
            100 // EFFICIENCY_BAND_WIDTH_PCT - 1,
        )
        return str(index * EFFICIENCY_BAND_WIDTH_PCT)

    def _band_for_measurement(self, start_soc: float | None, end_soc: float | None) -> str | None:
        """The band a finished measurement belongs to, or None.

        None when either end is unknown, or when the charge ran across more
        bands than EFFICIENCY_BAND_MAX_SPAN: such a sample is a blend of
        several bands and says nothing reliable about any one of them. It is
        still kept battery-wide, which is exactly what it is evidence for.
        """
        if start_soc is None or end_soc is None:
            return None
        span_pct = abs(end_soc - start_soc)
        if span_pct > EFFICIENCY_BAND_MAX_SPAN * EFFICIENCY_BAND_WIDTH_PCT:
            _LOGGER.debug(
                "Measurement ran from %.0f%% to %.0f%% - too wide for a band, keeping it "
                "battery-wide only",
                start_soc,
                end_soc,
            )
            return None
        return self._efficiency_band((start_soc + end_soc) / 2.0)

    def best_charge_power_w(self, soc: float | None = None) -> int | None:
        """The most efficient charge power, for this part of the battery if known.

        A band only overrules the battery-wide optimum once it has been
        searched rather than merely sampled - EFFICIENCY_BAND_MIN_SAMPLES
        distinct powers. Everything else falls back to the battery-wide value,
        so an installation that measured before this existed keeps its result.
        """
        data = self.get_auto_efficiency_data()
        overall = data.get("best_power_w")
        overall_w = overall if isinstance(overall, int) else None
        if soc is None:
            return overall_w
        bands = data.get("bands")
        if not isinstance(bands, dict):
            return overall_w
        band = bands.get(self._efficiency_band(soc))
        if not isinstance(band, dict):
            return overall_w
        history = band.get("history")
        best = band.get("best_power_w")
        if not isinstance(history, dict) or len(history) < EFFICIENCY_BAND_MIN_SAMPLES:
            return overall_w
        return best if isinstance(best, int) else overall_w

    def _record_band_sample(self, data: dict[str, Any], power_w: int, loss: float) -> None:
        """File a finished measurement under the band it was taken in."""
        band_key = self._band_for_measurement(self._auto_start_soc, self._current_battery_soc())
        if band_key is None:
            return
        bands = dict(data.get("bands") or {})
        band = dict(bands.get(band_key) or {})
        history = dict(band.get("history") or {})
        history[str(power_w)] = loss
        band["history"] = history
        best_loss = band.get("best_loss")
        if not isinstance(best_loss, (int, float)) or loss < best_loss:
            band["best_loss"] = loss
            band["best_power_w"] = power_w
            _LOGGER.info(
                "New best charge power for the %s-%d %% band: %.2f %% loss at %d W",
                band_key,
                int(band_key) + EFFICIENCY_BAND_WIDTH_PCT,
                loss * 100.0,
                power_w,
            )
        bands[band_key] = band
        data["bands"] = bands

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
        sun = self.hass.states.get(SUN_ENTITY_ID)
        if sun is None or sun.state != "below_horizon":
            self._discard_auto_test("daylight or unknown sun state can contaminate the AC/DC comparison")
            return
        power_w = self._auto_test_power_w
        if not self._auto_test_limit_confirmed or not self._auto_limit_matches_feedback():
            self._discard_auto_test("charge limit was never confirmed for this measurement")
            return
        if self._auto_measure_start is None:
            self._discard_auto_test("it never got past the %d s settling time", AUTO_TEST_SETTLE_S)
            return

        duration = elapsed_seconds(self._efficiency_now(), self._auto_measure_start)
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
        if not AUTO_TEST_MIN_FOLLOW_RATIO * power_w <= average_w <= power_w / AUTO_TEST_MIN_FOLLOW_RATIO:
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
        self._record_band_sample(data, power_w, loss)
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

    def _set_auto_wait_reason(self, reason: str) -> None:
        """Retain why a trial did not start and log changes once."""
        if self._auto_wait_reason != reason:
            _LOGGER.info("Efficiency search waiting: %s", reason)
            self._auto_wait_reason = reason

    async def _handle_auto_charge(self) -> None:
        """Handle auto efficient charging logic."""
        if not self.auto_efficient_charge:
            self._auto_wait_reason = None
            if self._auto_test_active:
                # Finder switched off during a test: record it and take the
                # test value off the inverter
                self._finalize_auto_test()
                await self._reset_ac_charge_limit()
            return
        sun = self.hass.states.get("sun.sun")
        if sun is None or sun.state != "below_horizon":
            self._set_auto_wait_reason("night_not_confirmed")
            if self._auto_test_active:
                self._discard_auto_test("daylight or unknown sun state can contaminate the AC/DC comparison")
            return
        if self._is_backup_active():
            # No inverter writes in backup mode; the window end restores the limit
            self._set_auto_wait_reason("backup_active_or_unknown")
            if self._auto_test_active:
                self._reset_auto_test_state()
            return

        charge_entity = self.config.get(CONF_CHARGE_POWER_ENTITY)
        sent_entity = self.config.get(CONF_CHARGE_POWER_SENT_ENTITY)
        received_entity = self.config.get(CONF_CHARGE_POWER_RECEIVED_ENTITY)
        if not charge_entity or not sent_entity or not received_entity:
            self._set_auto_wait_reason("missing_configuration")
            if not self._auto_missing_entities_logged:
                _LOGGER.warning(
                    "Auto efficient charge enabled but required entities are missing "
                    "(setpoint, sent, received)."
                )
                self._auto_missing_entities_logged = True
            return

        grid_charge_switch = self.config.get(CONF_GRID_CHARGE_SWITCH)
        grid_on = False
        if grid_charge_switch:
            state = self.hass.states.get(grid_charge_switch)
            grid_on = state is not None and state.state == "on"

        if not grid_on or self.target_reached:
            self._set_auto_wait_reason("target_reached" if self.target_reached else "grid_charge_not_confirmed")
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
        if self._auto_search_deferred:
            self._set_auto_wait_reason("deferred_after_failed_trials")
            return
        if candidate is None:
            self._set_auto_wait_reason("no_test_candidate")
            data = self.get_auto_efficiency_data()
            best_power = data.get("best_power_w")
            if isinstance(best_power, int):
                # Written like every other setpoint, which means the house
                # connection limit may cap it. That cap belongs to this moment,
                # not to the result, so the user's own value stays on record and
                # the window end restores it. What carries the result forward is
                # best_power_w: the planner prefers it in subsequent windows.
                await self._set_ac_charge_limit_w(best_power)
                self._auto_wait_reason = None
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
                    (self._on_scheduled_window_start, start),
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
            if self._reconfiguring:
                # The options are replacing entities and the old configuration is
                # still in place: a window started now would write to entities
                # nobody restores. The check after the swap starts it again.
                _LOGGER.debug("Options update in progress - skipping the window check")
                return

            if self.skip_next:
                if self.is_active:
                    _LOGGER.info("Skip next active - stopping window")
                    await self._on_window_end(dt_util.now())
                return

            if self._hands_off_until is not None:
                if dt_util.now() < self._hands_off_until:
                    # The reset_inverter action gave the inverter back for the
                    # rest of this window. Starting it again here would undo
                    # exactly what the caller asked for.
                    _LOGGER.debug(
                        "Inverter handed back until %s - leaving it alone",
                        self._hands_off_until.isoformat(),
                    )
                    if self.is_active:
                        await self._on_window_end(dt_util.now())
                    return
                _LOGGER.info("The window may be driven again")
                self._hands_off_until = None
                self._persist_state()

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
            in_window = self._scheduled_window_is_open(now_dt)

            if self._adhoc_until is not None and (in_window or elapsed_seconds(now_dt, self._adhoc_until) >= 0):
                # The configured window wins over an ad-hoc one: it has the
                # tariff contract behind it. Otherwise the ad-hoc window has
                # simply reached the end it was given.
                _LOGGER.info(
                    "Ending the ad-hoc window (%s): %s",
                    self._adhoc_reason,
                    "the configured window is starting" if in_window else "it is over",
                )
                await self._on_window_end(now_dt)

            # A restart during an ad-hoc window restores its deadline but not
            # is_active, which is not persisted. Without picking the window up
            # again the hold it was keeping is undone one poll later by the
            # "settings still on the inverter" branch below, and the deadline
            # is never cleared - it then answers every later planner question
            # and ends the next configured window one poll after it starts.
            if (
                not in_window
                and not self.is_active
                and self._adhoc_until is not None
                and elapsed_seconds(self._adhoc_until, now_dt) > 0
            ):
                _LOGGER.info(
                    "Resuming the ad-hoc window (%s) that a restart interrupted, until %s",
                    self._adhoc_reason,
                    self._adhoc_until.isoformat(timespec="minutes"),
                )
                await self._on_window_start(now_dt)

            # Handle state transitions
            if in_window and not self.is_active:
                # We're in the window but not active - start it
                _LOGGER.info("Currently in active window but not active - starting")
                await self._on_window_start(now_dt)
            elif not in_window and self.is_active and self._adhoc_until is None:
                # We're active but no longer in the window - reset
                _LOGGER.info("No longer in active window but still active - resetting")
                await self._on_window_end(now_dt)
            elif (
                not in_window
                and not self.is_active
                and not self._reset_abandoned
                # A pending reset belongs to its bounded ladder. This branch is
                # for a window end Home Assistant was not running for; run on
                # every backup-listener change it would reset outside the ladder.
                and not self._pending_reset
                and self._inverter_still_holds_our_settings()
            ):
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
            elif self.is_active:
                # In the configured window, or in an ad-hoc one that is still
                # running - ensure listeners are set up (e.g., after restart)
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

    async def _on_scheduled_window_start(self, now: datetime) -> None:
        """Time-trigger entry point: the configured window reached its start time.

        The configured window wins over an ad-hoc one, as in
        _check_current_window: it has the tariff behind it. Started directly on
        top of a running ad-hoc window it would inherit that window's target,
        deadline and grid-charge setting, and be ended at the ad-hoc deadline
        with nothing to start it again that night.
        """
        if not self._scheduled_window_is_open(dt_util.now()):
            _LOGGER.info("Ignoring a scheduled start outside its actual calendar window")
            return
        if self.is_active and self._adhoc_until is None:
            return  # A repeated fall-back trigger must not reinitialise the window.
        if self._adhoc_until is not None:
            _LOGGER.info(
                "Ending the ad-hoc window (%s): the configured window is starting",
                self._adhoc_reason,
            )
            await self._on_window_end(now)
        await self._on_window_start(now)

    async def _on_window_start(self, now: datetime) -> None:
        """Handle window start."""
        if not self.is_enabled:
            _LOGGER.debug("Window started but integration is disabled")
            return
        if self.skip_next:
            _LOGGER.info("Window start ignored - skip next is active")
            return
        if self._update_communication_health():
            _LOGGER.warning("Window start deferred: required communication is unavailable")
            return
        if self._is_backup_active():
            _LOGGER.info("Window start ignored - backup mode active")
            return
        if not self._is_within_date_range():
            _LOGGER.info("Window start ignored - outside active date range")
            return
        mode_label = "Morning discharge" if self.is_discharge_mode else "Night charge"
        _LOGGER.info("%s window started", mode_label)
        # A restart inside the window resumes it: what this window already
        # spent of its bounded charge-limit rewrites stays spent. The window is
        # named by its own deadline, not guessed from its length (Codex on #36).
        identity = self._window_identity(now)
        same_window = identity is not None and identity == self._ac_limit_window
        self._ac_limit_window = identity
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
        self._clear_reset_abandoned()  # this window's end starts a fresh backoff
        if not same_window:
            self._clear_ac_limit_not_held()
        self._grid_stop_pending = False
        self._grid_stop_retries = 0
        self._pending_reset = False

        # Arm the listeners and the verification first. The target calculation
        # no longer waits for the inverter; if the min SOC entity is not
        # available yet, _control_charge skips the write and the verification
        # applies the target as soon as the entity reports a value.
        self._setup_battery_soc_listener()
        self._setup_inverter_min_soc_listener()
        self._setup_grid_import_listener()
        await self._start_periodic_verification()

        # Calculate and store initial SOC for this charging period
        await self._calculate_initial_soc()

        # Scheduled nights preserve only the target, not the initial battery SOC.
        if not self.is_discharge_mode:
            await self._apply_discharge_block(announce=True)
            # The floor has to reach the inverter now when already above target:
            # with the battery already above the target the update below returns
            # early ("target reached") and _control_charge never runs - which is
            # exactly the case this block exists for.
            await self._write_raised_min_soc_floor()

        await self.async_request_refresh()

    async def _calculate_initial_soc(self) -> None:
        """Calculate and store the initial SOC for this charging period."""
        if (self.snow_nights > 0 or self._automatic_pv_shortfall_active()) and not self.is_discharge_mode:
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
            if self._adhoc_target_soc is not None:
                # Before the restart branch below, deliberately. An ad-hoc
                # window carries its own target, and it is persisted, so it is
                # the right answer after a restart too. Checking the restart
                # case first meant that once stage 1 of the evening rescue had
                # run a single poll - which is all it takes to capture the
                # inverter's min SOC - stage 2's higher target was discarded
                # and the grid top-up the whole feature exists for never
                # happened.
                adhoc_soc = max(user_min_soc, min(self._adhoc_target_soc, user_max_soc))
                self.initial_calculated_soc = adhoc_soc
                self.minimum_calculated_soc = adhoc_soc
                self.calculated_soc = adhoc_soc
                _LOGGER.info(
                    "Ad-hoc window (%s): target %.1f%% until %s",
                    self._adhoc_reason,
                    adhoc_soc,
                    self._adhoc_until.isoformat(timespec="minutes") if self._adhoc_until else "?",
                )
                self._persist_state()
                return

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
            elif (
                plan is None
                and not forecast_available
                and calculated_soc >= user_max_soc - 1.0
            ):
                # Forecast entity UNAVAILABLE (not just 0 kWh) and would charge to max - use safe fallback.
                # Only for the headroom formula: it charges to the maximum precisely
                # because it read no forecast. The bridge planner has its own
                # fallback and derives its target from the house load, so cutting
                # it back to 50 % here would drop a need it just worked out.
                safe_fallback = max(user_min_soc, min(DEFAULT_SAFE_FALLBACK_SOC, user_max_soc))
                calculated_soc = safe_fallback
                _LOGGER.warning(
                    "Forecast entity unavailable would result in %.1f%% target. Using safe fallback: %.1f%% "
                    "(prevents overcharging to 100%%)",
                    user_max_soc,
                    safe_fallback
                )

            calculated_soc = await self._floor_discharge_at_the_evening_reserve(
                calculated_soc, forecast_energy, forecast_available
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
                self._conflict_prices_source = None
                self.planned_charge_power_w = None
                self._persist_state()
            self.operation_mode = mode
        finally:
            self._switching_mode = False

    async def async_release_changed_entities(self, new_config: Mapping[str, Any]) -> None:
        """End a running window whose inverter entities the options are replacing.

        Its end has to address the entities it wrote to: the night target on
        the min SOC, grid charging, the discharge block, the charge limits.
        Left running on the new configuration, its end restores the new
        entities and the old ones keep the night's floor and grid charging for
        good. Called while ``self.config`` is still the old configuration; the
        window check after the swap starts the window again on the new one.
        """
        if not self.is_active:
            return
        changed = [
            key
            for key in _WINDOW_WRITTEN_ENTITY_KEYS
            if self.config.get(key) != new_config.get(key)
        ]
        if not changed:
            return
        _LOGGER.info(
            "The options replace %s while a window is running - ending it on the old entities",
            ", ".join(changed),
        )
        await self._on_window_end(dt_util.now())

    async def _maybe_rescue_the_evening(self) -> None:
        """Act on a shortfall the outlook has already worked out (plan 013).

        Two stages, and what separates them is what they cost:

        1. **Block the discharge.** The house then runs from the sun, and from
           the grid at the day tariff when the sun is not enough - rather than
           from a battery that is needed in three hours at the peak tariff.
           Nothing is spent that the same evening does not pay back, so this
           runs by itself.
        2. **Buy the rest.** Only with the switch on, and only once the period
           is close enough that the forecast hardly turns any more: bought too
           early is bought for a sun that might still have come.

        The stage only ever rises within a day. A poll that sees the shortfall
        close again does not release the block - the battery is still the
        cheapest place for that energy to be, and the window ends when the
        period starts anyway.
        """
        await self.async_end_evening_rescue_if_off()
        if not self.evening_rescue_charge:
            # Only on request (21 October 2026): held at 35 % from the morning
            # on, the min SOC went back up every time it was set by hand
            return
        outlook = self.last_evening_outlook
        if outlook is None or outlook.missing_kwh <= 0:
            return
        if not self.is_enabled or self._is_backup_active() or self.is_discharge_mode:
            # The ad-hoc window would refuse the discharge mode on every poll
            return
        current_soc = self._current_battery_soc()
        if current_soc is None:
            return

        now = dt_util.now()
        close_enough = (outlook.zone_start - now) <= timedelta(hours=EVENING_RESCUE_CHARGE_LEAD_H)
        stage = 2 if close_enough else 1
        if stage <= self._rescue_stage:
            return

        if stage == 1:
            _LOGGER.info(
                "The battery is %.1f kWh short for the %s period - holding what is in it",
                outlook.missing_kwh,
                outlook.zone_start.strftime("%H:%M"),
            )
        else:
            _LOGGER.info(
                "The battery is %.1f kWh short for the %s period - charging to %.1f%% from the "
                "grid at the day tariff, which is cheaper than the period itself",
                outlook.missing_kwh,
                outlook.zone_start.strftime("%H:%M"),
                outlook.required_soc,
            )
        if await self.async_open_adhoc_window(
            target_soc=outlook.required_soc if stage == 2 else current_soc,
            until=outlook.zone_start,
            reason=ADHOC_REASON_EVENING_RESCUE,
            allow_grid_charge=stage == 2,
        ):
            self._rescue_stage = stage

    async def async_end_evening_rescue_if_off(self) -> None:
        """Release a rescue window once the rescue is switched off.

        A window the rescue opened before it became opt-in, persisted across
        the update, ends here too.
        """
        if (self.evening_rescue_charge or self._adhoc_until is None
                or self._adhoc_reason != ADHOC_REASON_EVENING_RESCUE):
            return
        _LOGGER.info("The evening rescue is switched off - releasing its window")
        await self._on_window_end(dt_util.now())

    async def async_charge_to(self, target_soc: float, duration: timedelta) -> bool:
        """Charge to a level from the grid, for the ``charge_to`` action.

        An ad-hoc window with grid charging allowed, so the house connection
        limit, the capture of the inverter's own settings and the restore when
        it ends all apply exactly as they do at night.
        """
        return await self.async_open_adhoc_window(
            target_soc=target_soc,
            until=add_elapsed(dt_util.now(), duration),
            reason=ADHOC_REASON_SERVICE,
            allow_grid_charge=True,
        )

    async def async_block_discharge(self, duration: timedelta) -> bool:
        """Hold what is in the battery, for the ``block_discharge`` action.

        The same thing the evening rescue does by itself, on request: an ad-hoc
        window at the level the battery is at now, with no buying. The house
        runs from the sun or the grid until it ends.
        """
        current_soc = self._current_battery_soc()
        if current_soc is None:
            _LOGGER.warning("Cannot block the discharge - the battery level cannot be read")
            return False
        return await self.async_open_adhoc_window(
            target_soc=current_soc,
            until=add_elapsed(dt_util.now(), duration),
            reason=ADHOC_REASON_SERVICE,
            allow_grid_charge=False,
        )

    async def async_allow_discharge(self) -> bool:
        """End an ad-hoc window early, for the ``allow_discharge`` action.

        Only an ad-hoc one: a configured window is the schedule doing its job,
        and ending that is what ``reset_inverter`` is for.
        """
        if self._adhoc_until is None:
            _LOGGER.info("No ad-hoc window is running - nothing to release")
            return False
        _LOGGER.info("Releasing the ad-hoc window (%s) on request", self._adhoc_reason)
        await self._on_window_end(dt_util.now())
        return not self._pending_reset

    async def async_open_adhoc_window(
        self,
        *,
        target_soc: float,
        until: datetime,
        reason: str,
        allow_grid_charge: bool,
    ) -> bool:
        """Open a window that the clock did not ask for (plan 013).

        It is a window like any other - same capture of the inverter's own
        settings, same restore at the end, same retry ladder, same backup
        interlock, same house connection limit, same verification. Only its
        start comes from here and its end from ``until`` rather than from the
        configured times.

        Refused while a configured window runs (that one has the tariff behind
        it), while the integration is off, in the morning discharge mode, during
        backup mode, and for an end that is already past. ``allow_grid_charge=False`` holds what is in the
        battery without buying more.
        """
        now = dt_util.now()
        if elapsed_seconds(until, now) <= 0:
            _LOGGER.warning("Ad-hoc window (%s) refused: %s is not in the future", reason, until)
            return False
        if not self.is_enabled:
            _LOGGER.info("Ad-hoc window (%s) refused: the integration is switched off", reason)
            return False
        if self.is_discharge_mode:
            # Every ad-hoc window charges or holds. In this mode the window
            # machinery drives the battery down: a target above the battery
            # counts as reached at once, and nothing is written at all - while
            # the caller is told it worked.
            _LOGGER.info(
                "Ad-hoc window (%s) refused: it charges or holds, and the morning "
                "discharge mode drives the battery the other way",
                reason,
            )
            return False
        if self._is_backup_active():
            _LOGGER.info("Ad-hoc window (%s) refused: backup mode is active", reason)
            return False
        if self.is_active and self._adhoc_until is None:
            _LOGGER.info(
                "Ad-hoc window (%s) refused: the configured window is running", reason
            )
            return False
        if self._adhoc_until is not None:
            _LOGGER.info(
                "Ad-hoc window (%s) already running until %s - extending it to %s",
                self._adhoc_reason,
                self._adhoc_until.isoformat(timespec="minutes"),
                until.isoformat(timespec="minutes"),
            )
            if self._ac_limit_window == f"adhoc:{self._adhoc_until.isoformat()}":
                # Extending is the same window: its spent rewrites stay spent
                self._ac_limit_window = f"adhoc:{until.isoformat()}"
            self._adhoc_until = until
            self._adhoc_target_soc = target_soc
            self._adhoc_allow_grid_charge = allow_grid_charge
            self._adhoc_reason = reason
            # A new target has to be judged afresh. The poll only ever *sets*
            # target_reached, so a hold that had reached the level it found
            # left it True, and every later poll skipped control - stage 2 of
            # the evening rescue moved its target and then never bought.
            # _replan_in_window clears it for exactly this reason.
            self.target_reached = False
            self._persist_state()
            await self._calculate_initial_soc()
            return True

        self._adhoc_until = until
        self._adhoc_reason = reason
        self._adhoc_target_soc = target_soc
        self._adhoc_allow_grid_charge = allow_grid_charge
        _LOGGER.info(
            "Opening an ad-hoc window (%s) to %.1f%% until %s, grid charging %s",
            reason,
            target_soc,
            until.isoformat(timespec="minutes"),
            "allowed" if allow_grid_charge else "not allowed",
        )
        await self._on_window_start(now)
        if not self.is_active:
            # _on_window_start has its own refusals (skip next, date range).
            self._adhoc_until = None
            self._adhoc_reason = None
            self._adhoc_target_soc = None
            self._adhoc_allow_grid_charge = False
            return False
        return True

    async def async_reset_inverter(self) -> bool:
        """Put the inverter back to the settings captured before the window.

        The public way into :meth:`_reset_settings`, for the ``reset_inverter``
        action. Backup mode refuses: there the house runs on the battery and the
        inverter is not ours to write to, and a reset that "succeeded" by doing
        nothing would clear the captures we still need afterwards.

        Inside a running window the window is ended first, and not taken up
        again before its end time. A bare reset would not survive the second it
        was written in: the min SOC listener and the verification task exist to
        put the window's floor straight back whenever something else moves it.
        The next window runs as usual - this hands the inverter back for the
        window it is called in, it does not switch the integration off.

        The result is the reset's own: False also arms the retry ladder, because
        _reset_settings records its outcome itself.
        """
        if self._is_backup_active():
            _LOGGER.info("Backup mode active - not resetting the inverter on request")
            return False
        if self.is_active:
            now = dt_util.now()
            self._hands_off_until = self._window_end_datetime(now)
            _LOGGER.info(
                "Resetting the inverter settings on request - ending the window and "
                "leaving the inverter alone until %s",
                self._hands_off_until.isoformat(),
            )
            # The deadline is set before the end runs, so the end's own
            # _persist_state carries it.
            self._clear_reset_abandoned()
            await self._on_window_end(now)
            return not self._pending_reset
        _LOGGER.info("Resetting the inverter settings on request")
        # Asked for by hand: a ladder that ran out of attempts gets a fresh one.
        self._clear_reset_abandoned()
        return await self._reset_settings()

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
        self._conflict_prices_source = None
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
            if self._adhoc_until is not None:
                # There is nothing to reset, but the deadline still has to go:
                # left behind it becomes the answer to every later question
                # about when the current window ends.
                _LOGGER.info(
                    "Clearing the ad-hoc deadline (%s) of a window that is no longer active",
                    self._adhoc_reason,
                )
                self._adhoc_until = None
                self._adhoc_reason = None
                self._adhoc_target_soc = None
                self._adhoc_allow_grid_charge = False
                self._persist_state()
            else:
                _LOGGER.debug("Window end without an active window - nothing to reset")
            return
        mode_label = "Morning discharge" if self.is_discharge_mode else "Night charge"
        _LOGGER.info("%s window ended, resetting settings", mode_label)
        if self._window_started_at is not None and self._adhoc_until is None:
            bounds = scheduled_window_bounds(self._window_started_at, *self._window_times())
            if bounds is not None and elapsed_seconds(now, self._align_tz(bounds[1], now)) >= 0:
                self._last_scheduled_end = bounds[1]
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
                # Read the trial's feedback before our own restore changes it.
                self._finalize_auto_test()
            finally:
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
            self._reset_auto_test_state()
            self.target_reached = False
            self.initial_calculated_soc = None  # Reset initial SOC for next charging period
            self.minimum_calculated_soc = None  # Reset minimum SOC for next charging period
            self.override_soc = None  # Clear override when window ends
            self.last_plan = None
            self._conflict_prices_source = None
            self.planned_charge_power_w = None
            self._pv_crossover = None
            self._sun_fallback_logged = False
            # A raised floor must never outlive its window: the battery would be
            # blocked for good. _reset_settings puts original_min_soc back on the
            # inverter; this drops the floor that produced the raised value.
            self._window_floor_soc = None
            self._window_started_at = None
            self._discharge_block_logged = None
            # Whatever this window was, the next one is decided from scratch
            self._adhoc_until = None
            self._adhoc_reason = None
            self._adhoc_target_soc = None
            self._adhoc_allow_grid_charge = False
            self._rescue_stage = 0
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

    def _schedule_reset_retry(self, *, consume: bool = True) -> None:
        """Retry the failed reset after 60 s, 120 s, 240 s, then twice every 15 minutes.

        Then it stops. Each attempt writes to the inverter, and a restore that
        cannot succeed - a value the entity refuses, a device that ignores it -
        turned into a write every 15 minutes for as long as Home Assistant ran.
        Whatever is still owed is kept: the next window start, a manual reset
        or a successful one takes it from there.
        """
        self._cancel_reset_retry()
        if consume and self._reset_retry_count >= len(RESET_RETRY_DELAYS) + RESET_RETRY_SLOW_ATTEMPTS:
            self._abandon_reset()
            return
        if not consume:
            # A deferral (backup mode) writes nothing, so it spends nothing:
            # an outage longer than the ladder must not give the restore up.
            delay = RESET_RETRY_INTERVAL
        elif self._reset_retry_count < len(RESET_RETRY_DELAYS):
            delay = RESET_RETRY_DELAYS[self._reset_retry_count]
        else:
            delay = RESET_RETRY_INTERVAL
        # The attempt is counted when it runs (_retry_reset), not here: a
        # restart while it waits must neither lose it nor hand out an extra one.

        async def _run_reset_retry(_now: datetime) -> None:
            self._reset_retry_unsub = None
            await self._retry_reset()

        self._reset_retry_unsub = async_call_later(self.hass, delay, _run_reset_retry)
        _LOGGER.warning(
            "Inverter settings are not reset yet - retrying in %d seconds (attempt %d)",
            delay,
            self._reset_retry_count + 1,
        )

    def _reset_issue_id(self) -> str:
        return f"reset_incomplete_{self.entry.entry_id}"

    def _entities_still_owed(self) -> list[str]:
        """The entities whose restore has not been confirmed yet, for the repair issue."""
        owed: list[str] = []
        for capture, key in (
            (self.original_min_soc, CONF_MIN_SOC_ENTITY),
            (self._original_ac_charge_power, CONF_CHARGE_POWER_ENTITY),
            (self._original_discharge_limit, CONF_DISCHARGE_LIMIT_ENTITY),
            (self._original_discharge_block, CONF_DISCHARGE_BLOCK_SWITCH),
            (self._original_absolute_charge_power, CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY),
        ):
            entity_id = self.config.get(key)
            if capture is not None and entity_id:
                owed.append(str(entity_id))
        for key in (CONF_GRID_CHARGE_SWITCH, CONF_FORCE_DISCHARGE_SWITCH):
            entity_id = self.config.get(key)
            state = self.hass.states.get(str(entity_id)) if entity_id else None
            if entity_id and (state is None or state.state != "off"):
                owed.append(str(entity_id))
        return owed

    def _abandon_reset(self) -> None:
        """Stop the automatic reset and tell the user what is still on the inverter."""
        owed = self._entities_still_owed()
        _LOGGER.error(
            "Inverter settings could not be restored after %d attempts - no further automatic "
            "writes. Still to check by hand: %s",
            self._reset_retry_count,
            ", ".join(owed) or "nothing",
        )
        self._pending_reset = False
        self._reset_abandoned = True
        self._persist_state()
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._reset_issue_id(),
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.ERROR,
            translation_key="reset_incomplete",
            translation_placeholders={
                "name": str(self.entry.title),
                "entities": ", ".join(owed) or "-",
            },
        )

    def _clear_reset_abandoned(self) -> None:
        """A window start, a manual reset or a success takes the restore over again."""
        self._reset_retry_count = 0
        if not self._reset_abandoned:
            return
        self._reset_abandoned = False
        ir.async_delete_issue(self.hass, DOMAIN, self._reset_issue_id())

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
            self._clear_reset_abandoned()
            self._cancel_reset_retry()
        elif self._reset_abandoned:
            # Out of attempts: only a window start, a manual reset or a success
            # brings the automatic restore back.
            self._pending_reset = False
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
        if not self._pending_reset or self._reset_abandoned:
            return
        if self.is_active:
            # A new window owns the inverter; its end performs the reset
            return
        if self._is_backup_active():
            # Never override backup mode: try again later
            _LOGGER.info("Backup mode active - deferring the reset retry")
            self._schedule_reset_retry(consume=False)
            return
        # Counted and persisted before it writes, so a restart cannot repeat it
        self._reset_retry_count += 1
        self._persist_state()
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
        """Serialize restoration after any already-running floor verification."""
        async with self._min_soc_write_lock:
            return await self._reset_settings_locked()

    async def _reset_settings_locked(self) -> bool:
        """Reset the inverter to its original settings.

        Returns True when every target was reached: min SOC restored, grid
        charging (and force discharge) off and the charge/discharge limits
        restored. On False the values needed for a retry are kept, the pending
        flag is set and the retry is scheduled - callers do not have to look at
        the result to keep the inverter from staying in the window's state.
        """
        # CRITICAL: Use stored original value if available, otherwise use configured default
        reset_min_soc = self.original_min_soc if self.original_min_soc is not None else float(self.config.get(CONF_DEFAULT_MIN_SOC, 8.0))
        min_soc_entity = self.config.get(CONF_MIN_SOC_ENTITY)
        if min_soc_entity and self.original_min_soc is None:
            # No floor was captured, so this integration never changed it:
            # writing the configured default would clobber a min SOC the user
            # set by hand (reachable by disabling the integration outside a
            # window, or when the entity was unavailable at window start).
            # Switching grid charge and force discharge off below stays safe,
            # as do the limit restores, which carry their own captures.
            _LOGGER.debug(
                "Min SOC restore skipped for %s - no original value was captured",
                min_soc_entity,
            )
            min_soc_entity = None
        grid_charge_switch = self.config.get(CONF_GRID_CHARGE_SWITCH)

        ok = True

        # Reset min SOC
        if min_soc_entity:
            try:
                state = self.hass.states.get(min_soc_entity)
                if state and state.state not in ("unknown", "unavailable"):
                    reset_min_soc = _within_entity_range(
                        state, reset_min_soc, min_soc_entity, "Min SOC restore"
                    )
                    before_value = state.state
                    before_reported = state.last_reported
                    if not await self._already_confirmed(min_soc_entity, state, reset_min_soc, 0.5):
                        await self.hass.services.async_call(
                            "number",
                            "set_value",
                            {"entity_id": min_soc_entity, "value": reset_min_soc},
                            blocking=True,
                        )
                    # A following window may start in this reset's refresh.
                    # Never let it capture the previous target as its original
                    # floor while the restore is still queued or unconfirmed.
                    feedback = self.hass.states.get(min_soc_entity)
                    restored_soc = _as_float(feedback.state) if feedback is not None else None
                    fresh = feedback is not None and (
                        feedback is not state or feedback.state != before_value
                        or feedback.last_reported != before_reported
                    )
                    if restored_soc is not None and abs(restored_soc - reset_min_soc) <= 0.5 and not fresh:
                        # A cached original value may predate a successfully
                        # raised floor and an ignored restore. Ask the owning
                        # integration for a new observation before clearing it.
                        await self.hass.services.async_call(
                            "homeassistant", "update_entity", {"entity_id": min_soc_entity}, blocking=True
                        )
                        feedback = self.hass.states.get(min_soc_entity)
                        restored_soc = _as_float(feedback.state) if feedback is not None else None
                        fresh = feedback is not None and (
                            feedback is not state or feedback.state != before_value
                            or feedback.last_reported != before_reported
                        )
                    if not fresh or restored_soc is None or abs(restored_soc - reset_min_soc) > 0.5:
                        _LOGGER.error("Min SOC restore to %.1f%% has no matching feedback", reset_min_soc)
                        ok = False
                    else:
                        _LOGGER.info("Reset the inverter min SOC to %.1f%% (original: stored)", reset_min_soc)
                        # Done, on its own. Keeping the capture until every
                        # other setting was back too made each retry write the
                        # min SOC again - every 15 minutes, for hours, while
                        # an unrelated restore kept failing (20 October 2026).
                        self.original_min_soc = None
                        self._window_floor_soc = None
                        # The reset as a whole is still owed (grid switch, limits):
                        # a crash right after this persist must leave that on
                        # record, or nothing would switch grid charging off after
                        # the restart (Codex on #36). _record_reset_outcome sets
                        # the final value.
                        self._pending_reset = True
                        self._persist_state()
                else:
                    _LOGGER.error("Cannot reset min SOC - entity state unavailable")
                    ok = False
            except Exception as e:
                _LOGGER.error("Error resetting min SOC: %s", e, exc_info=True)
                ok = False
        elif not self.config.get(CONF_MIN_SOC_ENTITY):
            # Reaching here with one configured means the guard above skipped the
            # restore, which already logged why.
            _LOGGER.warning("No min SOC entity configured - cannot reset")

        # Turn off grid charge
        if grid_charge_switch:
            try:
                state = self.hass.states.get(grid_charge_switch)
                if state and state.state not in ("unknown", "unavailable"):
                    if state.state == "on":
                        await self.hass.services.async_call(
                            "switch",
                            "turn_off",
                            {"entity_id": grid_charge_switch},
                            blocking=True,
                        )
                        feedback = self.hass.states.get(grid_charge_switch)
                        if feedback is None or feedback.state != "off":
                            _LOGGER.error("Grid charge stop has no off feedback")
                            ok = False
                    else:
                        ok = ok and state.state == "off"
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
                        blocking=True,
                    )
                    feedback = self.hass.states.get(force_discharge_switch)
                    if feedback is None or feedback.state != "off":
                        _LOGGER.error("Force discharge stop has no off feedback")
                        ok = False
                elif state.state != "off":
                    ok = False
            except Exception as e:
                _LOGGER.error("Error turning off force discharge: %s", e, exc_info=True)
                ok = False

        # _last_soc_set is log information only; always forget it so the next
        # window starts from what the inverter actually reports.
        self._last_soc_set = None
        if not ok:
            # Restoring a higher ceiling while a stop is unconfirmed could
            # reactivate the last device command. Retain every capture for retry.
            self._record_reset_outcome(False)
            return False
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
        """Observe lost channels before control, retaining independent stop paths."""
        self._observe_pv_yield()
        faults = self._update_communication_health()
        if faults and self.is_active and not self._ending and not self._unloading:
            # No raised ceiling may replace a protective zero while the stop
            # path is missing. Keep captures for the normal recovery/reset.
            # Written once per outage, not on every refresh, and not at all
            # while the battery cannot draw from the grid anyway.
            if (self._ac_charge_limit_target() is not None
                    and self._planned_setpoint_written_w != 0
                    and not self._battery_idle_on_grid()):
                if await self._set_ac_charge_limit_w(0):
                    self._planned_setpoint_written_w = 0
            # Bounded while a stop is pending (see _pause_grid_charge); the
            # ceilings go back only once communication is back (CodeRabbit on #37)
            await self._pause_grid_charge("communication interrupted; stop requires feedback")
            # The same guard also runs in export mode. Do not leave forced
            # discharge active, or change the house/backup discharge ceiling.
            force_id = self.config.get(CONF_FORCE_DISCHARGE_SWITCH)
            force = self.hass.states.get(force_id) if force_id else None
            if force_id and (force is None or force.state != "off"):
                try:
                    await self.hass.services.async_call(
                        "switch", "turn_off", {"entity_id": force_id}, blocking=True,
                    )
                except Exception:
                    _LOGGER.exception("Cannot stop forced discharge; device stop is unconfirmed")
            self._update_communication_health()
            data = dict(self.data or {})
            data.update(is_active=self.is_active, current_soc=self._current_battery_soc(),
                        planned_charge_power_w=0, planned_power_is_applied=False)
            return data
        data = await self._async_calculate_and_control()
        self._update_communication_health()
        return data

    async def _async_calculate_and_control(self) -> dict[str, Any]:
        """Fetch data from entities and update coordinator."""
        inactive_data: dict[str, Any] = {
            "calculated_soc": None,
            "is_active": False,
            "target_reached": False,
            "operation_mode": self.operation_mode,
            "skip_next": self.skip_next,
        }
        # Worked out before every early return below: the question "will the
        # battery carry this evening" is a daytime one, and by then no window
        # is running (plan 013).
        self.last_evening_outlook = (
            await self.async_evening_outlook() if self.is_enabled else None
        )
        self.last_curtailment_outlook = (
            await self.async_curtailment_outlook() if self.is_enabled else None
        )
        self.last_curtailment_attributes = (
            await self.curtailment_attributes() if self.is_enabled else {}
        )
        await self._maybe_rescue_the_evening()
        if self.is_enabled and not self.is_active and not self._ending:
            # Safety net for a lost start trigger, the counterpart of the one
            # for the end below: a start in the hour the clocks skip in spring
            # never fires that day (Home Assistant moves the pattern to the
            # next day). The window check applies every refusal - skip next,
            # a handed-back inverter, backup mode, the date range. The end
            # minute is left out: the end trigger has just ended the window
            # there, and the clock check is inclusive at the end.
            now = dt_util.now()
            start, end = self._window_times()
            if self._scheduled_window_is_open(now):
                await self._check_current_window()
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
        if self._adhoc_until is not None:
            # An ad-hoc window runs outside the configured times by definition,
            # so the clock check below would end it at once. It ends by its own.
            if elapsed_seconds(dt_util.now(), self._adhoc_until) >= 0:
                _LOGGER.info(
                    "The ad-hoc window (%s) is over; ending it from the polling update",
                    self._adhoc_reason,
                )
                await self._on_window_end(dt_util.now())
                return inactive_data
        elif not self._scheduled_window_is_open(dt_util.now()):
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
            elif (
                plan is None
                and not forecast_available
                and calculated_soc >= user_max_soc - 1.0
            ):
                # Forecast entity UNAVAILABLE (not just 0 kWh) and would charge to max - use safe fallback.
                # Only for the headroom formula: it charges to the maximum precisely
                # because it read no forecast. The bridge planner has its own
                # fallback and derives its target from the house load, so cutting
                # it back to 50 % here would drop a need it just worked out.
                safe_fallback = max(user_min_soc, min(DEFAULT_SAFE_FALLBACK_SOC, user_max_soc))
                calculated_soc = safe_fallback
                _LOGGER.warning(
                    "Forecast entity unavailable would result in %.1f%% target. Using safe fallback: %.1f%% "
                    "(prevents overcharging to 100%%)",
                    user_max_soc,
                    safe_fallback
                )

            calculated_soc = await self._floor_discharge_at_the_evening_reserve(
                calculated_soc, forecast_energy, forecast_available
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

        # SAFETY: Before touching the inverter, verify we can check battery SOC
        # This prevents turning on grid charge switch if battery SOC is unavailable
        # (e.g., after restart when battery entity hasn't loaded yet)
        battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
        # A reading the integration cannot compare is no reading. "unavailable"
        # and a nan that parses fine but poisons every comparison have to take
        # the same path, or grid charging would run on a target that can never
        # count as reached.
        can_check_soc = battery_soc_entity is not None and self._current_battery_soc() is not None

        # A stop that was not confirmed off is retried first, bounded - whatever
        # the SOC reads now: unknown, or already below the target again
        # (CodeRabbit on #37).
        if self._grid_stop_pending and not self.target_reached:
            # A raised plan, override, snow night or ad-hoc target re-armed
            # charging: the stop belonged to the old target, and the control
            # path below decides afresh (Codex on #37)
            self._grid_stop_pending = False
            self._grid_stop_retries = 0
        pending_grid_stop = self._stop_pending_for_this_target()
        if pending_grid_stop:
            await self._retry_grid_stop()

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

        if self._current_battery_soc() is None:
            _LOGGER.warning("Battery SoC cannot be trusted - pausing inverter control until it reports again")
            if not pending_grid_stop:
                # A pending stop already had its bounded attempt above
                await self._pause_grid_charge("battery SoC cannot be trusted")
            await self._enforce_grid_limit_now()
            self.planned_charge_power_w = None
            self.planned_power_is_applied = False
            return {**inactive_data, "calculated_soc": self.calculated_soc,
                    "is_active": self.is_active, "target_reached": self.target_reached}

        # Control the inverter entities based on operation mode
        if not self.target_reached:
            if can_check_soc:
                if self.is_discharge_mode:
                    await self._control_discharge(target_soc)
                else:
                    await self._control_charge(target_soc)
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

    async def _control_charge(self, target_soc: float) -> None:
        """Control the inverter entities for night charging, from the calculated SOC."""
        if not self.is_active or self._ending or self._unloading:
            return
        if self._is_backup_active():
            _LOGGER.info("Backup mode active or unconfirmed - skipping inverter control")
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

        if self._current_battery_soc() is None:
            await self._pause_grid_charge("battery SoC cannot be trusted")
            return

        min_soc_entity = self.config.get(CONF_MIN_SOC_ENTITY)
        grid_charge_switch = self.config.get(CONF_GRID_CHARGE_SWITCH)

        # Check current SOC before starting charging (doesn't delay commands)
        # CRITICAL: If battery SOC is unavailable, we should NOT turn on grid charge
        # to prevent charging when we can't verify the current state (e.g., after restart)
        battery_soc_entity = self.config.get(CONF_BATTERY_SOC_ENTITY)
        current_soc = None
        should_skip_charging = False

        if self._adhoc_until is not None and not self._adhoc_allow_grid_charge:
            # An ad-hoc window that may only hold what is there, not buy more.
            # The floor still goes on the inverter; the grid switch does not.
            _LOGGER.debug(
                "Ad-hoc window (%s) does not allow grid charging", self._adhoc_reason
            )
            should_skip_charging = True

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
                    should_skip_charging = True
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
        floor_soc = self._charging_floor_soc(target_soc)
        if floor_soc is None:
            await self._pause_grid_charge("no representable minimum SoC within the configured bounds")
            return

        # Track if we need to set min SOC
        need_to_set_min_soc = False
        min_soc_current_value = None

        # Prepare min SOC setting (check if needed, store original value)
        if min_soc_entity:
            try:
                state = self.hass.states.get(min_soc_entity)
                if not state or state.state in ("unknown", "unavailable"):
                    # Without the entity the floor cannot be set, so charging must
                    # not start either. Nothing has been written yet, so no original
                    # is captured; the periodic verification applies the target as
                    # soon as the entity reports a value (finding F6).
                    _LOGGER.warning(
                        "Min SOC entity %s is unavailable - deferring min SOC and grid "
                        "charge until it reports a value",
                        min_soc_entity,
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

        if (need_to_set_min_soc and min_soc_entity and current_soc is not None
                and floor_soc > current_soc):
            # A floor above the SOC makes a Kostal charge from the grid at once,
            # at whatever limit stands - its own is 22 kW. The planned limit goes
            # on first, not seconds later (20 October 2026, 23:01).
            self._plan_limit_write_failed = False
            await self._plan_charge_power(target_soc)
            if self._plan_limit_write_failed:
                # Not without it: the next poll tries again (Codex on #38)
                _LOGGER.warning(
                    "The planned charge limit could not be written - not raising the min SOC "
                    "to %.1f%% this poll, the inverter would charge at its own limit",
                    floor_soc,
                )
                return

        # CRITICAL: Send commands together to avoid double DC checks
        # If both min SOC and grid charge need to be set, send them with minimal delay (0.1s)
        # so the inverter processes them together and only does DC checks once
        if need_to_set_min_soc and min_soc_entity:
            # Set min SOC first
            min_soc_set = False
            window_started_at = self._window_started_at
            try:
                async with self._min_soc_write_lock:
                    if (not self.is_active or self._ending or self._unloading
                            or self._window_started_at != window_started_at):
                        return
                    await self.hass.services.async_call(
                        "number",
                        "set_value",
                        {"entity_id": min_soc_entity, "value": floor_soc},
                        blocking=True,
                    )
                self._last_soc_set = floor_soc
                min_soc_set = True
                _LOGGER.info(
                    "Set the inverter min SOC to %.1f%% (was %s)",
                    floor_soc,
                    f"{min_soc_current_value:.1f}%" if min_soc_current_value is not None else "unknown",
                )
            except Exception as e:
                # Without the new floor the inverter would charge against the wrong
                # min SOC, so grid charging is not switched on; the next poll retries.
                _LOGGER.error("Error setting min SOC: %s", e, exc_info=True)
                should_skip_charging = True
                await self._pause_grid_charge("minimum SoC command failed")

            # Immediately send grid charge command (configurable delay) so inverter processes both together
            if min_soc_set and grid_charge_switch and not should_skip_charging:
                delay = _as_float(self.config.get(CONF_COMMAND_DELAY))
                # A configured 0 is a valid answer ("no delay"), so it must not
                # fall through to the default the way a falsy check would.
                command_delay = max(0.0, delay) if delay is not None else DEFAULT_COMMAND_DELAY
                await asyncio.sleep(command_delay)  # Configurable delay

                if not await self._grid_charge_activation_safe(target_soc):
                    return

                try:
                    state = self.hass.states.get(grid_charge_switch)
                    if state and state.state == "off":
                        await self.hass.services.async_call(
                            "switch",
                            "turn_on",
                            {"entity_id": grid_charge_switch},
                            blocking=True,
                        )
                        _LOGGER.info("Turned on the grid charge switch (immediately after min SOC)")
                        await self._confirm_limit_after_switch_on()
                    elif state and state.state == "on":
                        _LOGGER.debug("Grid charge switch already on")
                except Exception as e:
                    _LOGGER.error("Error turning on grid charge: %s", e, exc_info=True)
        elif grid_charge_switch and not should_skip_charging:
            # Only grid charge needs to be turned on (min SOC already set or not needed)
            if not await self._grid_charge_activation_safe(target_soc):
                return
            try:
                state = self.hass.states.get(grid_charge_switch)
                if state and state.state == "off":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_on",
                        {"entity_id": grid_charge_switch},
                        blocking=True,
                    )
                    _LOGGER.info("Turned on the grid charge switch")
                    await self._confirm_limit_after_switch_on()
                elif state and state.state == "on":
                    _LOGGER.debug("Grid charge switch already on")
            except Exception as e:
                _LOGGER.error("Error turning on grid charge: %s", e, exc_info=True)

        # Apply absolute AC+DC charge limit during AC charging
        if grid_charge_switch and not should_skip_charging:
            await self._apply_absolute_charge_power_limit()

    def _stop_pending_for_this_target(self) -> bool:
        """A stop on reaching the current target still waits for off feedback."""
        return self._grid_stop_pending and self.target_reached and not self.is_discharge_mode

    async def _pause_grid_charge(self, reason: str, *, assume_on: bool = False) -> None:
        """Request a stop while retaining captures and protective power limits.

        While a stop on reaching the target is pending, every turn_off counts
        against the same per-window bound, whichever path asks for it - the
        outage guard refreshes every 15 seconds (CodeRabbit on #37).

        ``assume_on``: this integration switched it on a moment ago, and KORE
        publishes the switch only with its next settings poll - an "off" in
        Home Assistant then says nothing about the inverter.
        """
        entity_id = self.config.get(CONF_GRID_CHARGE_SWITCH)
        state = self.hass.states.get(entity_id) if entity_id else None
        if entity_id and (assume_on or state is None or state.state != "off"):
            if self._stop_pending_for_this_target():
                if self._grid_stop_retries >= GRID_STOP_MAX_RETRIES:
                    return
                self._grid_stop_retries += 1
            try:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": entity_id},
                    blocking=True,
                )
                _LOGGER.warning("Requested grid-charge pause: %s", reason)
            except Exception:
                _LOGGER.exception("Cannot pause grid charging (%s); inverter stop is unconfirmed", reason)

    def _charge_permission_current(self) -> bool:
        """Check live lifecycle and the absolute deadline at the command sink."""
        if (not self.is_active or not self.is_enabled or self._ending
                or self._reconfiguring or self._unloading):
            return False
        now = dt_util.now()
        if self._adhoc_until is not None:
            return elapsed_seconds(self._adhoc_until, now) > 0
        return self._scheduled_window_is_open(now)

    def _suspend_grid_switch_if_on(self, *, switched_on: bool = False) -> None:
        """Keep the grid switch off for the window once a limit failed with it on.

        A Kostal treats Battery:ManualCharge as a forced charge: within a second
        of switching it on the charge limit read 22.3 kW instead of the 6.7 kW
        written, the write counted as failed, grid charging was paused - and the
        next poll switched it on again. Every 15 minutes, all night, each time
        a grid draw of up to 12 kW for a few seconds (20/21 October 2026).
        """
        if not self.is_active or self.is_discharge_mode or self._grid_switch_suspended:
            return
        switch_id = self.config.get(CONF_GRID_CHARGE_SWITCH)
        state = self.hass.states.get(str(switch_id)) if switch_id else None
        if not switched_on and (state is None or state.state != "on"):
            return
        self._grid_switch_suspended = True
        self._persist_state()
        _LOGGER.warning(
            "The charge limit did not hold with %s switched on - grid charging stays off "
            "for the rest of this window. The raised min SOC still applies; an inverter "
            "that charges up to it does so under the charge limit",
            switch_id,
        )

    async def _confirm_limit_after_switch_on(self) -> None:
        """Check the charge limit right after switching grid charging on.

        The forced charge lifts the limit within seconds; the verification
        would only notice it at the next poll, up to 15 minutes later. The
        limit is written again at once and read again SWITCH_ON_RECHECK_S
        later. If it was lifted, the switch goes off again, stays off for the
        window, and the planned limit is written back for the floor that keeps
        charging.
        """
        target = self._ac_charge_limit_target()
        written = self._planned_setpoint_written_w
        if target is None or written is None:
            return
        switch_id = self.config.get(CONF_GRID_CHARGE_SWITCH)
        switch = self.hass.states.get(str(switch_id)) if switch_id else None
        shown_on = switch is not None and switch.state == "on"
        # Written once more: KORE reads the register back directly, while Home
        # Assistant sees it only every 30 s. A forced charge fails this write
        # within a second, and the setter suspends the switch and pauses.
        if not await self._set_ac_charge_limit_w(int(written)):
            # Switched on a moment ago, whatever Home Assistant shows yet: the
            # lab caught KORE's switch still reading "off" here, and with it
            # neither the suspension nor the pause took effect. A switch that
            # did read "on" was paused by the setter when it was suspended or
            # the house connection limit is configured.
            paused = shown_on and self.is_active and (
                self._grid_switch_suspended or self._grid_limit_configured()
            )
            self._suspend_grid_switch_if_on(switched_on=True)
            if not paused:
                await self._pause_grid_charge(
                    "the charge limit did not hold with grid charging switched on", assume_on=True,
                )
            # Off again, the Kostal holds a limit: the floor charges under it
            # now, not under its own 22 kW until the next poll
            await self._set_ac_charge_limit_w(int(written))
            return
        if await self._limit_lifted_by_switch_on() or self._unloading:
            return
        self._cancel_switch_on_recheck()

        async def _recheck(_now: datetime) -> None:
            self._switch_on_recheck_unsub = None
            await self._limit_lifted_by_switch_on()

        self._switch_on_recheck_unsub = async_call_later(self.hass, SWITCH_ON_RECHECK_S, _recheck)

    def _cancel_switch_on_recheck(self) -> None:
        if self._switch_on_recheck_unsub:
            self._switch_on_recheck_unsub()
            self._switch_on_recheck_unsub = None

    async def _limit_lifted_by_switch_on(self) -> bool:
        """Handle a charge limit lifted while grid charging is on; True if it was."""
        target = self._ac_charge_limit_target()
        written = self._planned_setpoint_written_w
        if (target is None or written is None or not self.is_active or self._ending
                or self._unloading or self._grid_switch_suspended):
            return False
        switch_id = self.config.get(CONF_GRID_CHARGE_SWITCH)
        switch = self.hass.states.get(str(switch_id)) if switch_id else None
        if switch is None or switch.state != "on":
            return False
        actual = self._get_ac_charge_limit_w(target[0])
        if actual is None or actual <= written + PLANNED_POWER_WRITE_THRESHOLD_W:
            return False
        _LOGGER.warning(
            "Switching grid charging on lifted the charge limit to %.0f W (written: %.0f W)",
            actual, written,
        )
        self._suspend_grid_switch_if_on()
        # Off whether or not the house connection limit is configured: a
        # suspended switch left on is a forced charge to the target (Codex and
        # CodeRabbit on #38)
        if self._grid_switch_suspended or self._grid_limit_configured():
            await self._pause_grid_charge("the charge limit did not hold with grid charging switched on")
        await self._set_ac_charge_limit_w(int(written))
        return True

    async def _grid_charge_activation_safe(self, target_soc: float) -> bool:
        """Revalidate permission after awaited writes/delays, at the activation sink."""
        if self._grid_switch_suspended:
            _LOGGER.debug("Grid charge switch suspended for this window - not switching it on")
            return False
        if not self._charge_permission_current():
            await self._pause_grid_charge("window ended or charging permission was withdrawn")
            return False
        if self._is_backup_active():
            await self._pause_grid_charge("island status changed or is unconfirmed")
            if self.is_active:
                await self._check_current_window()  # Release the raised floor as well.
            return False
        soc = self._current_battery_soc()
        if soc is None or soc >= target_soc:
            await self._pause_grid_charge("battery SoC changed or cannot be trusted")
            return False
        if self._grid_limit_configured():
            target = self._ac_charge_limit_target()
            current = self._get_ac_charge_limit_w(target[0]) if target is not None else None
            requested = (
                float(self._auto_test_power_w)
                if self._auto_test_active and self._auto_test_power_w is not None
                else self.planned_charge_power_w
            )
            if requested is None:
                requested = current if current is not None else 0.0
            allowed = float(int(self._grid_limited_setpoint(requested)))
            if target is None or current is None or allowed <= 0:
                await self._pause_grid_charge("connection headroom or charge limit cannot be confirmed")
                return False
            if current > allowed:
                if not await self._set_ac_charge_limit_w(int(allowed)):
                    await self._pause_grid_charge("protective charge-limit write failed")
                    return False
            readback = self._get_ac_charge_limit_w(target[0])
            if readback is None or readback > allowed:
                await self._pause_grid_charge("protective charge limit is not confirmed by readback")
                return False
            # State may have changed while the protective write was awaited.
            if self._grid_limited_setpoint(allowed) < allowed:
                await self._pause_grid_charge("connection headroom changed during activation")
                return False
        # No await separates this final permission check from the switch call.
        soc = self._current_battery_soc()
        permitted = (self._charge_permission_current() and not self._is_backup_active()
                     and soc is not None and soc < target_soc)
        if not permitted:
            await self._pause_grid_charge("charging permission changed during activation")
        return permitted

    async def _control_discharge(self, target_soc: float) -> None:
        """Control the inverter entities for morning discharge mode.

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

        min_soc_entity = self.config.get(CONF_MIN_SOC_ENTITY)
        grid_charge_switch = self.config.get(CONF_GRID_CHARGE_SWITCH)
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
                            blocking=True,
                        )
                        _LOGGER.info("Turned off force discharge - target already reached")
                except Exception as e:
                    _LOGGER.error("Error turning off force discharge: %s", e, exc_info=True)
            return

        need_to_set_min_soc = False
        # Force discharge needs the floor in place; without the entity it waits
        # for the periodic verification to apply the target (finding F6).
        min_soc_available = True

        if min_soc_entity:
            try:
                state = self.hass.states.get(min_soc_entity)
                if not state or state.state in ("unknown", "unavailable"):
                    _LOGGER.warning(
                        "Min SOC entity %s is unavailable - deferring discharge floor and "
                        "force discharge until it reports a value",
                        min_soc_entity,
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
                # Same reasoning as the failed write below: preparation is where
                # the floor is read and captured, so after a failure here nobody
                # knows what the inverter's floor is - and a forced discharge
                # against an unknown floor can run the battery past the user
                # minimum. Without this the discharge started anyway, because
                # nothing had set the flag.
                _LOGGER.error("Error preparing min SOC for discharge: %s", e, exc_info=True)
                min_soc_available = False

        if need_to_set_min_soc and min_soc_entity:
            window_started_at = self._window_started_at
            try:
                async with self._min_soc_write_lock:
                    if (not self.is_active or self._ending or self._unloading
                            or self._window_started_at != window_started_at):
                        return
                    await self.hass.services.async_call(
                        "number",
                        "set_value",
                        {"entity_id": min_soc_entity, "value": target_soc},
                        blocking=True,
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
        if grid_charge_switch:
            try:
                state = self.hass.states.get(grid_charge_switch)
                if state and state.state == "on":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": grid_charge_switch},
                        blocking=True,
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
                        blocking=True,
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
                        blocking=True,
                    )
                    _LOGGER.info("Stopped force discharge - target SOC reached")
            except Exception as e:
                _LOGGER.error("Error stopping force discharge: %s", e, exc_info=True)

    async def _stop_grid_charging(self) -> bool:
        """Stop grid charging when target is reached; True once it is confirmed off.

        The charge ceilings are only handed back once the switch reports off.
        A stop that failed or has no off feedback may leave grid charging on,
        and raising the ceiling then turns a protective limit into full power
        - the window-end reset has the same rule (CodeRabbit on #35).
        """
        stopped = True
        grid_charge_switch = self.config.get(CONF_GRID_CHARGE_SWITCH)
        if grid_charge_switch:
            stopped = False
            try:
                state = self.hass.states.get(grid_charge_switch)
                if state and state.state == "on":
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": grid_charge_switch},
                        blocking=True,
                    )
                    feedback = self.hass.states.get(grid_charge_switch)
                    stopped = feedback is not None and feedback.state == "off"
                    if stopped:
                        _LOGGER.info("Stopped grid charging - target SOC reached")
                    else:
                        _LOGGER.error("Grid charge stop has no off feedback")
                elif state and state.state == "off":
                    stopped = True
                    _LOGGER.debug("Grid charge already off")
                else:
                    _LOGGER.warning("Cannot stop grid charge - entity state unavailable")
            except Exception as e:
                _LOGGER.error("Error stopping grid charge: %s", e, exc_info=True)
        self._finalize_auto_test()
        self._grid_stop_pending = not stopped
        if not stopped:
            # Keep the protective ceilings and their captures: the regular poll
            # retries the stop (bounded), and the window-end reset restores
            # only after off (Codex and CodeRabbit on #37).
            _LOGGER.warning("Keeping the charge limits - grid charging is not confirmed off")
            return False
        # The window is still open here - reaching the target does not end it -
        # so the captured original has to survive this restore.
        await self._reset_absolute_charge_power(forget=False)
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
        return True

    async def _retry_grid_stop(self) -> None:
        """Retry a stop that was not confirmed off, from the regular poll only.

        A switch that reports off by now is confirmed without another command,
        and the ceilings go back. One that still reports on gets turn_off at
        most GRID_STOP_MAX_RETRIES more times this window - a command on every
        SOC update is exactly what must not reach the inverter.
        """
        switch_id = self.config.get(CONF_GRID_CHARGE_SWITCH)
        state = self.hass.states.get(str(switch_id)) if switch_id else None
        if state is not None and state.state == "on":
            if self._grid_stop_retries >= GRID_STOP_MAX_RETRIES:
                return  # the window-end reset takes it from here
            self._grid_stop_retries += 1
        await self._stop_grid_charging()

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

        min_soc_entity = self.config.get(CONF_MIN_SOC_ENTITY)
        if not min_soc_entity:
            return

        async def _on_inverter_min_soc_change(
            event: Event[EventStateChangedData],
        ) -> None:
            """Handle inverter min SOC state changes."""
            # The last numeric floor survives the entity going blank
            self._observe_min_soc(event.data.get("old_state"))
            self._observe_min_soc(event.data.get("new_state"))
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
            min_soc_entity,
            _on_inverter_min_soc_change,
        )
        _LOGGER.debug("Set up inverter min SOC listener for %s", min_soc_entity)

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

        async def _keepalive_loop() -> None:
            while self.is_active and self.is_enabled:
                try:
                    await asyncio.sleep(CHARGE_LIMIT_KEEPALIVE_S)
                    if self.is_active and self.is_enabled:
                        await self._keep_charge_limit_alive()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    _LOGGER.error("Error keeping the charge limit alive: %s", e, exc_info=True)

        if self._charge_limit_expires():
            self._keepalive_task = self.hass.async_create_background_task(
                _keepalive_loop(), "inverter_charge_night_charge_limit_keepalive",
            )
        _LOGGER.debug("Started periodic verification task (interval: %d seconds)",
                     self.config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))

    async def _stop_periodic_verification(self) -> None:
        """Stop the periodic verification task and wait until it has finished.

        Waiting matters: a verification run that already passed its
        ``is_active`` check could otherwise write the night target back to
        the inverter after the window-end reset (finding F11).
        """
        for name in ("_verification_task", "_keepalive_task"):
            task: asyncio.Task[None] | None = getattr(self, name)
            setattr(self, name, None)
            if task is None or task.done():
                continue
            task.cancel()
            if task is asyncio.current_task():
                continue  # cancelling ourselves; the loop exits on its own
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
        if self._ac_charge_limit_write_lock.locked() or self._is_backup_active():
            return
        if self._auto_test_active:
            # The live trial owns its setpoint. Comparing it to the older
            # charging plan cancels valid measurements at every verification.
            # Connection protection still outranks the trial and may end it.
            await self._limit_the_efficiency_finder()
        written = (
            float(self._auto_test_power_w)
            if self._auto_test_active and self._auto_test_power_w is not None
            else self._planned_setpoint_written_w
        )
        if written is None:
            return
        target = self._ac_charge_limit_target()
        if target is None:
            return
        actual_w = self._get_ac_charge_limit_w(target[0])
        if actual_w is None:
            return
        if abs(actual_w - written) <= PLANNED_POWER_WRITE_THRESHOLD_W:
            if self._auto_test_active:
                self._auto_test_limit_confirmed = True
            # The rewrite budget is not refilled here: a Kostal holds the limit
            # for a while and then drops it, and every refill would hand it
            # two more writes (Codex on #36). Only a new window starts over.
            return
        if actual_w < written:
            if self._auto_test_active:
                # Hardware I/O can finish before the HA number publishes the
                # new setpoint. Give initial feedback the existing settling
                # interval, then reject it; never record an unconfirmed trial.
                # Once matched, a lower value is real drift and ends it now.
                if not self._auto_test_limit_confirmed and self._auto_test_start is not None:
                    pending_s = elapsed_seconds(self._efficiency_now(), self._auto_test_start)
                    if 0 <= pending_s < AUTO_TEST_SETTLE_S:
                        return
                self._discard_auto_test("inverter limit fell below the efficiency trial setpoint")
                return
            # Someone or something is charging less than we asked for. That is
            # never a danger, so it is noted and left alone.
            _LOGGER.debug(
                "AC charge limit is %.0f W, below the %.0f W we wrote - leaving it",
                actual_w,
                written,
            )
            return
        self._suspend_grid_switch_if_on()
        if self._grid_switch_suspended or self._grid_limit_configured():
            await self._pause_grid_charge("inverter readback exceeds the protective charge limit")
        if self._battery_idle_on_grid():
            _LOGGER.debug(
                "The inverter holds an AC charge limit of %.0f W, not %.0f W, but the battery "
                "cannot draw from the grid - not writing it again",
                actual_w,
                written,
            )
            return
        if self._ac_limit_rewrites >= AC_LIMIT_MAX_REWRITES:
            self._report_ac_limit_not_held(target[0], actual_w, written)
            return
        latest = (float(self._auto_test_power_w)
                  if self._auto_test_active and self._auto_test_power_w is not None
                  else self._planned_setpoint_written_w)
        if self._ac_charge_limit_write_lock.locked() or latest != written:
            # Another writer got in during the pause: nothing is rewritten here,
            # so nothing is spent (Codex on #36)
            return
        self._ac_limit_rewrites += 1
        self._persist_state()
        _LOGGER.warning(
            "The inverter holds an AC charge limit of %.0f W, not the %.0f W this "
            "integration wrote - writing it again (%d of %d). If this repeats, the inverter "
            "is not accepting external control, and the house connection limit cannot protect it",
            actual_w,
            written,
            self._ac_limit_rewrites,
            AC_LIMIT_MAX_REWRITES,
        )
        await self._set_ac_charge_limit_w(int(written))

    def _window_identity(self, now: datetime) -> str | None:
        """Name the window that is starting: an ad-hoc deadline or the scheduled end."""
        if self._adhoc_until is not None:
            return f"adhoc:{self._adhoc_until.isoformat()}"
        bounds = scheduled_window_bounds(now, *self._window_times())
        return f"scheduled:{bounds[1].isoformat()}" if bounds is not None else None

    def _ac_limit_issue_id(self) -> str:
        return f"charge_limit_not_held_{self.entry.entry_id}"

    def _report_ac_limit_not_held(self, entity_id: str, actual_w: float, written: float) -> None:
        """Stop rewriting a limit the inverter drops, once, and say so.

        Grid charging stays blocked: its activation requires a readback at or
        below the protective limit, which this inverter does not give.
        """
        if self._ac_limit_not_held:
            return
        self._ac_limit_not_held = True
        self._persist_state()
        _LOGGER.error(
            "The inverter keeps %.0f W on %s after %d writes of %.0f W - no further writes "
            "this window. The house connection limit cannot protect it",
            actual_w,
            entity_id,
            self._ac_limit_rewrites,
            written,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._ac_limit_issue_id(),
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="charge_limit_not_held",
            translation_placeholders={
                "name": str(self.entry.title),
                "entity": entity_id,
                "written": f"{written:.0f}",
                "actual": f"{actual_w:.0f}",
            },
        )

    def _clear_ac_limit_not_held(self) -> None:
        """Give every window its own bounded rewrites."""
        self._ac_limit_rewrites = 0
        self._grid_switch_suspended = False
        if not self._ac_limit_not_held:
            return
        self._ac_limit_not_held = False
        ir.async_delete_issue(self.hass, DOMAIN, self._ac_limit_issue_id())

    async def _verify_and_restore_min_soc(self) -> None:
        """Finish in-flight writes before a reset and reject unloading callbacks."""
        async with self._min_soc_write_lock:
            await self._verify_and_restore_min_soc_locked()

    async def _verify_and_restore_min_soc_locked(self) -> None:
        """Verify inverter min SOC matches our target and restore if needed."""
        if not self.is_active or not self.is_enabled or self._ending or self._unloading:
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

            floor_soc = self._charging_floor_soc(target_soc)
            if floor_soc is None:
                return

            min_soc_entity = self.config.get(CONF_MIN_SOC_ENTITY)
            if not min_soc_entity:
                return

            state = self.hass.states.get(min_soc_entity)
            if not state or state.state in ("unknown", "unavailable"):
                _LOGGER.debug("Cannot verify min SOC - entity unavailable")
                return

            current_inverter_soc = _as_float(state.state)
            if current_inverter_soc is None:
                _LOGGER.debug("Cannot verify min SOC - %s is not a usable number", min_soc_entity)
                return

            # Not without the planned limit: the poll that could not write it
            # held the floor back, and so does this (Codex on #38)
            held_back = (self._plan_limit_write_failed and floor_soc > current_inverter_soc + 0.5
                         and floor_soc > (self._current_battery_soc() or 0.0))
            if held_back:
                _LOGGER.debug("Planned charge limit not written - not raising the min SOC here either")
            # Check if inverter min SOC doesn't match the floor (with tolerance)
            elif abs(current_inverter_soc - floor_soc) > 0.5:
                _LOGGER.warning(
                    "Inverter min SOC (%.1f%%) doesn't match our target (%.1f%%), restoring to target",
                    current_inverter_soc,
                    floor_soc
                )
                # The window may have ended while this run was waiting
                if not self.is_active or self._unloading:
                    return
                # First write of the window (entity was unavailable at window
                # start): remember what to restore before overwriting it
                self._capture_original_min_soc(current_inverter_soc)
                # Restore to our target value
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": min_soc_entity, "value": floor_soc},
                    blocking=True,
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
        for key in (CONF_MAIN_FUSE_A, CONF_GRID_MAX_CONTINUOUS_W):
            raw = self.config.get(key)
            if raw is not None and raw != "":
                value = _as_float(raw)
                if value is None or value <= 0:
                    self._grid_budget_cache_w = 0.0
                    return 0.0  # Corruption must not turn an opted-in guard off.
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
                    return elapsed_seconds(now, stamp)
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
        if age_s is None or not 0 <= age_s <= GRID_LIMIT_STALE_AFTER_S:
            _LOGGER.debug("Grid import %s has untrusted age %s - treating it as unknown", entity_id, age_s)
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
        own share, so the limit errs towards charging less. Without a fresh,
        correctly dimensioned measurement no own draw is credited: a setpoint
        is a ceiling, not evidence of actual consumption.
        """
        written = self._planned_setpoint_written_w
        measured: float | None = None
        sent_entity = self.config.get(CONF_CHARGE_POWER_SENT_ENTITY)
        if sent_entity:
            state = self.hass.states.get(str(sent_entity))
            age_s = self._state_age_s(state, dt_util.now()) if state else None
            if (age_s is not None and 0 <= age_s <= GRID_LIMIT_STALE_AFTER_S
                    and _unit_of(state) in ("w", "watt", "watts", "kw", "kilowatt", "kilowatts")):
                value = self._get_power_w(sent_entity)
                if value is not None and value >= 0:
                    measured = value
        if measured is None:
            return 0.0
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
        if self._battery_idle_on_grid():
            _LOGGER.debug("House connection limit: the battery cannot draw from the grid - not writing")
            return
        reference = self._planned_setpoint_written_w
        if reference is None:
            reference = float(self.config.get(CONF_MAX_CHARGE_POWER_W, DEFAULT_MAX_CHARGE_POWER_W))
        allowed = self._grid_limited_setpoint(reference)
        if allowed >= reference - PLANNED_POWER_WRITE_THRESHOLD_W:
            return
        if allowed > 0 and self._grid_write_is_debounced():
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
        # an unchanged load does not write the same value over and over. The
        # setter recorded it - the control's maximum if it was lower.
        self.planned_charge_power_w = self._planned_setpoint_written_w
        self.planned_power_is_applied = True

    def _grid_write_is_debounced(self) -> bool:
        """True while the last charge-limit write is too recent to follow up.

        A power sensor reports every few seconds; without this the inverter
        would be written to on every reading for as long as the limit engages.
        """
        last_write = self._grid_limit_last_write
        if last_write is None:
            return False
        age_s = elapsed_seconds(dt_util.now(), last_write)
        if not 0 <= age_s < GRID_LIMIT_MIN_WRITE_INTERVAL_S:
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
            # Blind: even minimum charging could overload an occupied connection.
            if not self._grid_stale_logged:
                _LOGGER.warning(
                    "Grid import %s is untrusted - pausing grid charging at "
                    "0 W until a fresh measurement reports again",
                    entity_id,
                )
                self._grid_stale_logged = True
            self._grid_allowed_w = None
            self._grid_other_load_w = None
            self._grid_limited = True
            return 0.0

        self._grid_stale_logged = False
        headroom = _as_float(self.config.get(CONF_GRID_HEADROOM_W, DEFAULT_GRID_HEADROOM_W))
        own_w = min(self._own_charge_draw_w(), max(0.0, import_w))
        if not self.config.get(CONF_CHARGE_POWER_SENT_ENTITY) and not self._grid_own_draw_logged:
            # Treat all import as foreign load until actual own draw is measured.
            _LOGGER.info(
                "No measured charge-power sensor: all grid import counts as foreign load. "
                "Configure the charge power sensor to measure the battery's actual draw"
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
        # Releasing the battery cannot wait behind a recent nonzero setpoint.
        if limited > 0 and self._grid_write_is_debounced():
            return
        _LOGGER.info(
            "House connection limit: lowering the charge power from %.0f W to %.0f W",
            written,
            limited,
        )
        if not await self._set_ac_charge_limit_w(int(limited)):
            return
        self.planned_charge_power_w = self._planned_setpoint_written_w
        self.planned_power_is_applied = True
