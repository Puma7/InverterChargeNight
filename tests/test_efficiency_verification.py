"""Periodic verification must check the live trial and retain connection protection."""
from unittest.mock import patch
from datetime import timedelta

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
        coordinator._auto_test_limit_confirmed = True
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
async def test_downward_trial_drift_discards_measurement_without_recording_loss(mock_hass, active_trial):
    mock_hass.states.async_set(AC_LIMIT, "7500", {"unit_of_measurement": "W"})
    await active_trial._verify_ac_charge_limit()
    assert not active_trial._auto_test_active
    assert active_trial._auto_test_power_w is None
    assert "fell below" in active_trial._auto_last_result["discarded"]
    assert active_trial._auto_last_result["loss"] is None
    assert _ac_writes(mock_hass) == []
    # The inverter holds a lower limit of its own: no trial is written over it
    # again in this window (21/22 October 2026)
    with patch(NOW, return_value=INSIDE):
        assert active_trial._auto_search_stopped() == "inverter_lowered_limit"


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


@pytest.mark.asyncio
async def test_new_trial_waits_for_first_matching_ha_limit_then_rejects_downward_drift(mock_hass, active_trial):
    active_trial._auto_test_limit_confirmed = False
    mock_hass.states.async_set(AC_LIMIT, "8000", {"unit_of_measurement": "W"})
    await active_trial._verify_ac_charge_limit()
    assert active_trial._auto_test_active
    assert _ac_writes(mock_hass) == []
    mock_hass.states.async_set(AC_LIMIT, "9000", {"unit_of_measurement": "W"})
    await active_trial._verify_ac_charge_limit()
    assert active_trial._auto_test_limit_confirmed
    mock_hass.states.async_set(AC_LIMIT, "8000", {"unit_of_measurement": "W"})
    await active_trial._verify_ac_charge_limit()
    assert not active_trial._auto_test_active
    assert active_trial._auto_last_result["loss"] is None


@pytest.mark.asyncio
async def test_missing_initial_limit_feedback_discards_trial_at_settling_deadline(mock_hass, active_trial):
    active_trial._auto_test_limit_confirmed = False
    mock_hass.states.async_set(AC_LIMIT, "8000", {"unit_of_measurement": "W"})
    with patch.object(active_trial, "_efficiency_now", return_value=INSIDE + timedelta(seconds=120)):
        await active_trial._verify_ac_charge_limit()
    assert not active_trial._auto_test_active
    assert active_trial._auto_last_result["loss"] is None


@pytest.mark.asyncio
async def test_unconfirmed_trial_still_protects_against_excess_power(mock_hass, active_trial):
    active_trial._auto_test_limit_confirmed = False
    mock_hass.states.async_set(AC_LIMIT, "10000", {"unit_of_measurement": "W"})
    await active_trial._verify_ac_charge_limit()
    assert _ac_writes(mock_hass) == [9000]
    assert ("switch", "turn_off", GRID) in _service_calls(mock_hass)


@pytest.mark.asyncio
async def test_starting_next_trial_clears_previous_limit_confirmation(mock_hass, active_trial):
    with patch.object(active_trial, "_set_ac_charge_limit_w", return_value=True):
        await active_trial._start_auto_test(8200)
    assert not active_trial._auto_test_limit_confirmed


def _measurement_inputs(hass, coordinator):
    coordinator.config.update(charge_power_sent_entity="sensor.sent", charge_power_received_entity="sensor.received")
    hass.states.async_set("sun.sun", "below_horizon")
    hass.states.async_set("sensor.sent", "8000", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.received", "7200", {"unit_of_measurement": "W"})


def test_unconfirmed_limit_cannot_begin_measurement_without_periodic_verifier(mock_hass, active_trial):
    _measurement_inputs(mock_hass, active_trial)
    active_trial._auto_test_limit_confirmed = False
    mock_hass.states.async_set(AC_LIMIT, "8000", {"unit_of_measurement": "W"})
    with patch.object(active_trial, "_efficiency_now", return_value=INSIDE + timedelta(seconds=120)):
        active_trial._accumulate_auto_energy()
    assert not active_trial._auto_test_active
    assert active_trial._auto_measure_start is None


def test_measurement_confirms_matching_feedback_without_periodic_verifier(mock_hass, active_trial):
    _measurement_inputs(mock_hass, active_trial)
    active_trial._auto_test_limit_confirmed = False
    with patch.object(active_trial, "_efficiency_now", return_value=INSIDE + timedelta(seconds=120)):
        active_trial._accumulate_auto_energy()
    assert active_trial._auto_test_active
    assert active_trial._auto_test_limit_confirmed
    assert active_trial._auto_measure_start is not None


def test_unconfirmed_limit_cannot_record_plausible_efficiency_history(mock_hass, active_trial):
    mock_hass.states.async_set("sun.sun", "below_horizon")
    active_trial._auto_test_limit_confirmed = False
    active_trial._auto_measure_start = INSIDE
    active_trial._auto_energy_sent_wh = 1500
    active_trial._auto_energy_received_wh = 1350
    with patch.object(active_trial, "_efficiency_now", return_value=INSIDE + timedelta(seconds=600)):
        active_trial._finalize_auto_test()
    assert not active_trial.get_auto_efficiency_data().get("history")
    assert active_trial._auto_last_result["loss"] is None


@pytest.mark.parametrize("finalize", [False, True], ids=["sample", "finalize"])
def test_fresh_drift_cannot_record_even_after_initial_confirmation(mock_hass, active_trial, finalize):
    _measurement_inputs(mock_hass, active_trial)
    mock_hass.states.async_set(AC_LIMIT, "8000", {"unit_of_measurement": "W"})
    active_trial._auto_measure_start = INSIDE
    active_trial._auto_last_sample_time = INSIDE
    active_trial._auto_last_sent_w, active_trial._auto_last_received_w = 8000, 7200
    active_trial._auto_energy_sent_wh, active_trial._auto_energy_received_wh = 1500, 1350
    with patch.object(active_trial, "_efficiency_now", return_value=INSIDE + timedelta(seconds=600 if finalize else 30)):
        if finalize:
            active_trial._finalize_auto_test()
        else:
            active_trial._accumulate_auto_energy()
    assert not active_trial._auto_test_active
    assert not active_trial.get_auto_efficiency_data().get("history")


@pytest.mark.asyncio
async def test_unitless_kw_feedback_confirms_trial_and_starts_measurement(mock_hass, active_trial):
    _measurement_inputs(mock_hass, active_trial)
    mock_hass.states.async_set(AC_LIMIT, "9", {"max": 20})
    active_trial._auto_test_limit_confirmed = False
    await active_trial._verify_ac_charge_limit()
    assert active_trial._auto_test_active
    assert active_trial._auto_test_limit_confirmed
    active_trial._begin_auto_measurement(INSIDE + timedelta(seconds=120))
    assert active_trial._auto_measure_start is not None


@pytest.mark.asyncio
async def test_unitless_kw_original_limit_survives_write_restore_roundtrip(mock_hass, active_trial):
    mock_hass.states.async_set(AC_LIMIT, "6", {"max": 20})
    assert await active_trial._set_ac_charge_limit_w(9000)
    assert active_trial._original_ac_charge_power == 6000
    assert await active_trial._reset_ac_charge_limit()
    assert _ac_writes(mock_hass) == [9, 6]
    assert float(mock_hass.states.get(AC_LIMIT).state) == 6


@pytest.mark.asyncio
async def test_activation_corrects_unitless_kw_limit_above_trial(mock_hass, active_trial):
    mock_hass.states.async_set(AC_LIMIT, "12", {"max": 20})
    assert await active_trial._grid_charge_activation_safe(100)
    assert _ac_writes(mock_hass) == [9]
    assert float(mock_hass.states.get(AC_LIMIT).state) == 9


@pytest.mark.asyncio
async def test_window_end_records_valid_trial_before_restoring_limit(mock_hass, active_trial):
    mock_hass.states.async_set("sun.sun", "below_horizon")
    active_trial._auto_measure_start = INSIDE
    active_trial._auto_energy_sent_wh, active_trial._auto_energy_received_wh = 1500, 1350
    active_trial._original_ac_charge_power = 5000

    async def restore():
        assert await active_trial._reset_ac_charge_limit()

    with patch.object(active_trial, "_reset_settings", side_effect=restore), \
         patch.object(active_trial, "_efficiency_now", return_value=INSIDE + timedelta(seconds=600)):
        await active_trial._on_window_end(INSIDE + timedelta(seconds=600))
    assert _ac_writes(mock_hass) == [5000]
    assert float(mock_hass.states.get(AC_LIMIT).state) == 5000
    assert active_trial.get_auto_efficiency_data()["history"]["9000"] == pytest.approx(.1)
    assert not active_trial.is_active
