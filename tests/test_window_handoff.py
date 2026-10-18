"""Night charging must hand the inverter's original floor to the next hold."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import pytest

from custom_components.inverter_charge_night.sensor import CalculatedSOCSensor
from custom_components.inverter_charge_night.coordinator import InverterChargeNightCoordinator
from tests.test_evening_rescue import COORDINATOR, _make_coordinator, _rescue, _short


@pytest.mark.asyncio
async def test_evening_hold_after_night_reset_restores_the_pre_night_floor(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set("number.min_soc", "16")
    mock_hass.states.async_set("sensor.soc", "16")
    coordinator.is_active = True
    coordinator.initial_calculated_soc = 16
    coordinator.original_min_soc = 8
    morning = datetime(2026, 6, 1, 5, 1)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=morning):
        await coordinator._on_window_end(morning, scheduled=True)
    await _rescue(coordinator, _short(), now=morning)

    assert coordinator._adhoc_reason == "evening_rescue"
    assert coordinator.original_min_soc == 8
    # The ordinary service actor only completes blocking number writes. A
    # queued reset would still expose the night target when this hold starts.
    assert call("number", "set_value", {"entity_id": "number.min_soc", "value": 8},
                blocking=True) in mock_hass.services.async_call.await_args_list

    # Model the hold's later feedback, then its release at the peak tariff.
    mock_hass.states.async_set("number.min_soc", "16")
    await coordinator._on_window_end(coordinator._adhoc_until)
    assert float(mock_hass.states.get("number.min_soc").state) == 8
    assert coordinator.original_min_soc is None
    assert coordinator._pending_reset is False
    mock_hass.services.async_call.assert_awaited_with(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 8}, blocking=True)


@pytest.mark.parametrize("feedback", [None, "16", "unavailable", "nan"])
@pytest.mark.asyncio
async def test_unconfirmed_min_soc_restore_keeps_original_for_retry(mock_hass, feedback):
    coordinator = _make_coordinator(mock_hass)
    coordinator.original_min_soc = 8
    mock_hass.states.async_set("number.min_soc", "16")
    original_get = mock_hass.states.get.side_effect

    async def ignored_write(*args, **kwargs):
        mock_hass.states.get.side_effect = lambda entity: (
            None if feedback is None else SimpleNamespace(state=feedback)
        ) if entity == "number.min_soc" else original_get(entity)

    mock_hass.services.async_call = AsyncMock(side_effect=ignored_write)
    with patch(f"{COORDINATOR}.async_call_later"):
        assert await coordinator._reset_settings() is False
    assert coordinator.original_min_soc == 8
    assert coordinator._pending_reset is True
    assert coordinator.entry.options["runtime_state"]["original_min_soc"] == 8
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 8}, blocking=True)


def test_target_sensor_explains_why_the_battery_is_held(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator.data = {}
    coordinator._adhoc_reason = "evening_rescue"
    coordinator._adhoc_until = datetime(2026, 6, 1, 18)
    coordinator.original_min_soc = 8
    coordinator._pending_reset = True
    attributes = CalculatedSOCSensor(coordinator, coordinator.entry).extra_state_attributes
    assert attributes["adhoc_reason"] == "evening_rescue"
    assert attributes["adhoc_until"] == "2026-06-01T18:00:00"
    assert attributes["original_min_soc"] == 8
    assert attributes["pending_reset"] is True


@pytest.mark.parametrize("original", [3, 8, 11])
@pytest.mark.asyncio
async def test_unconfirmed_night_restore_then_reserve_returns_to_actual_original(mock_hass, original):
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set("number.min_soc", "16")
    mock_hass.states.async_set("sensor.soc", "16")
    coordinator.is_active = True
    coordinator.initial_calculated_soc = 16
    coordinator.original_min_soc = original
    complete_write = mock_hass.services.async_call.side_effect
    mock_hass.services.async_call = AsyncMock()  # First reset is acknowledged but ignored.
    morning = datetime(2026, 6, 1, 5, 1)

    with patch(f"{COORDINATOR}.async_call_later"), patch(
        f"{COORDINATOR}.dt_util.now", return_value=morning
    ):
        await coordinator._on_window_end(morning, scheduled=True)
        assert coordinator._pending_reset
        assert coordinator.original_min_soc == original
        await _rescue(coordinator, _short(), now=morning)

    assert coordinator.is_active
    assert coordinator._adhoc_reason == "evening_rescue"
    assert coordinator.original_min_soc == original
    # The new window owns cleanup now; it must retain the original capture.
    assert not coordinator._pending_reset
    mock_hass.services.async_call = AsyncMock(side_effect=complete_write)
    with patch(f"{COORDINATOR}.dt_util.now", return_value=coordinator._adhoc_until):
        await coordinator._on_window_end(coordinator._adhoc_until)
    assert float(mock_hass.states.get("number.min_soc").state) == original
    assert coordinator.original_min_soc is None
    assert not coordinator._pending_reset
    mock_hass.services.async_call.assert_awaited_with(
        "number", "set_value", {"entity_id": "number.min_soc", "value": original}, blocking=True
    )


@pytest.mark.parametrize("restart_after_release", [False, True], ids=["during_reserve", "missed_release"])
@pytest.mark.parametrize("original", [3, 8, 11])
@pytest.mark.asyncio
async def test_restart_preserves_reserve_floor_and_releases_at_deadline(mock_hass, original, restart_after_release):
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set("sensor.soc", "16")
    coordinator.original_min_soc = original
    await _rescue(coordinator, _short(), now=datetime(2026, 6, 1, 5, 1))
    deadline = coordinator._adhoc_until
    assert deadline is not None
    mock_hass.states.async_set("number.min_soc", "16")
    coordinator._persist_state()
    restart_at = deadline + timedelta(minutes=5) if restart_after_release else deadline - timedelta(hours=1)

    with patch(f"{COORDINATOR}.async_call_later"), patch(
        f"{COORDINATOR}.dt_util.now", return_value=restart_at
    ):
        restarted = InverterChargeNightCoordinator(mock_hass, coordinator.entry)
        restarted.entry.runtime_data = restarted
        restarted.async_request_refresh = AsyncMock()
        restarted._start_periodic_verification = AsyncMock()
        restarted._stop_periodic_verification = AsyncMock()
        assert restarted.original_min_soc == original
        await restarted._check_current_window()
        if not restart_after_release:
            assert restarted.is_active
            assert restarted.original_min_soc == original
            assert restarted._adhoc_until == deadline

    if not restart_after_release:
        with patch(f"{COORDINATOR}.dt_util.now", return_value=deadline):
            await restarted._on_window_end(deadline)
    assert float(mock_hass.states.get("number.min_soc").state) == original
    assert restarted.original_min_soc is None
    assert not restarted._pending_reset
    assert not restarted.is_active
    assert restarted._adhoc_until is None


@pytest.mark.parametrize("original", [3, 8, 11])
@pytest.mark.asyncio
async def test_restart_while_reset_is_pending_keeps_floor_until_real_feedback(mock_hass, original):
    coordinator = _make_coordinator(mock_hass)
    coordinator.original_min_soc = original
    coordinator.is_active = True
    mock_hass.states.async_set("number.min_soc", "16")
    complete_write = mock_hass.services.async_call.side_effect
    mock_hass.services.async_call = AsyncMock()
    morning = datetime(2026, 6, 1, 5, 1)

    with patch(f"{COORDINATOR}.async_call_later"), patch(
        f"{COORDINATOR}.dt_util.now", return_value=morning
    ):
        await coordinator._on_window_end(morning, scheduled=True)
        restarted = InverterChargeNightCoordinator(mock_hass, coordinator.entry)
        restarted.entry.runtime_data = restarted
        assert restarted._pending_reset
        assert restarted.original_min_soc == original
        # Another ignored attempt must still retain the original after restart.
        await restarted._retry_reset()
        assert restarted._pending_reset
        assert restarted.original_min_soc == original
        mock_hass.services.async_call = AsyncMock(side_effect=complete_write)
        await restarted._retry_reset()

    assert float(mock_hass.states.get("number.min_soc").state) == original
    assert not restarted._pending_reset
    assert restarted.original_min_soc is None
    assert restarted.entry.options["runtime_state"]["original_min_soc"] is None
    mock_hass.services.async_call.assert_awaited_with(
        "number", "set_value", {"entity_id": "number.min_soc", "value": original}, blocking=True
    )
