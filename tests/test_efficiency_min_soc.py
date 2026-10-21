"""The efficiency search over the min SOC floor, and how long a trial measures.

On the field Kostal `Battery:ManualCharge` is a forced charge, so 3.10.8 keeps
it off for the window and the raised min SOC charges the battery, under the
written limit. The search used to wait for the switch and never measured. It
now runs over the floor, measures each power until its loss is known to
AUTO_TEST_TARGET_SE, and starts a trial only when the night has enough left to
charge to finish it.
"""
from __future__ import annotations

import random
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.inverter_charge_night.const import (
    AUTO_TEST_MAX_DURATION_S,
    AUTO_TEST_TARGET_SE,
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
)
from tests.test_efficiency_finder import CONFIG, NOW, START, _make, _measure, _power

GRID = "switch.grid"
MIN_SOC = "number.min_soc"
BATTERY = "sensor.soc"
NIGHT = {**CONFIG, CONF_GRID_CHARGE_SWITCH: GRID, CONF_MIN_SOC_ENTITY: MIN_SOC,
         CONF_BATTERY_SOC_ENTITY: BATTERY, CONF_BATTERY_CAPACITY: 35.8}


@pytest.fixture
def night(mock_hass):
    """The night of 20/21 October: ManualCharge suspended, floor 44 % over a 33 % battery."""
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(MIN_SOC, "44", {"unit_of_measurement": "%"})
    mock_hass.states.async_set(BATTERY, "33", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("number.ac_limit", "1000", {"unit_of_measurement": "W"})
    _power(mock_hass, 0, 0)
    coordinator = _make(mock_hass, NIGHT)
    coordinator.is_active = True
    coordinator.target_reached = False
    coordinator.initial_calculated_soc = 44.0
    coordinator.auto_efficient_charge = True
    coordinator._grid_switch_suspended = True
    coordinator._start_auto_test = AsyncMock()
    yield mock_hass, coordinator
    coordinator._remove_auto_sample_listener()


@pytest.mark.asyncio
async def test_the_floor_carries_the_trial_when_the_switch_is_suspended(night):
    hass, coordinator = night

    with patch(NOW, return_value=START):
        await coordinator._handle_auto_charge()

    coordinator._start_auto_test.assert_awaited_once_with(1000)


@pytest.mark.asyncio
async def test_without_the_suspension_the_switch_is_still_required(night):
    """An inverter whose switch works keeps the switch path; nothing changes there."""
    hass, coordinator = night
    coordinator._grid_switch_suspended = False

    with patch(NOW, return_value=START):
        await coordinator._handle_auto_charge()

    coordinator._start_auto_test.assert_not_awaited()
    assert coordinator._auto_wait_reason == "grid_charge_not_confirmed"


@pytest.mark.asyncio
async def test_a_floor_at_the_battery_level_charges_nothing(night):
    hass, coordinator = night
    hass.states.get(MIN_SOC).state = "33"

    with patch(NOW, return_value=START):
        await coordinator._handle_auto_charge()

    coordinator._start_auto_test.assert_not_awaited()
    assert coordinator._auto_wait_reason == "grid_charge_not_confirmed"


@pytest.mark.asyncio
async def test_no_trial_the_target_would_cut_short(night):
    """43.9 % of a 44 % target: 36 Wh left, a 7-minute trial at 1 kW puts 105 Wh in."""
    hass, coordinator = night
    hass.states.get(BATTERY).state = "43.9"
    hass.states.get(MIN_SOC).state = "45"

    with patch(NOW, return_value=START):
        await coordinator._handle_auto_charge()

    coordinator._start_auto_test.assert_not_awaited()
    assert coordinator._auto_wait_reason == "not_enough_charge_left"


def _noisy(spread_w: float, count: int, seed: int = 7) -> list[tuple[float, float]]:
    rng = random.Random(seed)
    return [(10000, 9000 + rng.uniform(-spread_w, spread_w)) for _ in range(count)]


def test_a_steady_trial_is_done_after_the_minimum(mock_hass, finder):
    end = _measure(finder, mock_hass, power_w=10000, samples=[(10000, 9000)] * 10, minutes=6)
    with patch(NOW, return_value=end):
        assert finder._auto_measurement_is_complete() is True
        assert finder._auto_loss_standard_error() == pytest.approx(0.0)


def test_a_noisy_trial_measures_on_until_its_loss_is_known(mock_hass, finder):
    """Telling 9.5 % from 9.6 % loss needs more than five noisy minutes."""
    end = _measure(finder, mock_hass, power_w=10000, samples=_noisy(400, 12), minutes=6)
    with patch(NOW, return_value=end):
        error = finder._auto_loss_standard_error()
        assert error is not None and error > AUTO_TEST_TARGET_SE
        assert finder._auto_measurement_is_complete() is False


def test_the_longest_trial_ends_it_whatever_the_noise(mock_hass, finder):
    minutes = AUTO_TEST_MAX_DURATION_S / 60 + 1
    end = _measure(finder, mock_hass, power_w=10000, samples=_noisy(400, 60), minutes=minutes)
    with patch(NOW, return_value=end):
        assert finder._auto_measurement_is_complete() is True


def test_the_uncertainty_is_recorded_with_the_loss(mock_hass, finder):
    end = _measure(finder, mock_hass, power_w=10000, samples=_noisy(100, 40), minutes=20)
    with patch(NOW, return_value=end):
        finder._finalize_auto_test()

    data = finder.entry.options[CONF_AUTO_EFFICIENCY_DATA]
    assert data["history"]["10000"] == pytest.approx(0.1, abs=0.005)
    assert 0 < data["uncertainty"]["10000"] < AUTO_TEST_TARGET_SE
    attributes = finder.auto_test_attributes()
    assert attributes["uncertainty_by_power_pct"]["10000"] == pytest.approx(
        data["uncertainty"]["10000"] * 100, abs=0.01
    )


@pytest.fixture
def finder(mock_hass):
    mock_hass.states.async_set("number.ac_limit", "6000", {"unit_of_measurement": "W"})
    _power(mock_hass, 0, 0)
    coordinator = _make(mock_hass)
    yield coordinator
    coordinator._remove_auto_sample_listener()


def test_blocks_are_a_minute_each(mock_hass, finder):
    end = _measure(finder, mock_hass, power_w=10000, samples=[(10000, 9000)] * 10, minutes=6)
    assert len(finder._auto_block_losses) in (5, 6)
    assert end - START >= timedelta(minutes=8)
