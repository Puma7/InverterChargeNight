"""The efficiency finder: what it measures, and what it refuses to measure.

The finder looks for the charge power that loses the least energy between the
grid and the battery. Its whole value rests on one property: every recorded
sample must really have been measured at the power it is filed under. These
tests are mostly about the ways that can fail - a sample taken during the ramp,
one taken while the battery was full and the inverter charged at a third of the
setpoint, one where the two sensors measure the same side of the charger.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    AUTO_TEST_MAX_ATTEMPTS,
    AUTO_TEST_SETTLE_S,
    AUTO_TEST_STATE_FINISHED,
    AUTO_TEST_STATE_IDLE,
    AUTO_TEST_STATE_MEASURING,
    AUTO_TEST_STATE_SETTLING,
    AUTO_TEST_STATE_WAITING,
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_CHARGE_ENERGY_RECEIVED_ENTITY,
    CONF_CHARGE_ENERGY_SENT_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
)

SENT = "sensor.charge_power_in"
RECEIVED = "sensor.charge_power_into_battery"
METER_SENT = "sensor.charge_energy_in"
METER_RECEIVED = "sensor.charge_energy_into_battery"
AC_LIMIT = "number.ac_limit"

START = datetime(2026, 1, 15, 0, 0)
NOW = "custom_components.inverter_charge_night.dt_util.now"

CONFIG = {
    CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
    CONF_CHARGE_POWER_SENT_ENTITY: SENT,
    CONF_CHARGE_POWER_RECEIVED_ENTITY: RECEIVED,
    CONF_MIN_CHARGE_POWER_W: 1000,
    CONF_MAX_CHARGE_POWER_W: 20000,
}


def _make(hass, config=None) -> InverterChargeNightCoordinator:
    """A coordinator whose efficiency data really round-trips through options."""
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = config or CONFIG
    entry.options = {}

    def _update(config_entry, options=None, data=None, **kwargs):
        if options is not None:
            entry.options = options
        if data is not None:
            entry.data = data

    hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = MagicMock()
    return coordinator


def _power(hass, sent_w, received_w):
    hass.states.async_set(SENT, str(sent_w), {"unit_of_measurement": "W"})
    hass.states.async_set(RECEIVED, str(received_w), {"unit_of_measurement": "W"})


def _meters(hass, sent_kwh, received_kwh):
    hass.states.async_set(METER_SENT, str(sent_kwh), {"unit_of_measurement": "kWh"})
    hass.states.async_set(METER_RECEIVED, str(received_kwh), {"unit_of_measurement": "kWh"})


@pytest_asyncio.fixture
async def finder(mock_hass):
    hass = mock_hass
    hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    _power(hass, 0, 0)
    coordinator = _make(hass)
    yield coordinator
    coordinator._remove_auto_sample_listener()


def _measure(coordinator, hass, *, power_w, samples, minutes, settle=True):
    """Run a measurement: start the test, wait out the settling, feed samples."""
    now = START
    with patch(NOW, return_value=now):
        coordinator._auto_test_active = True
        coordinator._auto_test_power_w = power_w
        coordinator._auto_test_start = now
        coordinator._auto_measure_start = None
        coordinator._auto_last_sample_time = None
        coordinator._auto_last_sent_w = None
        coordinator._auto_last_received_w = None
        coordinator._auto_energy_sent_wh = 0.0
        coordinator._auto_energy_received_wh = 0.0
    if settle:
        now = START + timedelta(seconds=AUTO_TEST_SETTLE_S + 1)
        with patch(NOW, return_value=now):
            coordinator._accumulate_auto_energy()  # begins the measurement
    step = timedelta(minutes=minutes / max(1, len(samples)))
    for sent_w, received_w in samples:
        now = now + step
        _power(hass, sent_w, received_w)
        with patch(NOW, return_value=now):
            coordinator._accumulate_auto_energy()
    return now


# --- 1. What counts as a measurement ----------------------------------------


def test_the_ramp_to_the_setpoint_is_not_part_of_the_measurement(mock_hass, finder):
    """Whatever the inverter does in the first two minutes belongs to no power."""
    with patch(NOW, return_value=START):
        finder._auto_test_active = True
        finder._auto_test_power_w = 10000
        finder._auto_test_start = START

    _power(mock_hass, 2000, 1800)
    with patch(NOW, return_value=START + timedelta(seconds=60)):
        finder._accumulate_auto_energy()

    assert finder._auto_measure_start is None
    assert finder._auto_energy_sent_wh == 0.0

    with patch(NOW, return_value=START + timedelta(seconds=AUTO_TEST_SETTLE_S + 1)):
        finder._accumulate_auto_energy()

    assert finder._auto_measure_start is not None
    assert finder._auto_energy_sent_wh == 0.0  # counting starts here, at zero


def test_the_power_is_integrated_trapezoidally(mock_hass, finder):
    """A rectangle valued at the end of a fifteen-minute poll is not a measurement."""
    _measure(
        finder,
        mock_hass,
        power_w=10000,
        samples=[(10000, 9000), (0, 0)],
        minutes=120,
    )

    # One hour at 10 000 -> 10 000 W, then an hour ramping down to 0 -> 5 000 Wh
    assert finder._auto_energy_sent_wh == pytest.approx(15000.0)
    assert finder._auto_energy_received_wh == pytest.approx(13500.0)


def test_a_valid_measurement_is_recorded(mock_hass, finder):
    end = _measure(
        finder,
        mock_hass,
        power_w=10000,
        samples=[(10000, 9000), (10000, 9000)],
        minutes=60,
    )
    with patch(NOW, return_value=end):
        finder._finalize_auto_test()

    data = finder.get_auto_efficiency_data()
    assert data["history"]["10000"] == pytest.approx(0.1)
    assert data["best_power_w"] == 10000
    assert data["best_loss"] == pytest.approx(0.1)
    assert finder._auto_test_active is False


# --- 2. Samples that must not be recorded ------------------------------------


def test_a_loss_below_zero_is_refused(mock_hass, finder, caplog):
    """More energy in the battery than went in means the sensors are wrong.

    This one mattered: the loss used to be clamped at zero, so a pair of
    sensors measuring the same side of the charger produced a perfect score
    and won the search for good.
    """
    caplog.set_level(logging.INFO)
    end = _measure(
        finder,
        mock_hass,
        power_w=10000,
        samples=[(10000, 10500), (10000, 10500)],
        minutes=60,
    )
    with patch(NOW, return_value=end):
        finder._finalize_auto_test()

    assert finder.get_auto_efficiency_data()["history"] == {}
    assert "not physical" in caplog.text


def test_an_implausibly_large_loss_is_refused(mock_hass, finder):
    """Above 50 % the wiring is wrong, not the inverter."""
    end = _measure(
        finder,
        mock_hass,
        power_w=10000,
        samples=[(10000, 2000), (10000, 2000)],
        minutes=60,
    )
    with patch(NOW, return_value=end):
        finder._finalize_auto_test()

    assert finder.get_auto_efficiency_data()["history"] == {}


def test_a_setpoint_the_inverter_did_not_follow_is_refused(mock_hass, finder, caplog):
    """A full battery tapers the charge; the sample would be filed under a lie."""
    caplog.set_level(logging.INFO)
    end = _measure(
        finder,
        mock_hass,
        power_w=10000,
        samples=[(3000, 2700), (3000, 2700)],
        minutes=60,
    )
    with patch(NOW, return_value=end):
        finder._finalize_auto_test()

    assert finder.get_auto_efficiency_data()["history"] == {}
    assert "not the 10000 W it was set to" in caplog.text


def test_a_measurement_that_is_too_short_is_refused(mock_hass, finder):
    end = _measure(
        finder,
        mock_hass,
        power_w=10000,
        samples=[(10000, 9000)],
        minutes=2,
    )
    with patch(NOW, return_value=end):
        finder._finalize_auto_test()

    assert finder.get_auto_efficiency_data()["history"] == {}


def test_a_test_that_never_settled_is_refused(mock_hass, finder):
    with patch(NOW, return_value=START):
        finder._auto_test_active = True
        finder._auto_test_power_w = 10000
        finder._auto_test_start = START
    with patch(NOW, return_value=START + timedelta(seconds=30)):
        finder._finalize_auto_test()

    assert finder.get_auto_efficiency_data()["history"] == {}
    assert finder._auto_test_active is False


# --- 3. A power that cannot be measured at all -------------------------------


def test_a_power_that_never_completes_lowers_the_ceiling(mock_hass, finder, caplog):
    """20 kW into a small battery is full before the measurement is over.

    Without this the search would ask for the same impossible sample every
    night and never finish.
    """
    caplog.set_level(logging.INFO)
    for _ in range(AUTO_TEST_MAX_ATTEMPTS):
        end = _measure(finder, mock_hass, power_w=15000, samples=[(15000, 13500)], minutes=2)
        with patch(NOW, return_value=end):
            finder._finalize_auto_test()

    data = finder.get_auto_efficiency_data()
    assert data["failed"]["15000"] == AUTO_TEST_MAX_ATTEMPTS
    assert data["range_max_w"] == 14900
    assert "the battery is full before a measurement completes" in caplog.text


def test_the_search_skips_a_power_it_cannot_measure(mock_hass, finder):
    options = dict(finder.entry.options)
    options[CONF_AUTO_EFFICIENCY_DATA] = {
        "history": {},
        "failed": {str(c): AUTO_TEST_MAX_ATTEMPTS for c in (8300, 12700)},
    }
    finder.entry.options = options

    candidate = finder._select_next_auto_test_power_w()

    assert candidate not in (8300, 12700)


def test_an_unmeasurable_power_narrows_the_interval_instead_of_ending_the_search(
    mock_hass, finder
):
    """Ending there would keep a far-from-optimal best for good.

    Both golden-section points being unmeasurable used to mean "done", with
    large unexplored stretches left in the interval and whatever had been
    measured so far written to the inverter as the optimum.
    """
    options = dict(finder.entry.options)
    options[CONF_AUTO_EFFICIENCY_DATA] = {
        "history": {},
        "failed": {str(c): AUTO_TEST_MAX_ATTEMPTS for c in (8300, 12700)},
    }
    finder.entry.options = options

    candidate = finder._select_next_auto_test_power_w()

    assert candidate is not None, "the search must carry on"
    assert candidate not in (8300, 12700)
    # ... and the interval really moved, so the next round asks something new
    narrowed = finder.get_auto_efficiency_data()
    assert narrowed["range_min_w"] > 1000 or narrowed["range_max_w"] < 20000


def test_the_search_ends_only_when_the_interval_is_exhausted(mock_hass, finder):
    options = dict(finder.entry.options)
    options[CONF_AUTO_EFFICIENCY_DATA] = {
        "history": {},
        "range_min_w": 9000,
        "range_max_w": 9050,
    }
    finder.entry.options = options

    assert finder._select_next_auto_test_power_w() is None


def test_many_measured_points_do_not_end_the_search_early(mock_hass, finder):
    """The old five-round cap could report "done" with the interval still open."""
    options = dict(finder.entry.options)
    options[CONF_AUTO_EFFICIENCY_DATA] = {
        # A dense history: every round finds both points measured and narrows
        "history": {str(p): 0.1 + (p - 1000) / 200000 for p in range(1000, 20001, 100)},
    }
    finder.entry.options = options

    candidate = finder._select_next_auto_test_power_w()
    data = finder.get_auto_efficiency_data()

    assert candidate is None
    # Narrowed until the two golden-section points sit on the interval bounds
    assert data["range_max_w"] - data["range_min_w"] <= 200


def test_an_impossible_power_range_is_reported_once(mock_hass, caplog):
    caplog.set_level(logging.WARNING)
    _power(mock_hass, 0, 0)
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    coordinator = _make(
        mock_hass, {**CONFIG, CONF_MIN_CHARGE_POWER_W: 9000, CONF_MAX_CHARGE_POWER_W: 9000}
    )

    assert coordinator._select_next_auto_test_power_w() is None
    assert "nothing to search" in caplog.text
    caplog.clear()
    assert coordinator._select_next_auto_test_power_w() is None
    assert caplog.text == ""


# --- 4. Energy meters --------------------------------------------------------


@pytest_asyncio.fixture
async def metered(mock_hass):
    hass = mock_hass
    hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    _power(hass, 0, 0)
    _meters(hass, 100.0, 90.0)
    coordinator = _make(
        hass,
        {
            **CONFIG,
            CONF_CHARGE_ENERGY_SENT_ENTITY: METER_SENT,
            CONF_CHARGE_ENERGY_RECEIVED_ENTITY: METER_RECEIVED,
        },
    )
    yield coordinator
    coordinator._remove_auto_sample_listener()


def test_meters_are_used_instead_of_the_integrated_power(mock_hass, metered):
    """The difference of two readings is what actually flowed."""
    end = _measure(metered, mock_hass, power_w=10000, samples=[(9000, 8000)], minutes=60)
    # 10 kWh drawn, 9 kWh stored, whatever the power sensor said in between
    _meters(mock_hass, 110.0, 99.0)
    with patch(NOW, return_value=end):
        metered._finalize_auto_test()

    data = metered.get_auto_efficiency_data()
    assert data["history"]["10000"] == pytest.approx(0.1)
    assert metered._auto_last_result["source"] == "meters"


def test_a_meter_that_went_backwards_is_refused(mock_hass, metered):
    end = _measure(metered, mock_hass, power_w=10000, samples=[(10000, 9000)], minutes=60)
    _meters(mock_hass, 5.0, 4.0)  # the meter was reset during the measurement
    with patch(NOW, return_value=end):
        metered._finalize_auto_test()

    assert metered.get_auto_efficiency_data()["history"] == {}


def test_the_wizard_asks_for_the_second_meter(mock_hass):
    from unittest.mock import MagicMock as _MagicMock

    from homeassistant.helpers import entity_registry as er

    from custom_components.inverter_charge_night import config_flow

    base = {
        "start_time": "00:00",
        "end_time": "05:59",
        "user_min_soc": 8.0,
        "user_max_soc": 100.0,
        "battery_capacity": 10.0,
        "default_min_soc": 8.0,
        "min_charge_power_w": 500,
        "max_charge_power_w": 10000,
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: _MagicMock())
        errors = config_flow._validate_user_input(
            {**base, CONF_CHARGE_ENERGY_SENT_ENTITY: METER_SENT}, mock_hass
        )
    assert errors == {CONF_CHARGE_ENERGY_RECEIVED_ENTITY: "required_entity"}


# --- 5. What the user can see ------------------------------------------------


def test_the_sensor_follows_the_search(mock_hass, finder):
    assert finder.auto_test_state == AUTO_TEST_STATE_IDLE

    finder.auto_efficient_charge = True
    finder.is_active = True
    assert finder.auto_test_state == AUTO_TEST_STATE_WAITING

    with patch(NOW, return_value=START):
        finder._auto_test_active = True
        finder._auto_test_power_w = 9000
        finder._auto_test_start = START
    assert finder.auto_test_state == AUTO_TEST_STATE_SETTLING

    finder._auto_measure_start = START
    assert finder.auto_test_state == AUTO_TEST_STATE_MEASURING

    finder._reset_auto_test_state()
    finder.auto_efficient_charge = False
    options = dict(finder.entry.options)
    options[CONF_AUTO_EFFICIENCY_DATA] = {"history": {}, "best_power_w": 9000}
    finder.entry.options = options
    assert finder.auto_test_state == AUTO_TEST_STATE_FINISHED


def test_the_attributes_show_the_measurements(mock_hass, finder):
    end = _measure(finder, mock_hass, power_w=10000, samples=[(10000, 9000)] * 2, minutes=60)
    with patch(NOW, return_value=end):
        finder._finalize_auto_test()

    attributes = finder.auto_test_attributes()
    assert attributes["best_power_w"] == 10000
    assert attributes["best_loss_pct"] == pytest.approx(10.0)
    assert attributes["loss_by_power_pct"] == {"10000": pytest.approx(10.0)}
    assert attributes["last_result"]["minutes"] == pytest.approx(60.0, abs=1.0)
    assert attributes["measurement_source"] == "power sensors"


# --- 6. Housekeeping ---------------------------------------------------------


def test_changing_the_power_range_starts_the_search_over(mock_hass, finder):
    end = _measure(finder, mock_hass, power_w=10000, samples=[(10000, 9000)] * 2, minutes=60)
    with patch(NOW, return_value=end):
        finder._finalize_auto_test()
    assert finder.get_auto_efficiency_data()["history"]

    # The user raises the maximum charge power in the options
    finder.entry.data = {**CONFIG, CONF_MAX_CHARGE_POWER_W: 15000}
    finder.config = finder.entry.data

    assert finder.get_auto_efficiency_data()["history"] == {}


@pytest.mark.asyncio
async def test_the_sampling_listener_is_registered_and_removed(mock_hass, finder):
    with patch("custom_components.inverter_charge_night.async_track_state_change_event") as track:
        await finder._start_auto_test(9000)

    assert track.call_args.args[1] == [SENT, RECEIVED]
    assert finder._auto_sample_listener is not None

    finder._reset_auto_test_state()
    assert finder._auto_sample_listener is None


# --- 7. Several measurements in one window -----------------------------------


@pytest.mark.asyncio
async def test_a_finished_measurement_frees_the_window_for_the_next(mock_hass, finder):
    """One sample per night would need a week and a half to find the optimum.

    As soon as a measurement has its time and its energy, it is recorded and
    the next candidate starts - so a six-hour window can carry the whole
    search.
    """
    mock_hass.states.async_set("switch.grid", "on")
    finder.entry.data = {
        **CONFIG,
        "kostal_grid_charge_switch": "switch.grid",
    }
    finder.config = finder.entry.data
    finder.auto_efficient_charge = True
    finder.target_reached = False

    end = _measure(finder, mock_hass, power_w=10000, samples=[(10000, 9000)] * 2, minutes=60)
    started: list[int] = []

    async def _record(power_w):
        started.append(power_w)

    finder._start_auto_test = _record
    with patch(NOW, return_value=end):
        await finder._handle_auto_charge()

    assert finder.get_auto_efficiency_data()["history"]["10000"] == pytest.approx(0.1)
    assert started and started[0] != 10000


@pytest.mark.asyncio
async def test_an_unfinished_measurement_keeps_running(mock_hass, finder):
    mock_hass.states.async_set("switch.grid", "on")
    finder.entry.data = {**CONFIG, "kostal_grid_charge_switch": "switch.grid"}
    finder.config = finder.entry.data
    finder.auto_efficient_charge = True
    finder.target_reached = False

    end = _measure(finder, mock_hass, power_w=10000, samples=[(10000, 9000)], minutes=1)
    finder._start_auto_test = MagicMock()

    with patch(NOW, return_value=end):
        await finder._handle_auto_charge()

    assert finder._auto_test_active is True
    assert finder.get_auto_efficiency_data()["history"] == {}
    assert not finder._start_auto_test.called
