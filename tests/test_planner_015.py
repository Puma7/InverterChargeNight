"""Plan 015: the night target with a safety buffer, learned need and weather.

Pascal's 23/24 October: 44 % on 35.8 kWh, and the next morning the evening
outlook already 0.85 kWh short. The target sat on the edge of what the day
needed. These tests run the coordinator end to end, from the options to the
plan the sensor shows.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_SAFETY_BUFFER_KWH,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)
from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)

COORDINATOR = "custom_components.inverter_charge_night.coordinator"
NIGHT = datetime(2026, 1, 15, 2, 0)
SUN = (datetime(2026, 1, 15, 7, 30), datetime(2026, 1, 15, 17, 0))

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
}


def _coordinator(hass, extra=None, profile=None):
    hass.states.async_set("sensor.soc", "40", {"unit_of_measurement": "%"})
    entry = MagicMock()
    entry.entry_id = "entry_015"
    entry.title = "Test"
    entry.data = CONFIG | (extra or {})
    entry.options = {}
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator._house_load_profile = AsyncMock(return_value=profile or [0.5] * 24)
    coordinator._sun_times = MagicMock(return_value=SUN)
    return coordinator


# --- stage A: the safety buffer ----------------------------------------------


@pytest.mark.asyncio
async def test_the_buffer_option_raises_the_night_target(mock_hass, caplog):
    plain = _coordinator(mock_hass)
    buffered = _coordinator(mock_hass, {CONF_SAFETY_BUFFER_KWH: 2.0})
    with patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        without = await plain._plan_target(3.0, True)
        caplog.clear()
        caplog.set_level("INFO")
        with_buffer = await buffered._plan_target(3.0, True)

    assert without is not None and with_buffer is not None
    assert with_buffer.lower_bound_soc == without.lower_bound_soc
    assert with_buffer.target_soc == pytest.approx(without.target_soc + 20.0, abs=0.11)
    assert "buffer 20.0%" in caplog.text
    attributes = buffered._plan_attributes()
    assert attributes["buffer_soc"] == pytest.approx(20.0, abs=0.11)
    assert attributes["buffer_capped"] is False


@pytest.mark.asyncio
async def test_a_sunny_tomorrow_caps_the_buffer(mock_hass, caplog):
    """9 kWh of sun need room: the buffer only fills what is left."""
    buffered = _coordinator(mock_hass, {CONF_SAFETY_BUFFER_KWH: 8.0})
    caplog.set_level("INFO")
    with patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        plan = await buffered._plan_target(9.0, True)

    assert plan is not None
    assert plan.target_soc == plan.upper_bound_soc
    assert plan.buffer_capped
    assert "(capped)" in caplog.text


# --- stage C: the safety level -------------------------------------------------


@pytest.mark.asyncio
async def test_a_safety_level_raises_the_target_and_keeps_the_pv_room(mock_hass):
    plain = _coordinator(mock_hass)
    safe = _coordinator(mock_hass)
    safe._house_load_need_profile = AsyncMock(return_value=[0.8] * 24)
    with patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        before = await plain._plan_target(9.0, True)
        after = await safe._plan_target(9.0, True)

    assert before is not None and after is not None
    assert after.lower_bound_soc > before.lower_bound_soc
    assert after.target_soc > before.target_soc
    assert after.upper_bound_soc == before.upper_bound_soc


# --- stage D: the weather ------------------------------------------------------

from datetime import date, timedelta, timezone  # noqa: E402

from homeassistant.helpers.recorder import DATA_INSTANCE  # noqa: E402

from custom_components.inverter_charge_night.const import (  # noqa: E402
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_WEATHER_ENTITY,
)
from custom_components.inverter_charge_night.heating import (  # noqa: E402
    MIN_SAMPLES_PER_DAY,
)

WEATHER = "weather.home"
OUTDOOR = "sensor.outdoor"
PLAN_DAY = date(2026, 1, 15)


def _forecast_service(mock_hass, daily=None, hourly=None, raises=()):
    """weather.get_forecasts as Home Assistant answers it, per forecast type."""
    calls = []

    async def call(domain, service, data=None, **kwargs):
        calls.append((domain, service, data, kwargs))
        kind = (data or {}).get("type")
        if kind in raises:
            raise RuntimeError(f"no {kind} forecast")
        entries = daily if kind == "daily" else hourly
        return {WEATHER: {"forecast": entries or []}}

    mock_hass.services.async_call = AsyncMock(side_effect=call)
    return calls


def _fortnight(coordinator, temperatures, base=10.0, slope=0.8):
    """Whole days of a heat-pump house: 10 kWh plus 0.8 kWh per heating degree."""
    samples = [[] for _ in range(24)]
    for offset, temperature in enumerate(temperatures):
        day = PLAN_DAY - timedelta(days=len(temperatures) - offset)
        daily = base + slope * max(0.0, 15.0 - temperature)
        for hour in range(24):
            samples[hour].append((day, daily / 24))
        coordinator._temperature_log.days[day] = temperature
    coordinator._house_load_samples = samples


def _weather_coordinator(mock_hass, extra=None):
    coordinator = _coordinator(mock_hass, {CONF_WEATHER_ENTITY: WEATHER, **(extra or {})})
    mock_hass.states.async_set(WEATHER, "cloudy", {"temperature": 4.0, "temperature_unit": "°C"})
    return coordinator


@pytest.mark.asyncio
async def test_a_cold_day_plans_more_and_keeps_the_pv_room(mock_hass):
    """A fortnight around 5 °C, tomorrow -5 °C: 18 kWh a day become 26."""
    coordinator = _weather_coordinator(mock_hass)
    _fortnight(coordinator, [2.0 + (i % 7) for i in range(14)])
    calls = _forecast_service(mock_hass, daily=[
        {"datetime": "2026-01-15T11:00:00+00:00", "temperature": -2.0, "templow": -8.0},
    ])
    plain = _coordinator(mock_hass)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        cold = await coordinator._plan_target(9.0, True)
        before = await plain._plan_target(9.0, True)

    status = coordinator.weather_status
    assert status["status"] == "active"
    assert status["forecast_temp_c"] == -5.0
    assert status["kwh_per_kelvin"] == pytest.approx(0.8)
    assert status["factor"] > 1.2
    assert cold is not None and before is not None
    assert cold.lower_bound_soc > before.lower_bound_soc
    assert cold.upper_bound_soc == before.upper_bound_soc  # the PV room is untouched
    domain, service, data, kwargs = calls[0]
    assert (domain, service, data) == ("weather", "get_forecasts", {"type": "daily"})
    assert kwargs["target"] == {"entity_id": WEATHER} and kwargs["return_response"] is True


@pytest.mark.asyncio
async def test_without_a_weather_entity_nothing_is_scaled(mock_hass):
    coordinator = _coordinator(mock_hass)
    _fortnight(coordinator, [2.0 + (i % 7) for i in range(14)])
    assert await coordinator._house_load_need_profile(PLAN_DAY) == [0.5] * 24
    assert coordinator.weather_status == {"status": "off"}


@pytest.mark.asyncio
async def test_without_a_forecast_the_need_is_unscaled(mock_hass):
    coordinator = _weather_coordinator(mock_hass)
    _fortnight(coordinator, [2.0 + (i % 7) for i in range(14)])
    _forecast_service(mock_hass, raises=("daily", "hourly"))

    assert await coordinator._house_load_need_profile(PLAN_DAY) == [0.5] * 24
    assert coordinator.weather_status["status"] == "forecast_unavailable"
    assert coordinator.weather_status["factor"] == 1.0


@pytest.mark.asyncio
async def test_until_the_slope_is_learned_the_need_is_unscaled(mock_hass):
    """A week of diary is not enough to trust a heating slope."""
    coordinator = _weather_coordinator(mock_hass)
    _fortnight(coordinator, [2.0 + i for i in range(7)])
    _forecast_service(mock_hass, daily=[
        {"datetime": "2026-01-15T11:00:00+00:00", "temperature": -10.0},
    ])
    assert await coordinator._house_load_need_profile(PLAN_DAY) == [0.5] * 24
    assert coordinator.weather_status["status"] == "learning"
    assert coordinator.weather_status["learning_days"] == 7


@pytest.mark.asyncio
async def test_the_hourly_forecast_stands_in_and_fahrenheit_is_converted(mock_hass):
    coordinator = _weather_coordinator(mock_hass)
    mock_hass.states.async_set(WEATHER, "cloudy", {"temperature": 40.0, "temperature_unit": "°F"})
    hours = [{"datetime": f"2026-01-15T{h:02d}:00:00+00:00", "temperature": 23.0}
             for h in range(6, 18)]
    _forecast_service(mock_hass, hourly=hours, raises=("daily",))

    assert await coordinator._forecast_mean_temperature(PLAN_DAY) == pytest.approx(-5.0)
    # Cached: a second plan within half an hour asks nobody
    mock_hass.services.async_call.reset_mock()
    assert await coordinator._forecast_mean_temperature(PLAN_DAY) == pytest.approx(-5.0)
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_polls_keep_a_temperature_diary(mock_hass):
    coordinator = _weather_coordinator(mock_hass)
    coordinator._persist_state = MagicMock()
    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 14, 12, 0)):
        for _ in range(MIN_SAMPLES_PER_DAY):
            coordinator._observe_outdoor_temperature()
    coordinator._persist_state.assert_not_called()
    mock_hass.states.async_set(OUTDOOR, "-3.5", {"unit_of_measurement": "°C"})
    coordinator.config = {**coordinator.config, CONF_OUTDOOR_TEMPERATURE_ENTITY: OUTDOOR}
    with patch(f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 1, 15, 0, 5)):
        coordinator._observe_outdoor_temperature()
    coordinator._persist_state.assert_called_once()
    assert coordinator._temperature_log.days == {date(2026, 1, 14): 4.0}
    assert coordinator._temperature_log._sum == -3.5  # the sensor wins once it is set


@pytest.mark.asyncio
async def test_a_temperature_sensor_brings_its_fortnight_from_the_recorder(mock_hass):
    coordinator = _weather_coordinator(mock_hass, {CONF_OUTDOOR_TEMPERATURE_ENTITY: OUTDOOR})
    mock_hass.states.async_set(OUTDOOR, "1", {"unit_of_measurement": "°C"})
    rows = [{"start": datetime(2026, 1, d, tzinfo=timezone.utc).timestamp(), "mean": float(d)}
            for d in range(1, 15)]
    rows.append({"start": None, "mean": 3.0})
    instance = MagicMock()
    instance.async_add_executor_job = AsyncMock(return_value={OUTDOOR: rows})
    mock_hass.data[DATA_INSTANCE] = instance

    days = await coordinator._daily_outdoor_c()

    assert days[date(2026, 1, 5)] == 5.0 and len(days) == 14
    await coordinator._daily_outdoor_c()
    instance.async_add_executor_job.assert_awaited_once()  # cached for an hour


@pytest.mark.asyncio
async def test_a_failing_recorder_leaves_the_diary(mock_hass):
    coordinator = _weather_coordinator(mock_hass, {CONF_OUTDOOR_TEMPERATURE_ENTITY: OUTDOOR})
    coordinator._temperature_log.days[date(2026, 1, 3)] = 2.0
    instance = MagicMock()
    instance.async_add_executor_job = AsyncMock(side_effect=RuntimeError("db locked"))
    mock_hass.data[DATA_INSTANCE] = instance
    assert await coordinator._daily_outdoor_c() == {date(2026, 1, 3): 2.0}


def test_the_status_sensor_shows_the_weather(mock_hass):
    from custom_components.inverter_charge_night.sensor import CalculatedSOCSensor

    coordinator = _weather_coordinator(mock_hass)
    coordinator.data = {}
    coordinator.weather_status = {"status": "active", "factor": 1.2}
    sensor = CalculatedSOCSensor(coordinator, coordinator.entry)
    assert sensor.extra_state_attributes["weather"] == {"status": "active", "factor": 1.2}

