"""3.14.6: the night of 3/4 November 2026.

At 23:01 the plan bought 64.1 %, at 00:16 87.4 %. The weather factor had
switched on: 5.7 °C forecast against a fortnight of 8.4 °C, 5.1 kWh more per
kelvin, 47.5 % more load. The model behind it had 11 days, r² 0.56 and a base
load of about -4.7 kWh a day - a house that draws nothing at the heating limit
and less than nothing above it. And the log line said nothing about a factor.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.inverter_charge_night.const import (
    CONF_WEATHER_FACTOR_MAX,
    DEFAULT_WEATHER_FACTOR_MAX,
    WEATHER_FACTOR_MAX_RANGE,
    WEATHER_FACTOR_PRESETS,
)
from custom_components.inverter_charge_night.heating import (
    FACTOR_MAX,
    MIN_FIT_DAYS,
    fit_heating_model,
    heating_degrees,
    parse_factor_max,
    temperature_factor,
)
from custom_components.inverter_charge_night import config_flow
from custom_components.inverter_charge_night.coordinator import _hours_in_local_day
from tests.test_config_flow_validation import _errors
from tests.test_planner_015 import (
    COORDINATOR,
    NIGHT,
    PLAN_DAY,
    _coordinator,
    _forecast_service,
    _fortnight,
    _weather_coordinator,
)

WEATHER = "weather.home"
COLD = [{"datetime": "2026-01-15T11:00:00+00:00", "temperature": -2.0, "templow": -8.0}]
FROST = [{"datetime": "2026-01-15T11:00:00+00:00", "temperature": -12.0, "templow": -18.0}]
FORTNIGHT = [2.0 + (i % 7) for i in range(14)]


def _asked_for_the_daily_forecast(calls):
    """The forecast came from weather.get_forecasts, daily, for the configured entity."""
    assert calls, "no forecast asked for"
    domain, service, data, kwargs = calls[0]
    assert (domain, service, data) == ("weather", "get_forecasts", {"type": "daily"})
    assert kwargs["target"] == {"entity_id": WEATHER} and kwargs["return_response"] is True

LIMIT = 15.0
# The fortnight of late October 2026: daily means between 6.4 and 12.4 °C
OCTOBER = [6.4, 7.4, 8.0, 12.4, 9.3, 7.4, 6.9, 9.0, 8.0, 10.1, 7.2, 6.5, 9.3]


def _days(base: float, slope: float, temperatures: list[float]) -> list[tuple[float, float]]:
    return [(t, base + slope * heating_degrees(t, LIMIT)) for t in temperatures]


def test_a_base_load_below_zero_is_no_heating_model():
    model = fit_heating_model(_days(-4.7, 5.124, OCTOBER), LIMIT)
    assert model is not None
    assert model.slope_kwh_per_k == pytest.approx(5.124)
    assert model.intercept_kwh == pytest.approx(-4.7)
    assert not model.usable
    assert temperature_factor(model, 5.7, 6.6, LIMIT) == 1.0


def test_the_same_slope_over_a_real_base_load_still_scales():
    model = fit_heating_model(_days(8.0, 3.0, OCTOBER), LIMIT)
    assert model is not None and model.usable
    assert temperature_factor(model, 5.7, 6.6, LIMIT) > 1.2


def test_eleven_days_are_not_a_fortnight():
    assert MIN_FIT_DAYS == 13
    model = fit_heating_model(_days(8.0, 3.0, OCTOBER[:11]), LIMIT)
    assert model is not None and not model.usable
    assert fit_heating_model(_days(8.0, 3.0, OCTOBER[:13]), LIMIT).usable


@pytest.mark.asyncio
async def test_the_night_of_3_4_november_plans_what_the_fortnight_drew(mock_hass):
    """The model of that night leaves the need as learned: no 87 % for 5.7 °C."""
    coordinator = _weather_coordinator(mock_hass)
    _fortnight(coordinator, OCTOBER, base=-4.7, slope=5.124)
    calls = _forecast_service(mock_hass, daily=[
        {"datetime": "2026-01-15T11:00:00+00:00", "temperature": 8.0, "templow": 3.4},
    ])
    unscaled = list(await coordinator._house_load_profile())

    assert await coordinator._house_load_need_profile(PLAN_DAY) == unscaled
    status = coordinator.weather_status
    assert status["status"] == "learning"
    assert status["factor"] == 1.0
    assert status["base_kwh_per_day"] == pytest.approx(-4.7)
    _asked_for_the_daily_forecast(calls)


@pytest.mark.asyncio
async def test_the_bridge_plan_line_names_the_weather_factor(mock_hass, caplog):
    """Nothing in the log said why the target rose; now the line says it."""
    coordinator = _weather_coordinator(mock_hass)
    _fortnight(coordinator, [2.0 + (i % 7) for i in range(14)])
    calls = _forecast_service(mock_hass, daily=[
        {"datetime": "2026-01-15T11:00:00+00:00", "temperature": -2.0, "templow": -8.0},
    ])
    with caplog.at_level(logging.INFO), patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        plan = await coordinator._plan_target(9.0, True)

    assert plan is not None
    factor = coordinator.weather_status["factor"]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Bridge plan:")]
    assert lines and lines[-1].endswith(f", load x{factor:.2f} for -5.0 °C")
    _asked_for_the_daily_forecast(calls)


@pytest.mark.asyncio
async def test_without_a_factor_the_line_stays_as_it_was(mock_hass, caplog):
    coordinator = _coordinator(mock_hass)
    _fortnight(coordinator, [2.0 + (i % 7) for i in range(14)])
    with caplog.at_level(logging.INFO), patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        await coordinator._plan_target(9.0, True)

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Bridge plan:")]
    assert lines and "load x" not in lines[-1]


@pytest.mark.asyncio
async def test_the_line_names_this_plans_factor_not_a_projections(mock_hass, caplog):
    """A projection that runs meanwhile rewrites weather_status (Sourcery on #52)."""
    coordinator = _weather_coordinator(mock_hass)
    _fortnight(coordinator, [2.0 + (i % 7) for i in range(14)])
    calls = _forecast_service(mock_hass, daily=COLD)
    profile = coordinator._house_load_profile
    impostor = {"status": "active", "factor": 1.3, "forecast_temp_c": 20.0}

    async def meanwhile():
        if coordinator.weather_status.get("forecast_temp_c") == -5.0:
            coordinator.weather_status = impostor  # the projection's day
        return await profile()

    coordinator._house_load_profile = AsyncMock(side_effect=meanwhile)
    with caplog.at_level(logging.INFO), patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        plan = await coordinator._plan_target(9.0, True)

    assert plan is not None and coordinator.weather_status is impostor
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Bridge plan:")]
    assert lines and lines[-1].endswith(" for -5.0 °C")
    _asked_for_the_daily_forecast(calls)


@pytest.fixture
def berlin():
    previous = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Berlin"))
    try:
        yield
    finally:
        dt_util.set_default_time_zone(previous)


def test_the_days_the_clocks_change(berlin):
    assert _hours_in_local_day(date(2026, 10, 25)) == 25
    assert _hours_in_local_day(date(2026, 3, 29)) == 23
    assert _hours_in_local_day(date(2026, 11, 3)) == 24


@pytest.mark.asyncio
async def test_the_day_the_clocks_go_back_is_a_whole_day(mock_hass, berlin):
    """25 October 2026 has 25 hours; the 13 days behind 4 November include it (Sourcery on #52)."""
    coordinator = _weather_coordinator(mock_hass)
    days = [date(2026, 11, 4) - timedelta(days=13 - i) for i in range(13)]
    assert date(2026, 10, 25) in days
    samples = [[] for _ in range(24)]
    for day, temperature in zip(days, OCTOBER):
        daily = 10.0 + 0.8 * heating_degrees(temperature, LIMIT)
        hours = _hours_in_local_day(day)
        for hour in range(24):
            samples[hour].append((day, daily / hours))
        if hours == 25:
            samples[2].append((day, daily / hours))  # 02:00-03:00 twice
        coordinator._temperature_log.days[day] = temperature
    # A first day only partly in the window is no whole day
    samples[23].append((date(2026, 10, 21), 0.4))
    coordinator._temperature_log.days[date(2026, 10, 21)] = 8.0
    coordinator._house_load_samples = samples
    calls = _forecast_service(mock_hass, daily=[
        {"datetime": "2026-11-04T11:00:00+00:00", "temperature": 2.0, "templow": -2.0},
    ])

    await coordinator._house_load_need_profile(date(2026, 11, 4))

    status = coordinator.weather_status
    assert status["learning_days"] == 13
    assert status["status"] == "active"
    assert status["factor"] > 1.2
    _asked_for_the_daily_forecast(calls)


# --- the house's own cap on the weather factor (Pascal, 4 November 2026) ------



def test_the_presets_are_the_four_houses():
    assert WEATHER_FACTOR_PRESETS == {
        "ground_source": 1.2, "air_source": 1.3,
        "medium_insulation": 1.5, "poor_insulation": 2.0,
    }
    # An update keeps the cap it had
    assert WEATHER_FACTOR_PRESETS[DEFAULT_WEATHER_FACTOR_MAX] == FACTOR_MAX == 1.5


@pytest.mark.parametrize(("raw", "expected"), [
    ("ground_source", 1.2), ("poor_insulation", 2.0),
    ("1.4", 1.4), (" 1,75 ", 1.75), (1.1, 1.1), (2, 2.0), ("1.0", 1.0), ("3", 3.0),
    ("abc", None), ("", None), ("0.9", None), ("3.5", None), ("nan", None), ("inf", None),
    (True, None), (None, None), ([1.2], None),
])
def test_a_stored_cap_is_a_preset_or_a_typed_number(raw, expected):
    assert parse_factor_max(raw, WEATHER_FACTOR_PRESETS, WEATHER_FACTOR_MAX_RANGE) == expected


def test_the_cap_holds_the_factor():
    model = fit_heating_model(_days(10.0, 0.8, OCTOBER), LIMIT)
    assert model is not None and model.usable
    free = temperature_factor(model, -30.0, 6.6, LIMIT, 3.0)
    assert free > 2.0
    assert temperature_factor(model, -30.0, 6.6, LIMIT) == 1.5
    assert temperature_factor(model, -30.0, 6.6, LIMIT, 1.2) == 1.2
    assert temperature_factor(model, -30.0, 6.6, LIMIT, 2.0) == 2.0
    # A mild day is not raised by any cap
    assert temperature_factor(model, 9.0, 6.6, LIMIT, 1.2) < 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize(("option", "cap"), [
    ("ground_source", 1.2), ("air_source", 1.3), ("1.35", 1.35),
])
async def test_a_ground_source_house_plans_less_for_the_same_cold(mock_hass, option, cap):
    """18 kWh at 5 °C would be 26 kWh at -5 °C; a ground-source house caps it at 1.2."""
    coordinator = _weather_coordinator(mock_hass, {CONF_WEATHER_FACTOR_MAX: option})
    _fortnight(coordinator, FORTNIGHT)
    calls = _forecast_service(mock_hass, daily=COLD)
    unscaled = list(await coordinator._house_load_profile())

    need = await coordinator._house_load_need_profile(PLAN_DAY)

    status = coordinator.weather_status
    assert status["status"] == "active"
    assert status["factor_max"] == cap
    assert status["factor"] == pytest.approx(cap)
    assert need == pytest.approx([kwh * cap for kwh in unscaled])
    _asked_for_the_daily_forecast(calls)


@pytest.mark.asyncio
async def test_without_the_option_the_cap_is_1_5_as_before(mock_hass):
    coordinator = _weather_coordinator(mock_hass)
    _fortnight(coordinator, FORTNIGHT)
    calls = _forecast_service(mock_hass, daily=FROST)

    await coordinator._house_load_need_profile(PLAN_DAY)

    assert coordinator.weather_status["factor_max"] == 1.5
    assert coordinator.weather_status["factor"] == pytest.approx(1.5)
    _asked_for_the_daily_forecast(calls)


@pytest.mark.asyncio
async def test_direct_heating_may_draw_more_than_1_5(mock_hass):
    """At -15 °C the fortnight's 18 kWh become 34: 1.89, more than the old cap."""
    coordinator = _weather_coordinator(mock_hass, {CONF_WEATHER_FACTOR_MAX: "poor_insulation"})
    _fortnight(coordinator, FORTNIGHT)
    calls = _forecast_service(mock_hass, daily=FROST)

    await coordinator._house_load_need_profile(PLAN_DAY)

    status = coordinator.weather_status
    assert status["factor_max"] == 2.0
    assert 1.8 < status["factor"] < 2.0
    _asked_for_the_daily_forecast(calls)


@pytest.mark.asyncio
async def test_a_cap_that_means_nothing_falls_back_to_1_5(mock_hass):
    coordinator = _weather_coordinator(mock_hass, {CONF_WEATHER_FACTOR_MAX: "viel"})
    _fortnight(coordinator, FORTNIGHT)
    calls = _forecast_service(mock_hass, daily=FROST)

    await coordinator._house_load_need_profile(PLAN_DAY)

    assert coordinator.weather_status["factor_max"] == 1.5
    assert coordinator.weather_status["factor"] == pytest.approx(1.5)
    _asked_for_the_daily_forecast(calls)


def _factor_field(defaults):
    for marker, field in config_flow._schema_advanced(defaults).schema.items():
        if marker.schema == CONF_WEATHER_FACTOR_MAX:
            return marker, field
    raise AssertionError("no weather factor field")


def test_the_options_offer_the_presets_and_a_number_of_your_own():
    marker, field = _factor_field({})
    assert marker.default() == "medium_insulation"
    assert field.config["options"] == list(WEATHER_FACTOR_PRESETS)
    assert field.config["custom_value"] is True
    assert field.config["translation_key"] == "weather_factor_max"
    assert _factor_field({CONF_WEATHER_FACTOR_MAX: "1.4"})[0].default() == "1.4"
    assert CONF_WEATHER_FACTOR_MAX in config_flow.STEP_ADVANCED_KEYS
    assert CONF_WEATHER_FACTOR_MAX in config_flow._GUIDED_GROUPS["reserve"]


@pytest.mark.parametrize("value", ["ground_source", "poor_insulation", "1.4", "1,25", "3.0"])
def test_a_preset_or_a_number_in_range_is_accepted(mock_hass, value):
    assert _errors(mock_hass, **{CONF_WEATHER_FACTOR_MAX: value}) == {}


@pytest.mark.parametrize("value", ["viel", "0.8", "4", ""])
def test_anything_else_is_refused(mock_hass, value):
    assert _errors(mock_hass, **{CONF_WEATHER_FACTOR_MAX: value}) == {
        CONF_WEATHER_FACTOR_MAX: "invalid_weather_factor_max",
    }
