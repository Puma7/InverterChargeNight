"""Config flow for Inverter Charge Night integration.

The configuration is collected in a four-step wizard (entities, time & SOC,
charge power, advanced). The same four steps are used for the initial setup,
the reconfigure flow and the options flow; only the ``step_id`` values and the
way the result is stored differ.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from typing import Any, cast

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_START_DATE,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_BACKUP_MODE_ENTITY,
    CONF_BACKUP_MODE_STATES,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BRIDGE_RESERVE_KWH,
    CONF_CHARGE_EFFICIENCY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_ENERGY_RECEIVED_ENTITY,
    CONF_CHARGE_ENERGY_SENT_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DAY_PRICE_CT,
    CONF_DEFAULT_MIN_SOC,
    CONF_DISCHARGE_BLOCK_MODE,
    CONF_DISCHARGE_BLOCK_SWITCH,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_END_TIME,
    CONF_FEED_IN_PRICE_CT,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_NIGHT_PRICE_CT,
    CONF_OPERATION_MODE,
    CONF_PLANNER_MODE,
    CONF_PV_CROSSOVER_DELAY_MIN,
    CONF_PV_FORECAST_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_START_TIME,
    CONF_UPDATE_INTERVAL,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    DEFAULT_AVG_HOUSE_LOAD_KW,
    DEFAULT_BRIDGE_RESERVE_KWH,
    DEFAULT_CHARGE_EFFICIENCY,
    DEFAULT_COMMAND_DELAY,
    DEFAULT_DISCHARGE_BLOCK_MODE,
    DEFAULT_END_TIME,
    DEFAULT_FORECAST_ERROR_MARGIN,
    DEFAULT_MAX_CHARGE_POWER_W,
    DEFAULT_MAX_SOC,
    DEFAULT_MIN_CHARGE_POWER_W,
    DEFAULT_MIN_SOC,
    DEFAULT_OPERATION_MODE,
    DEFAULT_PLANNER_MODE,
    DEFAULT_PV_CROSSOVER_DELAY_MIN,
    DEFAULT_START_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DISCHARGE_BLOCK_AUTO,
    DISCHARGE_BLOCK_OFF,
    DOMAIN,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
    PLANNER_MODE_BRIDGE,
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
)
STEP_POWER_KEYS: tuple[str, ...] = (
    CONF_MIN_CHARGE_POWER_W,
    CONF_MAX_CHARGE_POWER_W,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
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
    CONF_UPDATE_INTERVAL,
    CONF_COMMAND_DELAY,
    CONF_ACTIVE_START_DATE,
    CONF_ACTIVE_END_DATE,
    CONF_BACKUP_MODE_ENTITY,
    CONF_BACKUP_MODE_STATES,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_PV_CROSSOVER_DELAY_MIN,
    CONF_BRIDGE_RESERVE_KWH,
    CONF_CHARGE_EFFICIENCY,
    CONF_NIGHT_PRICE_CT,
    CONF_DAY_PRICE_CT,
    CONF_FEED_IN_PRICE_CT,
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


_ENTITY_KEYS_TO_VALIDATE = [
    CONF_MIN_SOC_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_PV_FORECAST_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BACKUP_MODE_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_ENERGY_SENT_ENTITY,
    CONF_CHARGE_ENERGY_RECEIVED_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_GRID_IMPORT_ENTITY,
    CONF_DISCHARGE_BLOCK_SWITCH,
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
        if not user_input.get(CONF_CHARGE_POWER_SENT_ENTITY):
            errors[CONF_CHARGE_POWER_SENT_ENTITY] = "required_entity"
        if not user_input.get(CONF_CHARGE_POWER_RECEIVED_ENTITY):
            errors[CONF_CHARGE_POWER_RECEIVED_ENTITY] = "required_entity"

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
        if grid_max_w is None and fuse_a is None:
            # Without a budget the entity would be read but never act.
            errors[CONF_MAIN_FUSE_A] = "required_value"
        if not user_input.get(CONF_CHARGE_POWER_ENTITY):
            # The limit works by writing a lower charge setpoint. Without that
            # entity there is nothing to write to and the protection would be
            # configured but inert - which is worse than not offering it.
            errors[CONF_CHARGE_POWER_ENTITY] = "required_entity"

    prices_set = [key for key in _PRICE_KEYS if user_input.get(key) is not None]
    if prices_set and len(prices_set) != len(_PRICE_KEYS):
        for key in _PRICE_KEYS:
            if key not in prices_set:
                errors[key] = "all_prices_required"

    # Validate entities exist
    entity_registry = er.async_get(hass)
    for entity_key in _ENTITY_KEYS_TO_VALIDATE:
        entity_id = user_input.get(entity_key)
        if entity_id:
            entity = entity_registry.async_get(entity_id)
            if not entity:
                if not hass.states.get(entity_id):
                    errors[entity_key] = "invalid_entity"

    return errors


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
    errors = _errors_for(step_keys, _validate_user_input({**data, **user_input}, hass))
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
        }
    )


def _schema_power(defaults: Mapping[str, Any]) -> vol.Schema:
    """Step 3: charge power limits and the optional power entities."""
    return vol.Schema(
        {
            _required(
                CONF_MIN_CHARGE_POWER_W, defaults, DEFAULT_MIN_CHARGE_POWER_W
            ): _number_selector(**_WATTS),
            _required(
                CONF_MAX_CHARGE_POWER_W, defaults, DEFAULT_MAX_CHARGE_POWER_W
            ): _number_selector(**_WATTS),
            _optional(
                CONF_ABSOLUTE_MAX_CHARGE_POWER_W, defaults.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_W)
            ): _number_selector(**_WATTS),
            _optional(
                CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
                defaults.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY),
            ): _entity_selector(_CONTROL_DOMAINS),
            _optional(
                CONF_CHARGE_POWER_ENTITY, defaults.get(CONF_CHARGE_POWER_ENTITY)
            ): _entity_selector(_CONTROL_DOMAINS),
            _optional(
                CONF_CHARGE_POWER_SENT_ENTITY, defaults.get(CONF_CHARGE_POWER_SENT_ENTITY)
            ): _entity_selector("sensor", "power"),
            _optional(
                CONF_CHARGE_POWER_RECEIVED_ENTITY, defaults.get(CONF_CHARGE_POWER_RECEIVED_ENTITY)
            ): _entity_selector("sensor", "power"),
            _optional(
                CONF_CHARGE_ENERGY_SENT_ENTITY, defaults.get(CONF_CHARGE_ENERGY_SENT_ENTITY)
            ): _entity_selector("sensor", "energy"),
            _optional(
                CONF_CHARGE_ENERGY_RECEIVED_ENTITY, defaults.get(CONF_CHARGE_ENERGY_RECEIVED_ENTITY)
            ): _entity_selector("sensor", "energy"),
            vol.Required(
                CONF_AUTO_EFFICIENT_CHARGE,
                default=bool(defaults.get(CONF_AUTO_EFFICIENT_CHARGE, False)),
            ): _bool_selector(),
            _optional(
                CONF_FORCE_DISCHARGE_SWITCH, defaults.get(CONF_FORCE_DISCHARGE_SWITCH)
            ): _entity_selector("switch"),
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
                CONF_CHARGE_EFFICIENCY, defaults, DEFAULT_CHARGE_EFFICIENCY
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
        }
    )


# ---------------------------------------------------------------------------
# Config flow (initial setup + reconfigure), four steps each
# ---------------------------------------------------------------------------


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


class InverterChargeNightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Inverter Charge Night."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}

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
        """Step 1 of 4: entities."""
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
            "advanced", STEP_ADVANCED_KEYS, _schema_advanced, user_input, self._async_create
        )

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
            self._async_reconfigure_finish,
        )

    async def _async_reconfigure_finish(self) -> ConfigFlowResult:
        data = _finalize_data(self._data)
        entry = self._get_reconfigure_entry()
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


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Inverter Charge Night."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._data: dict[str, Any] = dict(config_entry.data)
        # The state entry.data had when the dialog opened. _async_save compares
        # against it to tell a field the user edited from one they only passed
        # through, so that a runtime write does not get reverted on save.
        self._opened_with: dict[str, Any] = dict(config_entry.data)

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
                return await next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=build_schema({**self._data, **(user_input or {})}),
            errors=errors,
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options step 1 of 4: entities."""
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
            "advanced", STEP_ADVANCED_KEYS, _schema_advanced, user_input, self._async_save
        )

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
        collected = _finalize_data(self._data)
        data = dict(self._config_entry.data)
        for key in _ALL_STEP_KEYS:
            if key not in collected:
                data.pop(key, None)  # optional field cleared in the dialog
        for key, value in collected.items():
            if key in data and self._opened_with.get(key) == value:
                continue  # untouched here; keep the value entry.data has now
            data[key] = value
        return data

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
