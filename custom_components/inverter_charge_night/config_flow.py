"""Config flow for Inverter Charge Night integration."""

import logging
from datetime import date
from typing import Any, Literal, cast

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_UPDATE_INTERVAL,
    CONF_COMMAND_DELAY,
    CONF_ACTIVE_START_DATE,
    CONF_ACTIVE_END_DATE,
    CONF_BACKUP_MODE_ENTITY,
    CONF_MIN_CHARGE_POWER_W,
    CONF_MAX_CHARGE_POWER_W,
    CONF_CHARGE_POWER_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_BATTERY_CHARGE_ENERGY_ENTITY,
    CONF_HOME_CONSUMPTION_ENERGY_ENTITY,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_END_TIME,
    CONF_USER_MIN_SOC,
    CONF_USER_MAX_SOC,
    CONF_FORECAST_ERROR_MARGIN,
    DEFAULT_END_TIME,
    DEFAULT_FORECAST_ERROR_MARGIN,
    DEFAULT_MAX_SOC,
    DEFAULT_MIN_SOC,
    DEFAULT_START_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_COMMAND_DELAY,
    DEFAULT_ACTIVE_START_DATE,
    DEFAULT_ACTIVE_END_DATE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def validate_time_format(time_str: str) -> bool:
    """Validate time format HH:MM."""
    try:
        hour, minute = map(int, time_str.split(":"))
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except (ValueError, AttributeError):
        return False


def validate_soc(value: float) -> bool:
    """Validate SOC value is between 0 and 100."""
    return 0.0 <= value <= 100.0


def validate_date_optional(value: str | date | None) -> bool:
    """Validate optional date format YYYY-MM-DD (allow empty)."""
    if value in (None, ""):
        return True
    if isinstance(value, date):
        return True
    if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _normalize_date_value(value: str | date | None) -> str | None:
    """Normalize date selector values to ISO strings for storage."""
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        return None
    try:
        parsed = date.fromisoformat(value)
        return parsed.isoformat()
    except ValueError:
        return None


def _default_date_value(value: str | date | None) -> date | None:
    """Convert stored ISO string to date for selector defaults."""
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _entity_selector(domain: str | None = None) -> Any:
    config = selector.EntitySelectorConfig(domain=domain) if domain else selector.EntitySelectorConfig()
    return cast(Any, selector.EntitySelector(config))  # pyright: ignore[reportUnknownMemberType]


def _date_selector() -> Any:
    return cast(Any, selector.DateSelector())  # pyright: ignore[reportUnknownMemberType]


def _time_selector() -> Any:
    return cast(Any, selector.TimeSelector())  # pyright: ignore[reportUnknownMemberType]


def _number_selector(
    min_val: float = 0,
    max_val: float = 100,
    step: float | Literal["any"] = "any",
    unit: str = "",
    mode: str = "box",
) -> Any:
    sel_mode = (
        selector.NumberSelectorMode.SLIDER
        if mode == "slider"
        else selector.NumberSelectorMode.BOX
    )
    config = selector.NumberSelectorConfig(
        min=min_val, max=max_val, step=step, unit_of_measurement=unit, mode=sel_mode
    )
    return cast(Any, selector.NumberSelector(config))  # pyright: ignore[reportUnknownMemberType]


def _boolean_selector() -> Any:
    return cast(Any, selector.BooleanSelector())  # pyright: ignore[reportUnknownMemberType]


def _text_selector() -> Any:
    return cast(Any, selector.TextSelector())  # pyright: ignore[reportUnknownMemberType]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_entities(
    hass: Any,
    user_input: dict[str, Any],
    entity_keys: list[str],
    errors: dict[str, str],
) -> None:
    """Validate that entity IDs exist in registry or state machine."""
    entity_registry = er.async_get(hass)
    for entity_key in entity_keys:
        entity_id = user_input.get(entity_key)
        if entity_id:
            entity = entity_registry.async_get(entity_id)
            if not entity:
                if not hass.states.get(entity_id):
                    errors[entity_key] = "invalid_entity"


def _validate_step_user(user_input: dict[str, Any], errors: dict[str, str]) -> None:
    """Validate step 1: core entities & battery capacity."""
    if user_input.get(CONF_BATTERY_CAPACITY, 0) <= 0:
        errors[CONF_BATTERY_CAPACITY] = "invalid_capacity"


def _validate_step_time_soc(user_input: dict[str, Any], errors: dict[str, str]) -> None:
    """Validate step 2: time window & SOC values."""
    if not validate_time_format(user_input.get(CONF_START_TIME, "")):
        errors[CONF_START_TIME] = "invalid_time"
    if not validate_time_format(user_input.get(CONF_END_TIME, "")):
        errors[CONF_END_TIME] = "invalid_time"
    user_min_soc = user_input.get(CONF_USER_MIN_SOC, 0)
    user_max_soc = user_input.get(CONF_USER_MAX_SOC, 0)
    if not validate_soc(user_min_soc):
        errors[CONF_USER_MIN_SOC] = "invalid_soc"
    if not validate_soc(user_max_soc):
        errors[CONF_USER_MAX_SOC] = "invalid_soc"
    if user_min_soc >= user_max_soc:
        errors[CONF_USER_MAX_SOC] = "max_soc_must_be_greater_than_min"
    if not validate_soc(user_input.get(CONF_DEFAULT_MIN_SOC, 0)):
        errors[CONF_DEFAULT_MIN_SOC] = "invalid_soc"
    if not validate_soc(user_input.get(CONF_FORECAST_ERROR_MARGIN, 0)):
        errors[CONF_FORECAST_ERROR_MARGIN] = "invalid_soc"


def _validate_step_power(user_input: dict[str, Any], errors: dict[str, str]) -> None:
    """Validate step 3: charge power settings."""
    min_power = user_input.get(CONF_MIN_CHARGE_POWER_W)
    max_power = user_input.get(CONF_MAX_CHARGE_POWER_W)
    if min_power is not None and min_power <= 0:
        errors[CONF_MIN_CHARGE_POWER_W] = "invalid_power"
    if max_power is not None and max_power <= 0:
        errors[CONF_MAX_CHARGE_POWER_W] = "invalid_power"
    if min_power is not None and max_power is not None and min_power >= max_power:
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


def _validate_step_advanced(
    user_input: dict[str, Any],
    errors: dict[str, str],
    collected: dict[str, Any] | None = None,
) -> None:
    """Validate step 4: advanced options."""
    if not validate_date_optional(user_input.get(CONF_ACTIVE_START_DATE)):
        errors[CONF_ACTIVE_START_DATE] = "invalid_date"
    if not validate_date_optional(user_input.get(CONF_ACTIVE_END_DATE)):
        errors[CONF_ACTIVE_END_DATE] = "invalid_date"
    if user_input.get(CONF_AUTO_EFFICIENT_CHARGE):
        all_data = dict(collected or {})
        all_data.update(user_input)
        if not all_data.get(CONF_CHARGE_POWER_ENTITY):
            errors[CONF_AUTO_EFFICIENT_CHARGE] = "auto_efficiency_requires_power_entities"
        elif not all_data.get(CONF_GRID_IMPORT_ENERGY_ENTITY):
            errors[CONF_AUTO_EFFICIENT_CHARGE] = "auto_efficiency_requires_power_entities"
        elif not all_data.get(CONF_BATTERY_CHARGE_ENERGY_ENTITY):
            errors[CONF_AUTO_EFFICIENT_CHARGE] = "auto_efficiency_requires_power_entities"
        elif not all_data.get(CONF_HOME_CONSUMPTION_ENERGY_ENTITY):
            errors[CONF_AUTO_EFFICIENT_CHARGE] = "auto_efficiency_requires_power_entities"
        elif not all_data.get(CONF_MIN_CHARGE_POWER_W):
            errors[CONF_AUTO_EFFICIENT_CHARGE] = "auto_efficiency_requires_power_entities"
        elif not all_data.get(CONF_MAX_CHARGE_POWER_W):
            errors[CONF_AUTO_EFFICIENT_CHARGE] = "auto_efficiency_requires_power_entities"


# ---------------------------------------------------------------------------
# Schema builders
# ---------------------------------------------------------------------------

def _schema_step_user(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build schema for step 1: core entities."""
    d = defaults or {}
    return vol.Schema({
        vol.Required(CONF_NAME, default=d.get(CONF_NAME, "Inverter Charge Night")): _text_selector(),
        vol.Required(CONF_KOSTAL_MIN_SOC_ENTITY, default=d.get(CONF_KOSTAL_MIN_SOC_ENTITY)): _entity_selector("number"),
        vol.Required(CONF_KOSTAL_GRID_CHARGE_SWITCH, default=d.get(CONF_KOSTAL_GRID_CHARGE_SWITCH)): _entity_selector("switch"),
        vol.Required(CONF_PV_FORECAST_ENTITY, default=d.get(CONF_PV_FORECAST_ENTITY)): _entity_selector(),
        vol.Required(CONF_BATTERY_SOC_ENTITY, default=d.get(CONF_BATTERY_SOC_ENTITY)): _entity_selector("sensor"),
        vol.Required(CONF_BATTERY_CAPACITY, default=d.get(CONF_BATTERY_CAPACITY, 10.0)): _number_selector(
            min_val=0.1, max_val=500, step=0.1, unit="kWh", mode="box"
        ),
    })


def _schema_step_time_soc(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build schema for step 2: time window & SOC."""
    d = defaults or {}
    return vol.Schema({
        vol.Required(CONF_START_TIME, default=d.get(CONF_START_TIME, DEFAULT_START_TIME)): _time_selector(),
        vol.Required(CONF_END_TIME, default=d.get(CONF_END_TIME, DEFAULT_END_TIME)): _time_selector(),
        vol.Required(CONF_USER_MIN_SOC, default=d.get(CONF_USER_MIN_SOC, DEFAULT_MIN_SOC)): _number_selector(
            min_val=0, max_val=100, step=1, unit="%", mode="slider"
        ),
        vol.Required(CONF_USER_MAX_SOC, default=d.get(CONF_USER_MAX_SOC, DEFAULT_MAX_SOC)): _number_selector(
            min_val=0, max_val=100, step=1, unit="%", mode="slider"
        ),
        vol.Required(CONF_FORECAST_ERROR_MARGIN, default=d.get(CONF_FORECAST_ERROR_MARGIN, DEFAULT_FORECAST_ERROR_MARGIN)): _number_selector(
            min_val=0, max_val=100, step=1, unit="%", mode="slider"
        ),
        vol.Required(CONF_DEFAULT_MIN_SOC, default=d.get(CONF_DEFAULT_MIN_SOC, DEFAULT_MIN_SOC)): _number_selector(
            min_val=0, max_val=100, step=1, unit="%", mode="slider"
        ),
    })


def _schema_step_power(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build schema for step 3: charge power."""
    d = defaults or {}
    return vol.Schema({
        vol.Optional(CONF_MIN_CHARGE_POWER_W, default=d.get(CONF_MIN_CHARGE_POWER_W)): vol.Any(
            None, vol.All(vol.Coerce(int), vol.Range(min=100))
        ),
        vol.Optional(CONF_MAX_CHARGE_POWER_W, default=d.get(CONF_MAX_CHARGE_POWER_W)): vol.Any(
            None, vol.All(vol.Coerce(int), vol.Range(min=100))
        ),
        vol.Optional(CONF_ABSOLUTE_MAX_CHARGE_POWER_W, default=d.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_W)): vol.Any(
            None, vol.All(vol.Coerce(int), vol.Range(min=100))
        ),
        vol.Optional(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY, default=d.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY)): vol.Any(
            None, _entity_selector("number")
        ),
        vol.Optional(CONF_CHARGE_POWER_ENTITY, default=d.get(CONF_CHARGE_POWER_ENTITY)): vol.Any(
            None, _entity_selector("number")
        ),
        vol.Optional(CONF_GRID_IMPORT_ENERGY_ENTITY, default=d.get(CONF_GRID_IMPORT_ENERGY_ENTITY)): vol.Any(
            None, _entity_selector("sensor")
        ),
        vol.Optional(CONF_BATTERY_CHARGE_ENERGY_ENTITY, default=d.get(CONF_BATTERY_CHARGE_ENERGY_ENTITY)): vol.Any(
            None, _entity_selector("sensor")
        ),
        vol.Optional(CONF_HOME_CONSUMPTION_ENERGY_ENTITY, default=d.get(CONF_HOME_CONSUMPTION_ENERGY_ENTITY)): vol.Any(
            None, _entity_selector("sensor")
        ),
    })


def _schema_step_advanced(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build schema for step 4: advanced options."""
    d = defaults or {}
    return vol.Schema({
        vol.Required(CONF_UPDATE_INTERVAL, default=d.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)): _number_selector(
            min_val=60, max_val=3600, step=60, unit="s", mode="slider"
        ),
        vol.Required(CONF_COMMAND_DELAY, default=d.get(CONF_COMMAND_DELAY, DEFAULT_COMMAND_DELAY)): _number_selector(
            min_val=0.0, max_val=5.0, step=0.1, unit="s", mode="slider"
        ),
        vol.Optional(
            CONF_ACTIVE_START_DATE,
            default=_default_date_value(d.get(CONF_ACTIVE_START_DATE, DEFAULT_ACTIVE_START_DATE)),
        ): vol.Any(None, _date_selector()),
        vol.Optional(
            CONF_ACTIVE_END_DATE,
            default=_default_date_value(d.get(CONF_ACTIVE_END_DATE, DEFAULT_ACTIVE_END_DATE)),
        ): vol.Any(None, _date_selector()),
        vol.Optional(CONF_BACKUP_MODE_ENTITY, default=d.get(CONF_BACKUP_MODE_ENTITY)): vol.Any(
            None, _entity_selector()
        ),
        vol.Optional(CONF_AUTO_EFFICIENT_CHARGE, default=d.get(CONF_AUTO_EFFICIENT_CHARGE, False)): _boolean_selector(),
    })


def _finalize_data(collected: dict[str, Any]) -> dict[str, Any]:
    """Normalize collected data before storage."""
    data = dict(collected)
    data[CONF_ACTIVE_START_DATE] = _normalize_date_value(data.get(CONF_ACTIVE_START_DATE))
    data[CONF_ACTIVE_END_DATE] = _normalize_date_value(data.get(CONF_ACTIVE_END_DATE))
    return data


# ---------------------------------------------------------------------------
# Config Flow (multi-step wizard)
# ---------------------------------------------------------------------------

class InverterChargeNightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Inverter Charge Night."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._collected: dict[str, Any] = {}

    # -- Step 1: Core Entities -----------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Name, core entities and battery capacity."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _validate_step_user(user_input, errors)
            _validate_entities(
                self.hass, user_input,
                [CONF_KOSTAL_MIN_SOC_ENTITY, CONF_KOSTAL_GRID_CHARGE_SWITCH,
                 CONF_PV_FORECAST_ENTITY, CONF_BATTERY_SOC_ENTITY],
                errors,
            )
            if not errors:
                self._collected.update(user_input)
                return await self.async_step_time_soc()

        return self.async_show_form(
            step_id="user",
            data_schema=_schema_step_user(),
            errors=errors,
        )

    # -- Step 2: Time Window & SOC -------------------------------------------

    async def async_step_time_soc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Time window and SOC settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _validate_step_time_soc(user_input, errors)
            if not errors:
                self._collected.update(user_input)
                return await self.async_step_power()

        return self.async_show_form(
            step_id="time_soc",
            data_schema=_schema_step_time_soc(),
            errors=errors,
        )

    # -- Step 3: Charge Power ------------------------------------------------

    async def async_step_power(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Charge power settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _validate_step_power(user_input, errors)
            _validate_entities(
                self.hass, user_input,
                [CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY, CONF_CHARGE_POWER_ENTITY,
                 CONF_GRID_IMPORT_ENERGY_ENTITY, CONF_BATTERY_CHARGE_ENERGY_ENTITY,
                 CONF_HOME_CONSUMPTION_ENERGY_ENTITY],
                errors,
            )
            if not errors:
                self._collected.update(user_input)
                return await self.async_step_advanced()

        return self.async_show_form(
            step_id="power",
            data_schema=_schema_step_power(),
            errors=errors,
        )

    # -- Step 4: Advanced Options --------------------------------------------

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 4: Advanced options – finalize and create entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _validate_step_advanced(user_input, errors, collected=self._collected)
            _validate_entities(
                self.hass, user_input,
                [CONF_BACKUP_MODE_ENTITY],
                errors,
            )
            if not errors:
                self._collected.update(user_input)
                # Set unique ID
                unique_id = (
                    f"{self._collected.get(CONF_KOSTAL_MIN_SOC_ENTITY)}"
                    f"_{self._collected.get(CONF_BATTERY_SOC_ENTITY)}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                data = _finalize_data(self._collected)
                return self.async_create_entry(
                    title=data.get(CONF_NAME, "Inverter Charge Night"),
                    data=data,
                )

        return self.async_show_form(
            step_id="advanced",
            data_schema=_schema_step_advanced(),
            errors=errors,
        )

    # -- Reconfigure Flow (multi-step) ---------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure step 1: core entities."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        defaults = dict(reconfigure_entry.data)

        if user_input is not None:
            _validate_step_user(user_input, errors)
            _validate_entities(
                self.hass, user_input,
                [CONF_KOSTAL_MIN_SOC_ENTITY, CONF_KOSTAL_GRID_CHARGE_SWITCH,
                 CONF_PV_FORECAST_ENTITY, CONF_BATTERY_SOC_ENTITY],
                errors,
            )
            if not errors:
                self._collected.update(user_input)
                return await self.async_step_reconfigure_time_soc()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema_step_user(defaults),
            errors=errors,
        )

    async def async_step_reconfigure_time_soc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure step 2: time & SOC."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        defaults = dict(reconfigure_entry.data)

        if user_input is not None:
            _validate_step_time_soc(user_input, errors)
            if not errors:
                self._collected.update(user_input)
                return await self.async_step_reconfigure_power()

        return self.async_show_form(
            step_id="reconfigure_time_soc",
            data_schema=_schema_step_time_soc(defaults),
            errors=errors,
        )

    async def async_step_reconfigure_power(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure step 3: power."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        defaults = dict(reconfigure_entry.data)

        if user_input is not None:
            _validate_step_power(user_input, errors)
            _validate_entities(
                self.hass, user_input,
                [CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY, CONF_CHARGE_POWER_ENTITY,
                 CONF_GRID_IMPORT_ENERGY_ENTITY, CONF_BATTERY_CHARGE_ENERGY_ENTITY,
                 CONF_HOME_CONSUMPTION_ENERGY_ENTITY],
                errors,
            )
            if not errors:
                self._collected.update(user_input)
                return await self.async_step_reconfigure_advanced()

        return self.async_show_form(
            step_id="reconfigure_power",
            data_schema=_schema_step_power(defaults),
            errors=errors,
        )

    async def async_step_reconfigure_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure step 4: advanced – finalize."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        defaults = dict(reconfigure_entry.data)

        if user_input is not None:
            _validate_step_advanced(user_input, errors, collected=self._collected)
            _validate_entities(
                self.hass, user_input,
                [CONF_BACKUP_MODE_ENTITY],
                errors,
            )
            if not errors:
                self._collected.update(user_input)
                unique_id = (
                    f"{self._collected.get(CONF_KOSTAL_MIN_SOC_ENTITY)}"
                    f"_{self._collected.get(CONF_BATTERY_SOC_ENTITY)}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                data = _finalize_data(self._collected)
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data=data,
                )

        return self.async_show_form(
            step_id="reconfigure_advanced",
            data_schema=_schema_step_advanced(defaults),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


# ---------------------------------------------------------------------------
# Options Flow (multi-step wizard)
# ---------------------------------------------------------------------------

class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Inverter Charge Night."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._collected: dict[str, Any] = {}

    # -- Step 1: Core Entities -----------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options step 1: core entities."""
        errors: dict[str, str] = {}
        defaults = dict(self._config_entry.data)

        if user_input is not None:
            _validate_step_user(user_input, errors)
            _validate_entities(
                self.hass, user_input,
                [CONF_KOSTAL_MIN_SOC_ENTITY, CONF_KOSTAL_GRID_CHARGE_SWITCH,
                 CONF_PV_FORECAST_ENTITY, CONF_BATTERY_SOC_ENTITY],
                errors,
            )
            if not errors:
                self._collected.update(user_input)
                return await self.async_step_time_soc()

        return self.async_show_form(
            step_id="init",
            data_schema=_schema_step_user(defaults),
            errors=errors,
        )

    # -- Step 2: Time Window & SOC -------------------------------------------

    async def async_step_time_soc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options step 2: time & SOC."""
        errors: dict[str, str] = {}
        defaults = dict(self._config_entry.data)

        if user_input is not None:
            _validate_step_time_soc(user_input, errors)
            if not errors:
                self._collected.update(user_input)
                return await self.async_step_power()

        return self.async_show_form(
            step_id="time_soc",
            data_schema=_schema_step_time_soc(defaults),
            errors=errors,
        )

    # -- Step 3: Charge Power ------------------------------------------------

    async def async_step_power(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options step 3: power."""
        errors: dict[str, str] = {}
        defaults = dict(self._config_entry.data)

        if user_input is not None:
            _validate_step_power(user_input, errors)
            _validate_entities(
                self.hass, user_input,
                [CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY, CONF_CHARGE_POWER_ENTITY,
                 CONF_GRID_IMPORT_ENERGY_ENTITY, CONF_BATTERY_CHARGE_ENERGY_ENTITY,
                 CONF_HOME_CONSUMPTION_ENERGY_ENTITY],
                errors,
            )
            if not errors:
                self._collected.update(user_input)
                return await self.async_step_advanced()

        return self.async_show_form(
            step_id="power",
            data_schema=_schema_step_power(defaults),
            errors=errors,
        )

    # -- Step 4: Advanced Options --------------------------------------------

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options step 4: advanced – finalize."""
        errors: dict[str, str] = {}
        defaults = dict(self._config_entry.data)

        if user_input is not None:
            _validate_step_advanced(user_input, errors, collected=self._collected)
            _validate_entities(
                self.hass, user_input,
                [CONF_BACKUP_MODE_ENTITY],
                errors,
            )
            if not errors:
                self._collected.update(user_input)
                data = _finalize_data(self._collected)
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=data
                )
                return self.async_create_entry(
                    title="", data=dict(self._config_entry.options)
                )

        return self.async_show_form(
            step_id="advanced",
            data_schema=_schema_step_advanced(defaults),
            errors=errors,
        )

