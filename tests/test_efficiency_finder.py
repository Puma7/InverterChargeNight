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

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
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
NOW = "custom_components.inverter_charge_night.coordinator.dt_util.now"


@pytest.mark.parametrize("start", [(2026, 3, 29, 1, 59), (2026, 10, 25, 2, 59)])
def test_efficiency_energy_and_minimum_duration_use_real_elapsed_time(finder, mock_hass, start):
    from datetime import UTC
    from zoneinfo import ZoneInfo
    begin = datetime(*start, tzinfo=ZoneInfo("Europe/Berlin"))
    end = (begin.astimezone(UTC) + timedelta(seconds=300)).astimezone(begin.tzinfo)
    finder._auto_test_active = True
    finder._auto_test_start = begin
    finder._auto_test_power_w = 5000
    finder._auto_test_limit_confirmed = True
    mock_hass.states.async_set(AC_LIMIT, "5000", {"unit_of_measurement": "W"})
    finder._auto_measure_start = begin
    finder._auto_last_sample_time = begin
    _power(mock_hass, 5000, 4500)
    for seconds in range(30, 301, 30):
        instant = (begin.astimezone(UTC) + timedelta(seconds=seconds)).astimezone(begin.tzinfo)
        with patch.object(finder, "_efficiency_now", return_value=instant):
            finder._accumulate_auto_energy()
    with patch.object(finder, "_efficiency_now", return_value=end):
        assert finder._auto_energy_sent_wh == pytest.approx(5000 * 300 / 3600)
        assert finder._auto_measurement_is_complete() is True

CONFIG = {
    CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
    CONF_CHARGE_POWER_SENT_ENTITY: SENT,
    CONF_CHARGE_POWER_RECEIVED_ENTITY: RECEIVED,
    CONF_MIN_CHARGE_POWER_W: 1000,
    CONF_MAX_CHARGE_POWER_W: 20000,
}


def _make(hass, config=None) -> InverterChargeNightCoordinator:
    """A coordinator whose efficiency data really round-trips through options."""
    hass.states.async_set("sun.sun", "below_horizon")
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
    hass.states.async_set("sun.sun", "below_horizon")
    hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    _power(hass, 0, 0)
    coordinator = _make(hass)
    yield coordinator
    coordinator._remove_auto_sample_listener()


def _measure(coordinator, hass, *, power_w, samples, minutes, settle=True):
    """Run a measurement: start the test, wait out the settling, feed samples."""
    hass.states.async_set(AC_LIMIT, str(power_w), {"unit_of_measurement": "W"})
    coordinator._auto_test_limit_confirmed = False
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
    _power(hass, *samples[0])
    if settle:
        now = START + timedelta(seconds=AUTO_TEST_SETTLE_S + 1)
        with patch(NOW, return_value=now):
            coordinator._accumulate_auto_energy()  # begins the measurement
    import math
    segment_seconds = minutes * 60 / max(1, len(samples))
    count = max(1, math.ceil(segment_seconds / 30))
    previous = samples[0]
    for sent_w, received_w in samples:
        for index in range(1, count + 1):
            ratio = index / count
            now += timedelta(seconds=segment_seconds / count)
            _power(hass, previous[0] + (sent_w - previous[0]) * ratio,
                   previous[1] + (received_w - previous[1]) * ratio)
            with patch(NOW, return_value=now):
                coordinator._accumulate_auto_energy()
        previous = (sent_w, received_w)
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

    mock_hass.states.async_set(AC_LIMIT, "10000", {"unit_of_measurement": "W"})
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
    mock_hass.states.async_set(AC_LIMIT, "10000", {"unit_of_measurement": "W"})
    with patch(NOW, return_value=START):
        finder._auto_test_active = True
        finder._auto_test_power_w = 10000
        finder._auto_test_limit_confirmed = True
        finder._auto_test_start = START
    with patch(NOW, return_value=START + timedelta(seconds=30)):
        finder._finalize_auto_test()

    assert finder.get_auto_efficiency_data()["history"] == {}
    assert finder._auto_test_active is False


# --- 3. A power that cannot be measured at all -------------------------------


def test_short_measurements_never_lower_the_search_ceiling(mock_hass, finder, caplog):
    """20 kW into a small battery is full before the measurement is over.

    Without this the search would ask for the same impossible sample every
    night and never finish.
    """
    caplog.set_level(logging.INFO)
    for _ in range(AUTO_TEST_MAX_ATTEMPTS):
        end = _measure(finder, mock_hass, power_w=15000, samples=[(15000, 13500)], minutes=2)
        with patch(NOW, return_value=end):
            finder._finalize_auto_test()

    data = finder.entry.options[CONF_AUTO_EFFICIENCY_DATA]
    assert data["failed"]["15000"] == AUTO_TEST_MAX_ATTEMPTS
    assert "range_max_w" not in data
    assert "only" in caplog.text


def _store(finder, history=None, uncertainty=None, failed=None):
    options = dict(finder.entry.options)
    data = {"history": {str(p): loss for p, loss in (history or {}).items()}}
    if uncertainty:
        data["uncertainty"] = {str(p): e for p, e in uncertainty.items()}
    if failed:
        data["failed"] = {str(p): n for p, n in failed.items()}
        data["failed_window"] = finder._efficiency_window_key()
    options[CONF_AUTO_EFFICIENCY_DATA] = data
    finder.entry.options = options


COARSE = [1000, 3000, 5000, 7000, 9000, 11000, 13000, 15000, 17000, 19000, 20000]


def _curve(power_w: int) -> float:
    """A loss curve with its optimum at 6300 W: high at both ends, flat near it."""
    return 0.06 + ((power_w - 6300) / 10000) ** 2


def test_the_coarse_pass_stops_once_the_loss_has_risen_twice(finder):
    """Pascal: 1, 2, 3 kW and so on - 80, 85, 90, 92, 91, 90 % - and then left and right
    of the best. Here 2 kW apart, from the bottom of the range."""
    asked = []
    while len(asked) < 6:
        asked.append(finder._select_next_auto_test_power_w())
        _store(finder, {p: _curve(p) for p in asked})
    assert asked == [1000, 3000, 5000, 7000, 9000, 11000]
    # 9000 and 11000 lost more than 7000: refining between 5000 and 9000 now
    assert finder._select_next_auto_test_power_w() == 6000


def test_a_curve_that_keeps_falling_is_scanned_to_the_top(finder):
    asked = []
    while (power := finder._select_next_auto_test_power_w()) in COARSE and len(asked) < 20:
        asked.append(power)
        _store(finder, {p: 0.2 - p / 200000 for p in asked})
    assert asked == COARSE


def test_the_gap_next_to_the_best_power_is_halved(finder):
    _store(finder, {p: _curve(p) for p in COARSE})
    assert finder._select_next_auto_test_power_w() == 6000  # between 5000 and 7000
    _store(finder, {p: _curve(p) for p in [*COARSE, 6000]})
    assert finder._select_next_auto_test_power_w() == 5500  # 6000 is the best now


def test_the_search_finds_the_optimum_in_few_trials(finder):
    asked = []
    while (power := finder._select_next_auto_test_power_w()) is not None:
        asked.append(power)
        _store(finder, {p: _curve(p) for p in asked})
        assert len(asked) < 40
    best = min(asked, key=_curve)
    assert abs(best - 6300) <= 250
    assert len(asked) <= 12  # six coarse, then the gaps around the best
    assert finder._auto_search_deferred is False


def test_a_flat_optimum_ends_the_search_early(finder):
    """Neighbours within the measured uncertainty: a finer step cannot tell them apart."""
    history = {p: _curve(p) for p in [*COARSE, 6000, 8000]}
    history[6000] = history[7000] + 0.0005
    history[8000] = history[7000] + 0.0008
    _store(finder, history, uncertainty={p: 0.001 for p in history})

    assert finder._select_next_auto_test_power_w() is None
    assert finder._auto_search_deferred is False


def test_without_an_uncertainty_the_search_refines_on(finder):
    history = {p: _curve(p) for p in [*COARSE, 6000, 8000]}
    history[6000] = history[7000] + 0.0005
    history[8000] = history[7000] + 0.0008
    _store(finder, history)

    assert finder._select_next_auto_test_power_w() == 6500


def test_an_unmeasurable_coarse_power_is_skipped(finder):
    _store(finder, {1000: 0.1}, failed={3000: AUTO_TEST_MAX_ATTEMPTS})
    assert finder._select_next_auto_test_power_w() == 5000


def test_an_unmeasurable_neighbour_of_the_best_keeps_the_search_open(finder):
    """It might be the better one: a failure proves nothing about its loss."""
    history = {p: _curve(p) for p in COARSE}
    failed = {6000: AUTO_TEST_MAX_ATTEMPTS}
    _store(finder, history, failed=failed)
    assert finder._select_next_auto_test_power_w() == 8000
    asked = [8000]
    while (power := finder._select_next_auto_test_power_w()) is not None:
        asked.append(power)
        _store(finder, {**history, **{p: _curve(p) for p in asked}}, failed=failed)
        assert len(asked) < 20
    assert finder._auto_search_deferred is True


def test_a_loss_that_falls_again_does_not_end_the_coarse_pass(finder):
    """Codex on #39: 10, 20, then 15 % - the second point worse than the best is
    no second rise, the curve has turned down again. Rises count against the
    point before, as Pascal described it."""
    _store(finder, {1000: 0.10, 3000: 0.20, 5000: 0.15})
    assert finder._select_next_auto_test_power_w() == 7000


def test_an_unmeasurable_boundary_next_to_the_best_defers(finder):
    """Codex on #39: 1000 W could not be measured and the loss rises from 3000 W
    on. The search refines right of 3000 W - and must not end for good without
    1000 W, which may be the better one."""
    failed = {1000: AUTO_TEST_MAX_ATTEMPTS}
    _store(finder, failed=failed)
    asked: list[int] = []
    while (power := finder._select_next_auto_test_power_w()) is not None:
        asked.append(power)
        _store(finder, {p: 0.05 + (p - 3000) / 100000 for p in asked}, failed=failed)
        assert len(asked) < 20
    assert 1000 not in asked and min(asked) == 3000
    assert finder._auto_search_deferred is True


def test_nothing_measurable_at_all_defers(finder):
    _store(finder, failed={p: AUTO_TEST_MAX_ATTEMPTS for p in COARSE})
    assert finder._select_next_auto_test_power_w() is None
    assert finder._auto_search_deferred is True


def test_the_search_stays_below_the_controls_maximum(mock_hass, finder):
    """A trial above it would be written as the maximum and filed under the wrong power."""
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W", "max": 12540.0})
    asked = []
    while (power := finder._select_next_auto_test_power_w()) is not None and len(asked) < 20:
        asked.append(power)
        _store(finder, {p: 0.2 - p / 200000 for p in asked})  # best at the top
    assert asked[:7] == [1000, 3000, 5000, 7000, 9000, 11000, 12540]
    assert max(asked) == 12540


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
    with patch("custom_components.inverter_charge_night.coordinator.async_track_state_change_event") as track:
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
        "grid_charge_switch": "switch.grid",
    }
    finder.config = finder.entry.data
    finder.auto_efficient_charge = True
    finder.target_reached = False

    end = _measure(finder, mock_hass, power_w=10000, samples=[(10000, 9000)] * 2, minutes=60)
    started: list[int] = []

    async def _record(power_w):
        started.append(power_w)

    finder._start_auto_test = _record
    finder._enough_charge_left_for = MagicMock(return_value=True)  # a night with plenty to charge
    with patch(NOW, return_value=end):
        await finder._handle_auto_charge()

    assert finder.get_auto_efficiency_data()["history"]["10000"] == pytest.approx(0.1)
    assert started and started[0] != 10000


@pytest.mark.asyncio
async def test_an_unfinished_measurement_keeps_running(mock_hass, finder):
    mock_hass.states.async_set("switch.grid", "on")
    finder.entry.data = {**CONFIG, "grid_charge_switch": "switch.grid"}
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


@pytest.mark.parametrize("fault", ["unavailable", "stale", "gap", "backwards"])
def test_interrupted_power_measurement_never_bridges_missing_energy(finder, mock_hass, fault):
    mock_hass.states.async_set(AC_LIMIT, "5000", {"unit_of_measurement": "W"})
    _power(mock_hass, 5000, 4500)
    finder._auto_test_active = True
    finder._auto_test_power_w = 5000
    finder._auto_test_start = START - timedelta(minutes=2)
    with patch(NOW, return_value=START):
        finder._begin_auto_measurement(START)
    if fault == "unavailable":
        mock_hass.states.async_set(SENT, "unavailable")
    elif fault == "stale":
        mock_hass.states.get(SENT).last_reported = START - timedelta(minutes=10)
    next_time = START + timedelta(seconds=90 if fault == "gap" else -10 if fault == "backwards" else 30)
    with patch(NOW, return_value=next_time):
        finder._accumulate_auto_energy()
    assert not finder._auto_test_active
    assert finder.get_auto_efficiency_data()["history"] == {}
    assert finder._auto_energy_sent_wh == 0
    assert finder._auto_last_result["discarded"]


def test_measurement_signature_invalidates_changed_sensor_paths(finder):
    finder._save_auto_efficiency_data({"history": {"5000": .1}, "best_power_w": 5000})
    assert finder.best_charge_power_w() == 5000
    finder.config = {**finder.config, CONF_CHARGE_POWER_RECEIVED_ENTITY: "sensor.replaced"}
    assert finder.best_charge_power_w() is None
    assert finder.get_auto_efficiency_data()["history"] == {}


def test_transient_failure_is_retried_in_a_future_window(finder):
    with patch(NOW, return_value=START):
        for point in COARSE:
            for _ in range(AUTO_TEST_MAX_ATTEMPTS):
                finder._record_failed_attempt(point, too_short=False)
        assert finder._select_next_auto_test_power_w() is None
        assert finder._auto_search_deferred
    with patch(NOW, return_value=START + timedelta(days=1)):
        assert finder._select_next_auto_test_power_w() == 1000
        assert not finder._auto_search_deferred


@pytest.mark.asyncio
@pytest.mark.parametrize("sun_state", [None, "above_horizon"])
async def test_solar_contamination_defers_search_without_saving_losses(finder, mock_hass, sun_state):
    finder.auto_efficient_charge = True
    finder._auto_test_active = True
    if sun_state is None:
        mock_hass.states.async_remove("sun.sun")
    else:
        mock_hass.states.async_set("sun.sun", sun_state)
    await finder._handle_auto_charge()
    assert not finder._auto_test_active
    assert finder.get_auto_efficiency_data()["history"] == {}
    mock_hass.services.async_call.assert_not_awaited()


def test_legacy_measurements_without_provenance_are_invalidated(mock_hass):
    entry = MagicMock()
    entry.entry_id, entry.title, entry.data = "legacy", "Legacy", CONFIG
    entry.options = {CONF_AUTO_EFFICIENCY_DATA: {"history": {"10000": .01}, "best_power_w": 10000}}
    InverterChargeNightCoordinator(mock_hass, entry)
    saved = mock_hass.config_entries.async_update_entry.call_args.kwargs["options"][CONF_AUTO_EFFICIENCY_DATA]
    assert saved["history"] == {}
    assert "best_power_w" not in saved
    assert "measurement_signature" in saved


@pytest.mark.parametrize("failure", ["unavailable", "reset"])
def test_counter_failure_discards_live_measurement_without_pruning(metered, mock_hass, failure):
    end = _measure(metered, mock_hass, power_w=5000, samples=[(5000, 4500)], minutes=1)
    if failure == "unavailable":
        mock_hass.states.async_set(METER_SENT, "unavailable")
    else:
        _meters(mock_hass, 0, 0)
    with patch(NOW, return_value=end + timedelta(seconds=10)):
        metered._accumulate_auto_energy()
    assert not metered._auto_test_active
    assert metered.get_auto_efficiency_data()["history"] == {}
    assert "counters" in metered._auto_last_result["discarded"]


def test_sunrise_at_target_stop_cannot_commit_contaminated_sample(finder, mock_hass):
    end = _measure(finder, mock_hass, power_w=5000, samples=[(5000, 4500)], minutes=10)
    mock_hass.states.async_set("sun.sun", "above_horizon")
    with patch(NOW, return_value=end):
        finder._finalize_auto_test()
    assert finder.get_auto_efficiency_data()["history"] == {}
    assert not finder._auto_test_active


def test_missing_configured_counter_never_falls_back_to_guessed_power_energy(metered, mock_hass):
    mock_hass.states.async_set(AC_LIMIT, "5000", {"unit_of_measurement": "W"})
    mock_hass.states.async_set(METER_SENT, "unavailable")
    _power(mock_hass, 5000, 4500)
    metered._auto_test_active = True
    metered._auto_test_power_w = 5000
    metered._begin_auto_measurement(START)
    assert not metered._auto_test_active
    assert metered._auto_meter_start is None
