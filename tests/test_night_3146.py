"""3.14.6: the night of 3/4 November 2026.

At 23:01 the plan bought 64.1 %, at 00:16 87.4 %. The weather factor had
switched on: 5.7 °C forecast against a fortnight of 8.4 °C, 5.1 kWh more per
kelvin, 47.5 % more load. The model behind it had 11 days, r² 0.56 and a base
load of about -4.7 kWh a day - a house that draws nothing at the heating limit
and less than nothing above it. And the log line said nothing about a factor.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from custom_components.inverter_charge_night.heating import (
    MIN_FIT_DAYS,
    fit_heating_model,
    heating_degrees,
    temperature_factor,
)
from tests.test_planner_015 import (
    COORDINATOR,
    NIGHT,
    PLAN_DAY,
    _coordinator,
    _forecast_service,
    _fortnight,
    _weather_coordinator,
)

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
    _forecast_service(mock_hass, daily=[
        {"datetime": "2026-01-15T11:00:00+00:00", "temperature": 8.0, "templow": 3.4},
    ])
    unscaled = list(await coordinator._house_load_profile())

    assert await coordinator._house_load_need_profile(PLAN_DAY) == unscaled
    status = coordinator.weather_status
    assert status["status"] == "learning"
    assert status["factor"] == 1.0
    assert status["base_kwh_per_day"] == pytest.approx(-4.7)


@pytest.mark.asyncio
async def test_the_bridge_plan_line_names_the_weather_factor(mock_hass, caplog):
    """Nothing in the log said why the target rose; now the line says it."""
    coordinator = _weather_coordinator(mock_hass)
    _fortnight(coordinator, [2.0 + (i % 7) for i in range(14)])
    _forecast_service(mock_hass, daily=[
        {"datetime": "2026-01-15T11:00:00+00:00", "temperature": -2.0, "templow": -8.0},
    ])
    with caplog.at_level(logging.INFO), patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        plan = await coordinator._plan_target(9.0, True)

    assert plan is not None
    factor = coordinator.weather_status["factor"]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Bridge plan:")]
    assert lines and lines[-1].endswith(f", load x{factor:.2f} for -5.0 °C")


@pytest.mark.asyncio
async def test_without_a_factor_the_line_stays_as_it_was(mock_hass, caplog):
    coordinator = _coordinator(mock_hass)
    _fortnight(coordinator, [2.0 + (i % 7) for i in range(14)])
    with caplog.at_level(logging.INFO), patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        await coordinator._plan_target(9.0, True)

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Bridge plan:")]
    assert lines and "load x" not in lines[-1]
