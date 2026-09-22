"""The price read path at coordinator level (plan 012, stage 3).

The contract this module exists to hold: **every refusal produces exactly the
behaviour of an installation with no price entity at all.** A price that cannot
be trusted must never produce a worse decision than having no price.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_END_TIME,
    CONF_HIGH_PRICE_END,
    CONF_HIGH_PRICE_START,
    CONF_MIN_SOC_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_PRICE_SURCHARGE_CT,
    CONF_PRICE_SURCHARGE_WINDOW_CT,
    CONF_PRICE_UNIT,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    PRICE_UNIT_EUR_KWH,
)

COORDINATOR = "custom_components.inverter_charge_night.coordinator"
PRICE = "sensor.price"
NOW = datetime(2026, 9, 20, 14, 0)

CONFIG = {
    CONF_MIN_SOC_ENTITY: "number.min_soc",
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_PV_FORECAST_ENTITY: "sensor.pv",
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_START_TIME: "23:00",
    CONF_END_TIME: "05:00",
    CONF_HIGH_PRICE_START: "18:00",
    CONF_HIGH_PRICE_END: "21:00",
    CONF_PRICE_ENTITY: PRICE,
}


def _hourly(value=0.30, hours=36, unit="EUR/kWh"):
    """A price series starting at midnight, long enough to cover tonight."""
    midnight = NOW.replace(hour=0, minute=0)
    return (
        [
            {"start": (midnight + timedelta(hours=h)).isoformat(), "total": value}
            for h in range(hours)
        ],
        unit,
    )


def _make_coordinator(mock_hass, extra=None, attributes=None, unit="EUR/kWh", state="0.30"):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = CONFIG | (extra or {})
    entry.options = {}
    mock_hass.states.async_set("sensor.soc", "40", {"unit_of_measurement": "%"})
    if attributes is not None:
        mock_hass.states.async_set(PRICE, state, {"unit_of_measurement": unit} | attributes)
    coordinator = InverterChargeNightCoordinator(mock_hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def test_a_full_price_series_is_read_and_reported(mock_hass):
    items, unit = _hourly()
    coordinator = _make_coordinator(mock_hass, attributes={"raw_today": items}, unit=unit)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW):
        snapshot = coordinator.price_snapshot()

    assert snapshot["current_ct"] == pytest.approx(30.0)
    assert snapshot["source_attribute"] == "raw_today"
    assert snapshot["unit_resolved"] == PRICE_UNIT_EUR_KWH
    assert snapshot["reason"] is None
    assert snapshot["evening_ct"] == pytest.approx(30.0)


def test_the_window_gets_its_own_grid_fee(mock_hass):
    """The reduced tariff applies to those hours and nowhere else."""
    items, unit = _hourly(value=0.06)
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_PRICE_SURCHARGE_CT: 24.0, CONF_PRICE_SURCHARGE_WINDOW_CT: 16.0},
        attributes={"raw_today": items},
        unit=unit,
    )

    with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW):
        snapshot = coordinator.price_snapshot()

    assert snapshot["evening_ct"] == pytest.approx(30.0)   # 6 + 24
    assert snapshot["window_ct"] == pytest.approx(22.0)    # 6 + 16


def test_without_a_window_surcharge_the_day_one_applies_throughout(mock_hass):
    items, unit = _hourly(value=0.06)
    coordinator = _make_coordinator(
        mock_hass, {CONF_PRICE_SURCHARGE_CT: 24.0}, attributes={"raw_today": items}, unit=unit
    )

    with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW):
        snapshot = coordinator.price_snapshot()

    assert snapshot["window_ct"] == pytest.approx(30.0)


@pytest.mark.parametrize(
    "case,extra,attributes,unit,state",
    [
        ("no entity configured", {CONF_PRICE_ENTITY: ""}, {"raw_today": _hourly()[0]}, "EUR/kWh", "0.30"),
        ("entity unavailable", {}, {"raw_today": _hourly()[0]}, "EUR/kWh", "unavailable"),
        ("nothing series shaped", {}, {"friendly_name": "Price"}, "EUR/kWh", "0.30"),
        ("no unit to go on", {}, {"raw_today": _hourly()[0]}, "", "0.30"),
        ("exchange price, no surcharge", {}, {"raw_today": _hourly(value=0.06)[0]}, "EUR/kWh", "0.06"),
        ("cents the size of euros", {}, {"raw_today": _hourly(value=0.30)[0]}, "ct/kWh", "0.30"),
        ("a series that is over", {}, {"raw_today": [
            {"start": (NOW - timedelta(days=2) + timedelta(hours=h)).isoformat(), "total": 0.30}
            for h in range(24)]}, "EUR/kWh", "0.30"),
    ],
)
def test_every_refusal_falls_back_to_no_price_at_all(mock_hass, case, extra, attributes, unit, state):
    """The contract, one line per way it can go wrong."""
    coordinator = _make_coordinator(mock_hass, extra, attributes=attributes, unit=unit, state=state)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW):
        snapshot = coordinator.price_snapshot()

    assert snapshot["current_ct"] is None, case
    assert snapshot["window_ct"] is None, case
    assert snapshot["evening_ct"] is None, case


def test_the_reason_is_logged_once_not_every_poll(mock_hass, caplog):
    coordinator = _make_coordinator(
        mock_hass, attributes={"raw_today": _hourly(value=0.06)[0]}, unit="EUR/kWh"
    )

    with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW):
        coordinator.price_snapshot()
        first = caplog.text.count("Cannot use the price entity")
        coordinator.price_snapshot()
        assert caplog.text.count("Cannot use the price entity") == first == 1


def test_the_attributes_seen_are_named_in_the_warning(mock_hass, caplog):
    """So a bug report says what the entity actually offered."""
    coordinator = _make_coordinator(
        mock_hass, attributes={"something_else": [1, 2, 3, 4]}, unit="EUR/kWh"
    )

    with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW):
        coordinator.price_snapshot()

    assert "something_else" in caplog.text


def test_a_recovered_entity_is_reported_too(mock_hass, caplog):
    caplog.set_level(logging.INFO)
    coordinator = _make_coordinator(
        mock_hass, attributes={"raw_today": _hourly(value=0.06)[0]}, unit="EUR/kWh"
    )
    with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW):
        coordinator.price_snapshot()
        mock_hass.states.async_set(
            PRICE, "0.30", {"unit_of_measurement": "EUR/kWh", "raw_today": _hourly()[0]}
        )
        coordinator.price_snapshot()

    assert "usable again" in caplog.text


def test_the_sensor_shows_what_was_matched(mock_hass):
    from custom_components.inverter_charge_night.sensor import PriceSignalSensor

    items, unit = _hourly()
    coordinator = _make_coordinator(mock_hass, attributes={"raw_today": items}, unit=unit)
    sensor = PriceSignalSensor(coordinator, coordinator.entry)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW):
        assert sensor.native_value == pytest.approx(30.0)
        attributes = sensor.extra_state_attributes

    assert attributes["source_attribute"] == "raw_today"
    assert attributes["value_key"] == "total"
    assert "current_ct" not in attributes


def test_the_sensor_is_empty_without_a_price_entity(mock_hass):
    from custom_components.inverter_charge_night.sensor import PriceSignalSensor

    coordinator = _make_coordinator(mock_hass, {CONF_PRICE_ENTITY: ""})
    sensor = PriceSignalSensor(coordinator, coordinator.entry)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW):
        assert sensor.native_value is None
        assert sensor.extra_state_attributes["intervals"] == 0


@pytest.mark.asyncio
async def test_a_cheap_evening_reaches_the_plan(mock_hass):
    """End to end: the price entity decides whether the reserve is held.

    The gate is a pure function with its own tests; this is the wire between
    it and the entity, which is the part that can be connected wrongly.
    """
    # The series has to start before the window does: at 02:00 the running
    # 23:00 to 05:00 window began *yesterday*, and pricing it is the whole
    # point. Starting at today's midnight only ever worked because the
    # coordinator was asking about the wrong night.
    midnight = NOW.replace(hour=0, minute=0) - timedelta(days=1)
    dear_evening = [
        {
            "start": (midnight + timedelta(hours=h)).isoformat(),
            "total": 0.45 if 18 <= (h % 24) < 21 else 0.22,
        }
        for h in range(60)
    ]
    cheap_evening = [
        {
            "start": (midnight + timedelta(hours=h)).isoformat(),
            "total": 0.18 if 18 <= (h % 24) < 21 else 0.40,
        }
        for h in range(60)
    ]

    async def _plan(items):
        coordinator = _make_coordinator(mock_hass, attributes={"raw_today": items})
        coordinator._house_load_profile = AsyncMock(return_value=[0.5] * 24)
        coordinator._sun_times = MagicMock(
            return_value=(NOW.replace(hour=7), NOW.replace(hour=19))
        )
        with patch(f"{COORDINATOR}.dt_util.now", return_value=NOW.replace(hour=2)):
            plan_input, _ = await coordinator._build_plan_input(0.0, True)
        return plan_input

    dear = await _plan(dear_evening)
    assert dear.evening_price_ct == pytest.approx(45.0)
    assert dear.window_price_ct == pytest.approx(22.0)

    cheap = await _plan(cheap_evening)
    assert cheap.evening_price_ct == pytest.approx(18.0)
    assert cheap.window_price_ct == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_the_running_window_is_priced_not_the_next_one(mock_hass):
    """At 02:00 inside a 23:00 to 05:00 window, the window began yesterday.

    ``_window_start_datetime`` answers "when does the next one begin", so it
    returned *tonight's* 23:00 and priced a night that has not happened yet.
    With real day-ahead data that stretch is usually unpublished, so
    ``mean_price_ct`` refused it and the reserve was held whatever the prices
    said - the gate looked implemented and decided nothing.

    The bounds now come from the window's end, which is also the ``window_end``
    the planner is handed in the same ``PlanInput``.
    """
    midnight = NOW.replace(hour=0, minute=0)
    yesterday = midnight - timedelta(days=1)
    # Yesterday's night hours are cheap, tonight's are dear. Only one of them
    # is the window we are standing in.
    items = [
        {
            "start": (yesterday + timedelta(hours=h)).isoformat(),
            "total": 0.10 if h < 29 else 0.50,
        }
        for h in range(60)
    ]
    coordinator = _make_coordinator(mock_hass, attributes={"raw_today": items})
    at_two = NOW.replace(hour=2)

    start, end = coordinator._priced_window_bounds(at_two)
    assert start < at_two < end, "the window we are standing in"
    assert start.day == yesterday.day

    with patch(f"{COORDINATOR}.dt_util.now", return_value=at_two):
        series = coordinator._price_series()
        price = coordinator._window_price_ct(series, at_two)

    assert price is not None, "the running window is covered by the series"
    assert price < 20.0, "the cheap night we are in, not the dear one to come"
