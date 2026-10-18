"""Night charging must hand the inverter's original floor to the next hold."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import pytest

from custom_components.inverter_charge_night.sensor import CalculatedSOCSensor
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
