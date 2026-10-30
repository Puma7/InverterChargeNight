"""The night of 21/22 October 2026: the efficiency search on a battery that does not charge.

Target 21.8 %, the battery at 21 % of 36.25 kWh, min SOC 22 %, ManualCharge
suspended. The Kostal did not charge at all under a floor 1 % above the
battery. A trial at 1000 W started anyway, measured 0 W for half an hour,
was discarded when the Kostal dropped the limit to 51 W, and the search then
wrote 1000 W to register 1038 every 15 minutes until 05:00 - 22 writes, each
one ignored.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.inverter_charge_night.const import (
    AUTO_TEST_MAX_START_FAILURES,
    AUTO_TEST_START_WAIT_S,
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_BATTERY_CAPACITY,
    CONF_CHARGE_ENERGY_RECEIVED_ENTITY,
    CONF_CHARGE_ENERGY_SENT_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
)
from custom_components.inverter_charge_night.coordinator import InverterChargeNightCoordinator
from tests.test_efficiency_finder import (
    METER_RECEIVED,
    METER_SENT,
    NOW,
    RECEIVED,
    SENT,
    START,
    _make,
    _measure,
    _meters,
)
from tests.test_efficiency_min_soc import BATTERY, MIN_SOC, NIGHT, night  # noqa: F401

NEXT_NIGHT = START + timedelta(days=1)


@pytest.mark.asyncio
async def test_no_trial_without_room_for_its_minimum_energy(night):  # noqa: F811
    """0.29 kWh to the target; a trial needs 0.3 kWh measured after settling."""
    hass, coordinator = night
    coordinator.config = {**coordinator.config, CONF_BATTERY_CAPACITY: 36.25}
    coordinator.initial_calculated_soc = 21.8
    hass.states.get(BATTERY).state = "21"
    hass.states.get(MIN_SOC).state = "22"

    with patch(NOW, return_value=START):
        await coordinator._handle_auto_charge()

    coordinator._start_auto_test.assert_not_awaited()
    assert coordinator._auto_wait_reason == "not_enough_charge_left"


@pytest.mark.asyncio
async def test_room_for_the_minimum_energy_starts_the_trial(night):  # noqa: F811
    """The bound is the minimum energy, not more: 0.4 kWh left is enough."""
    hass, coordinator = night
    coordinator.config = {**coordinator.config, CONF_BATTERY_CAPACITY: 36.25}
    coordinator.initial_calculated_soc = 22.1
    hass.states.get(BATTERY).state = "21"
    hass.states.get(MIN_SOC).state = "23"

    with patch(NOW, return_value=START):
        await coordinator._handle_auto_charge()

    coordinator._start_auto_test.assert_awaited_once_with(1000)


@pytest.mark.asyncio
async def test_a_battery_that_does_not_charge_ends_the_trial_and_the_night(night):  # noqa: F811
    """One idle trial leaves its power out for this window (Codex on #40: an
    inverter with a minimum charge rate idles at 1 kW and charges at 3 kW);
    a second idle trial at another power ends the window's search."""
    hass, coordinator = night
    end = _measure(coordinator, hass, power_w=1000, samples=[(0, 0)] * 14, minutes=14)

    # The wait for the battery is over: an idle trial, judged by the sampler
    assert not coordinator._auto_test_active
    assert "trying another power" in coordinator._auto_last_result["discarded"]
    with patch(NOW, return_value=end):
        assert coordinator._auto_search_stopped() is None
        await coordinator._handle_auto_charge()  # the next poll
    coordinator._start_auto_test.assert_awaited_once_with(3000)
    coordinator._start_auto_test.reset_mock()

    end = _measure(coordinator, hass, power_w=3000, samples=[(0, 0)] * 14, minutes=14)
    with patch(NOW, return_value=end):
        await coordinator._handle_auto_charge()
    assert "not charging" in coordinator._auto_last_result["discarded"]
    assert coordinator._auto_wait_reason == "battery_not_charging"
    coordinator._start_auto_test.assert_not_awaited()

    with patch(NOW, return_value=end + timedelta(minutes=15)):
        await coordinator._handle_auto_charge()
    coordinator._start_auto_test.assert_not_awaited()

    # Neither power is a loss sample, and the next window offers 1 kW again
    assert coordinator.get_auto_efficiency_data()["history"] == {}
    with patch(NOW, return_value=NEXT_NIGHT):
        await coordinator._handle_auto_charge()
    coordinator._start_auto_test.assert_awaited_once_with(1000)


@pytest.mark.asyncio
async def test_a_restart_keeps_the_idle_trial_count(night):  # noqa: F811
    """An idle trial, a reload, another idle trial: two in the window, the search stops."""
    hass, coordinator = night
    end = _measure(coordinator, hass, power_w=1000, samples=[(0, 0)] * 14, minutes=14)
    with patch(NOW, return_value=end):
        await coordinator._handle_auto_charge()

    restarted = _make(hass, NIGHT)
    restarted.entry.options = coordinator.entry.options
    restarted._restore_state()
    restarted.is_active = True
    restarted.auto_efficient_charge = True
    restarted._grid_switch_suspended = True
    restarted.initial_calculated_soc = 44.0
    restarted._start_auto_test = AsyncMock()
    end = _measure(restarted, hass, power_w=3000, samples=[(0, 0)] * 14, minutes=14)
    with patch(NOW, return_value=end):
        await restarted._handle_auto_charge()
        assert restarted._auto_search_stopped() == "battery_not_charging"
    restarted._remove_auto_sample_listener()


@pytest.mark.asyncio
async def test_extending_an_adhoc_window_keeps_the_search_stop(night):  # noqa: F811
    """Codex on #40: the key is the window end, which an extension moves.
    The stop, the counts and the window's left-out powers move with it."""
    hass, coordinator = night
    until = START + timedelta(hours=1)
    coordinator._adhoc_until = until  # a running ad-hoc window
    coordinator._calculate_initial_soc = AsyncMock()
    with patch(NOW, return_value=START):
        assert coordinator._efficiency_window_key() == until.isoformat()
        coordinator._stop_auto_search_for_window("charge_limit_not_taken")
        coordinator._count_trial_lost_to_telemetry()
        coordinator._record_failed_attempt(1000, too_short=False)

        assert await coordinator.async_open_adhoc_window(
            until=until + timedelta(hours=2), target_soc=60.0, allow_grid_charge=True,
            reason="test",
        )
        assert coordinator._efficiency_window_key() == (until + timedelta(hours=2)).isoformat()
        assert coordinator._auto_search_stopped() == "charge_limit_not_taken"
        assert coordinator._auto_telemetry_losses[1] == 1
        assert coordinator.get_auto_efficiency_data()["failed"] == {"1000": 1}


@pytest.mark.asyncio
async def test_a_slow_start_is_not_mistaken_for_no_charge(night):  # noqa: F811
    """Below the minimum duration nothing is judged; at half the setpoint the
    battery is charging, and the trial goes on to its own evaluation."""
    hass, coordinator = night
    end = _measure(coordinator, hass, power_w=1000, samples=[(0, 0)] * 4, minutes=4)
    with patch(NOW, return_value=end):
        await coordinator._handle_auto_charge()
    assert coordinator._auto_test_active

    end = _measure(coordinator, hass, power_w=1000, samples=[(500, 450)] * 6, minutes=6)
    with patch(NOW, return_value=end):
        await coordinator._handle_auto_charge()
    assert coordinator._auto_test_active
    assert coordinator._auto_search_stopped() is None


@pytest.mark.asyncio
async def test_trial_starts_the_inverter_ignores_stop_for_the_window(night):  # noqa: F811
    hass, coordinator = night
    coordinator._start_auto_test = InverterChargeNightCoordinator._start_auto_test.__get__(coordinator)
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=False)

    for minutes in range(0, 15 * (AUTO_TEST_MAX_START_FAILURES + 3), 15):
        with patch(NOW, return_value=START + timedelta(minutes=minutes)):
            await coordinator._handle_auto_charge()

    assert coordinator._set_ac_charge_limit_w.await_count == AUTO_TEST_MAX_START_FAILURES
    coordinator._set_ac_charge_limit_w.assert_awaited_with(1000)
    assert coordinator._auto_wait_reason == "charge_limit_not_taken"

    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)
    with patch(NOW, return_value=NEXT_NIGHT):
        await coordinator._handle_auto_charge()
    coordinator._set_ac_charge_limit_w.assert_awaited_once_with(1000)
    assert coordinator._auto_test_active


@pytest.mark.asyncio
async def test_one_ignored_start_is_tried_again(night):  # noqa: F811
    """23:01 that night: a start right after switching ManualCharge off failed
    once, and the next one went through."""
    hass, coordinator = night
    coordinator._start_auto_test = InverterChargeNightCoordinator._start_auto_test.__get__(coordinator)
    coordinator._set_ac_charge_limit_w = AsyncMock(side_effect=[False, True])

    for minutes in (0, 15):
        with patch(NOW, return_value=START + timedelta(minutes=minutes)):
            await coordinator._handle_auto_charge()

    assert coordinator._set_ac_charge_limit_w.await_count == 2
    assert coordinator._auto_test_active


@pytest.mark.asyncio
async def test_a_late_start_that_charges_now_is_not_idle(night):  # noqa: F811
    """Codex on #40: four minutes at 0 W, then one at 500 W of 1000 W - the
    average is about 100 W, but the battery is charging now."""
    hass, coordinator = night
    end = _measure(coordinator, hass, power_w=1000,
                   samples=[(0, 0)] * 4 + [(500, 450)], minutes=5.5)

    with patch(NOW, return_value=end):
        await coordinator._handle_auto_charge()

    assert coordinator._auto_test_active
    assert coordinator._auto_search_stopped() is None


@pytest.mark.asyncio
async def test_only_failed_starts_in_a_row_stop_the_search(night):  # noqa: F811
    """Codex on #40: an accepted start in between clears the count."""
    hass, coordinator = night
    start = InverterChargeNightCoordinator._start_auto_test.__get__(coordinator)
    coordinator._set_ac_charge_limit_w = AsyncMock(side_effect=[False, True, False, False])

    with patch(NOW, return_value=START):
        await start(1000)
        await start(1000)
        coordinator._confirm_auto_test_limit()  # the inverter read it back
        coordinator._reset_auto_test_state()  # that trial ended
        await start(3000)
        assert coordinator._auto_search_stopped() is None
        await start(3000)
        assert coordinator._auto_search_stopped() == "charge_limit_not_taken"


@pytest.mark.asyncio
async def test_a_restart_inside_the_window_keeps_the_stop(night):  # noqa: F811
    """Codex on #40: a reload must not start the trials over."""
    hass, coordinator = night
    with patch(NOW, return_value=START):
        coordinator._stop_auto_search_for_window("battery_not_charging")

    restarted = _make(hass, NIGHT)
    restarted.entry.options = coordinator.entry.options
    restarted._restore_state()

    with patch(NOW, return_value=START + timedelta(minutes=30)):
        assert restarted._auto_search_stopped() == "battery_not_charging"
    with patch(NOW, return_value=NEXT_NIGHT):
        assert restarted._auto_search_stopped() is None


@pytest.mark.asyncio
async def test_no_trial_the_window_end_cuts_short_of_its_energy(night):  # noqa: F811
    """CodeRabbit on #40: 0.3 kWh at 1 kW takes 18 minutes once measuring,
    and the measurement may begin 15 minutes in (CodeRabbit on #49); twenty-
    five minutes before the end are not enough, forty are."""
    hass, coordinator = night
    end = coordinator._window_end_datetime(START)

    with patch(NOW, return_value=end - timedelta(minutes=25)):
        await coordinator._handle_auto_charge()
    coordinator._start_auto_test.assert_not_awaited()
    assert coordinator._auto_wait_reason == "not_enough_charge_left"

    with patch(NOW, return_value=end - timedelta(minutes=40)):
        await coordinator._handle_auto_charge()
    coordinator._start_auto_test.assert_awaited_once_with(1000)


@pytest.mark.asyncio
async def test_a_reload_keeps_the_failed_start_count(night):  # noqa: F811
    """Codex on #41: one ignored start, a reload, one more ignored start - that
    is two in a row, and the search stops."""
    hass, coordinator = night
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=False)
    with patch(NOW, return_value=START):
        await InverterChargeNightCoordinator._start_auto_test(coordinator, 1000)

    restarted = _make(hass, NIGHT)
    restarted.entry.options = coordinator.entry.options
    restarted._restore_state()
    restarted._set_ac_charge_limit_w = AsyncMock(return_value=False)
    with patch(NOW, return_value=START + timedelta(minutes=15)):
        await InverterChargeNightCoordinator._start_auto_test(restarted, 1000)
        assert restarted._auto_search_stopped() == "charge_limit_not_taken"
    restarted._remove_auto_sample_listener()


@pytest.mark.asyncio
async def test_an_idle_trial_asks_for_a_refresh_at_its_minimum(night):  # noqa: F811
    """Codex on #41: the 15-s check refreshes at the five minutes, not only
    for a complete trial, so the idle stop does not wait for the next poll."""
    hass, coordinator = night
    # Short of the start wait: the trial still waits for the battery
    end = _measure(coordinator, hass, power_w=1000, samples=[(0, 0)] * 12, minutes=12)
    assert coordinator._auto_test_active
    end += timedelta(minutes=1, seconds=1)
    coordinator.async_request_refresh = AsyncMock()
    module = "custom_components.inverter_charge_night.coordinator"
    with patch(f"{module}.async_track_time_interval") as interval, \
            patch(f"{module}.async_track_state_change_event"):
        coordinator.setup_communication_listener()
    check = interval.call_args.args[1]

    with patch(NOW, return_value=end):
        await check(end)

    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_limit_lowered_during_the_measurement_stops_the_search(night):  # noqa: F811
    """CodeRabbit on #40: the power sampler can see the Kostal's 51 W before the
    verification does; it stops the search for the window all the same."""
    hass, coordinator = night
    end = _measure(coordinator, hass, power_w=1000, samples=[(1000, 950)] * 4, minutes=4)
    hass.states.async_set("number.ac_limit", "51", {"unit_of_measurement": "W"})

    with patch(NOW, return_value=end):
        coordinator._accumulate_auto_energy()
        assert coordinator._auto_search_stopped() == "inverter_lowered_limit"
    assert not coordinator._auto_test_active


@pytest.mark.asyncio
async def test_an_unreadable_limit_during_the_measurement_only_discards(night):  # noqa: F811
    hass, coordinator = night
    end = _measure(coordinator, hass, power_w=1000, samples=[(1000, 950)] * 4, minutes=4)
    hass.states.async_set("number.ac_limit", "unavailable", {"unit_of_measurement": "W"})

    with patch(NOW, return_value=end):
        coordinator._accumulate_auto_energy()
        assert coordinator._auto_search_stopped() is None
    assert not coordinator._auto_test_active


@pytest.mark.asyncio
async def test_trials_lost_to_stale_power_readings_stop_the_search(night):  # noqa: F811
    """CodeRabbit on #40: a sensor that reports 0 W only on change goes stale
    before the idle check, with the 15-s check still sampling it; two such
    trials end the window's search, and a reload does not start the count over."""
    hass, coordinator = night

    def lose_a_trial(co):
        end = _measure(co, hass, power_w=1000, samples=[(0, 0)] * 2, minutes=2)
        for entity in (SENT, RECEIVED):
            hass.states.get(entity).last_reported = end  # and never again
        for step in range(1, 6):
            with patch(NOW, return_value=end + timedelta(seconds=15 * step)):
                co._accumulate_auto_energy()
        assert not co._auto_test_active
        assert "stale" in co._auto_last_result["discarded"]

    lose_a_trial(coordinator)
    with patch(NOW, return_value=START):
        assert coordinator._auto_search_stopped() is None

    restarted = _make(hass, NIGHT)
    restarted.entry.options = coordinator.entry.options
    restarted._restore_state()
    lose_a_trial(restarted)
    with patch(NOW, return_value=START):
        assert restarted._auto_search_stopped() == "power_telemetry_lost"
    restarted._remove_auto_sample_listener()


@pytest.mark.asyncio
async def test_a_trial_never_confirmed_is_a_start_not_taken_not_a_lowered_limit(mock_hass):
    """CI on #41: after the settling the slow register still showed the previous,
    lower trial. That is a start the inverter has not taken - retried once,
    counted with the failed starts - not an inverter holding a lower limit."""
    from tests.test_electrical_safety import AC_LIMIT, INSIDE, _make, _register
    from tests.test_electrical_safety import NOW as SAFETY_NOW, GRID
    with patch(SAFETY_NOW, return_value=INSIDE):
        _register(mock_hass)
        mock_hass.states.async_set(GRID, "on")
        coordinator = _make(mock_hass)
        coordinator.is_active = True
        coordinator.auto_efficient_charge = True
        coordinator._planned_setpoint_written_w = 13000
        coordinator._auto_test_active = True
        coordinator._auto_test_power_w = 13000
        coordinator._auto_test_start = INSIDE - timedelta(seconds=AUTO_TEST_START_WAIT_S + 60)
        coordinator._auto_test_limit_confirmed = False
        mock_hass.states.async_set(AC_LIMIT, "11000", {"unit_of_measurement": "W"})

        await coordinator._verify_ac_charge_limit()
        assert not coordinator._auto_test_active
        assert coordinator._auto_search_stopped() is None
        assert coordinator._auto_start_failures is not None and coordinator._auto_start_failures[1] == 1

        # Accepted again but still not read back: the count stands, or a
        # setpoint the inverter never shows would be written every poll
        coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)
        await coordinator._start_auto_test(13000)
        assert coordinator._auto_start_failures[1] == 1
        coordinator._auto_test_start = INSIDE - timedelta(seconds=AUTO_TEST_START_WAIT_S + 60)
        await coordinator._verify_ac_charge_limit()
        assert coordinator._auto_search_stopped() == "charge_limit_not_taken"


@pytest.mark.asyncio
async def test_no_trial_the_window_end_cuts_short_at_the_lowest_accepted_draw(night):  # noqa: F811
    """Codex on #40: a trial counts down to 800 W of 1000 W, and 0.3 kWh at
    800 W takes 22.5 minutes after the settling - 23 minutes are too few."""
    hass, coordinator = night
    end = coordinator._window_end_datetime(START)

    with patch(NOW, return_value=end - timedelta(minutes=23)):
        await coordinator._handle_auto_charge()
    coordinator._start_auto_test.assert_not_awaited()
    assert coordinator._auto_wait_reason == "not_enough_charge_left"


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", ["window_ended", "unloading"])
async def test_a_start_cancelled_by_the_lifecycle_is_no_failed_start(night, lifecycle):  # noqa: F811
    """Codex on #40: the setter returns False when the window ends or the entry
    unloads while the write waits for the lock. The inverter refused nothing."""
    hass, coordinator = night

    async def cancelled(power_w: int) -> bool:
        if lifecycle == "unloading":
            coordinator._unloading = True
        else:
            coordinator.is_active = False
        return False

    coordinator._set_ac_charge_limit_w = AsyncMock(side_effect=cancelled)
    with patch(NOW, return_value=START):
        await InverterChargeNightCoordinator._start_auto_test(coordinator, 1000)

    coordinator._set_ac_charge_limit_w.assert_awaited_once_with(1000)
    assert coordinator._auto_start_failures is None
    assert not coordinator._auto_test_active


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [SENT, RECEIVED])
async def test_trials_lost_to_missing_telemetry_at_their_start_stop_the_search(night, missing):  # noqa: F811
    """Codex and CodeRabbit on #40/#41: a power sensor that is gone as the
    settling ends discards the trial; two such trials end the window's search."""
    hass, coordinator = night
    hass.states.async_set("sun.sun", "below_horizon")

    for attempt in range(AUTO_TEST_MAX_START_FAILURES):
        _measure(coordinator, hass, power_w=1000, samples=[(1000, 950)], minutes=0, settle=False)
        hass.states.async_set(missing, "unavailable")
        with patch(NOW, return_value=START + timedelta(minutes=2, seconds=1)):
            coordinator._accumulate_auto_energy()
        assert not coordinator._auto_test_active
        assert "unavailable at measurement start" in coordinator._auto_last_result["discarded"]
        with patch(NOW, return_value=START):
            stopped = coordinator._auto_search_stopped()
        assert stopped == (None if attempt == 0 else "power_telemetry_lost")


@pytest.mark.asyncio
async def test_replacing_the_measurement_entities_lifts_the_window_stop(night):  # noqa: F811
    """Codex on #40: a stop earned on a faulty sensor says nothing about the
    healthy one the options put in its place inside the same window."""
    hass, coordinator = night
    with patch(NOW, return_value=START):
        coordinator._stop_auto_search_for_window("power_telemetry_lost")
        coordinator._count_failed_start()
        coordinator._count_trial_lost_to_telemetry()

    unchanged = coordinator._efficiency_signature()
    coordinator.forget_auto_search_after_rebinding(unchanged)
    with patch(NOW, return_value=START):
        assert coordinator._auto_search_stopped() == "power_telemetry_lost"

    coordinator.config = {**coordinator.config, CONF_CHARGE_POWER_SENT_ENTITY: "sensor.new_sent"}
    coordinator.forget_auto_search_after_rebinding(unchanged)
    with patch(NOW, return_value=START):
        assert coordinator._auto_search_stopped() is None
    assert coordinator._auto_start_failures is None
    assert coordinator._auto_telemetry_losses is None
    persisted = coordinator.entry.options["runtime_state"]
    assert persisted["auto_search_stopped"] is None
    assert persisted["auto_start_failures"] is None
    assert persisted["auto_telemetry_losses"] is None


def _with_meters(hass, coordinator):
    _meters(hass, 100.0, 90.0)
    coordinator.config = {
        **coordinator.config,
        CONF_CHARGE_ENERGY_SENT_ENTITY: METER_SENT,
        CONF_CHARGE_ENERGY_RECEIVED_ENTITY: METER_RECEIVED,
    }


@pytest.mark.asyncio
async def test_a_metered_trial_gets_the_time_and_charge_for_its_longest_run(night):  # noqa: F811
    """Codex on #40: with energy counters a trial runs its full 20 minutes.
    At 10 kW the power sensors need 15 + 5 minutes and 1.2 kWh; the meters
    need 15 + 20 minutes and 3.7 kWh. The 15 are the wait for the battery,
    after which the measurement begins at the latest (CodeRabbit on #49)."""
    hass, coordinator = night
    end = coordinator._window_end_datetime(START)
    coordinator.config = {**coordinator.config, CONF_BATTERY_CAPACITY: 36.25}  # 4 kWh to go

    with patch(NOW, return_value=end - timedelta(minutes=19)):
        assert not coordinator._enough_charge_left_for(10000)
    with patch(NOW, return_value=end - timedelta(minutes=28)):
        assert coordinator._enough_charge_left_for(10000)
        _with_meters(hass, coordinator)
        assert not coordinator._enough_charge_left_for(10000)
    with patch(NOW, return_value=end - timedelta(minutes=38)):
        assert coordinator._enough_charge_left_for(10000)

    coordinator.config = {**coordinator.config, CONF_BATTERY_CAPACITY: 18.2}  # 2 kWh to go
    with patch(NOW, return_value=end - timedelta(minutes=60)):
        assert not coordinator._enough_charge_left_for(10000)


@pytest.mark.asyncio
async def test_trials_lost_to_energy_counters_dropping_out_stop_the_search(night):  # noqa: F811
    """Codex on #40: counters that are there at every start and drop out
    during every measurement count like lost power readings."""
    hass, coordinator = night
    hass.states.async_set("sun.sun", "below_horizon")
    _with_meters(hass, coordinator)

    for attempt in range(AUTO_TEST_MAX_START_FAILURES):
        _meters(hass, 100.0, 90.0)
        end = _measure(coordinator, hass, power_w=1000, samples=[(1000, 950)], minutes=1)
        assert coordinator._auto_meter_start is not None
        hass.states.async_set(METER_SENT, "unavailable")
        with patch(NOW, return_value=end + timedelta(seconds=10)):
            coordinator._accumulate_auto_energy()
        assert not coordinator._auto_test_active
        assert "counters" in coordinator._auto_last_result["discarded"]
        with patch(NOW, return_value=START):
            stopped = coordinator._auto_search_stopped()
        assert stopped == (None if attempt == 0 else "power_telemetry_lost")


@pytest.mark.asyncio
async def test_replacing_the_measurement_entities_ends_a_running_trial(night):  # noqa: F811
    """Codex on #40: a trial measured on the old sensors must not go on
    against the new ones and file its sample under their signature."""
    hass, coordinator = night
    hass.states.async_set("sun.sun", "below_horizon")
    _measure(coordinator, hass, power_w=1000, samples=[(1000, 950)], minutes=2)
    assert coordinator._auto_test_active
    before = coordinator._efficiency_signature()

    coordinator.forget_auto_search_after_rebinding(before)
    assert coordinator._auto_test_active  # nothing replaced, nothing ended

    coordinator.config = {**coordinator.config, CONF_CHARGE_POWER_SENT_ENTITY: "sensor.new_sent"}
    coordinator.forget_auto_search_after_rebinding(before)
    assert not coordinator._auto_test_active
    assert coordinator._auto_last_result["discarded"] == "the measurement entities were replaced"
    assert coordinator.get_auto_efficiency_data()["history"] == {}
