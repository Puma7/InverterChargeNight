"""Charge ceilings are handed back on reaching the target only after a confirmed stop.

CodeRabbit on #35: without a house connection limit, ``_stop_grid_charging``
restored the AC charge limit even when ``switch.turn_off`` failed or the switch
never reported off. Since 3.10.6 a captured ceiling above the entity maximum is
restored as that maximum instead of being refused, so a failed stop could turn
the protective 0 W into 12.5 kW while grid charging went on.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night.const import (
    CONF_CHARGE_POWER_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_RUNTIME_STATE,
)
from custom_components.inverter_charge_night.coordinator import InverterChargeNightCoordinator

GRID = "switch.kostal_g3_grid_charge"
AC_LIMIT = "number.kostal_g3_battery_max_charge_limit_modbus"
CONFIG = {CONF_GRID_CHARGE_SWITCH: GRID, CONF_CHARGE_POWER_ENTITY: AC_LIMIT}


def _make_coordinator(hass: Any) -> InverterChargeNightCoordinator:
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = CONFIG
    entry.options = {CONF_RUNTIME_STATE: {}}
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    coordinator.is_active = True
    coordinator._original_ac_charge_power = 22063.31  # the field capture
    coordinator._planned_setpoint_written_w = 0.0
    return coordinator


def _ac_writes(hass: Any) -> list[Any]:
    return [
        c.args[2]["value"]
        for c in hass.services.async_call.await_args_list
        if c.args[1] == "set_value" and c.args[2].get("entity_id") == AC_LIMIT
    ]


def _device(hass: Any, *, switch_obeys: bool, switch_raises: bool = False) -> AsyncMock:
    async def call(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        state = hass.states.get(data.get("entity_id"))
        if domain == "switch":
            if switch_raises:
                raise RuntimeError("inverter refused")
            if switch_obeys and state is not None:
                state.state = "off"
        elif service == "set_value" and state is not None:
            state.state = str(data["value"])

    return AsyncMock(side_effect=call)


@pytest.mark.parametrize("failure", ["no_off_feedback", "turn_off_raises", "switch_unavailable"])
@pytest.mark.asyncio
async def test_an_unconfirmed_stop_keeps_the_protective_ceiling(mock_hass, failure):
    mock_hass.states.async_set(GRID, "unavailable" if failure == "switch_unavailable" else "on")
    mock_hass.states.async_set(AC_LIMIT, "0", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"})
    mock_hass.services.async_call = _device(
        mock_hass, switch_obeys=False, switch_raises=failure == "turn_off_raises"
    )
    coordinator = _make_coordinator(mock_hass)

    assert await coordinator._stop_grid_charging() is False

    # The stop itself was attempted wherever the switch could take it (Codex on #37)
    stop_calls = [c for c in mock_hass.services.async_call.await_args_list
                  if c.args[:2] == ("switch", "turn_off")]
    if failure == "switch_unavailable":
        assert stop_calls == []
    else:
        assert [c.args[2] for c in stop_calls] == [{"entity_id": GRID}]
    assert _ac_writes(mock_hass) == []
    assert coordinator._original_ac_charge_power == pytest.approx(22063.31)
    assert mock_hass.states.get(AC_LIMIT).state == "0"
    assert coordinator._grid_stop_pending is True


@pytest.mark.asyncio
async def test_a_confirmed_stop_hands_the_ceiling_back(mock_hass):
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(AC_LIMIT, "0", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"})
    mock_hass.services.async_call = _device(mock_hass, switch_obeys=True)
    coordinator = _make_coordinator(mock_hass)

    await coordinator._stop_grid_charging()

    assert mock_hass.states.get(GRID).state == "off"
    assert _ac_writes(mock_hass) == [12540.0]
    assert coordinator._original_ac_charge_power is None


@pytest.mark.parametrize(("unit", "maximum", "requested_w", "written"), [
    ("W", 12540.0, 18820, 12540.0),
    ("kW", 12.54, 18820, 12.54),
    (None, 12.54, 18820, 12.54),  # unitless, inferred kW (Codex on #37)
    ("W", 12540.0, 6000, 6000.0),
])
@pytest.mark.asyncio
async def test_a_protective_limit_above_the_control_maximum_is_written_as_that_maximum(
    mock_hass, unit, maximum, requested_w, written
):
    """Found by the morning-silence lab campaign.

    After the Kostal falls back to its own 22 kW, a connection limit of, say,
    18820 W was written as is - above the 12540 W the KORE control accepts.
    Home Assistant refused it and grid charging stayed paused. The control's
    maximum is lower than the limit asked for, so it protects at least as well.
    """
    attributes: dict[str, Any] = {"min": 0.0, "max": maximum}
    if unit is not None:
        attributes["unit_of_measurement"] = unit
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(AC_LIMIT, "22063.31", attributes)
    mock_hass.services.async_call = _device(mock_hass, switch_obeys=True)
    coordinator = _make_coordinator(mock_hass)

    assert await coordinator._set_ac_charge_limit_w(requested_w) is True

    assert _ac_writes(mock_hass) == [written]
    # What is recorded as written is what was written, in W (Codex on #37)
    assert coordinator._planned_setpoint_written_w == pytest.approx(min(requested_w, 12540.0))


@pytest.mark.asyncio
async def test_no_efficiency_trial_runs_above_the_control_maximum(mock_hass):
    """Codex on #37: the control would write its maximum, the sample be filed under more."""
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(AC_LIMIT, "0", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"})
    mock_hass.services.async_call = _device(mock_hass, switch_obeys=True)
    coordinator = _make_coordinator(mock_hass)
    coordinator._set_auto_wait_reason = MagicMock()

    await coordinator._start_auto_test(18820)

    assert _ac_writes(mock_hass) == []
    assert coordinator._auto_test_active is False
    coordinator._set_auto_wait_reason.assert_called_once_with("charge_limit_above_control_maximum")


def _turn_offs(hass: Any) -> int:
    return sum(1 for c in hass.services.async_call.await_args_list if c.args[:2] == ("switch", "turn_off"))


@pytest.mark.asyncio
async def test_a_late_off_is_picked_up_by_the_next_poll_without_another_command(mock_hass):
    """Codex and CodeRabbit on #37: the callers set target_reached, so nothing retried."""
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(AC_LIMIT, "0", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"})
    mock_hass.services.async_call = _device(mock_hass, switch_obeys=False)
    coordinator = _make_coordinator(mock_hass)
    assert await coordinator._stop_grid_charging() is False

    mock_hass.states.get(GRID).state = "off"  # the switch reports off after all
    await coordinator._retry_grid_stop()

    assert _turn_offs(mock_hass) == 1
    assert _ac_writes(mock_hass) == [12540.0]
    assert coordinator._original_ac_charge_power is None
    assert coordinator._grid_stop_pending is False


@pytest.mark.asyncio
async def test_a_switch_that_never_reports_off_gets_a_bounded_number_of_stops(mock_hass):
    from custom_components.inverter_charge_night.coordinator import GRID_STOP_MAX_RETRIES

    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(AC_LIMIT, "0", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"})
    mock_hass.services.async_call = _device(mock_hass, switch_obeys=False)
    coordinator = _make_coordinator(mock_hass)
    await coordinator._stop_grid_charging()

    for _ in range(10):  # ten polls, 15 minutes apart
        await coordinator._retry_grid_stop()

    assert _turn_offs(mock_hass) == 1 + GRID_STOP_MAX_RETRIES
    assert _ac_writes(mock_hass) == []
    assert coordinator._original_ac_charge_power == pytest.approx(22063.31)


@pytest.mark.asyncio
async def test_the_connection_limit_records_the_value_the_control_took(mock_hass):
    """Codex on #37: the reference for the next reading is what stands there, not the request."""
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(AC_LIMIT, "22063.31", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"})
    mock_hass.services.async_call = _device(mock_hass, switch_obeys=True)
    coordinator = _make_coordinator(mock_hass)
    coordinator._planned_setpoint_written_w = 22063.31
    coordinator.config = {**CONFIG, "grid_import_entity": "sensor.grid_import"}
    coordinator._grid_budget = MagicMock(return_value=34776.0)
    coordinator._grid_limited_setpoint = MagicMock(return_value=18820.0)
    coordinator._grid_write_is_debounced = MagicMock(return_value=False)
    coordinator._battery_idle_on_grid = MagicMock(return_value=False)

    await coordinator._enforce_grid_limit_now()

    assert _ac_writes(mock_hass) == [12540.0]
    assert coordinator._planned_setpoint_written_w == 12540.0
    assert coordinator.planned_charge_power_w == 12540.0


@pytest.mark.asyncio
async def test_the_efficiency_limiter_records_the_value_the_control_took(mock_hass):
    """Codex on #37: the limiter overwrote the setter's record with its request."""
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(AC_LIMIT, "22063.31", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"})
    mock_hass.services.async_call = _device(mock_hass, switch_obeys=True)
    coordinator = _make_coordinator(mock_hass)
    coordinator._planned_setpoint_written_w = 22063.31
    coordinator._grid_limit_configured = MagicMock(return_value=True)
    coordinator._grid_limited_setpoint = MagicMock(return_value=18820.0)
    coordinator._grid_write_is_debounced = MagicMock(return_value=False)
    coordinator._battery_idle_on_grid = MagicMock(return_value=False)

    await coordinator._limit_the_efficiency_finder()

    assert _ac_writes(mock_hass) == [12540.0]
    assert coordinator._planned_setpoint_written_w == 12540.0
