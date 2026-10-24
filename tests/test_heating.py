"""Plan 015 stage D: the heating model behind the weather factor, as plain arithmetic."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.inverter_charge_night.heating import (
    FACTOR_MAX,
    FACTOR_MIN,
    MIN_SAMPLES_PER_DAY,
    TEMPERATURE_LOG_DAYS,
    TemperatureLog,
    daily_mean_from_forecast,
    fit_heating_model,
    heating_degrees,
    hourly_mean_from_forecast,
    temperature_factor,
    to_celsius,
)

LIMIT = 15.0
BERLIN = timezone(timedelta(hours=1))


def _local(value: datetime) -> datetime:
    return value.astimezone(BERLIN)


def _heat_pump_days(n=12, base=10.0, slope=0.8, start_c=-2.0):
    """A house that draws 10 kWh a day plus 0.8 kWh per degree of heating."""
    return [(start_c + i, base + slope * heating_degrees(start_c + i, LIMIT)) for i in range(n)]


def test_heating_degrees_and_units():
    assert heating_degrees(20.0, LIMIT) == 0.0
    assert heating_degrees(-5.0, LIMIT) == 20.0
    assert to_celsius(50.0, "°F") == pytest.approx(10.0)
    assert to_celsius(10.0, "°C") == 10.0
    assert to_celsius(10.0, None) == 10.0


def test_a_heat_pump_house_is_fitted_exactly():
    model = fit_heating_model(_heat_pump_days(), LIMIT)
    assert model is not None and model.usable
    assert model.slope_kwh_per_k == pytest.approx(0.8)
    assert model.intercept_kwh == pytest.approx(10.0)
    assert model.r2 == pytest.approx(1.0)


@pytest.mark.parametrize(
    "days, why",
    [
        (_heat_pump_days(n=9), "too few days"),
        ([(10.0 + i * 0.2, 12.0 + i * 0.2) for i in range(12)], "days too alike"),
        ([(i - 2.0, 30.0 - 0.8 * (17 - i)) for i in range(12)], "more use on warm days"),
        ([(i - 2.0, 10.0 + (5.0 if i % 2 else -5.0)) for i in range(12)], "a fit explaining nothing"),
    ],
)
def test_an_unusable_model_leaves_the_factor_at_one(days, why):
    model = fit_heating_model(days, LIMIT)
    assert model is None or not model.usable, why
    assert temperature_factor(model, -10.0, 8.0, LIMIT) == 1.0


def test_no_model_without_two_days():
    assert fit_heating_model([(1.0, 10.0)], LIMIT) is None
    assert temperature_factor(None, -10.0, 8.0, LIMIT) == 1.0


def test_a_cold_day_scales_the_need_and_the_factor_is_clamped():
    model = fit_heating_model(_heat_pump_days(), LIMIT)
    # the fortnight averaged 10 heating degrees: 18 kWh a day
    assert temperature_factor(model, 0.0, 10.0, LIMIT) == pytest.approx(22.0 / 18.0)
    # 5 degrees warmer would be 14/18 = 0.78: floored, a warm forecast cannot cut deep
    assert temperature_factor(model, 10.0, 10.0, LIMIT) == FACTOR_MIN
    assert temperature_factor(model, 7.0, 10.0, LIMIT) == pytest.approx(16.4 / 18.0)
    assert temperature_factor(model, -40.0, 10.0, LIMIT) == FACTOR_MAX
    assert temperature_factor(model, 30.0, 10.0, LIMIT) == FACTOR_MIN


def test_the_daily_forecast_is_read_for_the_local_day():
    day = date(2026, 12, 24)
    forecast = [
        {"datetime": "2026-12-23T11:00:00+00:00", "temperature": 5.0, "templow": 1.0},
        {"datetime": "2026-12-24T11:00:00+00:00", "temperature": 2.0, "templow": -6.0},
        {"datetime": "2026-12-25T11:00:00+00:00", "temperature": 0.0},
    ]
    assert daily_mean_from_forecast(forecast, day, _local) == -2.0
    assert daily_mean_from_forecast(forecast, date(2026, 12, 25), _local) == 0.0  # no low
    assert daily_mean_from_forecast(forecast, date(2026, 12, 26), _local) is None
    # 23:30 UTC on the 23rd is already the 24th in Berlin
    late = [{"datetime": "2026-12-23T23:30:00+00:00", "temperature": -1.0, "templow": -3.0}]
    assert daily_mean_from_forecast(late, day, _local) == -2.0


def test_malformed_forecast_entries_are_skipped():
    day = date(2026, 12, 24)
    forecast = [
        {"datetime": "not a date", "temperature": 1.0},
        {"datetime": None, "temperature": 1.0},
        {"datetime": "2026-12-24T11:00:00+00:00", "temperature": "cold"},
        {"datetime": "2026-12-24T12:00:00+00:00", "temperature": True},
        {"datetime": "2026-12-24T13:00:00", "temperature": 3.0, "templow": None},
    ]
    assert daily_mean_from_forecast(forecast, day, _local) == 3.0


def test_the_hourly_forecast_needs_six_hours_of_the_day():
    day = date(2026, 12, 24)
    hours = [{"datetime": f"2026-12-24T{h:02d}:00:00+01:00", "temperature": float(h)}
             for h in range(8, 14)]
    assert hourly_mean_from_forecast(hours, day, _local) == pytest.approx(10.5)
    assert hourly_mean_from_forecast(hours[:5], day, _local) is None


def test_the_temperature_diary_keeps_whole_days():
    log = TemperatureLog()
    day = date(2026, 11, 1)
    for i in range(MIN_SAMPLES_PER_DAY):
        assert not log.observe(day, 2.0 + (i % 2))
    assert log.observe(day + timedelta(days=1), 0.0)
    assert log.days == {day: pytest.approx(2.5)}
    # A day watched too briefly (a restart late in the evening) is not a day
    assert not log.observe(day + timedelta(days=2), 1.0)
    assert set(log.days) == {day}
    # Unreadable samples are no samples
    assert not log.observe(day + timedelta(days=2), None)


def test_the_temperature_diary_survives_a_restart_and_stays_bounded():
    log = TemperatureLog()
    start = date(2026, 1, 1)
    for d in range(TEMPERATURE_LOG_DAYS + 5):
        for _ in range(MIN_SAMPLES_PER_DAY):
            log.observe(start + timedelta(days=d), float(d))
    log.observe(start + timedelta(days=TEMPERATURE_LOG_DAYS + 5), 0.0)
    assert len(log.days) == TEMPERATURE_LOG_DAYS
    assert min(log.days) == start + timedelta(days=5)

    restored = TemperatureLog(log.snapshot())
    assert restored.days == pytest.approx(log.days)
    assert TemperatureLog({"days": {"bad": 1.0, "2026-01-01": "x"}}).days == {}
    assert TemperatureLog("nonsense").days == {}
