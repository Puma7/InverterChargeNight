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
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_BATTERY_CAPACITY,
)
from custom_components.inverter_charge_night.coordinator import InverterChargeNightCoordinator
from tests.test_efficiency_finder import NOW, START, _make, _measure
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
    hass, coordinator = night
    end = _measure(coordinator, hass, power_w=1000, samples=[(0, 0)] * 6, minutes=6)

    with patch(NOW, return_value=end):
        await coordinator._handle_auto_charge()

    assert not coordinator._auto_test_active
    assert "not charging" in coordinator._auto_last_result["discarded"]
    assert coordinator._auto_wait_reason == "battery_not_charging"
    # The night's fault, not the power's: no failed attempt against 1000 W
    assert "failed" not in coordinator.entry.options.get(CONF_AUTO_EFFICIENCY_DATA, {})
    coordinator._start_auto_test.assert_not_awaited()

    with patch(NOW, return_value=end + timedelta(minutes=15)):
        await coordinator._handle_auto_charge()
    coordinator._start_auto_test.assert_not_awaited()

    with patch(NOW, return_value=NEXT_NIGHT):
        await coordinator._handle_auto_charge()
    coordinator._start_auto_test.assert_awaited_once_with(1000)


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
    """CodeRabbit on #40: 0.3 kWh at 1 kW takes 18 minutes after settling;
    ten minutes before the end is not enough, twenty-five are."""
    hass, coordinator = night
    end = coordinator._window_end_datetime(START)

    with patch(NOW, return_value=end - timedelta(minutes=10)):
        await coordinator._handle_auto_charge()
    coordinator._start_auto_test.assert_not_awaited()
    assert coordinator._auto_wait_reason == "not_enough_charge_left"

    with patch(NOW, return_value=end - timedelta(minutes=25)):
        await coordinator._handle_auto_charge()
    coordinator._start_auto_test.assert_awaited_once_with(1000)
