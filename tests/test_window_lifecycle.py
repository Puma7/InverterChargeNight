"""Characterization tests for the night-charge window lifecycle.

A real ``InverterChargeNightCoordinator`` runs against the strict ``mock_hass``
fixture: every entity the coordinator reads is registered explicitly with
``hass.states.async_set`` and nothing else answers. Only two things are mocked:

* ``hass.services.async_call`` (from the fixture) - the tests assert on it.
* ``coordinator.async_request_refresh`` - the DataUpdateCoordinator debouncer
  needs a real event loop on ``hass``; the tests call ``_async_update_data``
  directly instead, as the plan prescribes.

Home Assistant's own ``async_track_state_change_event`` and the periodic
verification task run for real (against ``hass.bus`` / ``hass.loop`` mocks and
a real asyncio task respectively), so the listener handles asserted here are
genuine unsubscribe callables.

These tests document today's behaviour. Plans 004 and 005 change parts of it
and are expected to adapt the affected tests; the ``xfail(strict=True)`` test
documents finding F2 and must be turned into a passing test by plan 004.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest
import pytest_asyncio

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.calculation import calculate_required_soc
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)
from custom_components.inverter_charge_night.number import MinSOCOverrideNumber

MIN_SOC = "number.min_soc"
GRID = "switch.grid"
BATTERY = "sensor.soc"
PV = "sensor.pv"

CAPACITY_KWH = 10.0
MARGIN_PCT = 10.0
USER_MIN = 8.0
USER_MAX = 100.0
DEFAULT_MIN = 8.0
FORECAST_KWH = 5.0

CONFIG = {
    CONF_KOSTAL_MIN_SOC_ENTITY: MIN_SOC,
    CONF_KOSTAL_GRID_CHARGE_SWITCH: GRID,
    CONF_BATTERY_SOC_ENTITY: BATTERY,
    CONF_PV_FORECAST_ENTITY: PV,
    CONF_BATTERY_CAPACITY: CAPACITY_KWH,
    CONF_FORECAST_ERROR_MARGIN: MARGIN_PCT,
    CONF_USER_MIN_SOC: USER_MIN,
    CONF_USER_MAX_SOC: USER_MAX,
    CONF_DEFAULT_MIN_SOC: DEFAULT_MIN,
    CONF_START_TIME: "00:00",
    CONF_END_TIME: "05:59",
    CONF_COMMAND_DELAY: 0,
}

WINDOW_START = datetime(2026, 1, 15, 0, 0)
WINDOW_END = datetime(2026, 1, 15, 5, 59)

# 5 kWh forecast + 10 % margin = 5.5 kWh of space needed in a 10 kWh battery -> 45 %
EXPECTED_TARGET = calculate_required_soc(
    FORECAST_KWH, CAPACITY_KWH, MARGIN_PCT, USER_MIN, USER_MAX
)


def _make_coordinator(hass) -> InverterChargeNightCoordinator:
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = CONFIG
    entry.options = {}
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _register_inverter(hass, *, min_soc="8", grid="off", battery="40", pv="5"):
    hass.states.async_set(MIN_SOC, min_soc)
    hass.states.async_set(GRID, grid)
    hass.states.async_set(BATTERY, battery)
    hass.states.async_set(PV, pv, {"unit_of_measurement": "kWh"})


@pytest_asyncio.fixture
async def coordinator(mock_hass):
    """Real coordinator with inverter entities registered and a real task runner."""
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    _register_inverter(mock_hass)
    coordinator = _make_coordinator(mock_hass)
    yield coordinator
    # Do not leave the periodic verification task pending after the test
    coordinator._stop_periodic_verification()
    await asyncio.sleep(0)


async def _start_and_apply(mock_hass, coordinator) -> float:
    """Start the window, run one update and mirror the inverter's response."""
    await coordinator._on_window_start(WINDOW_START)
    await coordinator._async_update_data()
    target = coordinator.initial_calculated_soc
    assert target is not None
    # The inverter has applied our two commands
    mock_hass.states.async_set(MIN_SOC, str(target))
    mock_hass.states.async_set(GRID, "on")
    mock_hass.services.async_call.reset_mock()
    return target


# 1. Window start -----------------------------------------------------------


@pytest.mark.asyncio
async def test_window_start_calculates_target_and_arms_listeners(mock_hass, coordinator):
    await coordinator._on_window_start(WINDOW_START)

    assert coordinator.is_active is True
    assert coordinator.target_reached is False
    assert EXPECTED_TARGET == 45.0
    assert coordinator.initial_calculated_soc == EXPECTED_TARGET
    assert coordinator.minimum_calculated_soc == EXPECTED_TARGET
    assert coordinator.calculated_soc == EXPECTED_TARGET

    # Listeners and the verification task are armed
    assert coordinator._battery_soc_listener is not None
    assert coordinator._inverter_min_soc_listener is not None
    assert coordinator._verification_task is not None
    assert not coordinator._verification_task.done()
    mock_hass.async_create_background_task.assert_called_once()

    # Window start itself only schedules the refresh; the refresh does the work
    coordinator.async_request_refresh.assert_awaited_once()
    mock_hass.services.async_call.assert_not_awaited()


# 2. Update inside the window ------------------------------------------------


@pytest.mark.asyncio
async def test_update_in_window_sets_min_soc_then_turns_grid_charge_on(
    mock_hass, coordinator
):
    await coordinator._on_window_start(WINDOW_START)

    data = await coordinator._async_update_data()

    # Order matters: the inverter must know the new min SOC before charging starts
    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": EXPECTED_TARGET}),
        call("switch", "turn_on", {"entity_id": GRID}),
    ]
    assert coordinator.original_min_soc == DEFAULT_MIN
    assert coordinator._last_soc_set == EXPECTED_TARGET
    assert data == {
        "calculated_soc": EXPECTED_TARGET,
        "is_active": True,
        "target_reached": False,
        "current_soc": 40.0,
        "operation_mode": "night_charge",
        "skip_next": False,
    }


# 3. Target reached ----------------------------------------------------------


@pytest.mark.asyncio
async def test_update_stops_grid_charge_once_battery_reaches_target(
    mock_hass, coordinator
):
    target = await _start_and_apply(mock_hass, coordinator)
    mock_hass.states.async_set(BATTERY, str(target))

    data = await coordinator._async_update_data()

    mock_hass.services.async_call.assert_awaited_once_with(
        "switch", "turn_off", {"entity_id": GRID}
    )
    assert coordinator.target_reached is True
    assert data["target_reached"] is True
    assert data["current_soc"] == target
    # The window stays active until its end trigger
    assert coordinator.is_active is True


# 4. Window end --------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_end_restores_original_min_soc_and_clears_state(
    mock_hass, coordinator
):
    await _start_and_apply(mock_hass, coordinator)

    await coordinator._on_window_end(WINDOW_END)

    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": DEFAULT_MIN}),
        call("switch", "turn_off", {"entity_id": GRID}),
    ]
    assert coordinator.is_active is False
    assert coordinator.target_reached is False
    assert coordinator.original_min_soc is None
    assert coordinator.initial_calculated_soc is None
    assert coordinator.minimum_calculated_soc is None
    assert coordinator.override_soc is None
    assert coordinator._last_soc_set is None
    assert coordinator._battery_soc_listener is None
    assert coordinator._inverter_min_soc_listener is None
    assert coordinator._verification_task is None
    # start + end each request a refresh
    assert coordinator.async_request_refresh.await_count == 2


# 5. Restart inside the window -----------------------------------------------


@pytest.mark.asyncio
async def test_restart_in_window_adopts_inverter_min_soc_as_target(
    mock_hass, coordinator
):
    """Documents today's restart recovery; plan 005 changes it (finding F4).

    A previous run left our night target (65 %) on the inverter, then HA
    restarted. The fresh coordinator treats the inverter value as the target
    instead of recalculating from the forecast (which would give 45 %).
    """
    mock_hass.states.async_set(MIN_SOC, "65")

    await coordinator._on_window_start(WINDOW_START)

    assert coordinator.initial_calculated_soc == 65.0
    assert coordinator.minimum_calculated_soc == 65.0
    assert coordinator.calculated_soc == 65.0

    await coordinator._async_update_data()

    # The inverter is already at the adopted target, so only charging is switched on
    mock_hass.services.async_call.assert_awaited_once_with(
        "switch", "turn_on", {"entity_id": GRID}
    )
    # 65 % is more than 10 points above the default, so the default is kept as
    # the "original" value to restore at window end
    assert coordinator.original_min_soc == DEFAULT_MIN


@pytest.mark.asyncio
async def test_restart_in_window_with_low_target_stores_it_as_original(
    mock_hass, coordinator
):
    """Documents finding F4 (plan 005): a low night target survives as "original".

    Restart recovery adopts any inverter value more than 1 point away from the
    default, but the sanity check in ``_control_kostal`` only rejects values more
    than 10 points above it. With the battery below that target, a 12 % night
    target therefore becomes both the target and the value restored at window
    end. (If the battery were already above 12 %, the update would return early
    as "target reached" and never capture an original value at all.)
    """
    mock_hass.states.async_set(MIN_SOC, "12")
    mock_hass.states.async_set(BATTERY, "10")

    await coordinator._on_window_start(WINDOW_START)
    assert coordinator.initial_calculated_soc == 12.0

    await coordinator._async_update_data()
    assert coordinator.original_min_soc == 12.0
    # The inverter already shows the adopted target, so only charging is switched on
    mock_hass.services.async_call.assert_awaited_once_with(
        "switch", "turn_on", {"entity_id": GRID}
    )

    mock_hass.states.async_set(GRID, "on")
    mock_hass.services.async_call.reset_mock()
    await coordinator._on_window_end(WINDOW_END)

    # The "original" restored at window end is the leftover night target, not 8 %
    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": 12.0}),
        call("switch", "turn_off", {"entity_id": GRID}),
    ]


# 6. Override upwards (finding F2) -------------------------------------------


@pytest.mark.xfail(strict=True, reason="plans/004")
@pytest.mark.asyncio
async def test_override_above_target_keeps_grid_charging(mock_hass, coordinator):
    """Raising the override from 45 % to 70 % with the battery at 50 % must keep charging.

    Today ``minimum_calculated_soc`` is only ever lowered, but it is what the
    "target reached" check compares against. The override is written to the
    inverter, and the very next update sees 50 % >= 45 % and turns grid
    charging off again (finding F2). Plan 004 fixes this and un-xfails the test.
    """
    target = await _start_and_apply(mock_hass, coordinator)
    assert target == 45.0
    mock_hass.states.async_set(BATTERY, "50")

    number = MinSOCOverrideNumber(coordinator, coordinator.entry)
    number.async_write_ha_state = MagicMock()
    await number.async_set_native_value(70.0)

    assert coordinator.override_soc == 70.0
    # The new target reaches the inverter immediately ...
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": MIN_SOC, "value": 70.0}
    )
    mock_hass.states.async_set(MIN_SOC, "70")

    data = await coordinator._async_update_data()

    # ... and the next update must not stop charging: 50 % is below the 70 % override
    assert (
        call("switch", "turn_off", {"entity_id": GRID})
        not in mock_hass.services.async_call.await_args_list
    )
    assert coordinator.target_reached is False
    assert data["target_reached"] is False
