"""3.14.1: what the night of 25/26 October 2026 logged about itself.

Two debug lines said something that was not so. The min SOC listener took the
35 % the inverter held for a deviation from the 35.7 % target, though 35 % is
exactly what was written: the inverter takes whole percent. And "the battery
cannot draw from the grid" stood 6905 times in six hours, twice per poll.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_grid_limit import (
    BATTERY, LIMITED_CONFIG, MIN_SOC, NOW, TRACK, _make_coordinator, _register,
)

TARGET = 35.7
IDLE_LINE = "House connection limit: the battery cannot draw from the grid - not writing"


def _listening(mock_hass: Any, *, soc: str, reached: bool) -> tuple[Any, Any]:
    _register(mock_hass)
    mock_hass.states.async_set(MIN_SOC, "35", {"min": 0, "max": 100, "step": 1})
    mock_hass.states.async_set(BATTERY, soc)
    co = _make_coordinator(mock_hass, LIMITED_CONFIG)
    co.is_active = True
    co.is_enabled = True
    co.target_reached = reached
    co.current_target_soc = MagicMock(return_value=TARGET)  # type: ignore[method-assign]
    co._verify_and_restore_min_soc = AsyncMock()  # type: ignore[method-assign]
    captured: dict[str, Any] = {}

    def _capture(hass: Any, entity_id: str, callback: Any) -> MagicMock:
        captured["callback"] = callback
        return MagicMock()

    with patch(TRACK, side_effect=_capture):
        co._setup_inverter_min_soc_listener()
    return co, captured["callback"]


def _event(value: str) -> MagicMock:
    event = MagicMock()
    event.data = {"new_state": MagicMock(state=value)}
    return event


@pytest.mark.asyncio
async def test_the_written_step_is_no_deviation(mock_hass):
    """68 % in the battery, target 35.7 %: the inverter holds the 35 % it was given."""
    co, handler = _listening(mock_hass, soc="68", reached=True)

    await handler(_event("35"))
    co._verify_and_restore_min_soc.assert_not_awaited()
    # Charged with the step rounded up: once the target is met that one step
    # stays, as the verification lets it (plan 017)
    await handler(_event("36"))
    co._verify_and_restore_min_soc.assert_not_awaited()

    await handler(_event("30"))  # somebody changed it
    co._verify_and_restore_min_soc.assert_awaited_once()


@pytest.mark.asyncio
async def test_while_charging_the_floor_rounds_up(mock_hass):
    """Below the target the floor is the next step up: 35 % is short of the 36 % written."""
    co, handler = _listening(mock_hass, soc="20", reached=False)

    await handler(_event("35"))
    co._verify_and_restore_min_soc.assert_awaited_once()

    co._verify_and_restore_min_soc.reset_mock()
    await handler(_event("36"))
    co._verify_and_restore_min_soc.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_idle_battery_is_logged_once_per_change(mock_hass, caplog):
    _register(mock_hass)
    co = _make_coordinator(mock_hass, LIMITED_CONFIG)
    co.is_active = True
    idle = {"value": True}
    co._battery_idle_on_grid = lambda: idle["value"]  # type: ignore[method-assign]
    co._grid_limited_setpoint = lambda reference: reference  # type: ignore[method-assign]
    caplog.set_level(logging.DEBUG, logger="custom_components.inverter_charge_night")

    for _ in range(5):
        await co._enforce_grid_limit_now()
    assert caplog.text.count(IDLE_LINE) == 1

    idle["value"] = False
    await co._enforce_grid_limit_now()
    idle["value"] = True
    await co._enforce_grid_limit_now()
    await co._enforce_grid_limit_now()
    assert caplog.text.count(IDLE_LINE) == 2


@pytest.mark.asyncio
async def test_a_new_window_says_it_again(mock_hass):
    from tests import test_discharge_block as night

    night._register(mock_hass, battery="70")
    made = night._make(mock_hass)
    made._grid_idle_noted = True
    try:
        with patch(NOW, return_value=night.INSIDE_WINDOW):
            await made._on_window_start(night.WINDOW_START)
        assert made._grid_idle_noted is False
    finally:
        await made._stop_periodic_verification()
