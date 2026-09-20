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
    CONF_OPERATION_MODE,
    CONF_PLANNER_MODE,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    DEFAULT_DISCHARGE_EFFICIENCY,
    MODE_MORNING_DISCHARGE,
    PLANNER_MODE_BRIDGE,
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
    # 0.5 kW over three hours is what the house draws; the battery has to hold
    # rather more of it, because the way out through the inverter costs too.
    assert dull.evening_reserve_kwh == pytest.approx(1.5)
    bought = 1.5 / DEFAULT_DISCHARGE_EFFICIENCY
    # The reserve is the house draw; the shortfall is what the battery has to
    # gain to deliver it, which is more.
    assert dull.evening_shortfall_kwh == pytest.approx(bought)
    assert dull.target_soc - sunny.target_soc == pytest.approx(bought / 10.0 * 100, abs=0.2)
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


# The interaction with the safe fallback ---------------------------------------


@pytest.mark.asyncio
async def test_an_unavailable_forecast_does_not_cut_the_bridge_back_to_the_fallback(mock_hass):
    """The 50 % fallback belongs to the headroom formula, not to the planner.

    Headroom charges to the maximum exactly because it read no forecast, which
    is what the fallback is there to stop. The planner derives its target from
    the house load and handles a missing forecast itself - cutting that back to
    50 % would drop a need it had just worked out, and on the coldest night.
    """
    mock_hass.states.async_set("sensor.soc", "20", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("sensor.pv", "unavailable")
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_PLANNER_MODE: PLANNER_MODE_BRIDGE,
            # A big evening on a small battery: the bridge alone wants the maximum
            CONF_BATTERY_CAPACITY: 4.0,
            CONF_HIGH_PRICE_START: "18:00",
            CONF_HIGH_PRICE_END: "21:00",
        },
    )
    coordinator._house_load_profile = AsyncMock(return_value=[0.5] * 24)
    coordinator._sun_times = MagicMock(
        return_value=(datetime(2026, 1, 15, 7, 30), datetime(2026, 1, 15, 17, 0))
    )

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 2, 0)):
        await coordinator._calculate_initial_soc()

    assert coordinator.initial_calculated_soc == 100.0


# The morning discharge floor ---------------------------------------------------


def _discharge_coordinator(mock_hass, extra=None):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE,
            CONF_START_TIME: "05:00",
            CONF_END_TIME: "08:00",
        }
        | (extra or {}),
    )
    coordinator._house_load_profile = AsyncMock(return_value=[0.5] * 24)
    coordinator._sun_times = MagicMock(
        return_value=(datetime(2026, 1, 15, 7, 30), datetime(2026, 1, 15, 17, 0))
    )
    return coordinator


@pytest.mark.asyncio
async def test_a_dull_day_stops_the_morning_discharge_selling_the_evening(mock_hass):
    """The reserve the night bought must not be sold three hours later."""
    mock_hass.states.async_set("sensor.soc", "90", {"unit_of_measurement": "%"})
    coordinator = _discharge_coordinator(mock_hass)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 6, 0)):
        floor = await coordinator._evening_reserve_floor(0.0, True)
        raised = await coordinator._floor_discharge_at_the_evening_reserve(8.0, 0.0, True)

    # 1.5 kWh of evening draw on a 10 kWh battery, grossed up by the discharge
    # loss, on top of the 8 % user minimum
    expected = 8.0 + (1.5 / DEFAULT_DISCHARGE_EFFICIENCY) / 10.0 * 100
    assert floor == pytest.approx(expected, abs=0.01)
    assert raised == pytest.approx(expected, abs=0.01)


@pytest.mark.asyncio
async def test_a_sunny_day_leaves_the_morning_discharge_alone(mock_hass):
    mock_hass.states.async_set("sensor.soc", "90", {"unit_of_measurement": "%"})
    coordinator = _discharge_coordinator(mock_hass)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 6, 0)):
        assert await coordinator._floor_discharge_at_the_evening_reserve(8.0, 20.0, True) == 8.0


@pytest.mark.asyncio
async def test_a_target_already_above_the_reserve_is_not_lowered(mock_hass):
    mock_hass.states.async_set("sensor.soc", "90", {"unit_of_measurement": "%"})
    coordinator = _discharge_coordinator(mock_hass)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 6, 0)):
        assert await coordinator._floor_discharge_at_the_evening_reserve(60.0, 0.0, True) == 60.0


@pytest.mark.asyncio
async def test_the_charge_mode_has_no_discharge_floor(mock_hass):
    """It is the night target's job there, not a floor's."""
    mock_hass.states.async_set("sensor.soc", "40", {"unit_of_measurement": "%"})
    coordinator = _make_coordinator(mock_hass)
    coordinator._house_load_profile = AsyncMock(return_value=[0.5] * 24)
    coordinator._sun_times = MagicMock(
        return_value=(datetime(2026, 1, 15, 7, 30), datetime(2026, 1, 15, 17, 0))
    )

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 2, 0)):
        assert await coordinator._evening_reserve_floor(0.0, True) is None


@pytest.mark.asyncio
async def test_without_a_high_price_period_the_discharge_is_untouched(mock_hass):
    mock_hass.states.async_set("sensor.soc", "90", {"unit_of_measurement": "%"})
    coordinator = _discharge_coordinator(
        mock_hass, {CONF_HIGH_PRICE_START: "", CONF_HIGH_PRICE_END: ""}
    )

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 6, 0)):
        assert await coordinator._evening_reserve_floor(0.0, True) is None


@pytest.mark.asyncio
async def test_a_broken_configuration_does_not_break_the_discharge(mock_hass):
    """A capacity of zero is a configuration error, not a reason to stop."""
    mock_hass.states.async_set("sensor.soc", "90", {"unit_of_measurement": "%"})
    coordinator = _discharge_coordinator(mock_hass, {CONF_BATTERY_CAPACITY: 0.0})

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 6, 0)):
        assert await coordinator._floor_discharge_at_the_evening_reserve(8.0, 0.0, True) == 8.0


# The evening outlook at coordinator level (plan 013) ----------------------------


def _outlook_coordinator(mock_hass, soc="50", forecast="20"):
    mock_hass.states.async_set("sensor.soc", soc, {"unit_of_measurement": "%"})
    mock_hass.states.async_set("sensor.pv", forecast, {"unit_of_measurement": "kWh"})
    mock_hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "next_rising": "2026-06-02T05:00:00+00:00",
            "next_setting": "2026-06-01T21:00:00+00:00",
        },
    )
    coordinator = _make_coordinator(mock_hass)
    coordinator._house_load_profile = AsyncMock(return_value=[0.5] * 24)
    return coordinator


@pytest.mark.asyncio
async def test_the_outlook_sees_a_battery_that_will_not_make_the_evening(mock_hass):
    coordinator = _outlook_coordinator(mock_hass, soc="12", forecast="0.5")

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 6, 1, 14, 0)):
        outlook = await coordinator.async_evening_outlook()

    assert outlook is not None
    assert outlook.zone_start == datetime(2026, 6, 1, 18, 0)
    assert outlook.missing_kwh > 0


@pytest.mark.asyncio
async def test_the_outlook_is_quiet_when_the_battery_will_make_it(mock_hass):
    coordinator = _outlook_coordinator(mock_hass)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 6, 1, 14, 0)):
        outlook = await coordinator.async_evening_outlook()

    assert outlook is not None and outlook.missing_kwh == 0.0


@pytest.mark.asyncio
async def test_no_high_price_period_means_no_outlook(mock_hass):
    coordinator = _outlook_coordinator(mock_hass)
    coordinator.config = dict(coordinator.config) | {CONF_HIGH_PRICE_START: ""}

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 6, 1, 14, 0)):
        assert await coordinator.async_evening_outlook() is None


@pytest.mark.asyncio
async def test_an_unreadable_battery_means_no_outlook(mock_hass):
    """Nothing to project from, and a guess is not a basis for acting."""
    coordinator = _outlook_coordinator(mock_hass)
    mock_hass.states.async_set("sensor.soc", "unavailable")

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 6, 1, 14, 0)):
        assert await coordinator.async_evening_outlook() is None


@pytest.mark.asyncio
async def test_a_broken_capacity_does_not_raise_out_of_the_update(mock_hass):
    coordinator = _outlook_coordinator(mock_hass)
    coordinator.config = dict(coordinator.config) | {CONF_BATTERY_CAPACITY: 0.0}

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 6, 1, 14, 0)):
        assert await coordinator.async_evening_outlook() is None


@pytest.mark.asyncio
async def test_the_outlook_is_worked_out_while_no_window_runs(mock_hass):
    """The whole point: it matters in the afternoon, when nothing is active."""
    coordinator = _outlook_coordinator(mock_hass, soc="12", forecast="0.5")
    coordinator.is_active = False

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 6, 1, 14, 0)):
        data = await coordinator._async_update_data()

    assert data["is_active"] is False
    assert coordinator.last_evening_outlook is not None
    assert coordinator.last_evening_outlook.missing_kwh > 0


@pytest.mark.asyncio
async def test_a_disabled_integration_projects_nothing(mock_hass):
    coordinator = _outlook_coordinator(mock_hass, soc="12", forecast="0.5")
    coordinator.is_enabled = False

    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 6, 1, 14, 0)):
        await coordinator._async_update_data()

    assert coordinator.last_evening_outlook is None


def test_the_sensor_shows_the_shortfall_and_its_reasoning(mock_hass):
    from custom_components.inverter_charge_night.planner import EveningOutlook
    from custom_components.inverter_charge_night.sensor import EveningOutlookSensor

    coordinator = _outlook_coordinator(mock_hass)
    coordinator.last_evening_outlook = EveningOutlook(
        zone_start=datetime(2026, 6, 1, 18, 0),
        zone_end=datetime(2026, 6, 1, 21, 0),
        required_soc=23.0,
        projected_soc=14.0,
        missing_kwh=0.9,
        pv_to_come_kwh=0.3,
        load_to_come_kwh=2.0,
        forecast_available=True,
    )
    sensor = EveningOutlookSensor(coordinator, coordinator.entry)

    assert sensor.native_value == 0.9
    attributes = sensor.extra_state_attributes
    assert attributes["required_soc"] == 23.0
    assert attributes["projected_soc"] == 14.0
    assert attributes["zone_start"] == "2026-06-01T18:00:00"


def test_the_sensor_is_empty_without_an_outlook(mock_hass):
    from custom_components.inverter_charge_night.sensor import EveningOutlookSensor

    coordinator = _outlook_coordinator(mock_hass)
    coordinator.last_evening_outlook = None
    sensor = EveningOutlookSensor(coordinator, coordinator.entry)

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}
