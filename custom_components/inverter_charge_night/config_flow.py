"""Config flow for Inverter Charge Night integration.

The guided setup uses a menu, short independently editable forms and durable
drafts. Initial setup, reconfigure and options share the same pages. The former
four-step entry points remain available to finish already-open legacy flows.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from uuid import uuid4
from typing import TYPE_CHECKING, Any, cast

# Current HA (Python 3.14) aliases voluptuous to probatio at runtime. Static
# checkers cannot see that alias; use its real types for the current CI leg.
# Keep the runtime import compatible with older HA's voluptuous engine.
if TYPE_CHECKING and sys.version_info >= (3, 14):
    import probatio as vol
else:
    import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigEntryBaseFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_AUTO_PV_SHORTFALL,
    CONF_PV_ENERGY_TODAY_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_RANGE_YEARLY,
    CONF_ACTIVE_START_DATE,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_BACKUP_MODE_ENTITY,
    CONF_BACKUP_MODE_STATES,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BRIDGE_RESERVE_KWH,
    CONF_SAFETY_BUFFER_KWH,
    CONF_LOAD_SAFETY_LEVEL,
    CONF_WEATHER_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_HEATING_LIMIT_C,
    CONF_BRIDGE_MODEL,
    CONF_CHARGE_EFFICIENCY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_ENERGY_RECEIVED_ENTITY,
    CONF_CHARGE_ENERGY_SENT_ENTITY,
    CONF_CHARGE_POWER_HOUSE_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DAY_PRICE_CT,
    CONF_DEFAULT_MIN_SOC,
    CONF_DISCHARGE_BLOCK_MODE,
    CONF_DISCHARGE_BLOCK_SWITCH,
    CONF_DISCHARGE_EFFICIENCY,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_END_TIME,
    CONF_FEED_IN_PRICE_CT,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_HIGH_PRICE_END,
    CONF_HIGH_PRICE_MARGIN_PCT,
    CONF_HIGH_PRICE_START,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_NIGHT_PRICE_CT,
    CONF_CURTAILMENT_FEED_IN_ENTITY,
    CONF_CURTAILMENT_LIMIT_W,
    CONF_PRICE_ENTITY,
    CONF_PRICE_SURCHARGE_CT,
    CONF_PRICE_SURCHARGE_WINDOW_CT,
    CONF_PRICE_UNIT,
    CONF_OPERATION_MODE,
    CONF_PLANNER_MODE,
    CONF_PV_CROSSOVER_DELAY_MIN,
    CONF_PV_FORECAST_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_START_TIME,
    CONF_UPDATE_INTERVAL,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    DEFAULT_ACTIVE_RANGE_YEARLY,
    DEFAULT_AVG_HOUSE_LOAD_KW,
    DEFAULT_BRIDGE_RESERVE_KWH,
    DEFAULT_SAFETY_BUFFER_KWH,
    DEFAULT_LOAD_SAFETY_LEVEL,
    LOAD_SAFETY_LEVELS,
    DEFAULT_HEATING_LIMIT_C,
    DEFAULT_BRIDGE_MODEL,
    BRIDGE_MODELS,
    DEFAULT_CHARGE_EFFICIENCY,
    DEFAULT_DISCHARGE_EFFICIENCY,
    DEFAULT_COMMAND_DELAY,
    DEFAULT_DISCHARGE_BLOCK_MODE,
    DEFAULT_END_TIME,
    DEFAULT_FORECAST_ERROR_MARGIN,
    DEFAULT_HIGH_PRICE_MARGIN_PCT,
    DEFAULT_MAX_CHARGE_POWER_W,
    DEFAULT_MAX_SOC,
    DEFAULT_MIN_CHARGE_POWER_W,
    DEFAULT_MIN_SOC,
    DEFAULT_OPERATION_MODE,
    DEFAULT_PLANNER_MODE,
    DEFAULT_PRICE_SURCHARGE_CT,
    DEFAULT_PRICE_UNIT,
    DEFAULT_PV_CROSSOVER_DELAY_MIN,
    DEFAULT_START_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DISCHARGE_BLOCK_AUTO,
    DISCHARGE_BLOCK_OFF,
    DOMAIN,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
    PLANNER_MODE_BRIDGE,
    PRICE_UNIT_AUTO,
    PRICE_UNIT_CT_KWH,
    PRICE_UNIT_EUR_KWH,
    PRICE_UNIT_EUR_MWH,
    PLANNER_MODE_HEADROOM,
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
)
from .util import parse_time_str
from .setup_drafts import get_drafts

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Inverter Charge Night"
DEFAULT_BATTERY_CAPACITY = 10.0

# Keys collected by each wizard step. Every stored config key belongs to
# exactly one step; the strings.json step of the same name labels it.
STEP_USER_KEYS: tuple[str, ...] = (
    CONF_NAME,
    CONF_OPERATION_MODE,
    CONF_MIN_SOC_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_PV_FORECAST_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_CAPACITY,
)
STEP_TIME_SOC_KEYS: tuple[str, ...] = (
    CONF_START_TIME,
    CONF_END_TIME,
    CONF_USER_MIN_SOC,
    CONF_USER_MAX_SOC,
    CONF_DEFAULT_MIN_SOC,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_PLANNER_MODE,
    CONF_HIGH_PRICE_START,
    CONF_HIGH_PRICE_END,
    CONF_HIGH_PRICE_MARGIN_PCT,
)
STEP_POWER_KEYS: tuple[str, ...] = (
    CONF_MIN_CHARGE_POWER_W,
    CONF_MAX_CHARGE_POWER_W,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_HOUSE_ENTITY,
    CONF_CHARGE_ENERGY_SENT_ENTITY,
    CONF_CHARGE_ENERGY_RECEIVED_ENTITY,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_DISCHARGE_LIMIT_ENTITY,
    # House connection limit (plan 008)
    CONF_GRID_IMPORT_ENTITY,
    CONF_MAIN_FUSE_A,
    CONF_GRID_PHASES,
    CONF_GRID_VOLTAGE_V,
    CONF_GRID_CONTINUOUS_PCT,
    CONF_GRID_MAX_CONTINUOUS_W,
    CONF_GRID_HEADROOM_W,
    CONF_DISCHARGE_BLOCK_SWITCH,
    CONF_DISCHARGE_BLOCK_MODE,
)
STEP_ADVANCED_KEYS: tuple[str, ...] = (
    CONF_AUTO_PV_SHORTFALL,
    CONF_PV_ENERGY_TODAY_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_COMMAND_DELAY,
    CONF_ACTIVE_START_DATE,
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_RANGE_YEARLY,
    CONF_BACKUP_MODE_ENTITY,
    CONF_BACKUP_MODE_STATES,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_PV_CROSSOVER_DELAY_MIN,
    CONF_BRIDGE_RESERVE_KWH,
    CONF_SAFETY_BUFFER_KWH,
    CONF_LOAD_SAFETY_LEVEL,
    CONF_WEATHER_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_HEATING_LIMIT_C,
    CONF_BRIDGE_MODEL,
    CONF_CHARGE_EFFICIENCY,
    CONF_DISCHARGE_EFFICIENCY,
    CONF_NIGHT_PRICE_CT,
    CONF_PRICE_ENTITY,
    CONF_PRICE_UNIT,
    CONF_PRICE_SURCHARGE_CT,
    CONF_PRICE_SURCHARGE_WINDOW_CT,
    CONF_DAY_PRICE_CT,
    CONF_FEED_IN_PRICE_CT,
    CONF_CURTAILMENT_LIMIT_W,
    CONF_CURTAILMENT_FEED_IN_ENTITY,
)
# Every key the wizard owns; anything else in entry.data is written at runtime.
_ALL_STEP_KEYS: frozenset[str] = frozenset(
    STEP_USER_KEYS + STEP_TIME_SOC_KEYS + STEP_POWER_KEYS + STEP_ADVANCED_KEYS
)
# The three tariffs are only meaningful together: the planner compares them.
_PRICE_KEYS: tuple[str, ...] = (CONF_NIGHT_PRICE_CT, CONF_DAY_PRICE_CT, CONF_FEED_IN_PRICE_CT)

# Number selectors return floats; these keys were always stored as integers.
_INT_KEYS: tuple[str, ...] = (
    CONF_UPDATE_INTERVAL,
    CONF_MIN_CHARGE_POWER_W,
    CONF_MAX_CHARGE_POWER_W,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_PV_CROSSOVER_DELAY_MIN,
    CONF_GRID_PHASES,
    CONF_GRID_MAX_CONTINUOUS_W,
    CONF_GRID_HEADROOM_W,
)

SchemaBuilder = Callable[[Mapping[str, Any]], vol.Schema]
NextStep = Callable[[], Awaitable[ConfigFlowResult]]


def validate_soc(value: float) -> bool:
    """Validate SOC value is between 0 and 100."""
    return 0.0 <= value <= 100.0


def validate_date_optional(value: str | date | None) -> bool:
    """Validate optional date format YYYY-MM-DD (allow empty)."""
    # Spelled out rather than ``value in (None, "")``: only mypy 2.x narrows the
    # membership form, so the older versions requirements-dev.txt still allows
    # see a ``str | None`` reach date.fromisoformat below.
    if value is None or value == "":
        return True
    if isinstance(value, date):
        return True
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


def _normalize_date_value(value: str | date | None) -> str | None:
    """Normalize date selector values to ISO strings for storage."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        parsed = date.fromisoformat(value)
        return parsed.isoformat()
    except (ValueError, TypeError):
        return None


# Only entity ids belong here: each one is looked up in the registry and then
# in the state machine, and Home Assistant lowercases what it is given. A plain
# number in this list aborts the whole config flow with an AttributeError.
_ENTITY_KEYS_TO_VALIDATE = [
    CONF_PV_ENERGY_TODAY_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_MIN_SOC_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_PV_FORECAST_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BACKUP_MODE_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_HOUSE_ENTITY,
    CONF_CHARGE_ENERGY_SENT_ENTITY,
    CONF_CHARGE_ENERGY_RECEIVED_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_GRID_IMPORT_ENTITY,
    CONF_DISCHARGE_BLOCK_SWITCH,
    CONF_CURTAILMENT_FEED_IN_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
]


def _validate_user_input(
    user_input: dict[str, Any], hass: HomeAssistant
) -> dict[str, str]:
    """Validate user input and return error dict (shared by initial and options flow)."""
    errors: dict[str, str] = {}

    start_time = parse_time_str(user_input.get(CONF_START_TIME, ""))
    end_time = parse_time_str(user_input.get(CONF_END_TIME, ""))
    if start_time is None:
        errors[CONF_START_TIME] = "invalid_time"
    if end_time is None:
        errors[CONF_END_TIME] = "invalid_time"
    elif start_time == end_time:
        # Compare parsed values so that e.g. "2:00" and "02:00" count as equal
        errors[CONF_END_TIME] = "start_end_time_must_differ"

    # The high-price period is optional, and only a pair of times is a period.
    high_start_raw = user_input.get(CONF_HIGH_PRICE_START)
    high_end_raw = user_input.get(CONF_HIGH_PRICE_END)
    high_start = parse_time_str(high_start_raw) if high_start_raw else None
    high_end = parse_time_str(high_end_raw) if high_end_raw else None
    if high_start_raw and high_start is None:
        errors[CONF_HIGH_PRICE_START] = "invalid_time"
    if high_end_raw and high_end is None:
        errors[CONF_HIGH_PRICE_END] = "invalid_time"
    if bool(high_start_raw) != bool(high_end_raw):
        # One end alone says when it starts but never when it is over, which
        # would leave the reserve either endless or zero.
        errors[CONF_HIGH_PRICE_END if high_start_raw else CONF_HIGH_PRICE_START] = (
            "high_price_needs_both_times"
        )
    elif high_start is not None and high_start == high_end:
        errors[CONF_HIGH_PRICE_END] = "start_end_time_must_differ"

    user_min_soc = user_input.get(CONF_USER_MIN_SOC, 0)
    user_max_soc = user_input.get(CONF_USER_MAX_SOC, 0)
    if not validate_soc(user_min_soc):
        errors[CONF_USER_MIN_SOC] = "invalid_soc"
    if not validate_soc(user_max_soc):
        errors[CONF_USER_MAX_SOC] = "invalid_soc"
    if user_min_soc >= user_max_soc:
        errors[CONF_USER_MAX_SOC] = "max_soc_must_be_greater_than_min"
    if user_input.get(CONF_BATTERY_CAPACITY, 0) <= 0:
        errors[CONF_BATTERY_CAPACITY] = "invalid_capacity"
    if not validate_soc(user_input.get(CONF_DEFAULT_MIN_SOC, 0)):
        errors[CONF_DEFAULT_MIN_SOC] = "invalid_soc"
    if not validate_date_optional(user_input.get(CONF_ACTIVE_START_DATE)):
        errors[CONF_ACTIVE_START_DATE] = "invalid_date"
    if not validate_date_optional(user_input.get(CONF_ACTIVE_END_DATE)):
        errors[CONF_ACTIVE_END_DATE] = "invalid_date"

    min_power = user_input.get(CONF_MIN_CHARGE_POWER_W, 0)
    max_power = user_input.get(CONF_MAX_CHARGE_POWER_W, 0)
    if min_power <= 0:
        errors[CONF_MIN_CHARGE_POWER_W] = "invalid_power"
    if max_power <= 0:
        errors[CONF_MAX_CHARGE_POWER_W] = "invalid_power"
    if min_power >= max_power:
        errors[CONF_MAX_CHARGE_POWER_W] = "max_power_must_be_greater_than_min"

    abs_max_power = user_input.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_W)
    abs_max_entity = user_input.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY)
    if abs_max_power is not None:
        if abs_max_power <= 0:
            errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_W] = "invalid_power"
        if not abs_max_entity:
            errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] = "required_entity"
    elif abs_max_entity:
        errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_W] = "required_value"

    meters = [
        key
        for key in (CONF_CHARGE_ENERGY_SENT_ENTITY, CONF_CHARGE_ENERGY_RECEIVED_ENTITY)
        if user_input.get(key)
    ]
    if len(meters) == 1:
        # One meter alone measures nothing: the loss is the difference of two.
        missing = (
            CONF_CHARGE_ENERGY_RECEIVED_ENTITY
            if meters[0] == CONF_CHARGE_ENERGY_SENT_ENTITY
            else CONF_CHARGE_ENERGY_SENT_ENTITY
        )
        errors[missing] = "required_entity"

    # Morning discharge only discharges through this switch. Without it the mode
    # raises the min SOC floor and turns grid charging off - and then waits, night
    # after night, for a discharge that cannot start. The switch is genuinely
    # optional in night charge mode, so the requirement is tied to the mode.
    if user_input.get(CONF_OPERATION_MODE) == MODE_MORNING_DISCHARGE and not user_input.get(
        CONF_FORCE_DISCHARGE_SWITCH
    ):
        errors[CONF_FORCE_DISCHARGE_SWITCH] = "required_for_discharge_mode"

    if user_input.get(CONF_AUTO_EFFICIENT_CHARGE):
        if not user_input.get(CONF_CHARGE_POWER_ENTITY):
            errors[CONF_CHARGE_POWER_ENTITY] = "required_entity"
        if not user_input.get(CONF_CHARGE_POWER_SENT_ENTITY) and not user_input.get(
            CONF_CHARGE_POWER_HOUSE_ENTITY
        ):
            # The AC side: a sensor of its own, or grid import minus house
            # consumption (plan 017)
            errors[CONF_CHARGE_POWER_SENT_ENTITY] = "ac_side_required"
        if not user_input.get(CONF_CHARGE_POWER_RECEIVED_ENTITY):
            errors[CONF_CHARGE_POWER_RECEIVED_ENTITY] = "required_entity"
    house = user_input.get(CONF_CHARGE_POWER_HOUSE_ENTITY)
    if house:
        if not user_input.get(CONF_GRID_IMPORT_ENTITY):
            errors[CONF_GRID_IMPORT_ENTITY] = "required_entity"
        if user_input.get(CONF_CHARGE_POWER_SENT_ENTITY):
            errors[CONF_CHARGE_POWER_HOUSE_ENTITY] = "ac_side_twice"
        if house in (user_input.get(CONF_GRID_IMPORT_ENTITY),
                     user_input.get(CONF_CHARGE_POWER_RECEIVED_ENTITY)):
            errors[CONF_CHARGE_POWER_HOUSE_ENTITY] = "separate_power_measurements"

    # Planner v2 inputs (plan 006); all optional in the sense that defaults exist
    avg_load = user_input.get(CONF_AVG_HOUSE_LOAD_KW)
    if avg_load is not None and avg_load <= 0:
        errors[CONF_AVG_HOUSE_LOAD_KW] = "invalid_power"
    efficiency = user_input.get(CONF_CHARGE_EFFICIENCY)
    if efficiency is not None and not 0 < efficiency <= 1:
        errors[CONF_CHARGE_EFFICIENCY] = "invalid_efficiency"
    # House connection limit (plan 008). The limit only ever charges less, but a
    # nonsensical value should be caught here rather than silently corrected.
    phases = user_input.get(CONF_GRID_PHASES)
    if phases is not None and int(phases) not in (1, 3):
        errors[CONF_GRID_PHASES] = "invalid_phases"
    fuse_a = user_input.get(CONF_MAIN_FUSE_A)
    if fuse_a is not None and fuse_a <= 0:
        errors[CONF_MAIN_FUSE_A] = "invalid_current"
    grid_max_w = user_input.get(CONF_GRID_MAX_CONTINUOUS_W)
    if grid_max_w is not None and grid_max_w <= 0:
        errors[CONF_GRID_MAX_CONTINUOUS_W] = "invalid_power"
    grid_headroom = user_input.get(CONF_GRID_HEADROOM_W)
    if grid_headroom is not None and grid_headroom < 0:
        errors[CONF_GRID_HEADROOM_W] = "invalid_power"
    voltage = user_input.get(CONF_GRID_VOLTAGE_V)
    if voltage is not None and voltage <= 0:
        errors[CONF_GRID_VOLTAGE_V] = "invalid_voltage"
    pct = user_input.get(CONF_GRID_CONTINUOUS_PCT)
    if pct is not None and not 0 < pct <= 100:
        errors[CONF_GRID_CONTINUOUS_PCT] = "invalid_soc"
    if user_input.get(CONF_GRID_IMPORT_ENTITY):
        if grid_max_w is None and fuse_a is None and not house:
            # Without a budget the entity would be read but never act - unless
            # the efficiency search reads it for the AC side (plan 017)
            errors[CONF_MAIN_FUSE_A] = "required_value"
        if (grid_max_w is not None or fuse_a is not None) and not user_input.get(
            CONF_CHARGE_POWER_ENTITY
        ):
            # The limit works by writing a lower charge setpoint. Without that
            # entity there is nothing to write to and the protection would be
            # configured but inert - which is worse than not offering it. A
            # meter read only for the measurement writes nothing (CodeRabbit
            # on #45).
            errors[CONF_CHARGE_POWER_ENTITY] = "required_entity"

    prices_set = [key for key in _PRICE_KEYS if user_input.get(key) is not None]
    # With a price entity the night and day prices come from it, and only the
    # feed-in tariff is left that no price source publishes. So the feed-in
    # price on its own is a complete setup there - and demanding the two fixed
    # ones as well would make users invent fallback numbers before the entity
    # could decide anything. Every other partial combination is still refused:
    # a lone night or day price would be a fallback that never takes effect,
    # because the planner only ever uses the fixed night and day as a pair.
    feed_in_only_with_entity = bool(user_input.get(CONF_PRICE_ENTITY)) and prices_set == [
        CONF_FEED_IN_PRICE_CT
    ]
    if prices_set and len(prices_set) != len(_PRICE_KEYS) and not feed_in_only_with_entity:
        for key in _PRICE_KEYS:
            if key not in prices_set:
                errors[key] = "all_prices_required"

    if user_input.get(CONF_AUTO_PV_SHORTFALL):
        for key in (CONF_PV_FORECAST_TODAY_ENTITY, CONF_PV_ENERGY_TODAY_ENTITY):
            if not user_input.get(key):
                errors[key] = "required_entity"

    # Validate entities exist
    entity_registry = er.async_get(hass)
    for entity_key in _ENTITY_KEYS_TO_VALIDATE:
        entity_id = user_input.get(entity_key)
        if entity_id:
            entity = entity_registry.async_get(entity_id)
            if not entity:
                if not hass.states.get(entity_id):
                    errors[entity_key] = "invalid_entity"

    for key in (CONF_PV_FORECAST_ENTITY, CONF_PV_FORECAST_TODAY_ENTITY):
        entity_id = user_input.get(key)
        entity = entity_registry.async_get(entity_id) if entity_id else None
        if entity is not None and entity.platform == DOMAIN:
            errors[key] = "forecast_is_planner_output"

    # A percent-based active-power command is not a battery watt ceiling.
    for key in (CONF_CHARGE_POWER_ENTITY, CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY, CONF_DISCHARGE_LIMIT_ENTITY):
        entity_id = user_input.get(key)
        state = hass.states.get(entity_id) if entity_id else None
        if state is not None:
            unit = state.attributes.get("unit_of_measurement")
            allowed = {"W", "kW"}
            if unit and unit not in allowed:
                errors[key] = "power_control_requires_watts"
    sent = user_input.get(CONF_CHARGE_POWER_SENT_ENTITY)
    received = user_input.get(CONF_CHARGE_POWER_RECEIVED_ENTITY)
    if sent and sent == received:
        errors[CONF_CHARGE_POWER_RECEIVED_ENTITY] = "separate_power_measurements"
    if abs_max_entity and abs_max_entity == user_input.get(CONF_CHARGE_POWER_ENTITY):
        errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] = "separate_power_controls"
    if sent:
        entity = entity_registry.async_get(sent)
        # Neither is the charger's AC input. Total AC power includes the
        # house; "Grid to Battery Power" is the inverter's split of the
        # battery's DC power and read a ratio of exactly -1 against the battery
        # power all night (24/25 October 2026, plan 017).
        if entity is not None and entity.platform == "kostal_kore" and entity.unique_id.endswith(
            ("_devices:local:ac_P", "_devices:local_Ac_P", "_devices:local_Grid2Bat_P")
        ):
            errors[CONF_CHARGE_POWER_SENT_ENTITY] = "requires_charge_input_power"

    return errors


def _grid_limit_set(data: Mapping[str, Any]) -> bool:
    """The house connection limit is on: a grid import *and* a budget.

    A grid import alone may only serve the efficiency measurement (plan 017).
    """
    return bool(data.get(CONF_GRID_IMPORT_ENTITY)) and (
        data.get(CONF_GRID_MAX_CONTINUOUS_W) is not None or data.get(CONF_MAIN_FUSE_A) is not None
    )


def _errors_for(step_keys: tuple[str, ...], errors: Mapping[str, str]) -> dict[str, str]:
    """Keep only the errors that belong to fields of the given step."""
    return {key: reason for key, reason in errors.items() if key in step_keys}


def _process_step(
    hass: HomeAssistant,
    data: dict[str, Any],
    step_keys: tuple[str, ...],
    user_input: Mapping[str, Any],
) -> dict[str, str]:
    """Validate one step's input against everything collected so far.

    On success the step's fields are merged into ``data``; optional fields the
    user cleared (absent from ``user_input``) are removed so that clearing a
    field in the reconfigure or options flow actually takes effect.
    """
    # Validate exactly what is about to be saved. A field of this step that the
    # user cleared is absent from user_input but still in data, and validating
    # {**data, **user_input} judged the entry by a value it was about to drop:
    # clear the price entity in the options flow, keep the feed-in price, and
    # the rule "feed-in alone needs an entity" passed on the entity that was
    # being removed. The same held for every rule that weighs one field of a
    # step against another - clearing the night price passed "all three prices
    # or none" on the old night price, and saved two of the three.
    candidate = {key: value for key, value in data.items() if key not in step_keys}
    candidate.update(user_input)
    errors = _errors_for(step_keys, _validate_user_input(candidate, hass))
    if errors:
        return errors
    for key in step_keys:
        if key in user_input:
            data[key] = user_input[key]
        else:
            data.pop(key, None)
    return errors


def _finalize_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize collected data before it is stored in the config entry."""
    result = dict(data)
    result[CONF_ACTIVE_START_DATE] = _normalize_date_value(result.get(CONF_ACTIVE_START_DATE))
    result[CONF_ACTIVE_END_DATE] = _normalize_date_value(result.get(CONF_ACTIVE_END_DATE))
    for key in _INT_KEYS:
        if result.get(key) is not None:
            result[key] = int(result[key])
    return result


# ---------------------------------------------------------------------------
# Selector helpers
# ---------------------------------------------------------------------------


def _entity_selector(
    domain: str | list[str] | None = None, device_class: str | None = None
) -> Any:
    """Entity picker, optionally limited to a domain and device class."""
    config = selector.EntitySelectorConfig()
    if domain:
        config["domain"] = domain
    if device_class:
        config["device_class"] = device_class
    return cast(Any, selector.EntitySelector(config))


def _number_selector(
    min_value: float,
    max_value: float,
    step: float,
    unit: str | None = None,
    mode: selector.NumberSelectorMode = selector.NumberSelectorMode.BOX,
) -> Any:
    """Number input with unit and bounds (box or slider)."""
    config = selector.NumberSelectorConfig(min=min_value, max=max_value, step=step, mode=mode)
    if unit:
        config["unit_of_measurement"] = unit
    return cast(Any, selector.NumberSelector(config))


def _time_selector() -> Any:
    return cast(Any, selector.TimeSelector())


def _price_unit_selector() -> Any:
    """What the price entity's numbers mean, when nothing else says."""
    return cast(
        Any,
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    PRICE_UNIT_AUTO,
                    PRICE_UNIT_CT_KWH,
                    PRICE_UNIT_EUR_KWH,
                    PRICE_UNIT_EUR_MWH,
                ],
                translation_key="price_unit",
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
    )


def _date_selector() -> Any:
    return cast(Any, selector.DateSelector())


def _bool_selector() -> Any:
    return cast(Any, selector.BooleanSelector())


def _text_selector() -> Any:
    return cast(Any, selector.TextSelector())


def _mode_selector() -> Any:
    return cast(
        Any,
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[MODE_NIGHT_CHARGE, MODE_MORNING_DISCHARGE],
                translation_key="operation_mode",
            )
        ),
    )


def _bridge_model_selector() -> Any:
    return cast(
        Any,
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(BRIDGE_MODELS),
                translation_key="bridge_model",
            )
        ),
    )


def _load_safety_level_selector() -> Any:
    return cast(
        Any,
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(LOAD_SAFETY_LEVELS),
                translation_key="load_safety_level",
            )
        ),
    )


def _planner_mode_selector() -> Any:
    return cast(
        Any,
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[PLANNER_MODE_HEADROOM, PLANNER_MODE_BRIDGE],
                translation_key="planner_mode",
            )
        ),
    )


def _discharge_block_mode_selector() -> Any:
    return cast(
        Any,
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[DISCHARGE_BLOCK_AUTO, DISCHARGE_BLOCK_OFF],
                translation_key="discharge_block_mode",
            )
        ),
    )


def _required(key: str, defaults: Mapping[str, Any], fallback: Any = None) -> vol.Required:
    """Required field, prefilled from stored data or a constant fallback."""
    value = defaults.get(key)
    if value in (None, ""):
        value = fallback
    if value is None:
        return vol.Required(key)
    return vol.Required(key, default=value)


def _optional(key: str, value: Any) -> vol.Optional:
    """Optional field; a stored value is only *suggested* so it can be cleared."""
    if value in (None, ""):
        return vol.Optional(key)
    return vol.Optional(key, description={"suggested_value": value})


# ---------------------------------------------------------------------------
# Schema builders (one per wizard step)
# ---------------------------------------------------------------------------

_PERCENT: dict[str, Any] = {
    "min_value": 0,
    "max_value": 100,
    "step": 1,
    "unit": "%",
    "mode": selector.NumberSelectorMode.SLIDER,
}
_WATTS: dict[str, Any] = {"min_value": 100, "max_value": 30000, "step": 10, "unit": "W"}
_CONTROL_DOMAINS = ["number", "input_number"]


def _schema_entities(defaults: Mapping[str, Any]) -> vol.Schema:
    """Step 1: name, operation mode, core entities and battery capacity."""
    return vol.Schema(
        {
            _required(CONF_NAME, defaults, DEFAULT_NAME): _text_selector(),
            _required(CONF_OPERATION_MODE, defaults, DEFAULT_OPERATION_MODE): _mode_selector(),
            _required(CONF_MIN_SOC_ENTITY, defaults): _entity_selector("number"),
            _required(CONF_GRID_CHARGE_SWITCH, defaults): _entity_selector("switch"),
            _required(CONF_PV_FORECAST_ENTITY, defaults): _entity_selector("sensor"),
            _optional(
                CONF_PV_FORECAST_TODAY_ENTITY, defaults.get(CONF_PV_FORECAST_TODAY_ENTITY)
            ): _entity_selector("sensor"),
            _required(CONF_BATTERY_SOC_ENTITY, defaults): _entity_selector("sensor", "battery"),
            _required(
                CONF_BATTERY_CAPACITY, defaults, DEFAULT_BATTERY_CAPACITY
            ): _number_selector(0.5, 200, 0.1, "kWh"),
        }
    )


def _schema_time_soc(defaults: Mapping[str, Any]) -> vol.Schema:
    """Step 2: time window and SOC limits."""
    return vol.Schema(
        {
            _required(CONF_START_TIME, defaults, DEFAULT_START_TIME): _time_selector(),
            _required(CONF_END_TIME, defaults, DEFAULT_END_TIME): _time_selector(),
            _required(CONF_USER_MIN_SOC, defaults, DEFAULT_MIN_SOC): _number_selector(**_PERCENT),
            _required(CONF_USER_MAX_SOC, defaults, DEFAULT_MAX_SOC): _number_selector(**_PERCENT),
            _required(CONF_DEFAULT_MIN_SOC, defaults, DEFAULT_MIN_SOC): _number_selector(**_PERCENT),
            _required(
                CONF_FORECAST_ERROR_MARGIN, defaults, DEFAULT_FORECAST_ERROR_MARGIN
            ): _number_selector(**_PERCENT),
            _required(CONF_PLANNER_MODE, defaults, DEFAULT_PLANNER_MODE): _planner_mode_selector(),
            _optional(CONF_HIGH_PRICE_START, defaults.get(CONF_HIGH_PRICE_START)): _time_selector(),
            _optional(CONF_HIGH_PRICE_END, defaults.get(CONF_HIGH_PRICE_END)): _time_selector(),
            _required(
                CONF_HIGH_PRICE_MARGIN_PCT, defaults, DEFAULT_HIGH_PRICE_MARGIN_PCT
            ): _number_selector(0, 100, 1, "%"),
        }
    )


def _schema_power(defaults: Mapping[str, Any]) -> vol.Schema:
    """Step 3: charge power limits and the optional power entities."""
    def dependent(key: str, needed: bool) -> vol.Required | vol.Optional:
        return _required(key, defaults) if needed else _optional(key, defaults.get(key))

    efficiency = bool(defaults.get(CONF_AUTO_EFFICIENT_CHARGE))
    control_needed = efficiency or _grid_limit_set(defaults)
    return vol.Schema(
        {
            vol.Required(CONF_AUTO_EFFICIENT_CHARGE, default=efficiency): _bool_selector(),
            _required(
                CONF_MIN_CHARGE_POWER_W, defaults, DEFAULT_MIN_CHARGE_POWER_W
            ): _number_selector(**_WATTS),
            _required(
                CONF_MAX_CHARGE_POWER_W, defaults, DEFAULT_MAX_CHARGE_POWER_W
            ): _number_selector(**_WATTS),
            _optional(CONF_ABSOLUTE_MAX_CHARGE_POWER_W, defaults.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_W)): _number_selector(**_WATTS),
            _optional(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY, defaults.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY)): _entity_selector(_CONTROL_DOMAINS),
            dependent(CONF_CHARGE_POWER_ENTITY, control_needed): _entity_selector(_CONTROL_DOMAINS),
            _optional(
                CONF_CHARGE_POWER_SENT_ENTITY, defaults.get(CONF_CHARGE_POWER_SENT_ENTITY)
            ): _entity_selector("sensor", "power"),
            _optional(
                CONF_CHARGE_POWER_HOUSE_ENTITY, defaults.get(CONF_CHARGE_POWER_HOUSE_ENTITY)
            ): _entity_selector("sensor", "power"),
            dependent(CONF_CHARGE_POWER_RECEIVED_ENTITY, efficiency): _entity_selector("sensor", "power"),
            _optional(
                CONF_CHARGE_ENERGY_SENT_ENTITY, defaults.get(CONF_CHARGE_ENERGY_SENT_ENTITY)
            ): _entity_selector("sensor", "energy"),
            _optional(
                CONF_CHARGE_ENERGY_RECEIVED_ENTITY, defaults.get(CONF_CHARGE_ENERGY_RECEIVED_ENTITY)
            ): _entity_selector("sensor", "energy"),
            dependent(CONF_FORCE_DISCHARGE_SWITCH, defaults.get(CONF_OPERATION_MODE) == MODE_MORNING_DISCHARGE): _entity_selector("switch"),
            _optional(
                CONF_DISCHARGE_LIMIT_ENTITY, defaults.get(CONF_DISCHARGE_LIMIT_ENTITY)
            ): _entity_selector(_CONTROL_DOMAINS),
            # House connection limit (plan 008)
            _optional(
                CONF_GRID_IMPORT_ENTITY, defaults.get(CONF_GRID_IMPORT_ENTITY)
            ): _entity_selector("sensor", "power"),
            _optional(CONF_MAIN_FUSE_A, defaults.get(CONF_MAIN_FUSE_A)): _number_selector(
                6, 250, 1, "A"
            ),
            _required(CONF_GRID_PHASES, defaults, DEFAULT_GRID_PHASES): _number_selector(1, 3, 2),
            _required(
                CONF_GRID_VOLTAGE_V, defaults, DEFAULT_GRID_VOLTAGE_V
            ): _number_selector(100, 500, 1, "V"),
            _required(
                CONF_GRID_CONTINUOUS_PCT, defaults, DEFAULT_GRID_CONTINUOUS_PCT
            ): _number_selector(**_PERCENT),
            _optional(
                CONF_GRID_MAX_CONTINUOUS_W, defaults.get(CONF_GRID_MAX_CONTINUOUS_W)
            ): _number_selector(500, 100000, 100, "W"),
            _required(
                CONF_GRID_HEADROOM_W, defaults, DEFAULT_GRID_HEADROOM_W
            ): _number_selector(0, 10000, 50, "W"),
            _optional(
                CONF_DISCHARGE_BLOCK_SWITCH, defaults.get(CONF_DISCHARGE_BLOCK_SWITCH)
            ): _entity_selector("switch"),
            _required(
                CONF_DISCHARGE_BLOCK_MODE, defaults, DEFAULT_DISCHARGE_BLOCK_MODE
            ): _discharge_block_mode_selector(),
        }
    )


def _schema_advanced(defaults: Mapping[str, Any]) -> vol.Schema:
    """Step 4: update interval, command delay, active dates and backup mode."""
    return vol.Schema(
        {
            _required(CONF_AUTO_PV_SHORTFALL, defaults, False): _bool_selector(),
            _optional(CONF_PV_ENERGY_TODAY_ENTITY, defaults.get(CONF_PV_ENERGY_TODAY_ENTITY)): _entity_selector("sensor", "energy"),
            _required(
                CONF_UPDATE_INTERVAL, defaults, DEFAULT_UPDATE_INTERVAL
            ): _number_selector(60, 3600, 60, "s", selector.NumberSelectorMode.SLIDER),
            _required(
                CONF_COMMAND_DELAY, defaults, DEFAULT_COMMAND_DELAY
            ): _number_selector(0, 5, 0.1, "s"),
            _optional(
                CONF_ACTIVE_START_DATE, _normalize_date_value(defaults.get(CONF_ACTIVE_START_DATE))
            ): _date_selector(),
            _optional(
                CONF_ACTIVE_END_DATE, _normalize_date_value(defaults.get(CONF_ACTIVE_END_DATE))
            ): _date_selector(),
            _required(
                CONF_ACTIVE_RANGE_YEARLY, defaults, DEFAULT_ACTIVE_RANGE_YEARLY
            ): _bool_selector(),
            _optional(
                CONF_BACKUP_MODE_ENTITY, defaults.get(CONF_BACKUP_MODE_ENTITY)
            ): _entity_selector(["binary_sensor", "switch", "sensor"]),
            _optional(
                CONF_BACKUP_MODE_STATES, defaults.get(CONF_BACKUP_MODE_STATES)
            ): _text_selector(),
            _optional(
                CONF_HOUSE_LOAD_ENTITY, defaults.get(CONF_HOUSE_LOAD_ENTITY)
            ): _entity_selector("sensor", "energy"),
            _required(
                CONF_AVG_HOUSE_LOAD_KW, defaults, DEFAULT_AVG_HOUSE_LOAD_KW
            ): _number_selector(0.05, 20, 0.05, "kW"),
            _required(
                CONF_PV_CROSSOVER_DELAY_MIN, defaults, DEFAULT_PV_CROSSOVER_DELAY_MIN
            ): _number_selector(0, 360, 5, "min"),
            _required(
                CONF_BRIDGE_RESERVE_KWH, defaults, DEFAULT_BRIDGE_RESERVE_KWH
            ): _number_selector(0, 50, 0.1, "kWh"),
            _required(
                CONF_SAFETY_BUFFER_KWH, defaults, DEFAULT_SAFETY_BUFFER_KWH
            ): _number_selector(0, 50, 0.5, "kWh"),
            _required(
                CONF_LOAD_SAFETY_LEVEL, defaults, DEFAULT_LOAD_SAFETY_LEVEL
            ): _load_safety_level_selector(),
            _optional(
                CONF_WEATHER_ENTITY, defaults.get(CONF_WEATHER_ENTITY)
            ): _entity_selector("weather"),
            _optional(
                CONF_OUTDOOR_TEMPERATURE_ENTITY, defaults.get(CONF_OUTDOOR_TEMPERATURE_ENTITY)
            ): _entity_selector("sensor", "temperature"),
            _required(
                CONF_HEATING_LIMIT_C, defaults, DEFAULT_HEATING_LIMIT_C
            ): _number_selector(5, 25, 0.5, "°C"),
            _required(
                CONF_BRIDGE_MODEL, defaults, DEFAULT_BRIDGE_MODEL
            ): _bridge_model_selector(),
            _required(
                CONF_CHARGE_EFFICIENCY, defaults, DEFAULT_CHARGE_EFFICIENCY
            ): _number_selector(0.5, 1.0, 0.01),
            _required(
                CONF_DISCHARGE_EFFICIENCY, defaults, DEFAULT_DISCHARGE_EFFICIENCY
            ): _number_selector(0.5, 1.0, 0.01),
            _optional(CONF_NIGHT_PRICE_CT, defaults.get(CONF_NIGHT_PRICE_CT)): _number_selector(
                0, 200, 0.1, "ct/kWh"
            ),
            _optional(CONF_DAY_PRICE_CT, defaults.get(CONF_DAY_PRICE_CT)): _number_selector(
                0, 200, 0.1, "ct/kWh"
            ),
            _optional(
                CONF_FEED_IN_PRICE_CT, defaults.get(CONF_FEED_IN_PRICE_CT)
            ): _number_selector(0, 200, 0.1, "ct/kWh"),
            _optional(CONF_PRICE_ENTITY, defaults.get(CONF_PRICE_ENTITY)): _entity_selector(
                "sensor"
            ),
            _required(CONF_PRICE_UNIT, defaults, DEFAULT_PRICE_UNIT): _price_unit_selector(),
            _required(
                CONF_PRICE_SURCHARGE_CT, defaults, DEFAULT_PRICE_SURCHARGE_CT
            ): _number_selector(0, 100, 0.1, "ct/kWh"),
            _optional(
                CONF_PRICE_SURCHARGE_WINDOW_CT, defaults.get(CONF_PRICE_SURCHARGE_WINDOW_CT)
            ): _number_selector(0, 100, 0.1, "ct/kWh"),
            _optional(
                CONF_CURTAILMENT_LIMIT_W, defaults.get(CONF_CURTAILMENT_LIMIT_W)
            ): _number_selector(100, 100000, 100, "W"),
            _optional(
                CONF_CURTAILMENT_FEED_IN_ENTITY, defaults.get(CONF_CURTAILMENT_FEED_IN_ENTITY)
            ): _entity_selector("sensor", "power"),
        }
    )


# ---------------------------------------------------------------------------
# Config flow (initial setup + reconfigure), four steps each
# ---------------------------------------------------------------------------


def _review_placeholders(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, str]:
    capacity = float(data.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY))
    floor = float(data.get(CONF_DEFAULT_MIN_SOC, DEFAULT_MIN_SOC))
    return {
        "name": str(data.get(CONF_NAME, DEFAULT_NAME)),
        "timezone": str(hass.config.time_zone),
        "start": str(data.get(CONF_START_TIME, DEFAULT_START_TIME)),
        "end": str(data.get(CONF_END_TIME, DEFAULT_END_TIME)),
        "capacity": f"{capacity:g}",
        "floor": f"{floor:g}",
        "usable": f"{capacity * (100 - floor) / 100:.3f}",
        "target_min": str(data.get(CONF_USER_MIN_SOC, DEFAULT_MIN_SOC)),
        "target_max": str(data.get(CONF_USER_MAX_SOC, DEFAULT_MAX_SOC)),
        "power_min": str(data.get(CONF_MIN_CHARGE_POWER_W, DEFAULT_MIN_CHARGE_POWER_W)),
        "power_max": str(data.get(CONF_MAX_CHARGE_POWER_W, DEFAULT_MAX_CHARGE_POWER_W)),
        "power_control": str(data.get(CONF_CHARGE_POWER_ENTITY) or "—"),
        "efficiency": "✓" if data.get(CONF_AUTO_EFFICIENT_CHARGE) else "—",
        "pv_shortfall": "✓" if data.get(CONF_AUTO_PV_SHORTFALL) else "—",
        "controls": "\n".join(f"- `{data[key]}`" for key in (
            CONF_MIN_SOC_ENTITY, CONF_GRID_CHARGE_SWITCH, CONF_CHARGE_POWER_ENTITY,
            CONF_CHARGE_POWER_SENT_ENTITY, CONF_CHARGE_POWER_RECEIVED_ENTITY,
            CONF_PV_FORECAST_ENTITY, CONF_PV_FORECAST_TODAY_ENTITY,
        ) if data.get(key)),
    }


def _review_errors(hass: HomeAssistant, data: dict[str, Any]) -> tuple[str, SchemaBuilder, dict[str, str]] | None:
    errors = _validate_user_input(data, hass)
    for step, keys, schema in (
        ("user", STEP_USER_KEYS, _schema_entities),
        ("time_soc", STEP_TIME_SOC_KEYS, _schema_time_soc),
        ("power", STEP_POWER_KEYS, _schema_power),
        ("advanced", STEP_ADVANCED_KEYS, _schema_advanced),
    ):
        relevant = _errors_for(keys, errors)
        if relevant:
            return step, schema, relevant
    return None


def _entry_using_min_soc_entity(
    hass: HomeAssistant, entity_id: str, except_entry_id: str | None = None
) -> ConfigEntry | None:
    """Return a configured entry other than ``except_entry_id`` driving this inverter."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == except_entry_id:
            continue
        if entity_id in (entry.unique_id, entry.data.get(CONF_MIN_SOC_ENTITY)):
            return entry
    return None


# Short, independently editable pages. Shared controls intentionally appear on
# the feature pages which need them; unrelated settings are never cleared.
_GUIDED_GROUPS: dict[str, tuple[str, ...]] = {
    "basics": (CONF_NAME, CONF_MIN_SOC_ENTITY, CONF_GRID_CHARGE_SWITCH,
               CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_CAPACITY, CONF_PV_FORECAST_ENTITY),
    "schedule": (CONF_START_TIME, CONF_END_TIME, CONF_USER_MIN_SOC, CONF_USER_MAX_SOC),
    "charging": (CONF_CHARGE_POWER_ENTITY, CONF_MIN_CHARGE_POWER_W, CONF_MAX_CHARGE_POWER_W),
    "efficiency": (CONF_CHARGE_POWER_ENTITY, CONF_CHARGE_POWER_HOUSE_ENTITY,
                   CONF_GRID_IMPORT_ENTITY, CONF_CHARGE_POWER_SENT_ENTITY,
                   CONF_CHARGE_POWER_RECEIVED_ENTITY, CONF_MIN_CHARGE_POWER_W,
                   CONF_MAX_CHARGE_POWER_W),
    "meters": (CONF_CHARGE_POWER_HOUSE_ENTITY, CONF_GRID_IMPORT_ENTITY,
               CONF_CHARGE_POWER_SENT_ENTITY, CONF_CHARGE_POWER_RECEIVED_ENTITY,
               CONF_CHARGE_ENERGY_SENT_ENTITY, CONF_CHARGE_ENERGY_RECEIVED_ENTITY),
    "grid": (CONF_GRID_IMPORT_ENTITY, CONF_GRID_MAX_CONTINUOUS_W,
             CONF_GRID_HEADROOM_W, CONF_CHARGE_POWER_ENTITY),
    "planning": (CONF_PLANNER_MODE, CONF_FORECAST_ERROR_MARGIN, CONF_DEFAULT_MIN_SOC,
                 CONF_PV_FORECAST_TODAY_ENTITY, CONF_HOUSE_LOAD_ENTITY,
                 CONF_AVG_HOUSE_LOAD_KW, CONF_PV_CROSSOVER_DELAY_MIN, CONF_BRIDGE_RESERVE_KWH),
    # Plan 015: how much the night target carries beyond the bare need
    "reserve": (CONF_SAFETY_BUFFER_KWH, CONF_LOAD_SAFETY_LEVEL, CONF_BRIDGE_MODEL,
                CONF_WEATHER_ENTITY, CONF_OUTDOOR_TEMPERATURE_ENTITY, CONF_HEATING_LIMIT_C),
    "evening": (CONF_HIGH_PRICE_START, CONF_HIGH_PRICE_END, CONF_HIGH_PRICE_MARGIN_PCT,
                CONF_DISCHARGE_BLOCK_MODE, CONF_DISCHARGE_BLOCK_SWITCH),
    "season": (CONF_ACTIVE_START_DATE, CONF_ACTIVE_END_DATE, CONF_ACTIVE_RANGE_YEARLY),
    "backup": (CONF_BACKUP_MODE_ENTITY, CONF_BACKUP_MODE_STATES),
    "pv_shortfall": (CONF_PV_FORECAST_TODAY_ENTITY, CONF_PV_ENERGY_TODAY_ENTITY),
    "prices": (CONF_NIGHT_PRICE_CT, CONF_DAY_PRICE_CT, CONF_FEED_IN_PRICE_CT,
               CONF_PRICE_ENTITY, CONF_PRICE_UNIT, CONF_PRICE_SURCHARGE_CT,
               CONF_PRICE_SURCHARGE_WINDOW_CT),
    "hardware": (CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY, CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
                 CONF_DISCHARGE_LIMIT_ENTITY),
    "grid_fuse": (CONF_GRID_IMPORT_ENTITY, CONF_CHARGE_POWER_ENTITY, CONF_MAIN_FUSE_A,
                  CONF_GRID_PHASES, CONF_GRID_VOLTAGE_V, CONF_GRID_CONTINUOUS_PCT, CONF_GRID_HEADROOM_W),
    "runtime": (CONF_UPDATE_INTERVAL, CONF_COMMAND_DELAY, CONF_CHARGE_EFFICIENCY,
                CONF_DISCHARGE_EFFICIENCY, CONF_CURTAILMENT_FEED_IN_ENTITY,
                CONF_CURTAILMENT_LIMIT_W, CONF_OPERATION_MODE, CONF_FORCE_DISCHARGE_SWITCH),
}
_BUILDERS = (_schema_entities, _schema_time_soc, _schema_power, _schema_advanced)
# The efficiency search's AC side is one sensor or house consumption with grid
# import, so none of the three is required on its own (plan 017); the
# validation asks for one complete choice.
_AC_SIDE_KEYS = frozenset({CONF_CHARGE_POWER_SENT_ENTITY, CONF_CHARGE_POWER_HOUSE_ENTITY,
                           CONF_GRID_IMPORT_ENTITY})
_FEATURE_FLAGS = {"efficiency": CONF_AUTO_EFFICIENT_CHARGE,
                  "pv_shortfall": CONF_AUTO_PV_SHORTFALL}
_KORE_SUFFIXES = {
    CONF_MIN_SOC_ENTITY: ("_devices:local_Battery:MinSoc", "_devices:local_Battery:MinSocRel"),
    CONF_GRID_CHARGE_SWITCH: ("_devices:local_Battery:ManualCharge",),
    CONF_BATTERY_SOC_ENTITY: ("_devices:local:battery_SoC",),
    CONF_CHARGE_POWER_ENTITY: ("_modbus_bat_max_charge_limit",),
    # The AC side of a charge is grid import minus house consumption: KORE's
    # "Grid to Battery Power" is the battery's DC power split by source and
    # carries no conversion loss (plan 017)
    CONF_CHARGE_POWER_HOUSE_ENTITY: ("_devices:local_Home_P",),
    CONF_CHARGE_POWER_RECEIVED_ENTITY: ("_devices:local:battery_P",),
}


def _kostal_suggestions(hass: HomeAssistant, min_soc: str | None = None) -> dict[str, str]:
    """Use registry identity, never a guessed or user-renamable entity ID."""
    registry = er.async_get(hass)
    groups: dict[str, dict[str, list[str]]] = {}
    for entity in registry.entities.values():
        if entity.platform != "kostal_kore" or entity.disabled_by or not entity.device_id:
            continue
        for key, suffixes in _KORE_SUFFIXES.items():
            if entity.unique_id.endswith(suffixes):
                groups.setdefault(entity.device_id, {}).setdefault(key, []).append(entity.entity_id)
    candidates = [group for group in groups.values()
                  if len(group.get(CONF_MIN_SOC_ENTITY, [])) == 1
                  and (not min_soc or min_soc in group[CONF_MIN_SOC_ENTITY])]
    if len(candidates) != 1:
        return {}
    return {key: values[0] for key, values in candidates[0].items() if len(values) == 1}


def _guided_defaults(data: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(data)
    for builder in _BUILDERS:
        for marker in builder(result).schema:
            if marker.schema not in result and marker.default is not vol.UNDEFINED:
                result[marker.schema] = marker.default()
    return result


def _guided_schema(group: str, defaults: Mapping[str, Any]) -> vol.Schema:
    fields = {marker.schema: (marker, validator)
              for builder in _BUILDERS for marker, validator in builder(defaults).schema.items()}
    required = set(_GUIDED_GROUPS[group]) if group in ("charging", "efficiency", "grid", "grid_fuse", "pv_shortfall") else set()
    if group == "efficiency":
        required -= _AC_SIDE_KEYS
    if defaults.get(CONF_AUTO_PV_SHORTFALL):
        required.add(CONF_PV_FORECAST_TODAY_ENTITY)
    return vol.Schema({(_required(key, defaults) if key in required else fields[key][0]): fields[key][1]
                       for key in _GUIDED_GROUPS[group]})


def _merge_setup_data(current: Mapping[str, Any], opened: Mapping[str, Any],
                      collected_values: Mapping[str, Any],
                      explicit: set[str] | None = None) -> dict[str, Any]:
    """Keep runtime changes made while an options/reconfigure draft was open."""
    collected = _finalize_data(collected_values)
    initial = _guided_defaults(opened)
    data = dict(current)
    for key in _ALL_STEP_KEYS:
        if key not in collected and (key in opened or key in (explicit or set())):
            data.pop(key, None)
    for key, value in collected.items():
        if (key in current or key in opened) and initial.get(key) == value and key not in (explicit or set()):
            continue
        data[key] = value
    return data


class GuidedSetupMixin(ConfigEntryBaseFlow):
    """Native HA menus, short forms, and durable drafts for all three flows."""

    _data: dict[str, Any]
    _guided = False
    _draft_id: str | None = None
    _explicit_features: set[str] | None = None

    def _draft_scope(self) -> str:
        entry = getattr(self, "_config_entry", None)
        return str(entry.entry_id if entry else self.context.get("entry_id", "new"))

    async def _guided_start(self) -> ConfigFlowResult:
        self._guided = True
        self._data = _guided_defaults(self._data)
        if not self._data.get(CONF_MIN_SOC_ENTITY):
            suggestions = _kostal_suggestions(self.hass)
            self._data.update({key: value for key, value in suggestions.items()
                               if key in _GUIDED_GROUPS["basics"]})
        return await self.async_step_setup()

    async def async_step_setup(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        drafts = await get_drafts(self.hass).list(self._draft_scope())
        options = ["basics", "schedule", "features", "summary", "save_draft"]
        if drafts:
            options.append("resume_draft")
        values = _review_placeholders(self.hass, self._data)
        values["core_count"] = str(sum(bool(self._data.get(key)) for key in _GUIDED_GROUPS["basics"]))
        return self.async_show_menu(step_id="setup", menu_options=options,
                                    description_placeholders=values)

    async def async_step_features(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="features", menu_options=[
            "charging", "grid", "efficiency", "planning_features", "advanced_features", "setup"],
            description_placeholders={"efficiency": "✓" if self._data.get(CONF_AUTO_EFFICIENT_CHARGE) else "—",
                                      "grid": "✓" if _grid_limit_set(self._data) else "—"})

    async def async_step_planning_features(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="planning_features", menu_options=[
            "planning", "reserve", "evening", "season", "pv_shortfall", "prices", "setup"])

    async def async_step_advanced_features(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="advanced_features", menu_options=[
            "meters", "backup", "hardware", "grid_fuse", "runtime", "disable_features", "setup"])

    async def async_step_disable_features(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="disable_features", menu_options=[
            "efficiency_off", "grid_off", "pv_shortfall_off", "charging_off", "setup"])

    async def async_step_grid_fuse(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("grid_fuse", user_input)

    async def _guided_form(self, group: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        flag = _FEATURE_FLAGS.get(group)
        if user_input is not None:
            values = dict(user_input)
            keys = _GUIDED_GROUPS[group]
            if flag:
                values[flag] = True
                keys += (flag,)
            candidate = dict(self._data)
            if group == "grid_fuse":
                candidate.pop(CONF_GRID_MAX_CONTINUOUS_W, None)
            errors = _process_step(self.hass, candidate, keys, values)
            for key in _GUIDED_GROUPS[group]:
                if (group in ("basics", "charging", "efficiency", "grid", "grid_fuse", "pv_shortfall")
                        and not values.get(key) and key != CONF_GRID_HEADROOM_W
                        and not (group == "efficiency" and key in _AC_SIDE_KEYS)):
                    errors[key] = "required_value"
            if group == "basics" and values.get(CONF_MIN_SOC_ENTITY):
                scope = self._draft_scope()
                if _entry_using_min_soc_entity(self.hass, values[CONF_MIN_SOC_ENTITY],
                                               None if scope == "new" else scope):
                    errors[CONF_MIN_SOC_ENTITY] = "entity_used_by_other_entry"
            if not errors:
                self._data = candidate
                if flag:
                    self._explicit_features = {*(self._explicit_features or set()), flag}
                if group == "planning":
                    self._explicit_features = {*(self._explicit_features or set()), CONF_DEFAULT_MIN_SOC}
                if (group == "schedule" and self._draft_scope() == "new"
                        and CONF_DEFAULT_MIN_SOC not in (self._explicit_features or set())):
                    self._data[CONF_DEFAULT_MIN_SOC] = self._data[CONF_USER_MIN_SOC]
                try:
                    await self._save_setup_draft()
                except OSError:
                    _LOGGER.exception("Could not save setup draft")
                    errors["base"] = "draft_save_failed"
                else:
                    return await self.async_step_setup()
        defaults = {**self._data, **(user_input or {})}
        suggestions = _kostal_suggestions(self.hass, self._data.get(CONF_MIN_SOC_ENTITY))
        defaults = {**suggestions, **defaults}
        # Show an existing fuse-based budget as its equivalent direct watt value.
        if group == "grid" and not defaults.get(CONF_GRID_MAX_CONTINUOUS_W) and defaults.get(CONF_MAIN_FUSE_A):
            defaults[CONF_GRID_MAX_CONTINUOUS_W] = int(float(defaults[CONF_MAIN_FUSE_A])
                * float(defaults[CONF_GRID_PHASES]) * float(defaults[CONF_GRID_VOLTAGE_V])
                * float(defaults[CONF_GRID_CONTINUOUS_PCT]) / 100)
        return self.async_show_form(step_id=group, data_schema=_guided_schema(group, defaults),
                                    errors=errors, last_step=False,
                                    description_placeholders={"timezone": str(self.hass.config.time_zone)})

    async def _save_setup_draft(self) -> None:
        if self._draft_id is None:
            self._draft_id = uuid4().hex
        baseline = getattr(self, "_opened_with", self._data)
        await get_drafts(self.hass).save(self._draft_id, self._draft_scope(),
            _finalize_data({key: value for key, value in self._data.items() if key in _ALL_STEP_KEYS}),
            {key: value for key, value in baseline.items() if key in _ALL_STEP_KEYS},
            sorted(self._explicit_features or set()))

    async def async_step_save_draft(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        try:
            await self._save_setup_draft()
        except OSError:
            _LOGGER.exception("Could not save setup draft")
            return self.async_abort(reason="draft_save_failed")
        return self.async_abort(reason="draft_saved")

    async def async_step_resume_draft(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        drafts = await get_drafts(self.hass).list(self._draft_scope())
        errors: dict[str, str] = {}
        if user_input is not None:
            draft_id = user_input.get("draft")
            if draft_id in drafts:
                saved = drafts[draft_id]
                self._data = _guided_defaults({key: value for key, value in saved["data"].items()
                                               if key in _ALL_STEP_KEYS})
                if hasattr(self, "_opened_with"):
                    self._opened_with = dict(saved.get("baseline", {}))
                self._draft_id = draft_id
                self._explicit_features = {key for key in saved.get("explicit_features", [])
                                           if key in _ALL_STEP_KEYS}
                return await self.async_step_setup()
            errors["draft"] = "draft_not_found"
        choices: list[selector.SelectOptionDict] = [{"value": key, "label": f"{draft['data'].get(CONF_NAME, DEFAULT_NAME)} — {draft['updated'][:16]} UTC"}
                   for key, draft in drafts.items()]
        return self.async_show_form(step_id="resume_draft", errors=errors, data_schema=vol.Schema({
            vol.Required("draft"): selector.SelectSelector(selector.SelectSelectorConfig(options=choices))}))

    async def async_step_summary(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="summary", menu_options=["finish", "setup"],
                                    description_placeholders=_review_placeholders(self.hass, self._data))

    async def _guided_validate(self, data: dict[str, Any] | None = None) -> ConfigFlowResult | None:
        errors = _validate_user_input(data if data is not None else self._data, self.hass)
        for key in _GUIDED_GROUPS["basics"]:
            if not self._data.get(key):
                errors[key] = "required_value"
        for group, keys in _GUIDED_GROUPS.items():
            relevant = _errors_for(keys, errors)
            if relevant:
                result = await self._guided_form(group, None)
                result["errors"] = relevant
                return result
        return None

    async def _remove_setup_draft(self) -> None:
        if self._draft_id:
            try:
                await get_drafts(self.hass).remove(self._draft_id)
            except OSError:
                _LOGGER.warning("Saved configuration, but could not remove its setup draft", exc_info=True)

    async def async_step_basics(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("basics", user_input)

    async def async_step_schedule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("schedule", user_input)

    async def async_step_charging(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("charging", user_input)

    async def async_step_efficiency(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("efficiency", user_input)

    async def async_step_meters(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("meters", user_input)

    async def async_step_grid(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("grid", user_input)

    async def async_step_planning(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("planning", user_input)

    async def async_step_reserve(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("reserve", user_input)

    async def async_step_evening(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("evening", user_input)

    async def async_step_season(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("season", user_input)

    async def async_step_backup(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("backup", user_input)

    async def async_step_pv_shortfall(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("pv_shortfall", user_input)

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("prices", user_input)

    async def async_step_hardware(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("hardware", user_input)

    async def async_step_runtime(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._guided_form("runtime", user_input)

    async def _save_disabled_feature(self) -> ConfigFlowResult:
        try:
            await self._save_setup_draft()
        except OSError:
            _LOGGER.exception("Could not save setup draft")
            return self.async_abort(reason="draft_save_failed")
        return await self.async_step_features()

    async def async_step_efficiency_off(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._data[CONF_AUTO_EFFICIENT_CHARGE] = False
        self._explicit_features = {*(self._explicit_features or set()), CONF_AUTO_EFFICIENT_CHARGE}
        return await self._save_disabled_feature()

    async def async_step_grid_off(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._data.get(CONF_CHARGE_POWER_HOUSE_ENTITY):
            # The efficiency search still reads the grid import for the AC side
            # (plan 017): the limit goes, the measurement stays (Codex on #45)
            self._data.pop(CONF_GRID_MAX_CONTINUOUS_W, None)
            self._data.pop(CONF_MAIN_FUSE_A, None)
        else:
            self._data.pop(CONF_GRID_IMPORT_ENTITY, None)
        self._explicit_features = {*(self._explicit_features or set()), CONF_GRID_IMPORT_ENTITY}
        return await self._save_disabled_feature()

    async def async_step_charging_off(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_disable_features()
        if self._data.get(CONF_AUTO_EFFICIENT_CHARGE) or _grid_limit_set(self._data):
            return self.async_show_form(step_id="charging_off", data_schema=vol.Schema({}),
                                        errors={"base": "disable_dependent_features"}, last_step=False)
        self._data.pop(CONF_CHARGE_POWER_ENTITY, None)
        self._explicit_features = {*(self._explicit_features or set()), CONF_CHARGE_POWER_ENTITY}
        return await self._save_disabled_feature()

    async def async_step_pv_shortfall_off(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._data[CONF_AUTO_PV_SHORTFALL] = False
        self._explicit_features = {*(self._explicit_features or set()), CONF_AUTO_PV_SHORTFALL}
        return await self._save_disabled_feature()


class InverterChargeNightConfigFlow(GuidedSetupMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Inverter Charge Night."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._reviewing = False

    async def _async_wizard_step(
        self,
        step_id: str,
        step_keys: tuple[str, ...],
        build_schema: SchemaBuilder,
        user_input: dict[str, Any] | None,
        next_step: NextStep,
    ) -> ConfigFlowResult:
        """Show one wizard step, or validate its input and continue."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _process_step(self.hass, self._data, step_keys, user_input)
            if not errors:
                if self._reviewing:
                    return await self.async_step_review()
                return await next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema({**self._data, **(user_input or {})}),
            errors=errors,
        )

    # -- Initial setup ------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start the guided setup; accept an already-open legacy wizard."""
        if user_input is None:
            return await self._guided_start()
        return await self._async_wizard_step(
            "user", STEP_USER_KEYS, _schema_entities, user_input, self.async_step_time_soc
        )

    async def async_step_time_soc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2 of 4: time window and SOC."""
        return await self._async_wizard_step(
            "time_soc", STEP_TIME_SOC_KEYS, _schema_time_soc, user_input, self.async_step_power
        )

    async def async_step_power(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3 of 4: charge power."""
        return await self._async_wizard_step(
            "power", STEP_POWER_KEYS, _schema_power, user_input, self.async_step_advanced
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4 of 4: advanced options, then create the entry."""
        return await self._async_wizard_step(
            "advanced", STEP_ADVANCED_KEYS, _schema_advanced, user_input, self.async_step_review
        )

    async def async_step_review(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Review everything and revisit a section without losing other edits."""
        self._reviewing = True
        prefix = "reconfigure_" if self.source == config_entries.SOURCE_RECONFIGURE else ""
        first = "reconfigure" if prefix else "user"
        return self.async_show_menu(step_id="review", menu_options=[
            first, prefix + "time_soc", prefix + "power", prefix + "advanced", "finish",
        ], description_placeholders=_review_placeholders(self.hass, self._data))

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Only commit after the user has reviewed the collected settings."""
        if self._guided:
            candidate = self._data
            if self.source == config_entries.SOURCE_RECONFIGURE:
                candidate = _merge_setup_data(self._get_reconfigure_entry().data, self._opened_with, self._data, self._explicit_features)
            invalid_result = await self._guided_validate(candidate)
            if invalid_result is not None:
                return invalid_result
        invalid = None if self._guided else _review_errors(self.hass, self._data)
        if invalid is not None:
            step_id, schema, errors = invalid
            if self.source == config_entries.SOURCE_RECONFIGURE:
                step_id = "reconfigure" if step_id == "user" else "reconfigure_" + step_id
            return self.async_show_form(step_id=step_id, data_schema=schema(self._data), errors=errors)
        if self.source == config_entries.SOURCE_RECONFIGURE:
            result = await self._async_reconfigure_finish()
        else:
            result = await self._async_create()
        if result.get("type") != "form":
            await self._remove_setup_draft()
        return result

    async def _async_create(self) -> ConfigFlowResult:
        data = _finalize_data(self._data)
        await self.async_set_unique_id(data[CONF_MIN_SOC_ENTITY])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=data[CONF_NAME], data=data)

    # -- Reconfigure --------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure step 1 of 4: entities."""
        if not self._data:
            self._data = dict(self._get_reconfigure_entry().data)
            self._opened_with = dict(self._data)
        if user_input is None:
            return await self._guided_start()
        return await self._async_wizard_step(
            "reconfigure",
            STEP_USER_KEYS,
            _schema_entities,
            user_input,
            self.async_step_reconfigure_time_soc,
        )

    async def async_step_reconfigure_time_soc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure step 2 of 4: time window and SOC."""
        return await self._async_wizard_step(
            "reconfigure_time_soc",
            STEP_TIME_SOC_KEYS,
            _schema_time_soc,
            user_input,
            self.async_step_reconfigure_power,
        )

    async def async_step_reconfigure_power(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure step 3 of 4: charge power."""
        return await self._async_wizard_step(
            "reconfigure_power",
            STEP_POWER_KEYS,
            _schema_power,
            user_input,
            self.async_step_reconfigure_advanced,
        )

    async def async_step_reconfigure_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure step 4 of 4: advanced options, then update the entry."""
        return await self._async_wizard_step(
            "reconfigure_advanced",
            STEP_ADVANCED_KEYS,
            _schema_advanced,
            user_input,
            self.async_step_review,
        )

    async def _async_reconfigure_finish(self) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        data = _merge_setup_data(entry.data, self._opened_with, self._data, self._explicit_features)
        # The min SOC entity identifies the inverter. Pointing this entry at an
        # inverter another entry already drives would leave two coordinators
        # fighting over it, so refuse that - but changing to a free inverter
        # (a replaced device, a renamed entity) must stay possible, which is
        # why this is not _abort_if_unique_id_mismatch: that one compares
        # against this entry's own id and would refuse every change.
        min_soc_entity = data[CONF_MIN_SOC_ENTITY]
        if _entry_using_min_soc_entity(self.hass, min_soc_entity, entry.entry_id):
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_schema_entities(self._data),
                errors={CONF_MIN_SOC_ENTITY: "entity_used_by_other_entry"},
            )
        # The unique id follows the inverter, so a later entry for the old
        # entity is not blocked and a later one for the new entity is. It has
        # to be passed here: async_set_unique_id only writes the flow context.
        await self.async_set_unique_id(min_soc_entity)
        return self.async_update_reload_and_abort(entry, data=data, unique_id=min_soc_entity)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


# ---------------------------------------------------------------------------
# Options flow, the same four steps writing back into entry.data
# ---------------------------------------------------------------------------


class OptionsFlowHandler(GuidedSetupMixin, config_entries.OptionsFlow):
    """Handle options flow for Inverter Charge Night."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._data: dict[str, Any] = dict(config_entry.data)
        # The state entry.data had when the dialog opened. _async_save compares
        # against it to tell a field the user edited from one they only passed
        # through, so that a runtime write does not get reverted on save.
        self._opened_with: dict[str, Any] = dict(config_entry.data)
        self._reviewing = False

    async def _async_wizard_step(
        self,
        step_id: str,
        step_keys: tuple[str, ...],
        build_schema: SchemaBuilder,
        user_input: dict[str, Any] | None,
        next_step: NextStep,
    ) -> ConfigFlowResult:
        """Show one wizard step, or validate its input and continue."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _process_step(self.hass, self._data, step_keys, user_input)
            if not errors:
                if self._reviewing:
                    return await self.async_step_review()
                return await next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema({**self._data, **(user_input or {})}),
            errors=errors,
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open the editable setup overview."""
        if user_input is None:
            return await self._guided_start()
        return await self._async_wizard_step(
            "init", STEP_USER_KEYS, _schema_entities, user_input, self.async_step_time_soc
        )

    async def async_step_time_soc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options step 2 of 4: time window and SOC."""
        return await self._async_wizard_step(
            "time_soc", STEP_TIME_SOC_KEYS, _schema_time_soc, user_input, self.async_step_power
        )

    async def async_step_power(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options step 3 of 4: charge power."""
        return await self._async_wizard_step(
            "power", STEP_POWER_KEYS, _schema_power, user_input, self.async_step_advanced
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options step 4 of 4: advanced options, then save."""
        return await self._async_wizard_step(
            "advanced", STEP_ADVANCED_KEYS, _schema_advanced, user_input, self.async_step_review
        )

    async def async_step_review(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._reviewing = True
        return self.async_show_menu(step_id="review", menu_options=[
            "init", "time_soc", "power", "advanced", "finish",
        ], description_placeholders=_review_placeholders(self.hass, self._data))

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._guided:
            invalid_result = await self._guided_validate(self._merged_data())
            if invalid_result is not None:
                return invalid_result
        invalid = None if self._guided else _review_errors(self.hass, self._merged_data())
        if invalid is not None:
            step_id, schema, errors = invalid
            return self.async_show_form(step_id="init" if step_id == "user" else step_id,
                                        data_schema=schema(self._data), errors=errors)
        result = await self._async_save()
        if result.get("type") != "form":
            await self._remove_setup_draft()
            # Cleanup yields to update listeners, which may persist newer
            # teardown/runtime options. HA writes this payload synchronously
            # after the step returns; capture it only after the final await.
            # Without a draft neither this path nor HA's options finalization
            # suspends: a queued listener persists after finalization instead.
            result["data"] = dict(self._config_entry.options)
        return result

    def _merged_data(self) -> dict[str, Any]:
        """Merge the collected values over the *current* entry.data.

        The dialog snapshots entry.data when it opens, but the integration also
        writes entry.data while it is open: the select entity stores the
        operation mode and the switch and the efficiency finder store the auto
        efficient charge flag. Writing the snapshot back wholesale would revert
        such a change -- for instance re-enabling a finder that just completed
        and disabled itself. So a field the user did not edit in this dialog
        keeps whatever entry.data holds now; only the fields the user actually
        changed (and the fields the user cleared) are written.
        """
        return _merge_setup_data(self._config_entry.data, self._opened_with, self._data, self._explicit_features)

    async def _async_save(self) -> ConfigFlowResult:
        min_soc_entity = self._data.get(CONF_MIN_SOC_ENTITY)
        if min_soc_entity and _entry_using_min_soc_entity(
            self.hass, min_soc_entity, self._config_entry.entry_id
        ):
            return self.async_show_form(
                step_id="init",
                data_schema=_schema_entities(self._data),
                errors={CONF_MIN_SOC_ENTITY: "entity_used_by_other_entry"},
            )
        # The settings live in entry.data (unchanged for existing installations);
        # entry.options only holds the auto-efficiency history, which is preserved.
        data = self._merged_data()
        # Keep the unique id on the inverter this entry now drives; otherwise a
        # second entry could be created for the new entity without being caught.
        unique_id = data.get(CONF_MIN_SOC_ENTITY, self._config_entry.unique_id)
        self.hass.config_entries.async_update_entry(
            self._config_entry, data=data, unique_id=unique_id
        )
        return self.async_create_entry(title="", data=dict(self._config_entry.options))
