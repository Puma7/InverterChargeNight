"""Branch coverage for the shared config-flow validation helpers.

The wizard tests in ``test_config_flow.py`` walk the happy path and the time
errors; this module drives the remaining validation branches directly, so that
every rejection a user can trigger has a test naming the error it produces.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.inverter_charge_night import config_flow
from custom_components.inverter_charge_night.config_flow import (
    OptionsFlowHandler,
    _normalize_date_value,
    validate_date_optional,
)
from custom_components.inverter_charge_night.const import (
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_OPERATION_MODE,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_START_DATE,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_BATTERY_CAPACITY,
    CONF_CHARGE_EFFICIENCY,
    CONF_DAY_PRICE_CT,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FEED_IN_PRICE_CT,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_NIGHT_PRICE_CT,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)


def _valid_input() -> dict:
    """A submission that passes every check, to be broken one field at a time."""
    return {
        CONF_START_TIME: "23:01",
        CONF_END_TIME: "04:58",
        CONF_USER_MIN_SOC: 8.0,
        CONF_USER_MAX_SOC: 100.0,
        CONF_BATTERY_CAPACITY: 10.0,
        CONF_DEFAULT_MIN_SOC: 8.0,
        CONF_MIN_CHARGE_POWER_W: 5000,
        CONF_MAX_CHARGE_POWER_W: 15000,
    }


def _errors(mock_hass, **overrides) -> dict:
    user_input = _valid_input() | overrides
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: MagicMock())
        return config_flow._validate_user_input(user_input, mock_hass)


def test_a_valid_submission_reports_no_errors(mock_hass):
    assert _errors(mock_hass) == {}


# --- dates -------------------------------------------------------------------


def test_a_date_object_passes_the_optional_date_check():
    """The date selector hands over a date, not a string."""
    assert validate_date_optional(date(2026, 3, 1)) is True


def test_a_date_object_is_normalized_to_its_iso_string():
    assert _normalize_date_value(date(2026, 3, 1)) == "2026-03-01"


@pytest.mark.parametrize("key", [CONF_ACTIVE_START_DATE, CONF_ACTIVE_END_DATE])
def test_an_unparsable_season_date_is_rejected(mock_hass, key):
    assert _errors(mock_hass, **{key: "31.03.2026"})[key] == "invalid_date"


# --- SOC, capacity and power -------------------------------------------------


@pytest.mark.parametrize(
    "key,value",
    [
        (CONF_USER_MIN_SOC, -1.0),
        (CONF_USER_MAX_SOC, 101.0),
        (CONF_DEFAULT_MIN_SOC, 120.0),
    ],
)
def test_a_soc_outside_zero_to_hundred_is_rejected(mock_hass, key, value):
    assert _errors(mock_hass, **{key: value})[key] == "invalid_soc"


def test_a_battery_capacity_of_zero_is_rejected(mock_hass):
    errors = _errors(mock_hass, **{CONF_BATTERY_CAPACITY: 0.0})
    assert errors[CONF_BATTERY_CAPACITY] == "invalid_capacity"


# --- the absolute charge power pair ------------------------------------------


def test_an_absolute_max_power_of_zero_is_rejected(mock_hass):
    """Zero would order "charge with at most nothing" for the whole window."""
    errors = _errors(mock_hass, **{CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 0})
    assert errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_W] == "invalid_power"
    assert errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] == "required_entity"


def test_an_absolute_max_power_without_its_entity_is_rejected(mock_hass):
    errors = _errors(mock_hass, **{CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 12000})
    assert errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] == "required_entity"
    assert CONF_ABSOLUTE_MAX_CHARGE_POWER_W not in errors


def test_an_absolute_max_entity_without_its_value_is_rejected(mock_hass):
    errors = _errors(
        mock_hass, **{CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.max_charge"}
    )
    assert errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_W] == "required_value"


# --- planner inputs ----------------------------------------------------------


def test_a_house_load_of_zero_is_rejected(mock_hass):
    """A zero average load would make the bridge reserve meaningless."""
    errors = _errors(mock_hass, **{CONF_AVG_HOUSE_LOAD_KW: 0.0})
    assert errors[CONF_AVG_HOUSE_LOAD_KW] == "invalid_power"


@pytest.mark.parametrize("efficiency", [0.0, 1.5, -0.2])
def test_an_efficiency_outside_zero_to_one_is_rejected(mock_hass, efficiency):
    errors = _errors(mock_hass, **{CONF_CHARGE_EFFICIENCY: efficiency})
    assert errors[CONF_CHARGE_EFFICIENCY] == "invalid_efficiency"


def test_a_full_set_of_efficiency_and_load_passes(mock_hass):
    assert _errors(mock_hass, **{CONF_AVG_HOUSE_LOAD_KW: 0.4, CONF_CHARGE_EFFICIENCY: 0.9}) == {}


# --- prices come as a set or not at all --------------------------------------


def test_one_price_alone_asks_for_the_other_two(mock_hass):
    """The saving is a difference of three prices; a single one says nothing."""
    errors = _errors(mock_hass, **{CONF_NIGHT_PRICE_CT: 18.0})
    assert errors[CONF_DAY_PRICE_CT] == "all_prices_required"
    assert errors[CONF_FEED_IN_PRICE_CT] == "all_prices_required"
    assert CONF_NIGHT_PRICE_CT not in errors


def test_all_three_prices_together_pass(mock_hass):
    assert (
        _errors(
            mock_hass,
            **{
                CONF_NIGHT_PRICE_CT: 18.0,
                CONF_DAY_PRICE_CT: 32.0,
                CONF_FEED_IN_PRICE_CT: 7.9,
            },
        )
        == {}
    )


# --- the discharge mode needs the switch that discharges ----------------------


def test_discharge_mode_without_its_switch_is_rejected(mock_hass):
    """Without it the mode raises the floor, stops charging and never discharges."""
    errors = _errors(mock_hass, **{CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE})
    assert errors[CONF_FORCE_DISCHARGE_SWITCH] == "required_for_discharge_mode"


def test_discharge_mode_with_its_switch_passes(mock_hass):
    assert (
        _errors(
            mock_hass,
            **{
                CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE,
                CONF_FORCE_DISCHARGE_SWITCH: "switch.force_discharge",
            },
        )
        == {}
    )


def test_night_charge_does_not_need_the_discharge_switch(mock_hass):
    """The switch is genuinely optional in the mode that never discharges."""
    assert _errors(mock_hass, **{CONF_OPERATION_MODE: MODE_NIGHT_CHARGE}) == {}


# --- options flow wiring -----------------------------------------------------


def test_the_handler_offers_an_options_flow(mock_config_entry):
    flow = config_flow.InverterChargeNightConfigFlow.async_get_options_flow(
        mock_config_entry
    )
    assert isinstance(flow, OptionsFlowHandler)
