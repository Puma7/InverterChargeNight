"""Config flow for Inverter Charge Night integration."""

import logging
from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_OPERATION_MODE,
    CONF_UPDATE_INTERVAL,
    CONF_COMMAND_DELAY,
    CONF_ACTIVE_START_DATE,
    CONF_ACTIVE_END_DATE,
    CONF_BACKUP_MODE_ENTITY,
    CONF_MIN_CHARGE_POWER_W,
    CONF_MAX_CHARGE_POWER_W,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
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
    DEFAULT_OPERATION_MODE,
    DEFAULT_START_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_COMMAND_DELAY,
    DEFAULT_ACTIVE_START_DATE,
    DEFAULT_ACTIVE_END_DATE,
    DEFAULT_MIN_CHARGE_POWER_W,
    DEFAULT_MAX_CHARGE_POWER_W,
    DEFAULT_ABSOLUTE_MAX_CHARGE_POWER_W,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
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
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


def _normalize_date_value(value: str | date | None) -> str | None:
    """Normalize date selector values to ISO strings for storage."""
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        parsed = date.fromisoformat(value)
        return parsed.isoformat()
    except (ValueError, TypeError):
        return None


def _default_date_value(value: str | date | None) -> date | None:
    """Convert stored ISO string to date for selector defaults."""
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class InverterChargeNightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Inverter Charge Night."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate inputs
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
            if user_input.get(CONF_AUTO_EFFICIENT_CHARGE):
                if not user_input.get(CONF_CHARGE_POWER_ENTITY):
                    errors[CONF_CHARGE_POWER_ENTITY] = "required_entity"
                if not user_input.get(CONF_CHARGE_POWER_SENT_ENTITY):
                    errors[CONF_CHARGE_POWER_SENT_ENTITY] = "required_entity"
                if not user_input.get(CONF_CHARGE_POWER_RECEIVED_ENTITY):
                    errors[CONF_CHARGE_POWER_RECEIVED_ENTITY] = "required_entity"

            # Validate entities exist
            entity_registry = er.async_get(self.hass)
            for entity_key in [
                CONF_KOSTAL_MIN_SOC_ENTITY,
                CONF_KOSTAL_GRID_CHARGE_SWITCH,
                CONF_PV_FORECAST_ENTITY,
                CONF_BATTERY_SOC_ENTITY,
                CONF_BACKUP_MODE_ENTITY,
                CONF_CHARGE_POWER_ENTITY,
                CONF_CHARGE_POWER_SENT_ENTITY,
                CONF_CHARGE_POWER_RECEIVED_ENTITY,
                CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
                CONF_PV_FORECAST_TODAY_ENTITY,
                CONF_FORCE_DISCHARGE_SWITCH,
            ]:
                entity_id = user_input.get(entity_key)
                if entity_id:
                    entity = entity_registry.async_get(entity_id)
                    if not entity:
                        # Check if state exists
                        if not self.hass.states.get(entity_id):
                            errors[entity_key] = "invalid_entity"

            if not errors:
                user_input = dict(user_input)
                user_input[CONF_ACTIVE_START_DATE] = _normalize_date_value(
                    user_input.get(CONF_ACTIVE_START_DATE)
                )
                user_input[CONF_ACTIVE_END_DATE] = _normalize_date_value(
                    user_input.get(CONF_ACTIVE_END_DATE)
                )
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, "Inverter Charge Night"),
                    data=user_input,
                )

        # Build schema with entity selectors
        # Labels come from strings.json
        schema = {
            vol.Required(CONF_NAME, default="Inverter Charge Night"): str,
            vol.Required(
                CONF_OPERATION_MODE, default=DEFAULT_OPERATION_MODE
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[MODE_NIGHT_CHARGE, MODE_MORNING_DISCHARGE],
                    translation_key="operation_mode",
                )
            ),
            vol.Required(CONF_KOSTAL_MIN_SOC_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="number")
            ),
            vol.Required(CONF_KOSTAL_GRID_CHARGE_SWITCH): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch")
            ),
            vol.Required(CONF_PV_FORECAST_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig()
            ),
            vol.Required(CONF_BATTERY_SOC_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_BATTERY_CAPACITY, default=10.0): vol.Coerce(float),
            vol.Required(CONF_START_TIME, default=DEFAULT_START_TIME): str,
            vol.Required(CONF_END_TIME, default=DEFAULT_END_TIME): str,
            vol.Required(CONF_USER_MIN_SOC, default=DEFAULT_MIN_SOC): vol.Coerce(float),
            vol.Required(CONF_USER_MAX_SOC, default=DEFAULT_MAX_SOC): vol.Coerce(float),
            vol.Required(
                CONF_FORECAST_ERROR_MARGIN, default=DEFAULT_FORECAST_ERROR_MARGIN
            ): vol.Coerce(float),
            vol.Required(CONF_DEFAULT_MIN_SOC, default=DEFAULT_MIN_SOC): vol.Coerce(
                float
            ),
            vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): vol.All(
                vol.Coerce(int), vol.Range(min=60, max=3600)
            ),
            vol.Required(CONF_COMMAND_DELAY, default=DEFAULT_COMMAND_DELAY): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=5.0)
            ),
            vol.Optional(
                CONF_ACTIVE_START_DATE,
                default=_default_date_value(DEFAULT_ACTIVE_START_DATE),
            ): selector.DateSelector(),
            vol.Optional(
                CONF_ACTIVE_END_DATE,
                default=_default_date_value(DEFAULT_ACTIVE_END_DATE),
            ): selector.DateSelector(),
            vol.Optional(CONF_BACKUP_MODE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig()
            ),
            vol.Optional(
                CONF_ABSOLUTE_MAX_CHARGE_POWER_W, default=None
            ): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=100))),
            vol.Optional(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="number")
            ),
            vol.Required(
                CONF_MIN_CHARGE_POWER_W, default=DEFAULT_MIN_CHARGE_POWER_W
            ): vol.All(vol.Coerce(int), vol.Range(min=100)),
            vol.Required(
                CONF_MAX_CHARGE_POWER_W, default=DEFAULT_MAX_CHARGE_POWER_W
            ): vol.All(vol.Coerce(int), vol.Range(min=100)),
            vol.Optional(CONF_CHARGE_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="number")
            ),
            vol.Optional(CONF_CHARGE_POWER_SENT_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_CHARGE_POWER_RECEIVED_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_AUTO_EFFICIENT_CHARGE, default=False): bool,
            vol.Optional(CONF_PV_FORECAST_TODAY_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig()
            ),
            vol.Optional(CONF_FORCE_DISCHARGE_SWITCH): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch")
            ),
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Inverter Charge Night."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate inputs
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
            if user_input.get(CONF_AUTO_EFFICIENT_CHARGE):
                if not user_input.get(CONF_CHARGE_POWER_ENTITY):
                    errors[CONF_CHARGE_POWER_ENTITY] = "required_entity"
                if not user_input.get(CONF_CHARGE_POWER_SENT_ENTITY):
                    errors[CONF_CHARGE_POWER_SENT_ENTITY] = "required_entity"
                if not user_input.get(CONF_CHARGE_POWER_RECEIVED_ENTITY):
                    errors[CONF_CHARGE_POWER_RECEIVED_ENTITY] = "required_entity"

            # Validate entities exist
            entity_registry = er.async_get(self.hass)
            for entity_key in [
                CONF_KOSTAL_MIN_SOC_ENTITY,
                CONF_KOSTAL_GRID_CHARGE_SWITCH,
                CONF_PV_FORECAST_ENTITY,
                CONF_BATTERY_SOC_ENTITY,
                CONF_BACKUP_MODE_ENTITY,
                CONF_CHARGE_POWER_ENTITY,
                CONF_CHARGE_POWER_SENT_ENTITY,
                CONF_CHARGE_POWER_RECEIVED_ENTITY,
                CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
                CONF_PV_FORECAST_TODAY_ENTITY,
                CONF_FORCE_DISCHARGE_SWITCH,
            ]:
                entity_id = user_input.get(entity_key)
                if entity_id:
                    entity = entity_registry.async_get(entity_id)
                    if not entity:
                        # Check if state exists
                        if not self.hass.states.get(entity_id):
                            errors[entity_key] = "invalid_entity"

            if not errors:
                user_input = dict(user_input)
                user_input[CONF_ACTIVE_START_DATE] = _normalize_date_value(
                    user_input.get(CONF_ACTIVE_START_DATE)
                )
                user_input[CONF_ACTIVE_END_DATE] = _normalize_date_value(
                    user_input.get(CONF_ACTIVE_END_DATE)
                )
                # Update the config entry with new data
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=user_input
                )
                # Preserve existing options (auto-efficiency history lives there)
                return self.async_create_entry(
                    title="", data=dict(self._config_entry.options)
                )

        # Build schema with current values as defaults
        current_data = self._config_entry.data
        schema = {
            vol.Required(CONF_NAME, default=current_data.get(CONF_NAME, "Inverter Charge Night")): str,
            vol.Required(
                CONF_OPERATION_MODE,
                default=current_data.get(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[MODE_NIGHT_CHARGE, MODE_MORNING_DISCHARGE],
                    translation_key="operation_mode",
                )
            ),
            vol.Required(
                CONF_KOSTAL_MIN_SOC_ENTITY,
                default=current_data.get(CONF_KOSTAL_MIN_SOC_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
            vol.Required(
                CONF_KOSTAL_GRID_CHARGE_SWITCH,
                default=current_data.get(CONF_KOSTAL_GRID_CHARGE_SWITCH),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            vol.Required(
                CONF_PV_FORECAST_ENTITY,
                default=current_data.get(CONF_PV_FORECAST_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),
            vol.Required(
                CONF_BATTERY_SOC_ENTITY,
                default=current_data.get(CONF_BATTERY_SOC_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_BATTERY_CAPACITY,
                default=current_data.get(CONF_BATTERY_CAPACITY, 10.0),
            ): vol.Coerce(float),
            vol.Required(
                CONF_START_TIME,
                default=current_data.get(CONF_START_TIME, DEFAULT_START_TIME),
            ): str,
            vol.Required(
                CONF_END_TIME,
                default=current_data.get(CONF_END_TIME, DEFAULT_END_TIME),
            ): str,
            vol.Required(
                CONF_USER_MIN_SOC,
                default=current_data.get(CONF_USER_MIN_SOC, DEFAULT_MIN_SOC),
            ): vol.Coerce(float),
            vol.Required(
                CONF_USER_MAX_SOC,
                default=current_data.get(CONF_USER_MAX_SOC, DEFAULT_MAX_SOC),
            ): vol.Coerce(float),
            vol.Required(
                CONF_FORECAST_ERROR_MARGIN,
                default=current_data.get(CONF_FORECAST_ERROR_MARGIN, DEFAULT_FORECAST_ERROR_MARGIN),
            ): vol.Coerce(float),
            vol.Required(
                CONF_DEFAULT_MIN_SOC,
                default=current_data.get(CONF_DEFAULT_MIN_SOC, DEFAULT_MIN_SOC),
            ): vol.Coerce(float),
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=current_data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
            vol.Required(
                CONF_COMMAND_DELAY,
                default=current_data.get(CONF_COMMAND_DELAY, DEFAULT_COMMAND_DELAY),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5.0)),
            vol.Optional(
                CONF_ACTIVE_START_DATE,
                default=_default_date_value(
                    current_data.get(CONF_ACTIVE_START_DATE, DEFAULT_ACTIVE_START_DATE)
                ),
            ): selector.DateSelector(),
            vol.Optional(
                CONF_ACTIVE_END_DATE,
                default=_default_date_value(
                    current_data.get(CONF_ACTIVE_END_DATE, DEFAULT_ACTIVE_END_DATE)
                ),
            ): selector.DateSelector(),
            vol.Optional(
                CONF_BACKUP_MODE_ENTITY,
                default=current_data.get(CONF_BACKUP_MODE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),
            vol.Optional(
                CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
                default=current_data.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_W),
            ): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=100))),
            vol.Optional(
                CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
                default=current_data.get(CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
            vol.Required(
                CONF_MIN_CHARGE_POWER_W,
                default=current_data.get(CONF_MIN_CHARGE_POWER_W, DEFAULT_MIN_CHARGE_POWER_W),
            ): vol.All(vol.Coerce(int), vol.Range(min=100)),
            vol.Required(
                CONF_MAX_CHARGE_POWER_W,
                default=current_data.get(CONF_MAX_CHARGE_POWER_W, DEFAULT_MAX_CHARGE_POWER_W),
            ): vol.All(vol.Coerce(int), vol.Range(min=100)),
            vol.Optional(
                CONF_CHARGE_POWER_ENTITY,
                default=current_data.get(CONF_CHARGE_POWER_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
            vol.Optional(
                CONF_CHARGE_POWER_SENT_ENTITY,
                default=current_data.get(CONF_CHARGE_POWER_SENT_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_CHARGE_POWER_RECEIVED_ENTITY,
                default=current_data.get(CONF_CHARGE_POWER_RECEIVED_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_AUTO_EFFICIENT_CHARGE,
                default=current_data.get(CONF_AUTO_EFFICIENT_CHARGE, False),
            ): bool,
            vol.Optional(
                CONF_PV_FORECAST_TODAY_ENTITY,
                default=current_data.get(CONF_PV_FORECAST_TODAY_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig()),
            vol.Optional(
                CONF_FORCE_DISCHARGE_SWITCH,
                default=current_data.get(CONF_FORCE_DISCHARGE_SWITCH),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
        }

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

