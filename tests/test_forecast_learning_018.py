"""Plan 018 part 1: the PV forecast, corrected by what this roof delivered.

Pascal, 25 October 2026: the forecast knows neither the pitch nor the gap
behind the panels nor how they behave in the cold, and it gets worse as they
age - so learn by season how far it is off, and how far it scatters.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import forecast_learning as fl
from custom_components.inverter_charge_night.const import (
    CONF_FORECAST_ERROR_MARGIN,
    CONF_PV_ENERGY_TODAY_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
)
from tests.test_grid_limit import BASE_CONFIG, NOW, PV, _make_coordinator, _register

TODAY = date(2026, 1, 14)
PV_METER = "sensor.pv_today"
PV_TODAY = "sensor.pv_forecast_today"
CONFIG = {**BASE_CONFIG, CONF_PV_ENERGY_TODAY_ENTITY: PV_METER,
          CONF_PV_FORECAST_TODAY_ENTITY: PV_TODAY}


def _season(log: fl.ForecastLog, *, ratio: float, days: int = 20, forecast: float = 20.0,
            kind: str = fl.KIND_AHEAD, end: date = TODAY, start_offset: int = 1) -> None:
    for offset in range(start_offset, start_offset + days):
        day = end - timedelta(days=offset)
        log.note(day, kind, forecast)
        log.note(day, fl.KIND_ACTUAL, forecast * ratio)


# --- The arithmetic ------------------------------------------------------------


def test_too_few_days_in_the_season_leave_the_forecast_alone():
    log = fl.ForecastLog()
    _season(log, ratio=0.8, days=fl.MIN_SEASON_DAYS - 1)

    calibration = log.calibrate(TODAY, fl.KIND_AHEAD, TODAY)

    assert calibration.status == fl.STATUS_LEARNING
    assert calibration.factor == 1.0
    assert calibration.days == fl.MIN_SEASON_DAYS - 1


def test_a_winter_that_always_overshoots_is_corrected():
    log = fl.ForecastLog()
    _season(log, ratio=0.8)

    calibration = log.calibrate(TODAY, fl.KIND_AHEAD, TODAY)

    assert calibration.active
    assert calibration.factor == pytest.approx(0.8)
    # No spread around the bias: the floor holds (Pascal: never below 5 %)
    assert calibration.margin_low_pct == fl.MARGIN_FLOOR_PCT
    assert calibration.margin_high_pct == fl.MARGIN_FLOOR_PCT
    # Off by a fifth of the forecast, every day - and not at all once corrected
    assert calibration.error_raw_pct == pytest.approx(20.0)
    assert calibration.error_corrected_pct == pytest.approx(0.0)


def test_the_summer_does_not_correct_the_winter():
    log = fl.ForecastLog()
    _season(log, ratio=0.8)
    # Last July: the forecast too low by a fifth
    _season(log, ratio=1.2, end=date(2025, 7, 30), start_offset=0, days=30)

    assert log.calibrate(TODAY, fl.KIND_AHEAD, TODAY).factor == pytest.approx(0.8)
    assert log.calibrate(date(2026, 7, 20), fl.KIND_AHEAD, TODAY).factor == pytest.approx(1.2)


def test_kilowatt_hours_count_not_cloudy_percentages():
    """A dull day off by half says less about the roof than a sunny one off by a fifth."""
    log = fl.ForecastLog()
    _season(log, ratio=0.8, days=10, forecast=30.0)
    _season(log, ratio=2.0, days=10, forecast=2.0, start_offset=11)

    factor = log.calibrate(TODAY, fl.KIND_AHEAD, TODAY).factor

    # About (10 * 24 + 10 * 4) / (10 * 30 + 10 * 2) = 0.875; the median of the
    # ratios would have said anything between 0.8 and 2
    assert 0.8 < factor < 0.9


def test_a_recent_year_counts_more_than_an_older_one():
    """The panels age: last year's ratio is worth half of this year's."""
    log = fl.ForecastLog()
    _season(log, ratio=0.8)
    _season(log, ratio=1.0, end=TODAY - timedelta(days=365))

    factor = log.calibrate(TODAY, fl.KIND_AHEAD, TODAY).factor

    assert 0.85 < factor < 0.88  # (0.8 + 0.5 * 1.0) / 1.5 = 0.867


def test_the_spread_sets_the_band_on_both_sides():
    log = fl.ForecastLog()
    for offset, ratio in enumerate([0.5, 0.7, 0.9, 1.0, 1.0, 1.0, 1.1, 1.1, 1.2, 1.3] * 2, start=1):
        day = TODAY - timedelta(days=offset)
        log.note(day, fl.KIND_AHEAD, 20.0)
        log.note(day, fl.KIND_ACTUAL, 20.0 * ratio)

    calibration = log.calibrate(TODAY, fl.KIND_AHEAD, TODAY)

    assert calibration.margin_low_pct is not None and calibration.margin_high_pct is not None
    assert calibration.margin_low_pct > 20.0  # four days in ten 30 % or more below
    assert calibration.margin_high_pct > 10.0


def test_the_factor_is_bounded():
    log = fl.ForecastLog()
    _season(log, ratio=0.1)

    assert log.calibrate(TODAY, fl.KIND_AHEAD, TODAY).factor == fl.FACTOR_MIN


def test_a_forecast_of_almost_nothing_says_nothing_about_the_roof():
    log = fl.ForecastLog()
    _season(log, ratio=3.0, forecast=0.5)

    assert log.calibrate(TODAY, fl.KIND_AHEAD, TODAY).status == fl.STATUS_LEARNING


def test_today_is_not_yet_a_completed_day():
    log = fl.ForecastLog()
    _season(log, ratio=0.8, days=fl.MIN_SEASON_DAYS - 1)
    log.note(TODAY, fl.KIND_AHEAD, 20.0)
    log.note(TODAY, fl.KIND_ACTUAL, 3.0)  # the meter at noon

    assert log.calibrate(TODAY, fl.KIND_AHEAD, TODAY).status == fl.STATUS_LEARNING


def test_the_season_wraps_around_new_year():
    assert fl.season_distance_days(date(2025, 12, 30), date(2026, 1, 5)) == 6


def test_each_kind_has_its_own_diary():
    log = fl.ForecastLog()
    _season(log, ratio=0.8)

    assert log.calibrate(TODAY, fl.KIND_TODAY, TODAY).status == fl.STATUS_LEARNING


def test_the_diary_survives_a_round_trip_and_ignores_junk():
    log = fl.ForecastLog()
    log.note(TODAY, fl.KIND_AHEAD, 12.5)
    raw = log.snapshot()
    raw["days"]["not-a-date"] = {"f": 1.0}
    raw["days"]["2026-01-01"] = {"f": "x", "a": -3, "t": True}

    again = fl.ForecastLog(raw)

    assert again.days == {TODAY: {fl.KIND_AHEAD: 12.5}}


def test_a_backfill_never_overwrites_what_was_observed():
    log = fl.ForecastLog()
    log.note(TODAY, fl.KIND_AHEAD, 12.0)

    assert log.note_missing(TODAY, fl.KIND_AHEAD, 99.0) is False
    assert log.days[TODAY][fl.KIND_AHEAD] == 12.0


def test_three_years_are_kept():
    log = fl.ForecastLog()
    log.note(TODAY - timedelta(days=fl.LOG_DAYS + 1), fl.KIND_AHEAD, 5.0)
    log.note(TODAY - timedelta(days=fl.LOG_DAYS - 1), fl.KIND_AHEAD, 5.0)

    assert log.prune(TODAY) is True
    assert len(log.days) == 1


def test_days_past_the_three_years_do_not_count_before_the_prune():
    log = fl.ForecastLog()
    _season(log, ratio=0.8, end=TODAY - timedelta(days=4 * 365))

    assert log.calibrate(TODAY, fl.KIND_AHEAD, TODAY).status == fl.STATUS_LEARNING
    assert len(log.days) == 20  # still there, until the prune
    assert log.calibrate(TODAY - timedelta(days=4 * 365), fl.KIND_AHEAD,
                         TODAY - timedelta(days=4 * 365)).active


def test_days_waiting_for_their_yield():
    log = fl.ForecastLog()
    log.note(TODAY - timedelta(days=2), fl.KIND_AHEAD, 5.0)
    log.note(TODAY - timedelta(days=1), fl.KIND_AHEAD, 5.0)
    log.note(TODAY - timedelta(days=1), fl.KIND_ACTUAL, 4.0)
    log.note(TODAY, fl.KIND_AHEAD, 5.0)

    assert log.days_without_actual(TODAY) == [TODAY - timedelta(days=2)]


def test_weighted_quantile_edges():
    assert fl.weighted_quantile([], 0.5) is None
    assert fl.weighted_quantile([(1.0, 1.0), (2.0, 3.0)], 0.5) == 2.0
    assert fl.weighted_quantile([(1.0, 1.0), (2.0, 1.0)], 1.0) == 2.0


# --- In the coordinator ---------------------------------------------------------


@pytest.fixture
def coordinator(mock_hass):
    _register(mock_hass)
    return _make_coordinator(mock_hass, CONFIG)


def _at(hour: int, day: date = TODAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0)


def test_the_evening_forecast_for_tomorrow_is_noted(coordinator, mock_hass):
    mock_hass.states.async_set(PV, "18.0", {"unit_of_measurement": "kWh"})
    with patch(NOW, return_value=_at(17)):
        coordinator._observe_forecasts()
    assert TODAY + timedelta(days=1) not in coordinator._forecast_log.days

    with patch(NOW, return_value=_at(19)):
        coordinator._observe_forecasts()
    mock_hass.states.async_set(PV, "16.5", {"unit_of_measurement": "kWh"})
    with patch(NOW, return_value=_at(23)):
        coordinator._observe_forecasts()

    assert coordinator._forecast_log.days[TODAY + timedelta(days=1)] == {fl.KIND_AHEAD: 16.5}


def test_the_morning_forecast_for_today_is_noted(coordinator, mock_hass):
    mock_hass.states.async_set(PV_TODAY, "9000", {"unit_of_measurement": "Wh"})
    with patch(NOW, return_value=_at(7)):
        coordinator._observe_forecasts()
    mock_hass.states.async_set(PV_TODAY, "4000", {"unit_of_measurement": "Wh"})
    with patch(NOW, return_value=_at(11)):
        coordinator._observe_forecasts()

    assert coordinator._forecast_log.days[TODAY] == {fl.KIND_TODAY: 9.0}


def test_an_unavailable_forecast_is_not_noted(coordinator, mock_hass):
    mock_hass.states.async_set(PV, "unavailable")
    with patch(NOW, return_value=_at(20)):
        coordinator._observe_forecasts()

    assert coordinator._forecast_log.days == {}


def test_without_a_pv_meter_there_is_nothing_to_learn_against(mock_hass):
    _register(mock_hass)
    co = _make_coordinator(mock_hass, BASE_CONFIG)
    _season(co._forecast_log, ratio=0.8)

    with patch(NOW, return_value=_at(23)):
        assert co.forecast_calibration(fl.KIND_AHEAD).status == fl.STATUS_NO_METER
        assert co._learned_forecast(10.0, fl.KIND_AHEAD, TODAY) == (10.0, 10.0, 10.0)


def test_the_learned_forecast_and_its_band_are_used(coordinator):
    _season(coordinator._forecast_log, ratio=0.8)

    with patch(NOW, return_value=_at(23)):
        kwh, low, high = coordinator._learned_forecast(10.0, fl.KIND_AHEAD, TODAY)

    assert kwh == pytest.approx(8.0)
    assert (low, high) == (fl.MARGIN_FLOOR_PCT, fl.MARGIN_FLOOR_PCT)


def test_switched_off_the_settings_apply_again(coordinator):
    _season(coordinator._forecast_log, ratio=0.8)
    coordinator.use_learned_values = False

    with patch(NOW, return_value=_at(23)):
        assert coordinator._learned_forecast(10.0, fl.KIND_AHEAD, TODAY) == (10.0, 10.0, 10.0)


@pytest.mark.asyncio
async def test_the_night_plan_works_with_the_corrected_forecast(coordinator):
    _season(coordinator._forecast_log, ratio=0.8)

    with patch(NOW, return_value=_at(23)):
        plan_input, _, _ = await coordinator._build_plan_input(10.0, True)

    assert plan_input.forecast_kwh_next_day == pytest.approx(8.0)
    assert plan_input.error_margin_pct == fl.MARGIN_FLOOR_PCT
    assert plan_input.error_margin_high_pct == fl.MARGIN_FLOOR_PCT


@pytest.mark.asyncio
async def test_without_learning_the_plan_keeps_the_configured_margin(coordinator):
    coordinator.config = {**coordinator.config, CONF_FORECAST_ERROR_MARGIN: 12.0}

    with patch(NOW, return_value=_at(23)):
        plan_input, _, _ = await coordinator._build_plan_input(10.0, True)

    assert plan_input.forecast_kwh_next_day == 10.0
    assert (plan_input.error_margin_pct, plan_input.error_margin_high_pct) == (12.0, 12.0)


@pytest.mark.asyncio
async def test_the_morning_forecast_is_judged_by_its_own_diary(coordinator):
    """The curtailment outlook reads today's entity: today's diary applies."""
    _season(coordinator._forecast_log, ratio=0.8)  # evening forecasts only
    _season(coordinator._forecast_log, ratio=0.5, kind=fl.KIND_TODAY)

    with patch(NOW, return_value=_at(10)):
        plan_input, _, _ = await coordinator._build_plan_input(10.0, True, entity_id=PV_TODAY)

    assert plan_input.forecast_kwh_next_day == pytest.approx(5.0)


def test_the_headroom_formula_takes_the_more_sun_margin(coordinator):
    log = coordinator._forecast_log
    for offset, ratio in enumerate([1.0, 1.0, 1.0, 1.4] * 5, start=1):
        day = TODAY - timedelta(days=offset)
        log.note(day, fl.KIND_AHEAD, 20.0)
        log.note(day, fl.KIND_ACTUAL, 20.0 * ratio)

    with patch(NOW, return_value=_at(23)):
        _, low, high = coordinator._plan_forecast(10.0, PV)

    assert high > low


def test_the_sensor_attributes(coordinator):
    _season(coordinator._forecast_log, ratio=0.9)

    with patch(NOW, return_value=_at(23)):
        attrs = coordinator.forecast_learning_attributes()

    assert attrs["status"] == fl.STATUS_ACTIVE
    assert attrs["factor"] == pytest.approx(0.9)
    assert attrs["applied"] is True
    assert attrs["today"]["status"] == fl.STATUS_LEARNING
    assert attrs["logged_days"] == 20


# --- Reading the recorder ----------------------------------------------------------


def _recorder(coordinator) -> Any:
    instance = MagicMock()
    instance.async_add_executor_job = AsyncMock(side_effect=lambda job: job())
    return patch("custom_components.inverter_charge_night.coordinator.get_instance",
                 return_value=instance)


def _state(value: str, when: datetime, unit: str = "kWh") -> SimpleNamespace:
    return SimpleNamespace(state=value, attributes={"unit_of_measurement": unit},
                           last_updated=when, entity_id=PV)


@pytest.mark.asyncio
async def test_the_recorder_fills_in_the_last_days_once(coordinator):
    history = {PV: [_state("15", _at(12, TODAY - timedelta(days=3))),
                    _state("11", _at(23, TODAY - timedelta(days=3)) + timedelta(minutes=30)),
                    # Midnight: the entity now forecasts the day after
                    _state("99", _at(0, TODAY - timedelta(days=2)) + timedelta(seconds=20))],
               PV_TODAY: [_state("20", _at(6, TODAY - timedelta(days=3))),
                          _state("10", _at(8, TODAY - timedelta(days=3)) + timedelta(minutes=30)),
                          _state("14", _at(9, TODAY - timedelta(days=3)))]}
    with patch(NOW, return_value=_at(12)), _recorder(coordinator), patch(
        "homeassistant.components.recorder.history.get_significant_states", return_value=history
    ):
        assert await coordinator._backfill_forecasts() is True

    # The same cut-offs as the live notes (Codex on #46): the last value before
    # midnight, not the rollover; the last before 09:00, the 08:30 revision
    assert coordinator._forecast_log.days[TODAY - timedelta(days=2)][fl.KIND_AHEAD] == 11.0
    assert coordinator._forecast_log.days[TODAY - timedelta(days=3)][fl.KIND_TODAY] == 10.0


@pytest.mark.asyncio
async def test_the_pv_meter_gives_each_day_its_yield(coordinator, mock_hass):
    mock_hass.states.async_set(PV_METER, "3.2", {"unit_of_measurement": "kWh"})
    day = TODAY - timedelta(days=1)
    coordinator._forecast_log.note(day, fl.KIND_AHEAD, 10.0)
    import homeassistant.util.dt as dt_util

    def hour(h: int) -> float:
        return dt_util.as_utc(datetime(day.year, day.month, day.day, h, 30).astimezone()).timestamp()

    changes = {10: 2.5, 12: 4.0, 13: -30.0}  # -30: the meter's midnight reset seen late
    rows = {PV_METER: [{"start": hour(h), "change": changes.get(h, 0.0)} for h in range(24)]}
    with patch(NOW, return_value=_at(12)), _recorder(coordinator), patch(
        "homeassistant.components.recorder.statistics.statistics_during_period", return_value=rows
    ):
        assert await coordinator._read_pv_actuals(TODAY) is True

    assert coordinator._forecast_log.days[day][fl.KIND_ACTUAL] == pytest.approx(6.5)


@pytest.mark.asyncio
async def test_a_day_the_statistics_cover_only_in_part_stays_open(coordinator, mock_hass):
    """Codex on #46: kept, it would be a low yield for good."""
    mock_hass.states.async_set(PV_METER, "3.2", {"unit_of_measurement": "kWh"})
    day = TODAY - timedelta(days=1)
    coordinator._forecast_log.note(day, fl.KIND_AHEAD, 10.0)
    import homeassistant.util.dt as dt_util

    def hour(h: int) -> float:
        return dt_util.as_utc(datetime(day.year, day.month, day.day, h, 30).astimezone()).timestamp()

    # Recording began at 14:00
    rows = {PV_METER: [{"start": hour(h), "change": 0.5} for h in range(14, 24)]}
    with patch(NOW, return_value=_at(12)), _recorder(coordinator), patch(
        "homeassistant.components.recorder.statistics.statistics_during_period", return_value=rows
    ):
        assert await coordinator._read_pv_actuals(TODAY) is False

    assert coordinator._forecast_log.days_without_actual(TODAY) == [day]


@pytest.mark.asyncio
async def test_the_daily_refresh_runs_each_step_and_saves(coordinator):
    coordinator._learning_store = MagicMock()
    coordinator._backfill_forecasts = AsyncMock(return_value=False)
    coordinator._read_pv_actuals = AsyncMock(side_effect=RuntimeError("recorder gone"))
    coordinator._learn_battery = AsyncMock(return_value=False)

    with patch(NOW, return_value=_at(12)):
        await coordinator.async_refresh_learning()

    coordinator._learn_battery.assert_awaited_once()
    assert coordinator._forecast_backfilled is True
    coordinator._learning_store.async_delay_save.assert_called_once()
    # A step without an answer: asked again on the next poll, not tomorrow
    assert coordinator._learning_refreshed is None

    coordinator._read_pv_actuals = AsyncMock(return_value=False)
    with patch(NOW, return_value=_at(12)):
        await coordinator.async_refresh_learning()
    assert coordinator._learning_refreshed == TODAY


def test_the_refresh_waits_for_the_recorder_and_runs_once_a_day(coordinator, mock_hass):
    coordinator._learning_store = MagicMock()
    mock_hass.async_create_background_task = MagicMock()
    with patch(NOW, return_value=_at(12)):
        coordinator._schedule_learning_refresh()
    mock_hass.async_create_background_task.assert_not_called()

    mock_hass.data = {"recorder_instance": object()}
    with patch(NOW, return_value=_at(12)):
        coordinator._schedule_learning_refresh()
    assert mock_hass.async_create_background_task.call_count == 1
    mock_hass.async_create_background_task.call_args.args[0].close()

    coordinator._learning_refreshed = TODAY
    with patch(NOW, return_value=_at(13)):
        coordinator._schedule_learning_refresh()
    assert mock_hass.async_create_background_task.call_count == 1


@pytest.mark.asyncio
async def test_the_store_is_read_at_setup(coordinator):
    stored = {"pv": {"days": {"2026-01-10": {"f": 7.0, "a": 6.0}}},
              "battery": {"samples": [{"ts": 1.0, "kind": "capacity", "v": 9.5}]},
              "backfilled": True, "since": {"capacity": 5.0}}
    with patch("custom_components.inverter_charge_night.coordinator.Store") as store:
        store.return_value.async_load = AsyncMock(
            return_value={**stored, "since": {"capacity": 5.0, "charge_efficiency": True, "x": "y"}})
        await coordinator.async_load_learning()

    assert coordinator._forecast_log.days[date(2026, 1, 10)] == {"f": 7.0, "a": 6.0}
    assert coordinator._battery_log.count("capacity") == 1
    assert coordinator._forecast_backfilled is True
    assert coordinator._learning_snapshot() == {**stored, "sources": coordinator._current_learning_sources()}


@pytest.mark.asyncio
async def test_a_diary_from_another_forecast_provider_is_not_used(coordinator):
    """Codex on #46: the old provider's bias is not the new one's."""
    _season(coordinator._forecast_log, ratio=0.8)
    _season(coordinator._forecast_log, ratio=0.7, kind=fl.KIND_TODAY)
    stored = {**coordinator._learning_snapshot(), "backfilled": True,
              "sources": {**coordinator._current_learning_sources(), fl.KIND_AHEAD: "sensor.old_provider"}}

    with patch("custom_components.inverter_charge_night.coordinator.Store") as store:
        store.return_value.async_load = AsyncMock(return_value=stored)
        await coordinator.async_load_learning()

    assert all(fl.KIND_AHEAD not in values for values in coordinator._forecast_log.days.values())
    # The morning forecasts and the yields came from the same entities as before
    assert sum(fl.KIND_TODAY in v for v in coordinator._forecast_log.days.values()) == 20
    assert sum(fl.KIND_ACTUAL in v for v in coordinator._forecast_log.days.values()) == 20
    assert coordinator._forecast_backfilled is False  # the new one is filled in
    assert coordinator._learning_snapshot()["sources"][fl.KIND_AHEAD] == PV


def test_an_options_change_is_noticed_on_the_next_poll(coordinator, mock_hass):
    _season(coordinator._forecast_log, ratio=0.8)
    with patch(NOW, return_value=_at(12)):
        coordinator._observe_forecasts()  # records the sources
    coordinator.config = {**coordinator.config, CONF_PV_ENERGY_TODAY_ENTITY: "sensor.new_meter"}

    with patch(NOW, return_value=_at(12)):
        coordinator._observe_forecasts()

    assert all(fl.KIND_ACTUAL not in values for values in coordinator._forecast_log.days.values())
    with patch(NOW, return_value=_at(23)):
        assert coordinator.forecast_calibration(fl.KIND_AHEAD).status == fl.STATUS_LEARNING


@pytest.mark.asyncio
async def test_a_diary_from_before_sources_were_recorded_is_kept(coordinator):
    _season(coordinator._forecast_log, ratio=0.8)
    stored = {"pv": coordinator._forecast_log.snapshot(), "backfilled": True}

    with patch("custom_components.inverter_charge_night.coordinator.Store") as store:
        store.return_value.async_load = AsyncMock(return_value=stored)
        await coordinator.async_load_learning()

    assert len(coordinator._forecast_log.days) == 20
    assert coordinator._learning_sources == coordinator._current_learning_sources()


@pytest.mark.asyncio
async def test_a_broken_store_starts_afresh(coordinator):
    with patch("custom_components.inverter_charge_night.coordinator.Store") as store:
        store.return_value.async_load = AsyncMock(side_effect=ValueError("corrupt"))
        await coordinator.async_load_learning()

    assert coordinator._forecast_log.days == {}
    assert coordinator._learning_store is store.return_value


@pytest.mark.asyncio
async def test_removing_the_entry_deletes_what_it_learned(mock_hass):
    from custom_components.inverter_charge_night import async_remove_entry

    entry = MagicMock()
    entry.entry_id = "entry_1"
    with patch("custom_components.inverter_charge_night.Store") as store:
        store.return_value.async_remove = AsyncMock()
        await async_remove_entry(mock_hass, entry)

    assert store.call_args.args[2] == "inverter_charge_night.learning.entry_1"
    store.return_value.async_remove.assert_awaited_once()


# --- Every forecast reader takes the correction ---------------------------------


def _asymmetric(log: fl.ForecastLog, end: date) -> None:
    """Mostly right, now and then far better: a narrow less-sun, a wide more-sun side."""
    for offset, ratio in enumerate([1.0, 1.0, 1.0, 1.6] * 5, start=1):
        day = end - timedelta(days=offset)
        log.note(day, fl.KIND_AHEAD, 20.0)
        log.note(day, fl.KIND_ACTUAL, 20.0 * ratio)


@pytest.mark.asyncio
async def test_the_headroom_formula_reckons_with_the_more_sun_side(mock_hass):
    from custom_components.inverter_charge_night.const import CONF_PLANNER_MODE, PLANNER_MODE_HEADROOM

    _register(mock_hass)
    mock_hass.states.async_set(PV, "10", {"unit_of_measurement": "kWh"})
    co = _make_coordinator(mock_hass, {**CONFIG, CONF_PLANNER_MODE: PLANNER_MODE_HEADROOM})
    _asymmetric(co._forecast_log, TODAY)
    with patch(NOW, return_value=_at(23)):
        calibration = co.forecast_calibration(fl.KIND_AHEAD, TODAY + timedelta(days=1))
    assert calibration.margin_high_pct > calibration.margin_low_pct

    with patch(NOW, return_value=_at(23)), patch(
        "custom_components.inverter_charge_night.coordinator.calculate_required_soc",
        return_value=50.0,
    ) as headroom:
        await co._calculate_initial_soc()

    forecast, _, margin = headroom.call_args.args[:3]
    assert forecast == pytest.approx(10.0 * calibration.factor)
    assert margin == pytest.approx(calibration.margin_high_pct)


@pytest.mark.asyncio
async def test_the_evening_outlook_takes_the_bias_but_no_band(mock_hass):
    """Plan 016 reckons tight; the learned bias is still the better forecast."""
    from tests.test_high_price_window import _outlook_coordinator

    co = _outlook_coordinator(mock_hass, soc="12", forecast="10")
    co.config = {**co.config, CONF_PV_ENERGY_TODAY_ENTITY: PV_METER}
    _season(co._forecast_log, ratio=0.5, kind=fl.KIND_TODAY, end=date(2026, 6, 1))

    with patch(NOW, return_value=datetime(2026, 6, 1, 14, 0)), patch(
        "custom_components.inverter_charge_night.coordinator.evening_outlook"
    ) as outlook:
        await co.async_evening_outlook()

    assert outlook.call_args.kwargs["forecast_kwh_today"] == pytest.approx(5.0)
    assert outlook.call_args.kwargs["forecast_error_margin_pct"] == 0.0


@pytest.mark.asyncio
async def test_the_curtailment_outlook_takes_the_morning_correction(mock_hass):
    from tests.test_curtailment import NOON, TODAY as CURTAIL_TODAY, _make_coordinator as curtail

    co = curtail(mock_hass, {CONF_PV_ENERGY_TODAY_ENTITY: PV_METER}, forecast_today="40")
    _season(co._forecast_log, ratio=1.25, kind=fl.KIND_TODAY, end=NOON.date())
    assert co.config[CONF_PV_FORECAST_TODAY_ENTITY] == CURTAIL_TODAY

    with patch(NOW, return_value=NOON), patch(
        "custom_components.inverter_charge_night.coordinator.curtailment_outlook"
    ) as outlook:
        await co.async_curtailment_outlook()

    assert outlook.call_args.kwargs["forecast_kwh_today"] == pytest.approx(50.0)
    assert outlook.call_args.kwargs["error_margin_pct"] == fl.MARGIN_FLOOR_PCT


def test_the_planner_keeps_each_side_of_the_band():
    from custom_components.inverter_charge_night.planner import PlanInput, surplus_kwh

    def plan(high: float | None) -> PlanInput:
        return PlanInput(
            capacity_kwh=10.0, current_soc=20.0, user_min_soc=8.0, user_max_soc=100.0,
            forecast_kwh_next_day=10.0, forecast_available=True, error_margin_pct=5.0,
            window_end=datetime(2026, 1, 15, 6, 0), pv_crossover=datetime(2026, 1, 15, 9, 0),
            sunset=datetime(2026, 1, 15, 17, 0), house_load_kw_profile=[0.0] * 24,
            reserve_kwh=0.0, charge_efficiency=0.9, error_margin_high_pct=high,
        )

    assert surplus_kwh(plan(None), cautious=True) == pytest.approx(9.5)
    assert surplus_kwh(plan(None)) == pytest.approx(10.5)
    assert surplus_kwh(plan(40.0), cautious=True) == pytest.approx(9.5)
    assert surplus_kwh(plan(40.0)) == pytest.approx(14.0)


@pytest.mark.asyncio
async def test_unloading_writes_the_store_and_closes_it(coordinator):
    """CodeRabbit on #46: no delayed save may outlive the coordinator."""
    import asyncio

    store = MagicMock()
    store.async_save = AsyncMock()
    coordinator._learning_store = store
    coordinator._forecast_log.note(TODAY, fl.KIND_AHEAD, 9.0)
    running = asyncio.get_running_loop().create_future()
    coordinator._learning_task = running

    await coordinator.async_unload_learning()

    store.async_save.assert_awaited_once()
    assert store.async_save.await_args.args[0]["pv"]["days"][TODAY.isoformat()] == {"f": 9.0}
    assert running.cancelled()
    coordinator._save_learning()
    store.async_delay_save.assert_not_called()


@pytest.mark.asyncio
async def test_a_store_that_cannot_be_written_does_not_stop_the_unload(coordinator):
    store = MagicMock()
    store.async_save = AsyncMock(side_effect=OSError("disk full"))
    coordinator._learning_store = store

    await coordinator.async_unload_learning()

    assert coordinator._learning_store is None
    await coordinator.async_unload_learning()  # twice is harmless
