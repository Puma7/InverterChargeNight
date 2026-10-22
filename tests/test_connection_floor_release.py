"""When the house connection needs throttling and the inverter cannot be throttled.

Pascal, 22 October 2026: safety first. A raised min SOC makes a Kostal charge
from the grid at whatever limit it holds - 22 kW of its own. When the
connection needs a lower charge limit and the write fails, the inverter does
not hold it, or it keeps reading higher, the min SOC goes down to the current
SOC, rounded down, for the rest of the window: the battery stops charging and
does not discharge either, which the user's own 8 % would let it.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.inverter_charge_night.const import CONF_CHARGE_POWER_ENTITY, CONF_RUNTIME_STATE
from custom_components.inverter_charge_night.coordinator import AC_LIMIT_MAX_REWRITES
from tests.test_electrical_safety import (
    AC_LIMIT,
    BATTERY,
    GRID,
    MIN_SOC,
    WINDOW_START,
    _inside_window,  # noqa: F401
    _make,
    _register,
    _service_calls,
)

LOADED = "33000"  # 34776 W budget, 500 W headroom: about 1276 W left for the battery


def _min_soc_writes(hass) -> list[float]:
    return [
        c.args[2]["value"]
        for c in hass.services.async_call.await_args_list
        if c.args[0] == "number" and c.args[2].get("entity_id") == MIN_SOC
    ]


def _ignore_charge_limit(hass):
    """The charge limit write fails; everything else completes as usual."""
    complete = hass.services.async_call.side_effect

    async def call(domain, service, data, **kwargs):
        if domain == "number" and data.get("entity_id") == AC_LIMIT:
            raise RuntimeError("Readback is unconfirmed for bat_max_charge_limit")
        await complete(domain, service, data, **kwargs)

    hass.services.async_call = AsyncMock(side_effect=call)


async def _charging_night(hass, *, battery="40.7", inverter_limit="22000"):
    """A window charging under a 60 % floor, the inverter at its own limit."""
    _register(hass, battery=battery, grid_import=LOADED)
    coordinator = _make(hass)
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 60.0
    hass.states.async_set(MIN_SOC, "60")
    hass.states.async_set(AC_LIMIT, inverter_limit, {"unit_of_measurement": "W"})
    coordinator._planned_setpoint_written_w = 6000.0
    coordinator.planned_charge_power_w = 6000.0
    coordinator._grid_plan_request_w = 6000.0
    _ignore_charge_limit(hass)
    return coordinator


@pytest.mark.asyncio
async def test_a_throttle_that_fails_takes_the_floor_down_to_the_battery(mock_hass, caplog):
    caplog.set_level(logging.ERROR)
    coordinator = await _charging_night(mock_hass)

    await coordinator._react_to_grid_import()

    assert _min_soc_writes(mock_hass) == [40]
    assert coordinator._floor_released_soc == 40
    assert coordinator.inverter_floor_soc() == 40  # for every reader of the floor
    assert mock_hass.states.get(MIN_SOC).state == "40"
    assert "taking the min SOC down to 40%" in caplog.text
    assert coordinator.entry.options[CONF_RUNTIME_STATE]["floor_released_soc"] == 40
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_an_inverter_already_below_the_need_keeps_its_floor(mock_hass):
    """The write failed, but the Kostal holds 51 W of its own: no danger."""
    coordinator = await _charging_night(mock_hass, inverter_limit="51")

    await coordinator._react_to_grid_import()

    assert _min_soc_writes(mock_hass) == []
    assert coordinator._floor_released_soc is None
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_without_a_need_to_throttle_a_failed_write_keeps_the_floor(mock_hass):
    coordinator = await _charging_night(mock_hass)
    coordinator._grid_limited = False

    await coordinator._protect_connection_by_floor(1000.0, "the write failed")

    assert _min_soc_writes(mock_hass) == []
    assert coordinator._floor_released_soc is None
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_the_floor_stays_down_for_the_rest_of_the_window(mock_hass):
    coordinator = await _charging_night(mock_hass)
    await coordinator._react_to_grid_import()
    mock_hass.services.async_call.reset_mock()

    await coordinator._async_update_data()
    await coordinator._verify_and_restore_min_soc()

    assert all(value <= 40 for value in _min_soc_writes(mock_hass))
    assert ("switch", "turn_on", GRID) not in _service_calls(mock_hass)
    assert float(mock_hass.states.get(MIN_SOC).state) == 40
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_floor_raised_by_someone_else_goes_back_down(mock_hass):
    coordinator = await _charging_night(mock_hass)
    await coordinator._react_to_grid_import()
    mock_hass.states.async_set(MIN_SOC, "50")
    mock_hass.services.async_call.reset_mock()

    await coordinator._verify_and_restore_min_soc()

    assert _min_soc_writes(mock_hass) == [40]
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_poll_waiting_to_raise_the_floor_does_not_undo_it(mock_hass):
    """Taken down while a poll waited for the min SOC lock: that poll writes
    nothing higher once it gets the lock."""
    _register(mock_hass, battery="40.7")
    coordinator = _make(mock_hass)
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 60.0
    mock_hass.services.async_call.reset_mock()

    await coordinator._min_soc_write_lock.acquire()
    poll = asyncio.ensure_future(coordinator._async_update_data())
    for _ in range(50):
        await asyncio.sleep(0)
    assert not poll.done()
    coordinator._floor_released_soc = 40.0
    coordinator._min_soc_write_lock.release()
    await poll

    assert 60 not in _min_soc_writes(mock_hass)
    assert ("switch", "turn_on", GRID) not in _service_calls(mock_hass)
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_limit_the_inverter_does_not_hold_takes_the_floor_down(mock_hass):
    coordinator = await _charging_night(mock_hass)
    coordinator._planned_setpoint_written_w = 1000.0
    coordinator._ac_limit_rewrites = AC_LIMIT_MAX_REWRITES
    coordinator._grid_limited = True

    with patch("custom_components.inverter_charge_night.coordinator.ir.async_create_issue") as issue:
        await coordinator._verify_ac_charge_limit()

    issue.assert_called_once()
    assert coordinator._ac_limit_not_held
    assert coordinator._floor_released_soc == 40
    assert _min_soc_writes(mock_hass) == [40]
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_battery_below_the_user_minimum_falls_back_to_the_user_minimum(mock_hass):
    """5 % cannot be written within the user's 8 %. Not the captured original
    either: it can lie well above the battery (Codex on #41)."""
    coordinator = await _charging_night(mock_hass, battery="5")
    coordinator.original_min_soc = 50.0

    await coordinator._react_to_grid_import()

    assert _min_soc_writes(mock_hass) == [8.0]
    assert coordinator._floor_released_soc == 8.0
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_restart_keeps_it_and_the_next_window_clears_it(mock_hass):
    coordinator = await _charging_night(mock_hass)
    await coordinator._react_to_grid_import()
    await coordinator._stop_periodic_verification()

    restarted = _make(mock_hass, options=coordinator.entry.options)
    restarted._restore_state()
    assert restarted._floor_released_soc == 40

    restarted._clear_ac_limit_not_held()  # a new window
    assert restarted._floor_released_soc is None


@pytest.mark.asyncio
async def test_no_grid_charge_switch_once_the_floor_is_down(mock_hass):
    coordinator = await _charging_night(mock_hass)
    await coordinator._react_to_grid_import()
    mock_hass.states.async_set(GRID, "on")  # whoever switched it, it goes off
    mock_hass.services.async_call.reset_mock()

    assert await coordinator._grid_charge_activation_safe(60.0) is False

    assert _service_calls(mock_hass) == [("switch", "turn_off", GRID)]
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_after_the_target_the_floor_comes_down_the_same_way(mock_hass):
    """Charging is over but the window is not: the held limit fails the same way."""
    coordinator = await _charging_night(mock_hass)
    coordinator.target_reached = True

    await coordinator._react_to_grid_import()

    assert coordinator._floor_released_soc == 40
    assert _min_soc_writes(mock_hass) == [40]
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_an_unreadable_battery_falls_back_to_the_user_minimum(mock_hass):
    coordinator = await _charging_night(mock_hass, battery="unavailable")
    coordinator.original_min_soc = 50.0

    await coordinator._react_to_grid_import()

    assert _min_soc_writes(mock_hass) == [8.0]
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_raised_floor_landing_after_the_release_is_put_right(mock_hass):
    """Codex on #41: a poll holding the min SOC lock was writing 60 % when the
    connection failed; its write lands after the release."""
    coordinator = await _charging_night(mock_hass)
    await coordinator._min_soc_write_lock.acquire()

    await coordinator._react_to_grid_import()
    assert _min_soc_writes(mock_hass) == [40]
    mock_hass.states.async_set(MIN_SOC, "60")  # the poll's late write
    coordinator._min_soc_write_lock.release()
    for _ in range(10):
        await asyncio.sleep(0)

    assert _min_soc_writes(mock_hass) == [40, 40]
    assert mock_hass.states.get(MIN_SOC).state == "40"
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_an_unreadable_charge_limit_takes_the_floor_down(mock_hass):
    """Codex on #41: no feedback while the connection needs throttling."""
    coordinator = await _charging_night(mock_hass)
    coordinator._grid_limited = True
    mock_hass.states.async_set(AC_LIMIT, "unavailable", {"unit_of_measurement": "W"})

    await coordinator._verify_ac_charge_limit()

    assert coordinator._floor_released_soc == 40
    assert _min_soc_writes(mock_hass) == [40]
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_replacing_the_controls_clears_the_release(mock_hass):
    """Codex on #41: the release is evidence about the old controls."""
    coordinator = await _charging_night(mock_hass)
    await coordinator._react_to_grid_import()
    new_config = {**coordinator.config, CONF_CHARGE_POWER_ENTITY: "number.new_limit"}

    await coordinator.async_release_changed_entities(new_config)

    assert coordinator._floor_released_soc is None
    assert coordinator.entry.options[CONF_RUNTIME_STATE]["floor_released_soc"] is None
    await coordinator._stop_periodic_verification()
