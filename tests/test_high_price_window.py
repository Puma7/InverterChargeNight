"""The high-price period and the reserve the planner derives from it (plan 011).

Pascal's tariff is the case these tests are written from: cheap 23:00-05:00,
and a peak period 18:00-21:00 in which a kilowatt-hour costs more than the
normal day tariff. What the house draws in those three hours has to be in the
battery by 18:00, or it is bought in the most expensive hour of the day -
energy that could have been bought overnight for a fraction.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_HIGH_PRICE_END,
    CONF_HIGH_PRICE_MARGIN_PCT,
    CONF_HIGH_PRICE_START,
    CONF_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)
from custom_components.inverter_charge_night.sensor import NextHighPriceWindowSensor

COORDINATOR = "custom_components.inverter_charge_night.coordinator"

CONFIG = {
    CONF_MIN_SOC_ENTITY: "number.min_soc",
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_PV_FORECAST_ENTITY: "sensor.pv",
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_START_TIME: "23:00",
    CONF_END_TIME: "05:00",
    CONF_HIGH_PRICE_START: "18:00",
    CONF_HIGH_PRICE_END: "21:00",
}


def _make_coordinator(hass, extra=None):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = CONFIG | (extra or {})
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


# Finding the period ------------------------------------------------------------


def test_the_evening_after_the_window_end_is_the_one_that_counts(mock_hass):
    """A window ending at 05:00 plans for the evening of that same day."""
    coordinator = _make_coordinator(mock_hass)
    window = coordinator._high_price_window(datetime(2026, 1, 15, 5, 0))
    assert window == (datetime(2026, 1, 15, 18, 0), datetime(2026, 1, 15, 21, 0))


def test_a_period_already_past_rolls_to_the_next_day(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    window = coordinator._high_price_window(datetime(2026, 1, 15, 22, 0))
    assert window == (datetime(2026, 1, 16, 18, 0), datetime(2026, 1, 16, 21, 0))


def test_a_period_crossing_midnight_ends_on_the_next_day(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_HIGH_PRICE_START: "22:00", CONF_HIGH_PRICE_END: "01:00"}
    )
    window = coordinator._high_price_window(datetime(2026, 1, 15, 5, 0))
    assert window == (datetime(2026, 1, 15, 22, 0), datetime(2026, 1, 16, 1, 0))


@pytest.mark.parametrize(
    "config",
    [
        {CONF_HIGH_PRICE_START: "", CONF_HIGH_PRICE_END: ""},
        {CONF_HIGH_PRICE_START: "18:00", CONF_HIGH_PRICE_END: ""},
        {CONF_HIGH_PRICE_START: "18:00", CONF_HIGH_PRICE_END: "18:00"},
        {CONF_HIGH_PRICE_START: "half past six", CONF_HIGH_PRICE_END: "21:00"},
    ],
)
def test_without_a_usable_pair_there_is_no_period(mock_hass, config):
    coordinator = _make_coordinator(mock_hass, config)
    assert coordinator._high_price_window(datetime(2026, 1, 15, 5, 0)) is None


# What the planner is given -----------------------------------------------------


@pytest.mark.asyncio
async def test_the_plan_input_carries_the_period_and_the_margin(mock_hass):
    mock_hass.states.async_set("sensor.soc", "40", {"unit_of_measurement": "%"})
    coordinator = _make_coordinator(mock_hass, {CONF_HIGH_PRICE_MARGIN_PCT: 15.0})
    coordinator._house_load_profile = AsyncMock(return_value=[0.5] * 24)
    coordinator._sun_times = MagicMock(
        return_value=(datetime(2026, 1, 15, 7, 30), datetime(2026, 1, 15, 17, 0))
    )

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 2, 0)):
        plan_input, _ = await coordinator._build_plan_input(5.0, True)

    assert plan_input.high_price_window == (
        datetime(2026, 1, 15, 18, 0),
        datetime(2026, 1, 15, 21, 0),
    )
    assert plan_input.reserve_margin_pct == 15.0


@pytest.mark.asyncio
async def test_a_dull_forecast_raises_the_night_target(mock_hass):
    """The whole of Pascal's case in one test: no sun tomorrow, so the three
    evening hours are bought tonight instead of at the peak tariff."""
    mock_hass.states.async_set("sensor.soc", "40", {"unit_of_measurement": "%"})
    coordinator = _make_coordinator(mock_hass)
    coordinator._house_load_profile = AsyncMock(return_value=[0.5] * 24)
    coordinator._sun_times = MagicMock(
        return_value=(datetime(2026, 1, 15, 7, 30), datetime(2026, 1, 15, 17, 0))
    )

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 2, 0)):
        dull = await coordinator._plan_target(0.0, True)
        sunny = await coordinator._plan_target(20.0, True)

    assert dull is not None and sunny is not None
    # 0.5 kW over three hours, on a 10 kWh battery
    assert dull.evening_reserve_kwh == pytest.approx(1.5)
    assert dull.evening_shortfall_kwh == pytest.approx(1.5)
    assert dull.target_soc - sunny.target_soc == pytest.approx(15.0, abs=0.2)
    assert sunny.evening_shortfall_kwh == 0.0


# The sensor --------------------------------------------------------------------


def test_the_sensor_shows_the_next_period_and_the_reserve(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator.data = {"evening_reserve_kwh": 1.5, "evening_shortfall_kwh": 0.4}
    sensor = NextHighPriceWindowSensor(coordinator, coordinator.entry)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 5, 0)):
        assert sensor.native_value == datetime(2026, 1, 15, 18, 0)
        attributes = sensor.extra_state_attributes

    assert attributes["end"] == "2026-01-15T21:00:00"
    assert attributes["duration_h"] == 3.0
    assert attributes["reserve_kwh"] == 1.5
    assert attributes["bought_at_night_kwh"] == 0.4


def test_the_sensor_is_unknown_without_a_configured_period(mock_hass):
    """It stays in place so a dashboard that mentions it does not break."""
    coordinator = _make_coordinator(mock_hass, {CONF_HIGH_PRICE_START: ""})
    coordinator.data = {}
    sensor = NextHighPriceWindowSensor(coordinator, coordinator.entry)

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["end"] is None
    assert sensor.extra_state_attributes["duration_h"] is None
