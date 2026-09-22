"""Curtailment: what a permanent feed-in cap throws away (plan 014, stage one).

A 60 % or 70 % cap at the grid connection point is not something the grid
operator switches on. It is always in force and only *bites* around midday,
when production exceeds it - so the hours it bites in follow from the forecast,
the sun times and the load profile rather than from a schedule.

Nothing here writes to the inverter. The point of this release is to hold the
model against the measurement for a season first: the owner does not know his
array size or whether the cap ever really bites, and the model on its own says
it would not. One of the two is wrong, and throttling on the wrong one costs
twice - short in the evening, then bought back from the grid.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.recorder import DATA_INSTANCE

from custom_components.inverter_charge_night.const import (
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CURTAILMENT_FEED_IN_ENTITY,
    CONF_CURTAILMENT_LIMIT_W,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)
from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)

COORDINATOR = "custom_components.inverter_charge_night.coordinator"

FEED_IN = "sensor.feed_in_power"
TODAY = "sensor.pv_today"
ABS_LIMIT = "number.absolute_charge_power"

NOON = datetime(2026, 6, 21, 12, 0)
SUNRISE = datetime(2026, 6, 21, 5, 30)
SUNSET = datetime(2026, 6, 21, 21, 30)

CONFIG = {
    CONF_MIN_SOC_ENTITY: "number.min_soc",
    CONF_GRID_CHARGE_SWITCH: "switch.grid",
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_PV_FORECAST_ENTITY: "sensor.pv",
    CONF_PV_FORECAST_TODAY_ENTITY: TODAY,
    CONF_BATTERY_CAPACITY: 35.0,
    CONF_USER_MIN_SOC: 10.0,
    CONF_USER_MAX_SOC: 95.0,
    CONF_DEFAULT_MIN_SOC: 10.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_START_TIME: "23:00",
    CONF_END_TIME: "05:00",
    CONF_CURTAILMENT_LIMIT_W: 7200,
}


def _make_coordinator(mock_hass, extra=None, forecast_today="100"):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = CONFIG | (extra or {})
    entry.options = {}
    mock_hass.config_entries.async_update_entry = MagicMock(return_value=True)
    mock_hass.states.async_set("number.min_soc", "10", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.states.async_set("sensor.soc", "40", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("sensor.pv", "80", {"unit_of_measurement": "kWh"})
    if forecast_today is not None:
        mock_hass.states.async_set(TODAY, forecast_today, {"unit_of_measurement": "kWh"})
    coordinator = InverterChargeNightCoordinator(mock_hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    coordinator._house_load_profile = AsyncMock(return_value=[0.7] * 24)
    coordinator._sun_times = MagicMock(return_value=(SUNRISE, SUNSET))
    return coordinator


async def _outlook(coordinator, now=NOON):
    with patch(f"{COORDINATOR}.dt_util.now", return_value=now):
        return await coordinator.async_curtailment_outlook()


# --- The outlook itself ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_cap_that_bites_reports_what_it_costs(mock_hass):
    coordinator = _make_coordinator(mock_hass)

    outlook = await _outlook(coordinator)

    assert outlook is not None
    assert outlook.overflow_kwh > 0
    assert outlook.binding_start is not None and outlook.binding_end is not None
    assert SUNRISE < outlook.binding_start < outlook.binding_end < SUNSET
    assert outlook.morning_target_soc < 95.0, "room has to be left for it"


@pytest.mark.asyncio
async def test_a_cap_above_the_peak_costs_nothing(mock_hass):
    """The honest answer on a big array is zero, not a made-up shortfall."""
    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_LIMIT_W: 12000})

    outlook = await _outlook(coordinator)

    assert outlook is not None
    assert outlook.overflow_kwh == 0.0
    assert outlook.binding_start is None
    assert outlook.morning_target_soc == 95.0


@pytest.mark.asyncio
async def test_no_cap_configured_means_no_opinion(mock_hass):
    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_LIMIT_W: 0})
    assert await _outlook(coordinator) is None


@pytest.mark.asyncio
async def test_the_feature_refuses_to_run_off_tomorrows_forecast(mock_hass):
    """_get_active_forecast_entity falls back to tomorrow's entity, which for a
    morning decision is quietly the wrong day. No answer beats the wrong day.
    """
    config = {k: v for k, v in CONFIG.items() if k != CONF_PV_FORECAST_TODAY_ENTITY}
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = config
    entry.options = {}
    mock_hass.config_entries.async_update_entry = MagicMock(return_value=True)
    mock_hass.states.async_set("number.min_soc", "10", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.states.async_set("sensor.soc", "40", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("sensor.pv", "80", {"unit_of_measurement": "kWh"})
    coordinator = InverterChargeNightCoordinator(mock_hass, entry)
    coordinator._house_load_profile = AsyncMock(return_value=[0.7] * 24)
    coordinator._sun_times = MagicMock(return_value=(SUNRISE, SUNSET))

    assert await _outlook(coordinator) is None


@pytest.mark.asyncio
async def test_an_unreadable_forecast_gives_no_opinion(mock_hass):
    coordinator = _make_coordinator(mock_hass, forecast_today="unavailable")
    assert await _outlook(coordinator) is None


# --- The reality check -----------------------------------------------------


def _peak_rows(entity, by_hour, days=(19, 20)):
    rows = []
    for day in days:
        for hour in range(24):
            start = datetime(2026, 6, day, hour, tzinfo=timezone.utc).timestamp()
            rows.append({"start": start, "end": start + 3600, "max": by_hour(hour)})
    return {entity: rows}


def _recorder(mock_hass, payload):
    instance = MagicMock()
    instance.async_add_executor_job = AsyncMock(return_value=payload)
    mock_hass.data[DATA_INSTANCE] = instance
    return instance


def _real_recorder(mock_hass):
    """One that actually runs the job, so the statistics call can be inspected."""
    instance = MagicMock()

    async def _run(job):
        return job()

    instance.async_add_executor_job = AsyncMock(side_effect=_run)
    mock_hass.data[DATA_INSTANCE] = instance
    return instance


@pytest.mark.asyncio
async def test_the_feed_in_peaks_are_read_with_max_not_change(mock_hass):
    """A power sensor is a ``measurement``: it has ``max`` and no ``change`` at all.

    The house load profile asks for ``change``, and asking for both in one call
    gets neither - so this has to be its own query, not another entity in the
    same set.
    """
    mock_hass.states.async_set(FEED_IN, "5000", {"unit_of_measurement": "W"})
    instance = _recorder(
        mock_hass, _peak_rows(FEED_IN, lambda h: 7200.0 if 11 <= h <= 15 else 500.0)
    )
    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_FEED_IN_ENTITY: FEED_IN})

    peaks = await coordinator._feed_in_peaks_by_hour()

    assert peaks is not None
    assert peaks[12] == 7200.0
    assert peaks[3] == 500.0
    instance.async_add_executor_job.assert_awaited_once()
    # Cached for fifteen minutes, then read again
    await coordinator._feed_in_peaks_by_hour()
    instance.async_add_executor_job.assert_awaited_once()
    coordinator._feed_in_peak_cache = (
        datetime.now(timezone.utc) - timedelta(minutes=16),
        peaks,
    )
    await coordinator._feed_in_peaks_by_hour()
    assert instance.async_add_executor_job.await_count == 2


@pytest.mark.asyncio
async def test_a_kilowatt_feed_in_sensor_is_scaled_to_watts(mock_hass):
    mock_hass.states.async_set(FEED_IN, "7.2", {"unit_of_measurement": "kW"})
    _recorder(mock_hass, _peak_rows(FEED_IN, lambda h: 7.2 if h == 12 else 0.5))
    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_FEED_IN_ENTITY: FEED_IN})

    peaks = await coordinator._feed_in_peaks_by_hour()

    assert peaks[12] == pytest.approx(7200.0)


@pytest.mark.asyncio
async def test_no_entity_no_recorder_and_no_rows_all_give_none(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    assert await coordinator._feed_in_peaks_by_hour() is None, "no entity configured"

    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_FEED_IN_ENTITY: FEED_IN})
    mock_hass.data.pop(DATA_INSTANCE, None)
    assert await coordinator._feed_in_peaks_by_hour() is None, "recorder not loaded"

    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_FEED_IN_ENTITY: FEED_IN})
    _recorder(mock_hass, {FEED_IN: []})
    assert await coordinator._feed_in_peaks_by_hour() is None, "no usable rows"


@pytest.mark.asyncio
async def test_a_failing_recorder_query_is_not_an_error(mock_hass):
    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_FEED_IN_ENTITY: FEED_IN})
    instance = MagicMock()
    instance.async_add_executor_job = AsyncMock(side_effect=Exception("database locked"))
    mock_hass.data[DATA_INSTANCE] = instance

    assert await coordinator._feed_in_peaks_by_hour() is None


# --- What the sensor shows -------------------------------------------------


@pytest.mark.asyncio
async def test_the_attributes_hold_the_model_next_to_the_measurement(mock_hass):
    """The number this whole release exists to produce."""
    mock_hass.states.async_set(FEED_IN, "5000", {"unit_of_measurement": "W"})
    _recorder(mock_hass, _peak_rows(FEED_IN, lambda h: 9000.0 if 11 <= h <= 15 else 500.0))
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_CURTAILMENT_FEED_IN_ENTITY: FEED_IN, CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: ABS_LIMIT},
    )
    coordinator.last_curtailment_outlook = await _outlook(coordinator)

    attrs = await coordinator.curtailment_attributes()

    assert attrs["limit_w"] == 7200.0
    assert attrs["control_entity_configured"] is True
    assert attrs["forecast_today_configured"] is True
    assert attrs["measured_peak_w"] == 9000
    assert attrs["measured_hours_at_the_cap"] == 5
    assert attrs["measured_peak_w_by_hour"]["12"] == 9000
    assert "3" in attrs["measured_peak_w_by_hour"], "a quiet hour is still a reading"
    # An upper envelope, not a day: each entry is the highest that hour ever
    # reached in 14 days, so no real day can beat it. A model above 100 % is
    # therefore claiming an overflow the inverter has never come close to.
    assert attrs["measured_overflow_kwh_envelope"] > 0
    assert 0 < attrs["model_vs_envelope_pct"] <= 100.0
    assert "model_vs_measured_pct" not in attrs, "the old name compared kW with kWh"
    assert attrs["clamped_by"] is None


@pytest.mark.asyncio
async def test_a_missing_control_entity_is_said_out_loud(mock_hass):
    """Stage two writes on that entity and nothing else can cap DC charging."""
    coordinator = _make_coordinator(mock_hass)
    coordinator.last_curtailment_outlook = await _outlook(coordinator)

    attrs = await coordinator.curtailment_attributes()

    assert attrs["control_entity_configured"] is False
    assert "measured_peak_w" not in attrs, "no feed-in entity, so nothing measured"


@pytest.mark.asyncio
async def test_without_an_outlook_only_the_configuration_is_reported(mock_hass):
    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_LIMIT_W: 0})

    attrs = await coordinator.curtailment_attributes()

    assert attrs == {
        "limit_w": None,
        "control_entity_configured": False,
        "forecast_today_configured": True,
    }


@pytest.mark.asyncio
async def test_the_sensor_reads_the_state_and_attributes_off_the_coordinator(mock_hass):
    from custom_components.inverter_charge_night.sensor import CurtailmentOutlookSensor

    coordinator = _make_coordinator(mock_hass)
    coordinator.last_curtailment_outlook = await _outlook(coordinator)
    coordinator.last_curtailment_attributes = await coordinator.curtailment_attributes()
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    sensor = CurtailmentOutlookSensor(coordinator, entry)

    assert sensor.native_value == pytest.approx(
        round(coordinator.last_curtailment_outlook.overflow_kwh, 2)
    )
    assert sensor.extra_state_attributes["limit_w"] == 7200.0
    assert sensor.entity_registry_enabled_default is False

    coordinator.last_curtailment_outlook = None
    assert sensor.native_value is None, "no cap configured is not the same as zero spill"


@pytest.mark.asyncio
async def test_the_statistics_call_asks_for_max_and_the_right_window(mock_hass):
    """Guards the one subtle thing: the statistic type.

    Reading rows back does not prove it - a mock returns whatever it was given
    whichever type was asked for. So this runs the real job and looks at the
    arguments. ``change`` here would return nothing at all for a power sensor,
    and the feature would silently never have a measurement to check against.
    """
    mock_hass.states.async_set(FEED_IN, "5000", {"unit_of_measurement": "W"})
    _real_recorder(mock_hass)
    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_FEED_IN_ENTITY: FEED_IN})

    with patch(
        "homeassistant.components.recorder.statistics.statistics_during_period",
        return_value={FEED_IN: []},
    ) as stats:
        await coordinator._feed_in_peaks_by_hour()

    args = stats.call_args.args
    assert args[3] == {FEED_IN}
    assert args[4] == "hour"
    assert args[6] == {"max"}, "a power sensor has no 'change' statistic at all"
    days = (datetime.now(timezone.utc) - args[1]).days
    assert days == 14


@pytest.mark.asyncio
async def test_broken_statistics_rows_are_skipped_not_believed(mock_hass):
    """A row with no timestamp or no maximum is not a peak of zero."""
    mock_hass.states.async_set(FEED_IN, "5000", {"unit_of_measurement": "W"})
    good = datetime(2026, 6, 20, 12, tzinfo=timezone.utc).timestamp()
    _recorder(
        mock_hass,
        {
            FEED_IN: [
                {"start": None, "max": 99999.0},
                {"start": good, "max": None},
                {"start": "noon", "max": 88888.0},
                {"start": good, "max": 7200.0},
            ]
        },
    )
    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_FEED_IN_ENTITY: FEED_IN})

    peaks = await coordinator._feed_in_peaks_by_hour()

    assert peaks is not None
    assert max(peaks) == 7200.0, "the bogus 99999 and 88888 must not have counted"


@pytest.mark.asyncio
async def test_a_broken_battery_capacity_gives_no_outlook_and_no_crash(mock_hass):
    """Both arithmetic paths refuse rather than throw at the caller."""
    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_CAPACITY: 0.0})

    assert await _outlook(coordinator) is None


@pytest.mark.asyncio
async def test_an_unusable_plan_input_still_leaves_the_user_minimum_as_the_floor(
    mock_hass, caplog
):
    """A missing evening reserve is a weaker floor, not a wrong one."""
    coordinator = _make_coordinator(mock_hass)
    coordinator._build_plan_input = AsyncMock(side_effect=ValueError("no window"))

    outlook = await _outlook(coordinator)

    assert outlook is not None, "the reserve is optional; the cap arithmetic is not"
    assert outlook.morning_target_soc >= 10.0


@pytest.mark.asyncio
async def test_the_documented_attribute_names_are_the_ones_emitted(mock_hass):
    """A rename that misses the docs leaves users reading a missing attribute.

    Exactly what happened to ``model_vs_measured_pct``: the emitted name moved
    and the README, the sensor's own docstring and the plan all still named the
    old one. Anybody building a template from those would have got nothing back
    and no error.
    """
    import re
    from pathlib import Path

    from custom_components.inverter_charge_night import sensor as sensor_module

    mock_hass.states.async_set(FEED_IN, "5000", {"unit_of_measurement": "W"})
    _recorder(mock_hass, _peak_rows(FEED_IN, lambda h: 9000.0 if 11 <= h <= 15 else 500.0))
    coordinator = _make_coordinator(mock_hass, {CONF_CURTAILMENT_FEED_IN_ENTITY: FEED_IN})
    coordinator.last_curtailment_outlook = await _outlook(coordinator)
    emitted = set(await coordinator.curtailment_attributes())

    # Only this sensor's own prose: the efficiency search has attributes of its
    # own whose names start the same way, and they are none of this test's
    # business.
    readme = Path(__file__).resolve().parents[1].joinpath("README.md").read_text()
    start = readme.index("### The feed-in cap")
    section = readme[start : readme.index("\n## ", start)]
    prose = (sensor_module.CurtailmentOutlookSensor.__doc__ or "") + section
    named = set(re.findall(r"`{1,2}(model_vs_\w+|measured_overflow\w*)`{1,2}", prose))
    assert named, "the guard is worthless if it matches nothing"
    assert named <= emitted, f"documented but not emitted: {sorted(named - emitted)}"
