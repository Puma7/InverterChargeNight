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

    await coordinator._stop_grid_charging()

    assert _ac_writes(mock_hass) == []
    assert coordinator._original_ac_charge_power == pytest.approx(22063.31)
    assert mock_hass.states.get(AC_LIMIT).state == "0"


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
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(AC_LIMIT, "22063.31", {"min": 0.0, "max": maximum, "unit_of_measurement": unit})
    mock_hass.services.async_call = _device(mock_hass, switch_obeys=True)
    coordinator = _make_coordinator(mock_hass)

    assert await coordinator._set_ac_charge_limit_w(requested_w) is True

    assert _ac_writes(mock_hass) == [written]
