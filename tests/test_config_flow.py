"""Tests for multi-step config flow, options flow and reconfigure flow."""
from unittest.mock import MagicMock

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from datetime import date

from custom_components.inverter_charge_night.config_flow import (
    InverterChargeNightConfigFlow,
    OptionsFlowHandler,
    _default_date_value,
    _normalize_date_value,
    _validate_step_user,
    _validate_step_time_soc,
    _validate_step_power,
    _validate_step_advanced,
    _validate_entities,
    validate_date_optional,
    validate_soc,
    validate_time_format,
)
from homeassistant.const import CONF_NAME
from custom_components.inverter_charge_night.const import (
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_START_DATE,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_BATTERY_CHARGE_ENERGY_ENTITY,
    CONF_HOME_CONSUMPTION_ENERGY_ENTITY,
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

# ---------------------------------------------------------------------------
# Shared test data helpers
# ---------------------------------------------------------------------------

STEP1_VALID = {
    CONF_NAME: "Test",
    CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
    CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
    CONF_PV_FORECAST_ENTITY: "sensor.forecast",
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_BATTERY_CAPACITY: 10.0,
}

STEP2_VALID = {
    CONF_START_TIME: "00:00",
    CONF_END_TIME: "05:59",
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_DEFAULT_MIN_SOC: 8.0,
}

STEP3_VALID = {
    CONF_MIN_CHARGE_POWER_W: 5000,
    CONF_MAX_CHARGE_POWER_W: 15000,
}

STEP4_VALID = {
    CONF_UPDATE_INTERVAL: 900,
    CONF_COMMAND_DELAY: 0.1,
    CONF_AUTO_EFFICIENT_CHARGE: False,
}


def _mock_entity_ok(mock_hass):
    """Set up mocks so entity validation passes."""
    entity_registry = MagicMock()
    entity_registry.async_get.return_value = MagicMock()
    mock_hass.states.get.return_value = MagicMock()
    return entity_registry


def _mock_entity_missing(mock_hass):
    """Set up mocks so entity validation fails."""
    entity_registry = MagicMock()
    entity_registry.async_get.return_value = None
    mock_hass.states.get.return_value = None
    return entity_registry


# ---------------------------------------------------------------------------
# Pure validation helper tests
# ---------------------------------------------------------------------------

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
    assert validate_date_optional(date(2025, 1, 1)) is True
    assert validate_date_optional(123) is False


def test_date_normalization_helpers():
    assert _normalize_date_value(None) is None
    assert _normalize_date_value("") is None
    assert _normalize_date_value("2025-01-01") == "2025-01-01"
    assert _normalize_date_value("bad") is None
    assert _normalize_date_value(date(2025, 2, 2)) == "2025-02-02"
    assert _normalize_date_value(123) is None

    assert _default_date_value(None) is None
    assert _default_date_value("") is None
    assert _default_date_value("2025-03-03") == date(2025, 3, 3)
    assert _default_date_value("bad") is None
    assert _default_date_value(date(2025, 4, 4)) == date(2025, 4, 4)
    assert _default_date_value(123) is None


def test_validate_step_user_errors():
    errors: dict[str, str] = {}
    _validate_step_user({CONF_BATTERY_CAPACITY: -1}, errors)
    assert errors[CONF_BATTERY_CAPACITY] == "invalid_capacity"


def test_validate_step_time_soc_errors():
    errors: dict[str, str] = {}
    _validate_step_time_soc({
        CONF_START_TIME: "bad", CONF_END_TIME: "bad",
        CONF_USER_MIN_SOC: -1, CONF_USER_MAX_SOC: -2,
        CONF_DEFAULT_MIN_SOC: 999, CONF_FORECAST_ERROR_MARGIN: 999,
    }, errors)
    assert CONF_START_TIME in errors
    assert CONF_END_TIME in errors
    assert CONF_USER_MIN_SOC in errors
    assert CONF_DEFAULT_MIN_SOC in errors
    assert CONF_FORECAST_ERROR_MARGIN in errors


def test_validate_step_power_errors():
    errors: dict[str, str] = {}
    _validate_step_power({
        CONF_MIN_CHARGE_POWER_W: -1, CONF_MAX_CHARGE_POWER_W: -2,
        CONF_ABSOLUTE_MAX_CHARGE_POWER_W: -5,
    }, errors)
    assert CONF_MIN_CHARGE_POWER_W in errors
    assert CONF_MAX_CHARGE_POWER_W in errors
    assert CONF_ABSOLUTE_MAX_CHARGE_POWER_W in errors


def test_validate_step_power_abs_max_entity_without_value():
    errors: dict[str, str] = {}
    _validate_step_power({
        CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000,
        CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.abs",
    }, errors)
    assert errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_W] == "required_value"


def test_validate_step_power_abs_max_value_without_entity():
    errors: dict[str, str] = {}
    _validate_step_power({
        CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000,
        CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 5000,
    }, errors)
    assert errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] == "required_entity"


def test_validate_step_power_min_ge_max():
    errors: dict[str, str] = {}
    _validate_step_power({
        CONF_MIN_CHARGE_POWER_W: 15000, CONF_MAX_CHARGE_POWER_W: 5000,
    }, errors)
    assert errors[CONF_MAX_CHARGE_POWER_W] == "max_power_must_be_greater_than_min"


def test_validate_step_advanced_errors():
    errors: dict[str, str] = {}
    _validate_step_advanced({
        CONF_ACTIVE_START_DATE: "2025-13-01", CONF_ACTIVE_END_DATE: "2025-13-02",
        CONF_AUTO_EFFICIENT_CHARGE: True,
    }, errors)
    assert CONF_ACTIVE_START_DATE in errors
    assert CONF_ACTIVE_END_DATE in errors
    assert CONF_AUTO_EFFICIENT_CHARGE in errors
    assert errors[CONF_AUTO_EFFICIENT_CHARGE] == "auto_efficiency_requires_power_entities"


def test_validate_step_advanced_auto_efficiency_missing_sent():
    errors: dict[str, str] = {}
    _validate_step_advanced(
        {CONF_AUTO_EFFICIENT_CHARGE: True},
        errors,
        collected={
            CONF_CHARGE_POWER_ENTITY: "number.x",
            CONF_MIN_CHARGE_POWER_W: 1000,
            CONF_MAX_CHARGE_POWER_W: 10000,
        },
    )
    assert errors[CONF_AUTO_EFFICIENT_CHARGE] == "auto_efficiency_requires_power_entities"


def test_validate_step_advanced_auto_efficiency_missing_received():
    errors: dict[str, str] = {}
    _validate_step_advanced(
        {CONF_AUTO_EFFICIENT_CHARGE: True},
        errors,
        collected={
            CONF_CHARGE_POWER_ENTITY: "number.x",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_MIN_CHARGE_POWER_W: 1000,
            CONF_MAX_CHARGE_POWER_W: 10000,
        },
    )
    assert errors[CONF_AUTO_EFFICIENT_CHARGE] == "auto_efficiency_requires_power_entities"


def test_validate_step_advanced_auto_efficiency_missing_home_energy():
    errors: dict[str, str] = {}
    _validate_step_advanced(
        {CONF_AUTO_EFFICIENT_CHARGE: True},
        errors,
        collected={
            CONF_CHARGE_POWER_ENTITY: "number.x",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_MAX_CHARGE_POWER_W: 10000,
        },
    )
    assert errors[CONF_AUTO_EFFICIENT_CHARGE] == "auto_efficiency_requires_power_entities"


def test_validate_step_advanced_auto_efficiency_missing_min_power():
    errors: dict[str, str] = {}
    _validate_step_advanced(
        {CONF_AUTO_EFFICIENT_CHARGE: True},
        errors,
        collected={
            CONF_CHARGE_POWER_ENTITY: "number.x",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
            CONF_MAX_CHARGE_POWER_W: 10000,
        },
    )
    assert errors[CONF_AUTO_EFFICIENT_CHARGE] == "auto_efficiency_requires_power_entities"


def test_validate_step_advanced_auto_efficiency_missing_max_power():
    errors: dict[str, str] = {}
    _validate_step_advanced(
        {CONF_AUTO_EFFICIENT_CHARGE: True},
        errors,
        collected={
            CONF_CHARGE_POWER_ENTITY: "number.x",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
            CONF_MIN_CHARGE_POWER_W: 1000,
        },
    )
    assert errors[CONF_AUTO_EFFICIENT_CHARGE] == "auto_efficiency_requires_power_entities"


def test_validate_step_advanced_auto_efficiency_missing_home():
    errors: dict[str, str] = {}
    _validate_step_advanced(
        {CONF_AUTO_EFFICIENT_CHARGE: True},
        errors,
        collected={
            CONF_CHARGE_POWER_ENTITY: "number.x",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_MIN_CHARGE_POWER_W: 1000,
            CONF_MAX_CHARGE_POWER_W: 10000,
        },
    )
    assert errors[CONF_AUTO_EFFICIENT_CHARGE] == "auto_efficiency_requires_power_entities"


def test_validate_step_advanced_auto_efficiency_all_present():
    errors: dict[str, str] = {}
    _validate_step_advanced(
        {CONF_AUTO_EFFICIENT_CHARGE: True},
        errors,
        collected={
            CONF_CHARGE_POWER_ENTITY: "number.x",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
            CONF_MIN_CHARGE_POWER_W: 1000,
            CONF_MAX_CHARGE_POWER_W: 10000,
        },
    )
    assert CONF_AUTO_EFFICIENT_CHARGE not in errors


def test_validate_entities_missing(mock_hass):
    entity_registry = _mock_entity_missing(mock_hass)
    errors: dict[str, str] = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        _validate_entities(mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: "number.x"}, [CONF_KOSTAL_MIN_SOC_ENTITY], errors)
    assert errors[CONF_KOSTAL_MIN_SOC_ENTITY] == "invalid_entity"


def test_validate_entities_ok(mock_hass):
    entity_registry = _mock_entity_ok(mock_hass)
    errors: dict[str, str] = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        _validate_entities(mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: "number.x"}, [CONF_KOSTAL_MIN_SOC_ENTITY], errors)
    assert not errors


def test_validate_entities_skips_empty(mock_hass):
    entity_registry = _mock_entity_missing(mock_hass)
    errors: dict[str, str] = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        _validate_entities(mock_hass, {}, [CONF_KOSTAL_MIN_SOC_ENTITY], errors)
    assert not errors


# ---------------------------------------------------------------------------
# Config Flow – multi-step wizard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_flow_shows_form_without_input(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    result = await flow.async_step_user(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_config_flow_step1_validation_error(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_user({**STEP1_VALID, CONF_BATTERY_CAPACITY: -1})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_BATTERY_CAPACITY] == "invalid_capacity"


@pytest.mark.asyncio
async def test_config_flow_step1_invalid_entity(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    entity_registry = _mock_entity_missing(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_user(STEP1_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_KOSTAL_MIN_SOC_ENTITY] == "invalid_entity"


@pytest.mark.asyncio
async def test_config_flow_step1_to_step2(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_user(STEP1_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "time_soc"


@pytest.mark.asyncio
async def test_config_flow_step2_shows_form(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    result = await flow.async_step_time_soc(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "time_soc"


@pytest.mark.asyncio
async def test_config_flow_step2_validation_error(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    result = await flow.async_step_time_soc({
        **STEP2_VALID, CONF_USER_MIN_SOC: 60.0, CONF_USER_MAX_SOC: 50.0,
    })
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_USER_MAX_SOC] == "max_soc_must_be_greater_than_min"


@pytest.mark.asyncio
async def test_config_flow_step2_to_step3(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_time_soc(STEP2_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "power"


@pytest.mark.asyncio
async def test_config_flow_step3_shows_form(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    result = await flow.async_step_power(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "power"


@pytest.mark.asyncio
async def test_config_flow_step3_validation_error(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_power({
            CONF_MIN_CHARGE_POWER_W: 15000, CONF_MAX_CHARGE_POWER_W: 5000,
        })
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_MAX_CHARGE_POWER_W] == "max_power_must_be_greater_than_min"


@pytest.mark.asyncio
async def test_config_flow_step3_to_step4(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_power(STEP3_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "advanced"


@pytest.mark.asyncio
async def test_config_flow_step4_shows_form(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    result = await flow.async_step_advanced(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "advanced"


@pytest.mark.asyncio
async def test_config_flow_step4_validation_error(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_advanced({
            **STEP4_VALID, CONF_ACTIVE_START_DATE: "2025-13-01",
        })
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_ACTIVE_START_DATE] == "invalid_date"


@pytest.mark.asyncio
async def test_config_flow_full_wizard_creates_entry(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    mock_hass.config_entries.async_entry_for_domain_unique_id = MagicMock(return_value=None)
    entity_registry = _mock_entity_ok(mock_hass)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        r1 = await flow.async_step_user(STEP1_VALID)
        assert r1["step_id"] == "time_soc"
        r2 = await flow.async_step_time_soc(STEP2_VALID)
        assert r2["step_id"] == "power"
        r3 = await flow.async_step_power(STEP3_VALID)
        assert r3["step_id"] == "advanced"
        r4 = await flow.async_step_advanced(STEP4_VALID)

    assert r4["type"] == FlowResultType.CREATE_ENTRY
    assert r4["title"] == "Test"


# ---------------------------------------------------------------------------
# Options Flow – multi-step wizard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_options_flow_shows_form(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    result = await flow.async_step_init(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"


@pytest.mark.asyncio
async def test_options_flow_step1_validation_error(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_init({**STEP1_VALID, CONF_BATTERY_CAPACITY: -1})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_BATTERY_CAPACITY] == "invalid_capacity"


@pytest.mark.asyncio
async def test_options_flow_step1_to_step2(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_init(STEP1_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "time_soc"


@pytest.mark.asyncio
async def test_options_flow_step2_shows_form(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    result = await flow.async_step_time_soc(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "time_soc"


@pytest.mark.asyncio
async def test_options_flow_step2_validation_error(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    result = await flow.async_step_time_soc({
        **STEP2_VALID, CONF_START_TIME: "bad",
    })
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_START_TIME] == "invalid_time"


@pytest.mark.asyncio
async def test_options_flow_step2_to_step3(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_time_soc(STEP2_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "power"


@pytest.mark.asyncio
async def test_options_flow_step3_shows_form(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    result = await flow.async_step_power(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "power"


@pytest.mark.asyncio
async def test_options_flow_step3_validation_error(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_power({
            CONF_MIN_CHARGE_POWER_W: -1, CONF_MAX_CHARGE_POWER_W: -2,
        })
    assert result["type"] == FlowResultType.FORM
    assert CONF_MIN_CHARGE_POWER_W in result["errors"]


@pytest.mark.asyncio
async def test_options_flow_step3_to_step4(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_power(STEP3_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "advanced"


@pytest.mark.asyncio
async def test_options_flow_step4_shows_form(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    result = await flow.async_step_advanced(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "advanced"


@pytest.mark.asyncio
async def test_options_flow_step4_validation_error(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_advanced({
            **STEP4_VALID, CONF_ACTIVE_END_DATE: "2025-13-01",
        })
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_ACTIVE_END_DATE] == "invalid_date"


@pytest.mark.asyncio
async def test_options_flow_full_wizard(mock_hass, mock_config_entry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass
    mock_hass.config_entries.async_update_entry = MagicMock()
    mock_config_entry.options = {"auto_efficiency_data": {}}
    entity_registry = _mock_entity_ok(mock_hass)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        r1 = await flow.async_step_init(STEP1_VALID)
        assert r1["step_id"] == "time_soc"
        r2 = await flow.async_step_time_soc(STEP2_VALID)
        assert r2["step_id"] == "power"
        r3 = await flow.async_step_power(STEP3_VALID)
        assert r3["step_id"] == "advanced"
        r4 = await flow.async_step_advanced(STEP4_VALID)

    assert r4["type"] == FlowResultType.CREATE_ENTRY
    mock_hass.config_entries.async_update_entry.assert_called_once()


def test_async_get_options_flow_returns_handler(mock_hass):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    handler = flow.async_get_options_flow(MagicMock())
    assert isinstance(handler, OptionsFlowHandler)


# ---------------------------------------------------------------------------
# Reconfigure Flow – multi-step wizard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconfigure_flow_shows_form(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    result = await flow.async_step_reconfigure(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"


@pytest.mark.asyncio
async def test_reconfigure_step1_validation_error(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_reconfigure({**STEP1_VALID, CONF_BATTERY_CAPACITY: -1})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_BATTERY_CAPACITY] == "invalid_capacity"


@pytest.mark.asyncio
async def test_reconfigure_step1_to_step2(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_reconfigure(STEP1_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_time_soc"


@pytest.mark.asyncio
async def test_reconfigure_step2_shows_form(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    result = await flow.async_step_reconfigure_time_soc(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_time_soc"


@pytest.mark.asyncio
async def test_reconfigure_step2_validation_error(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    result = await flow.async_step_reconfigure_time_soc({
        **STEP2_VALID, CONF_END_TIME: "bad",
    })
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_END_TIME] == "invalid_time"


@pytest.mark.asyncio
async def test_reconfigure_step2_to_step3(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_reconfigure_time_soc(STEP2_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_power"


@pytest.mark.asyncio
async def test_reconfigure_step3_shows_form(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    result = await flow.async_step_reconfigure_power(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_power"


@pytest.mark.asyncio
async def test_reconfigure_step3_validation_error(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_reconfigure_power({
            CONF_MIN_CHARGE_POWER_W: -1, CONF_MAX_CHARGE_POWER_W: -2,
        })
    assert result["type"] == FlowResultType.FORM
    assert CONF_MIN_CHARGE_POWER_W in result["errors"]


@pytest.mark.asyncio
async def test_reconfigure_step3_to_step4(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_reconfigure_power(STEP3_VALID)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_advanced"


@pytest.mark.asyncio
async def test_reconfigure_step4_shows_form(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    result = await flow.async_step_reconfigure_advanced(None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_advanced"


@pytest.mark.asyncio
async def test_reconfigure_step4_validation_error(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    entity_registry = _mock_entity_ok(mock_hass)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        result = await flow.async_step_reconfigure_advanced({
            **STEP4_VALID, CONF_AUTO_EFFICIENT_CHARGE: True,
        })
    assert result["type"] == FlowResultType.FORM
    assert CONF_AUTO_EFFICIENT_CHARGE in result["errors"]


@pytest.mark.asyncio
async def test_reconfigure_full_wizard(mock_hass, mock_config_entry):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    mock_hass.config_entries.async_entry_for_domain_unique_id = MagicMock(return_value=None)
    flow._get_reconfigure_entry = MagicMock(return_value=mock_config_entry)
    flow.async_update_reload_and_abort = MagicMock(
        return_value={"type": FlowResultType.ABORT, "reason": "reconfigure_successful"}
    )
    entity_registry = _mock_entity_ok(mock_hass)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: entity_registry)
        r1 = await flow.async_step_reconfigure(STEP1_VALID)
        assert r1["step_id"] == "reconfigure_time_soc"
        r2 = await flow.async_step_reconfigure_time_soc(STEP2_VALID)
        assert r2["step_id"] == "reconfigure_power"
        r3 = await flow.async_step_reconfigure_power(STEP3_VALID)
        assert r3["step_id"] == "reconfigure_advanced"
        r4 = await flow.async_step_reconfigure_advanced(STEP4_VALID)

    assert r4["type"] == FlowResultType.ABORT
    assert r4["reason"] == "reconfigure_successful"
