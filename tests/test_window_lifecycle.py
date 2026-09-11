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

Plan 004 made ``current_target_soc()`` the single target (finding F2) and added
a clock-based window check to the polling update, so ``dt_util.now`` is pinned
inside the window for every test. Plan 005 persists the window state in
``entry.options`` (findings F4/F5) and awaits the verification task before the
reset (finding F11); section 5 and 8 cover that.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call, patch

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
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_OPERATION_MODE,
    CONF_PV_FORECAST_ENTITY,
    CONF_RUNTIME_STATE,
    CONF_START_TIME,
    CONF_UPDATE_INTERVAL,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    MODE_MORNING_DISCHARGE,
)
from custom_components.inverter_charge_night.number import MinSOCOverrideNumber

MIN_SOC = "number.min_soc"
GRID = "switch.grid"
BATTERY = "sensor.soc"
PV = "sensor.pv"
FORCE = "switch.force_discharge"

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
INSIDE_WINDOW = datetime(2026, 1, 15, 2, 0)

# 5 kWh forecast + 10 % margin = 5.5 kWh of space needed in a 10 kWh battery -> 45 %
EXPECTED_TARGET = calculate_required_soc(
    FORECAST_KWH, CAPACITY_KWH, MARGIN_PCT, USER_MIN, USER_MAX
)


@pytest.fixture(autouse=True)
def _inside_window():
    """Pin the clock inside the window: the polling update ends a window it finds itself outside of."""
    with patch(
        "custom_components.inverter_charge_night.dt_util.now", return_value=INSIDE_WINDOW
    ):
        yield


def _make_coordinator(hass, config=CONFIG, options=None) -> InverterChargeNightCoordinator:
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = config
    entry.options = options or {}
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
    await coordinator._stop_periodic_verification()


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


def _persisted_window(target: float) -> dict:
    """The options a previous run persisted while its window was active."""
    return {
        CONF_RUNTIME_STATE: {
            "is_enabled": True,
            "skip_next_until": None,
            "override_soc": None,
            "original_min_soc": DEFAULT_MIN,
            "initial_calculated_soc": target,
            "original_ac_charge_power": None,
            "pending_reset": False,
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("night_target", "battery"),
    [
        (65.0, "40"),  # target above the forecast plan (45 %)
        (12.0, "10"),  # low target: before plan 005 it became the "original" (finding F4)
    ],
)
async def test_restart_in_window_continues_persisted_target_and_restores_original(
    mock_hass, night_target, battery
):
    """Finding F4 (plan 005): after a restart the persisted target and original are used.

    A previous run persisted original 8 % and its night target, then HA
    restarted with that target still on the inverter. The fresh coordinator
    neither adopts the live value as "original" nor recalculates the target
    from the forecast; at window end the inverter goes back to 8 %.
    """
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    _register_inverter(mock_hass, min_soc=str(night_target), battery=battery)
    coordinator = _make_coordinator(mock_hass, options=_persisted_window(night_target))
    assert coordinator.original_min_soc == DEFAULT_MIN
    assert coordinator.initial_calculated_soc == night_target
    try:
        await coordinator._on_window_start(WINDOW_START)

        assert coordinator.initial_calculated_soc == night_target
        assert coordinator.minimum_calculated_soc == night_target
        assert coordinator.calculated_soc == night_target

        await coordinator._async_update_data()

        # The inverter is already at the target, so only charging is switched on
        mock_hass.services.async_call.assert_awaited_once_with(
            "switch", "turn_on", {"entity_id": GRID}
        )
        assert coordinator.original_min_soc == DEFAULT_MIN

        mock_hass.states.async_set(GRID, "on")
        mock_hass.services.async_call.reset_mock()
        await coordinator._on_window_end(WINDOW_END)

        assert mock_hass.services.async_call.await_args_list == [
            call("number", "set_value", {"entity_id": MIN_SOC, "value": DEFAULT_MIN}),
            call("switch", "turn_off", {"entity_id": GRID}),
        ]
        assert coordinator._pending_reset is False
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_restart_in_window_without_persisted_state_recalculates(mock_hass, coordinator):
    """Without persisted state the live value is neither adopted nor waited for."""
    mock_hass.states.async_set(MIN_SOC, "65")

    await coordinator._on_window_start(WINDOW_START)

    assert coordinator.initial_calculated_soc == EXPECTED_TARGET
    await coordinator._async_update_data()
    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": EXPECTED_TARGET}),
        call("switch", "turn_on", {"entity_id": GRID}),
    ]


# 6. Override upwards (finding F2) -------------------------------------------


@pytest.mark.asyncio
async def test_override_above_target_keeps_grid_charging(mock_hass, coordinator):
    """Raising the override from 45 % to 70 % with the battery at 50 % must keep charging.

    ``current_target_soc()`` is the only target (finding F2): the next update
    writes the override to the inverter and, because 50 % is below 70 %, leaves
    grid charging on. ``minimum_calculated_soc`` stays a diagnostic value.
    """
    target = await _start_and_apply(mock_hass, coordinator)
    assert target == 45.0
    mock_hass.states.async_set(BATTERY, "50")

    number = MinSOCOverrideNumber(coordinator, coordinator.entry)
    number.async_write_ha_state = MagicMock()
    await number.async_set_native_value(70.0)

    assert coordinator.override_soc == 70.0
    assert coordinator.current_target_soc() == 70.0
    # The entity only requests a refresh; the update applies the override
    coordinator.async_request_refresh.assert_awaited()
    mock_hass.services.async_call.assert_not_awaited()

    data = await coordinator._async_update_data()

    # The new target reaches the inverter and charging is not stopped: 50 % < 70 %
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": MIN_SOC, "value": 70.0}
    )
    assert coordinator.target_reached is False
    assert data["target_reached"] is False
    assert coordinator.minimum_calculated_soc == 45.0


@pytest.mark.asyncio
async def test_verification_restores_override_not_lowest_target(mock_hass, coordinator):
    """After an override to 70 %, an external reset to 45 % is corrected back to 70 %."""
    await _start_and_apply(mock_hass, coordinator)
    mock_hass.states.async_set(BATTERY, "50")
    number = MinSOCOverrideNumber(coordinator, coordinator.entry)
    number.async_write_ha_state = MagicMock()
    await number.async_set_native_value(70.0)
    await coordinator._async_update_data()
    mock_hass.states.async_set(MIN_SOC, "70")
    mock_hass.services.async_call.reset_mock()

    # Something outside writes the old plan value back to the inverter
    mock_hass.states.async_set(MIN_SOC, "45")
    await coordinator._verify_and_restore_min_soc()

    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": MIN_SOC, "value": 70.0}
    )
    assert coordinator._last_soc_set == 70.0
    assert coordinator.target_reached is False


# 7. Override in discharge mode (finding F10) --------------------------------


@pytest.mark.asyncio
async def test_override_in_discharge_mode_uses_discharge_path(mock_hass):
    """An override during a discharge window must never switch grid charging on."""
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    _register_inverter(mock_hass)
    mock_hass.states.async_set(FORCE, "off")
    config = {
        **CONFIG,
        CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE,
        CONF_FORCE_DISCHARGE_SWITCH: FORCE,
    }
    coordinator = _make_coordinator(mock_hass, config)
    assert coordinator.is_discharge_mode is True
    try:
        await coordinator._on_window_start(WINDOW_START)
        mock_hass.services.async_call.reset_mock()

        number = MinSOCOverrideNumber(coordinator, coordinator.entry)
        number.async_write_ha_state = MagicMock()
        await number.async_set_native_value(30.0)
        assert coordinator.override_soc == 30.0
        mock_hass.services.async_call.assert_not_awaited()

        data = await coordinator._async_update_data()

        calls = mock_hass.services.async_call.await_args_list
        # Discharge floor is written and force discharge is switched on ...
        assert call("number", "set_value", {"entity_id": MIN_SOC, "value": 30.0}) in calls
        assert call("switch", "turn_on", {"entity_id": FORCE}) in calls
        # ... but the charge path is never taken
        assert call("switch", "turn_on", {"entity_id": GRID}) not in calls
        assert data["target_reached"] is False
        assert data["is_active"] is True
    finally:
        await coordinator._stop_periodic_verification()


# 8. Verification and window end (finding F11) -------------------------------


@pytest.mark.asyncio
async def test_window_end_waits_for_running_verification_before_reset(mock_hass):
    """A verification run in flight is cancelled and awaited before the reset writes."""
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    _register_inverter(mock_hass, grid="on")
    # Interval 0: the loop calls the verification right after the window start
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_UPDATE_INTERVAL: 0})
    events: list[object] = []
    entered = asyncio.Event()

    async def _slow_verify() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()  # blocks until cancelled
        except asyncio.CancelledError:
            events.append("verification cancelled")
            raise

    coordinator._verify_and_restore_min_soc = _slow_verify

    async def _record(domain: str, service: str, data: dict) -> None:
        events.append((service, data.get("value", data["entity_id"])))

    mock_hass.services.async_call = AsyncMock(side_effect=_record)

    await coordinator._on_window_start(WINDOW_START)
    await asyncio.wait_for(entered.wait(), 1)
    task = coordinator._verification_task
    assert task is not None and not task.done()

    await coordinator._on_window_end(WINDOW_END)

    assert task.done()
    assert coordinator._verification_task is None
    # The run was gone before the first reset command went out
    assert events == [
        "verification cancelled",
        ("set_value", DEFAULT_MIN),
        ("turn_off", GRID),
    ]
