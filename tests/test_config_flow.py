"""Tests for config flow validation helpers and flows."""
from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.inverter_charge_night import config_flow
from custom_components.inverter_charge_night.config_flow import (
    InverterChargeNightConfigFlow,
    OptionsFlowHandler,
    validate_date_optional,
    validate_soc,
    validate_time_format,
)
from homeassistant.const import CONF_NAME
from custom_components.inverter_charge_night.const import (
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_UPDATE_INTERVAL,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)


def test_validate_time_format():
    assert validate_time_format("00:00") is True
    assert validate_time_format("23:59") is True
    assert validate_time_format("24:00") is False
    assert validate_time_format("ab:cd") is False


def test_validate_soc():
    assert validate_soc(0.0) is True
    assert validate_soc(100.0) is True
    assert validate_soc(-1.0) is False
    assert validate_soc(101.0) is False


def test_validate_date_optional():
    assert validate_date_optional(None) is True
    assert validate_date_optional("") is True
    assert validate_date_optional("2025-12-31") is True
    assert validate_date_optional("2025-13-01") is False


@pytest.mark.asyncio
async def test_config_flow_user_success(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass

    entity_registry = MagicMock()
    entity_registry.async_get.return_value = MagicMock()
    mock_hass.states.get.return_value = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)

        user_input = {
            CONF_NAME: "Test",
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_PV_FORECAST_ENTITY: "sensor.forecast",
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
            CONF_BATTERY_CAPACITY: 10.0,
            CONF_START_TIME: "00:00",
            CONF_END_TIME: "05:59",
            CONF_USER_MIN_SOC: 8.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_FORECAST_ERROR_MARGIN: 10.0,
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_UPDATE_INTERVAL: 900,
            CONF_COMMAND_DELAY: 0.1,
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_AUTO_EFFICIENT_CHARGE: False,
        }

        result = await flow.async_step_user(user_input)

    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_options_flow_requires_auto_entities(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass

    entity_registry = MagicMock()
    entity_registry.async_get.return_value = MagicMock()
    mock_hass.states.get.return_value = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)

        user_input = dict(mock_config_entry.data)
        user_input[CONF_AUTO_EFFICIENT_CHARGE] = True
        # Missing required entities
        user_input[CONF_CHARGE_POWER_ENTITY] = None
        user_input[CONF_CHARGE_POWER_SENT_ENTITY] = None
        user_input[CONF_CHARGE_POWER_RECEIVED_ENTITY] = None

        result = await flow.async_step_init(user_input)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_CHARGE_POWER_ENTITY] == "required_entity"
