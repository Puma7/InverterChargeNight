"""Periodic verification must check the live trial and retain connection protection."""
from unittest.mock import patch

import pytest

from tests.test_electrical_safety import AC_LIMIT, GRID, GRID_IMPORT, INSIDE, NOW, _ac_writes, _make, _register, _service_calls


@pytest.fixture
def active_trial(mock_hass):
    with patch(NOW, return_value=INSIDE):
        _register(mock_hass)
        mock_hass.states.async_set(GRID, "on")
        mock_hass.states.async_set(AC_LIMIT, "9000", {"unit_of_measurement": "W"})
        coordinator = _make(mock_hass)
        coordinator.is_active = True
        coordinator.auto_efficient_charge = True
        coordinator._planned_setpoint_written_w = 2000
        coordinator.planned_charge_power_w = 2000
        coordinator._auto_test_active = True
        coordinator._auto_test_power_w = 9000
        coordinator._auto_test_start = INSIDE
        yield coordinator


@pytest.mark.asyncio
async def test_verification_preserves_confirmed_trial_above_old_planner_setpoint(mock_hass, active_trial):
    await active_trial._verify_ac_charge_limit()

    assert active_trial._auto_test_active
    assert active_trial._auto_test_power_w == 9000
    assert float(mock_hass.states.get(AC_LIMIT).state) == 9000
    assert mock_hass.states.get(GRID).state == "on"
    assert _ac_writes(mock_hass) == []


@pytest.mark.asyncio
async def test_verification_stops_and_corrects_drift_above_live_trial(mock_hass, active_trial):
    mock_hass.states.async_set(AC_LIMIT, "10000", {"unit_of_measurement": "W"})
    await active_trial._verify_ac_charge_limit()

    assert _ac_writes(mock_hass) == [9000]
    assert ("switch", "turn_off", GRID) in _service_calls(mock_hass)
    assert float(mock_hass.states.get(AC_LIMIT).state) == 9000


@pytest.mark.parametrize("grid_import", ["34000", "unavailable"])
@pytest.mark.asyncio
async def test_verification_abandons_trial_when_budget_is_tight_or_unreadable(mock_hass, active_trial, grid_import):
    state = mock_hass.states.get(GRID_IMPORT)
    state.state = grid_import
    await active_trial._verify_ac_charge_limit()

    assert not active_trial._auto_test_active
    assert active_trial._auto_test_power_w is None
    assert _ac_writes(mock_hass) == [0]
    assert float(mock_hass.states.get(AC_LIMIT).state) == 0


@pytest.mark.asyncio
async def test_activation_gate_checks_live_trial_instead_of_old_plan(mock_hass, active_trial):
    assert await active_trial._grid_charge_activation_safe(100)
    assert active_trial._auto_test_active
    assert float(mock_hass.states.get(AC_LIMIT).state) == 9000
    assert mock_hass.states.get(GRID).state == "on"
    assert _ac_writes(mock_hass) == []


@pytest.mark.parametrize("grid_import", ["34000", "unavailable"])
@pytest.mark.asyncio
async def test_activation_gate_refuses_trial_when_budget_is_tight_or_unreadable(mock_hass, active_trial, grid_import):
    mock_hass.states.get(GRID_IMPORT).state = grid_import
    assert not await active_trial._grid_charge_activation_safe(100)
    assert ("switch", "turn_off", GRID) in _service_calls(mock_hass)
    assert not any(value > 0 for value in _ac_writes(mock_hass))
