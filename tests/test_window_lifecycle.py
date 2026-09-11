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


# ===========================================================================
# Planner v2 (plan 006): bridge mode, discharge block, planned charge power
# ===========================================================================
#
# Bridge inputs for every test below: window 00:00-05:59, sunrise 07:30 with a
# 90 minute crossover delay (PV > load at 09:00), sunset 17:00, a flat 0.5 kW
# house load and a 0.5 kWh reserve. The bridge is therefore
# 0.5 kW * 3 h 01 min + 0.5 kWh = 2.008 kWh -> lower bound 28.1 % of 10 kWh.
# ``dt_util.now`` is naive in these tests, so the sun times are read as UTC
# and aligned to naive local time by the coordinator.

from datetime import timedelta, timezone

from homeassistant.helpers.recorder import DATA_INSTANCE
import homeassistant.util.dt as dt_util

from custom_components.inverter_charge_night import async_unload_entry
from custom_components.inverter_charge_night.const import (
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_BACKUP_MODE_ENTITY,
    CONF_BRIDGE_RESERVE_KWH,
    CONF_CHARGE_EFFICIENCY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_DAY_PRICE_CT,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_FEED_IN_PRICE_CT,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_NIGHT_PRICE_CT,
    CONF_PLANNER_MODE,
    CONF_PV_CROSSOVER_DELAY_MIN,
    PLANNER_MODE_BRIDGE,
)
from custom_components.inverter_charge_night.planner import (
    REASON_BRIDGE,
    REASON_CONFLICT_HEADROOM_WINS,
)

SUN = "sun.sun"
DISCHARGE_LIMIT = "number.discharge_limit"
AC_LIMIT = "number.ac_limit"
HOUSE_METER = "sensor.house_energy"
BACKUP = "binary_sensor.backup"

BRIDGE_CONFIG = {
    **CONFIG,
    CONF_PLANNER_MODE: PLANNER_MODE_BRIDGE,
    CONF_AVG_HOUSE_LOAD_KW: 0.5,
    CONF_PV_CROSSOVER_DELAY_MIN: 90,
    CONF_BRIDGE_RESERVE_KWH: 0.5,
    CONF_CHARGE_EFFICIENCY: 0.9,
}
BRIDGE_KWH = 0.5 * (3 + 1 / 60) + 0.5
BRIDGE_TARGET = round((BRIDGE_KWH + CAPACITY_KWH * USER_MIN / 100) / CAPACITY_KWH * 100, 1)  # 28.1
PV_CROSSOVER = datetime(2026, 1, 15, 9, 0)


def _register_sun(hass, rising="2026-01-15T07:30:00+00:00", setting="2026-01-15T17:00:00+00:00"):
    hass.states.async_set(SUN, "below_horizon", {"next_rising": rising, "next_setting": setting})


def _real_task_runner(hass):
    hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )


def _persisting(hass):
    """Let ``async_update_entry`` store the options like the real config entries do."""

    def _update(entry, options=None, **kwargs):
        if options is not None:
            entry.options = options
        return True

    hass.config_entries.async_update_entry = MagicMock(side_effect=_update)


@pytest_asyncio.fixture
async def bridge(mock_hass):
    """Real coordinator in bridge mode with inverter and sun entities registered.

    The battery starts at 10 %, below the 28.1 % bridge target.
    """
    _real_task_runner(mock_hass)
    _persisting(mock_hass)
    _register_inverter(mock_hass, battery="10")
    _register_sun(mock_hass)
    coordinator = _make_coordinator(mock_hass, BRIDGE_CONFIG)
    yield coordinator
    await coordinator._stop_periodic_verification()


# 9. Bridge target -------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_mode_targets_bridge_energy_not_headroom(mock_hass, bridge):
    """5 kWh forecast: headroom would say 45 %, the bridge planner says 28.1 %."""
    assert BRIDGE_TARGET == 28.1
    await bridge._on_window_start(WINDOW_START)

    assert bridge.initial_calculated_soc == BRIDGE_TARGET
    assert bridge.last_plan is not None
    assert bridge.last_plan.reason == REASON_BRIDGE
    assert bridge.last_plan.bridge_kwh == pytest.approx(BRIDGE_KWH)
    assert bridge._pv_crossover == PV_CROSSOVER

    data = await bridge._async_update_data()

    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": BRIDGE_TARGET}),
        call("switch", "turn_on", {"entity_id": GRID}),
    ]
    assert data["calculated_soc"] == BRIDGE_TARGET
    assert data["plan_reason"] == REASON_BRIDGE
    assert data["bridge_kwh"] == round(BRIDGE_KWH, 2)
    assert data["surplus_kwh"] == 1.5  # 5.5 kWh with margin - 4 kWh daytime load
    assert data["lower_bound_soc"] == BRIDGE_TARGET
    assert data["upper_bound_soc"] == USER_MAX
    assert data["pv_crossover"] == PV_CROSSOVER.isoformat()
    # No AC limit entity configured: the planned power is reported, not written
    assert data["planned_charge_power_w"] == 1000.0  # min charge power (default)


@pytest.mark.asyncio
async def test_bridge_mode_uses_headroom_when_planner_fails(mock_hass, caplog):
    """A planner error (capacity 0) falls back to the headroom path and its safe fallback."""
    _real_task_runner(mock_hass)
    _register_inverter(mock_hass)
    _register_sun(mock_hass)
    coordinator = _make_coordinator(mock_hass, {**BRIDGE_CONFIG, CONF_BATTERY_CAPACITY: 0.0})
    try:
        await coordinator._on_window_start(WINDOW_START)
        assert "Bridge planner failed" in caplog.text
        assert coordinator.initial_calculated_soc == 50.0
        assert coordinator.last_plan is None
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_bridge_mode_fallback_recalculation_in_update(mock_hass, bridge):
    """The update's fallback branch (no initial target) also uses the planner."""
    await bridge._on_window_start(WINDOW_START)
    bridge.initial_calculated_soc = None
    bridge.last_plan = None

    data = await bridge._async_update_data()

    assert bridge.initial_calculated_soc == BRIDGE_TARGET
    assert data["calculated_soc"] == BRIDGE_TARGET
    assert data["plan_reason"] == REASON_BRIDGE


@pytest.mark.asyncio
async def test_window_end_clears_plan(mock_hass, bridge):
    await bridge._on_window_start(WINDOW_START)
    await bridge._async_update_data()
    mock_hass.states.async_set(GRID, "on")

    await bridge._on_window_end(WINDOW_END)

    assert bridge.last_plan is None
    assert bridge.planned_charge_power_w is None
    assert bridge._pv_crossover is None
    data = await bridge._async_update_data()
    assert "plan_reason" not in data


# 10. Monotone target in the window ------------------------------------------


@pytest.mark.asyncio
async def test_replan_in_window_raises_target_but_never_lowers_it(mock_hass):
    """A smaller forecast raises the target; a larger one does not lower it.

    Forecast 13 kWh: surplus 10.3 kWh > bridge, conflict, headroom wins at
    28 / 30 / 20 ct -> target 17.1 % (upper bound). Forecast 5 kWh: no conflict
    -> 28.1 % (bridge). Back to 13 kWh: the plan says 17.1 % but the window
    target stays at 28.1 %.
    """
    _real_task_runner(mock_hass)
    _persisting(mock_hass)
    _register_inverter(mock_hass, battery="10", pv="13")
    _register_sun(mock_hass)
    config = {
        **BRIDGE_CONFIG,
        CONF_NIGHT_PRICE_CT: 28.0,
        CONF_DAY_PRICE_CT: 30.0,
        CONF_FEED_IN_PRICE_CT: 20.0,
    }
    coordinator = _make_coordinator(mock_hass, config)
    try:
        await coordinator._on_window_start(WINDOW_START)
        assert coordinator.last_plan.reason == REASON_CONFLICT_HEADROOM_WINS
        first_target = coordinator.initial_calculated_soc
        assert first_target == 17.1
        await coordinator._async_update_data()
        mock_hass.states.async_set(MIN_SOC, str(first_target))
        mock_hass.states.async_set(GRID, "on")
        mock_hass.services.async_call.reset_mock()

        # Solcast lowers the forecast: less surplus, the bridge fits again
        mock_hass.states.async_set(PV, "5", {"unit_of_measurement": "kWh"})
        data = await coordinator._async_update_data()

        assert coordinator.initial_calculated_soc == BRIDGE_TARGET
        assert coordinator.current_target_soc() == BRIDGE_TARGET
        assert data["calculated_soc"] == BRIDGE_TARGET
        mock_hass.services.async_call.assert_awaited_once_with(
            "number", "set_value", {"entity_id": MIN_SOC, "value": BRIDGE_TARGET}
        )
        assert coordinator.entry.options[CONF_RUNTIME_STATE]["initial_calculated_soc"] == BRIDGE_TARGET
        mock_hass.states.async_set(MIN_SOC, str(BRIDGE_TARGET))
        mock_hass.services.async_call.reset_mock()

        # Solcast raises the forecast again: the plan would lower the target
        mock_hass.states.async_set(PV, "13", {"unit_of_measurement": "kWh"})
        data = await coordinator._async_update_data()

        assert coordinator.last_plan.target_soc == 17.1
        assert coordinator.initial_calculated_soc == BRIDGE_TARGET
        assert data["calculated_soc"] == BRIDGE_TARGET
        mock_hass.services.async_call.assert_not_awaited()
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_replan_raising_target_resumes_charging(mock_hass, bridge):
    """A target raised by the replan un-sets target_reached so charging restarts."""
    await bridge._on_window_start(WINDOW_START)
    await bridge._async_update_data()
    # Battery reaches 28.1 %: charging stops
    mock_hass.states.async_set(MIN_SOC, str(BRIDGE_TARGET))
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(BATTERY, "30")
    await bridge._async_update_data()
    assert bridge.target_reached is True
    mock_hass.states.async_set(GRID, "off")
    mock_hass.services.async_call.reset_mock()

    # The household load turns out higher: the bridge (and target) grows
    bridge.config = {**BRIDGE_CONFIG, CONF_AVG_HOUSE_LOAD_KW: 1.5}
    data = await bridge._async_update_data()

    assert bridge.initial_calculated_soc > BRIDGE_TARGET
    assert bridge.target_reached is False
    assert data["target_reached"] is False
    assert call("switch", "turn_on", {"entity_id": GRID}) in mock_hass.services.async_call.await_args_list


@pytest.mark.asyncio
async def test_replan_ignores_fallback_plan(mock_hass, bridge):
    """A forecast that becomes unavailable mid-window never moves the target."""
    await bridge._on_window_start(WINDOW_START)
    mock_hass.states.async_set(PV, "unavailable")

    await bridge._async_update_data()

    assert bridge.initial_calculated_soc == BRIDGE_TARGET
    assert bridge.last_plan.reason == "fallback"


# 11. Sun times ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_sun_entity_falls_back_to_window_end_plus_two_hours(mock_hass, caplog):
    _real_task_runner(mock_hass)
    _register_inverter(mock_hass)
    coordinator = _make_coordinator(mock_hass, BRIDGE_CONFIG)
    try:
        await coordinator._on_window_start(WINDOW_START)

        assert "no usable next_rising" in caplog.text
        # sunrise 07:59 + 90 min
        assert coordinator._pv_crossover == datetime(2026, 1, 15, 9, 29)
        caplog.clear()
        await coordinator._async_update_data()
        assert "no usable next_rising" not in caplog.text  # logged once per window
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_sun_entity_without_setting_uses_ten_hour_day(mock_hass, bridge):
    mock_hass.states.async_set(SUN, "below_horizon", {"next_rising": "2026-01-15T07:30:00+00:00"})
    now = datetime(2026, 1, 15, 2, 0)

    sunrise, sunset = bridge._sun_times(now, bridge._window_end_datetime(now))

    assert sunrise == datetime(2026, 1, 15, 7, 30)
    assert sunset == datetime(2026, 1, 15, 17, 30)


def test_sun_times_keep_awareness_of_now(mock_hass, bridge):
    """An aware ``now`` gets aware sun times, a naive one naive times."""
    aware_now = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)
    sunrise, _ = bridge._sun_times(aware_now, aware_now + timedelta(hours=4))
    assert sunrise == datetime(2026, 1, 15, 7, 30, tzinfo=timezone.utc)
    naive_now = datetime(2026, 1, 15, 2, 0)
    sunrise, _ = bridge._sun_times(naive_now, naive_now + timedelta(hours=4))
    assert sunrise == datetime(2026, 1, 15, 7, 30)
    # A naive attribute value is given the awareness of ``now``
    mock_hass.states.async_set(SUN, "below_horizon", {"next_rising": "2026-01-15T07:30:00"})
    sunrise, _ = bridge._sun_times(aware_now, aware_now + timedelta(hours=4))
    assert sunrise == datetime(2026, 1, 15, 7, 30, tzinfo=timezone.utc)
    sunrise, _ = bridge._sun_times(naive_now, naive_now + timedelta(hours=4))
    assert sunrise == datetime(2026, 1, 15, 7, 30)


def test_window_end_datetime_today_tomorrow_and_end_minute(bridge):
    assert bridge._window_end_datetime(datetime(2026, 1, 15, 2, 0)) == datetime(2026, 1, 15, 5, 59)
    assert bridge._window_end_datetime(datetime(2026, 1, 15, 23, 0)) == datetime(2026, 1, 16, 5, 59)
    # The end minute still belongs to the window
    assert bridge._window_end_datetime(datetime(2026, 1, 15, 5, 59, 30)) == datetime(2026, 1, 15, 5, 59)
    assert bridge._window_end_datetime(datetime(2026, 1, 15, 6, 0)) == datetime(2026, 1, 16, 5, 59)


# 12. House load profile -------------------------------------------------------


def _hourly_rows(days, change_for_hour, unit_factor: float = 1.0):
    rows = []
    for day in days:
        for hour in range(24):
            start = datetime(2026, 1, day, hour, tzinfo=timezone.utc).timestamp()
            rows.append({"start": start, "end": start + 3600, "change": change_for_hour(hour) * unit_factor})
    return rows


def _recorder(mock_hass, rows):
    instance = MagicMock()
    instance.async_add_executor_job = AsyncMock(return_value={HOUSE_METER: rows})
    mock_hass.data[DATA_INSTANCE] = instance
    return instance


@pytest.mark.asyncio
async def test_house_load_profile_without_meter_is_flat_average(bridge):
    assert await bridge._house_load_profile() == [0.5] * 24


@pytest.mark.asyncio
async def test_house_load_profile_learned_from_recorder_and_cached(mock_hass):
    mock_hass.states.async_set(HOUSE_METER, "12345", {"unit_of_measurement": "kWh"})
    rows = _hourly_rows((13, 14), lambda h: 0.3 if h < 6 else 1.0)
    rows.append({"start": rows[0]["start"], "end": 0, "change": -5.0})  # meter reset: skipped
    rows.append({"start": rows[0]["start"], "end": 0, "change": None})  # no data: skipped
    instance = _recorder(mock_hass, rows)
    coordinator = _make_coordinator(mock_hass, {**BRIDGE_CONFIG, CONF_HOUSE_LOAD_ENTITY: HOUSE_METER})

    profile = await coordinator._house_load_profile()

    assert profile == pytest.approx([0.3] * 6 + [1.0] * 18)
    instance.async_add_executor_job.assert_awaited_once()
    # Within 15 minutes the cached profile is returned without a recorder query
    assert await coordinator._house_load_profile() == pytest.approx(profile)
    instance.async_add_executor_job.assert_awaited_once()
    # An expired cache queries again
    coordinator._house_load_cache = (dt_util.utcnow() - timedelta(minutes=16), profile)
    await coordinator._house_load_profile()
    assert instance.async_add_executor_job.await_count == 2


@pytest.mark.asyncio
async def test_house_load_profile_converts_wh_and_fills_missing_hours(mock_hass):
    mock_hass.states.async_set(HOUSE_METER, "1", {"unit_of_measurement": "Wh"})
    six = datetime(2026, 1, 14, 6, tzinfo=timezone.utc).timestamp()
    rows = [row for row in _hourly_rows((14,), lambda h: 800.0) if row["start"] < six]
    _recorder(mock_hass, rows)
    coordinator = _make_coordinator(mock_hass, {**BRIDGE_CONFIG, CONF_HOUSE_LOAD_ENTITY: HOUSE_METER})

    profile = await coordinator._house_load_profile()

    assert profile == pytest.approx([0.8] * 6 + [0.5] * 18)


@pytest.mark.asyncio
async def test_house_load_profile_mwh_unit(mock_hass):
    mock_hass.states.async_set(HOUSE_METER, "1", {"unit_of_measurement": "MWh"})
    _recorder(mock_hass, _hourly_rows((14,), lambda h: 0.0004))
    coordinator = _make_coordinator(mock_hass, {**BRIDGE_CONFIG, CONF_HOUSE_LOAD_ENTITY: HOUSE_METER})

    assert await coordinator._house_load_profile() == pytest.approx([0.4] * 24)


@pytest.mark.asyncio
@pytest.mark.parametrize("recorder", ["absent", "raises", "empty", "unusable"])
async def test_house_load_profile_falls_back_to_average(mock_hass, caplog, recorder):
    mock_hass.states.async_set(HOUSE_METER, "1", {"unit_of_measurement": "kWh"})
    if recorder == "raises":
        instance = _recorder(mock_hass, [])
        instance.async_add_executor_job = AsyncMock(side_effect=RuntimeError("db locked"))
    elif recorder == "empty":
        _recorder(mock_hass, [])
    elif recorder == "unusable":
        _recorder(mock_hass, [{"start": None, "end": 0, "change": 1.0}])
    coordinator = _make_coordinator(mock_hass, {**BRIDGE_CONFIG, CONF_HOUSE_LOAD_ENTITY: HOUSE_METER})

    with caplog.at_level("DEBUG"):
        profile = await coordinator._house_load_profile()

    assert profile == [0.5] * 24
    assert "using the flat 0.50 kW profile" in caplog.text


def test_prices_require_all_three(bridge):
    assert bridge._prices_ct() is None
    bridge.config = {**BRIDGE_CONFIG, CONF_NIGHT_PRICE_CT: 14.0, CONF_DAY_PRICE_CT: 30.0}
    assert bridge._prices_ct() is None
    bridge.config = {
        **BRIDGE_CONFIG,
        CONF_NIGHT_PRICE_CT: 14.0,
        CONF_DAY_PRICE_CT: 30.0,
        CONF_FEED_IN_PRICE_CT: 8.0,
    }
    assert bridge._prices_ct() == (14.0, 30.0, 8.0)


# 13. Discharge block ------------------------------------------------------------


@pytest_asyncio.fixture
async def blocking(mock_hass):
    """Headroom coordinator with a discharge limit entity at 5000 W."""
    _real_task_runner(mock_hass)
    _persisting(mock_hass)
    _register_inverter(mock_hass)
    mock_hass.states.async_set(DISCHARGE_LIMIT, "5000", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_DISCHARGE_LIMIT_ENTITY: DISCHARGE_LIMIT})
    yield coordinator
    await coordinator._stop_periodic_verification()


def _runtime_state(coordinator) -> dict:
    return coordinator.entry.options[CONF_RUNTIME_STATE]


@pytest.mark.asyncio
async def test_window_start_blocks_discharge_and_persists_original(mock_hass, blocking):
    await blocking._on_window_start(WINDOW_START)

    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": DISCHARGE_LIMIT, "value": 0.0}
    )
    assert blocking._original_discharge_limit == 5000.0
    assert _runtime_state(blocking)["original_discharge_limit"] == 5000.0
    # The target itself is unchanged by the block
    assert blocking.initial_calculated_soc == EXPECTED_TARGET


@pytest.mark.asyncio
async def test_window_end_restores_discharge_limit(mock_hass, blocking):
    await blocking._on_window_start(WINDOW_START)
    await blocking._async_update_data()
    mock_hass.states.async_set(DISCHARGE_LIMIT, "0")
    mock_hass.states.async_set(MIN_SOC, str(EXPECTED_TARGET))
    mock_hass.states.async_set(GRID, "on")
    mock_hass.services.async_call.reset_mock()

    await blocking._on_window_end(WINDOW_END)

    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": DEFAULT_MIN}),
        call("switch", "turn_off", {"entity_id": GRID}),
        call("number", "set_value", {"entity_id": DISCHARGE_LIMIT, "value": 5000.0}),
    ]
    assert blocking._original_discharge_limit is None
    assert _runtime_state(blocking)["original_discharge_limit"] is None
    assert blocking._pending_reset is False


@pytest.mark.asyncio
async def test_unload_during_window_restores_discharge_limit(mock_hass, blocking):
    await blocking._on_window_start(WINDOW_START)
    mock_hass.states.async_set(DISCHARGE_LIMIT, "0")
    mock_hass.services.async_call.reset_mock()
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    blocking.entry.runtime_data = blocking

    assert await async_unload_entry(mock_hass, blocking.entry) is True

    assert call("number", "set_value", {"entity_id": DISCHARGE_LIMIT, "value": 5000.0}) in (
        mock_hass.services.async_call.await_args_list
    )
    assert blocking._original_discharge_limit is None


@pytest.mark.asyncio
async def test_discharge_block_never_set_in_backup_mode(mock_hass):
    _real_task_runner(mock_hass)
    _register_inverter(mock_hass)
    mock_hass.states.async_set(DISCHARGE_LIMIT, "5000")
    mock_hass.states.async_set(BACKUP, "on")
    coordinator = _make_coordinator(
        mock_hass,
        {**CONFIG, CONF_DISCHARGE_LIMIT_ENTITY: DISCHARGE_LIMIT, CONF_BACKUP_MODE_ENTITY: BACKUP},
    )
    coordinator.is_active = True

    await coordinator._apply_discharge_block(announce=True)

    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator._original_discharge_limit is None


@pytest.mark.asyncio
async def test_discharge_block_never_set_in_discharge_mode(mock_hass):
    _real_task_runner(mock_hass)
    _register_inverter(mock_hass)
    mock_hass.states.async_set(DISCHARGE_LIMIT, "5000")
    coordinator = _make_coordinator(
        mock_hass,
        {
            **CONFIG,
            CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE,
            CONF_DISCHARGE_LIMIT_ENTITY: DISCHARGE_LIMIT,
        },
    )
    try:
        await coordinator._on_window_start(WINDOW_START)
        await coordinator._verify_and_restore_min_soc()

        for awaited in mock_hass.services.async_call.await_args_list:
            assert awaited.args[2]["entity_id"] != DISCHARGE_LIMIT
        assert coordinator._original_discharge_limit is None
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_window_start_without_discharge_entity_logs_info(mock_hass, coordinator, caplog):
    with caplog.at_level("INFO"):
        await coordinator._on_window_start(WINDOW_START)
    assert "No discharge limit entity configured" in caplog.text


@pytest.mark.asyncio
async def test_verification_reapplies_discharge_block(mock_hass, blocking):
    await blocking._on_window_start(WINDOW_START)
    await blocking._async_update_data()
    mock_hass.states.async_set(DISCHARGE_LIMIT, "0")
    mock_hass.states.async_set(MIN_SOC, str(EXPECTED_TARGET))
    mock_hass.services.async_call.reset_mock()

    # Already 0: nothing is written
    await blocking._verify_and_restore_min_soc()
    mock_hass.services.async_call.assert_not_awaited()

    # Something outside lifted the limit: the verification blocks again
    mock_hass.states.async_set(DISCHARGE_LIMIT, "3000")
    await blocking._verify_and_restore_min_soc()
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": DISCHARGE_LIMIT, "value": 0.0}
    )
    # The original stays the value captured at the window start
    assert blocking._original_discharge_limit == 5000.0


@pytest.mark.asyncio
async def test_restart_in_window_keeps_persisted_discharge_original(mock_hass):
    """After a restart the live 0 is not adopted as original; the window end restores 5000."""
    _real_task_runner(mock_hass)
    _register_inverter(mock_hass, min_soc="45")
    mock_hass.states.async_set(DISCHARGE_LIMIT, "0")
    options = _persisted_window(45.0)
    options[CONF_RUNTIME_STATE]["original_discharge_limit"] = 5000.0
    coordinator = _make_coordinator(
        mock_hass, {**CONFIG, CONF_DISCHARGE_LIMIT_ENTITY: DISCHARGE_LIMIT}, options=options
    )
    assert coordinator._original_discharge_limit == 5000.0
    try:
        await coordinator._on_window_start(WINDOW_START)
        assert coordinator._original_discharge_limit == 5000.0
        mock_hass.services.async_call.assert_not_awaited()  # already blocked

        await coordinator._on_window_end(WINDOW_END)

        assert call("number", "set_value", {"entity_id": DISCHARGE_LIMIT, "value": 5000.0}) in (
            mock_hass.services.async_call.await_args_list
        )
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_discharge_block_deferred_while_entity_unavailable(mock_hass, blocking, caplog):
    mock_hass.states.async_set(DISCHARGE_LIMIT, "unavailable")

    await blocking._on_window_start(WINDOW_START)

    assert "block deferred" in caplog.text
    assert blocking._original_discharge_limit is None
    mock_hass.services.async_call.assert_not_awaited()

    # The entity comes back: the verification captures the original and blocks
    mock_hass.states.async_set(DISCHARGE_LIMIT, "5000")
    mock_hass.states.async_set(MIN_SOC, str(EXPECTED_TARGET))
    await blocking._verify_and_restore_min_soc()
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": DISCHARGE_LIMIT, "value": 0.0}
    )
    assert blocking._original_discharge_limit == 5000.0


@pytest.mark.asyncio
async def test_discharge_block_not_written_when_value_unreadable(mock_hass, blocking, caplog):
    mock_hass.states.async_set(DISCHARGE_LIMIT, "n/a")

    await blocking._apply_discharge_block()

    assert "could not be restored" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_discharge_block_unsupported_domain(mock_hass, caplog):
    mock_hass.states.async_set("sensor.discharge", "5000")
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_DISCHARGE_LIMIT_ENTITY: "sensor.discharge"})

    await coordinator._apply_discharge_block()
    assert "unsupported domain sensor" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()

    # Nothing can be restored through an unusable entity: the reset is done
    coordinator._original_discharge_limit = 5000.0
    assert await coordinator._reset_discharge_limit() is True
    assert coordinator._original_discharge_limit is None


@pytest.mark.asyncio
async def test_failed_discharge_reset_keeps_original_for_retry(mock_hass, blocking):
    await blocking._on_window_start(WINDOW_START)
    mock_hass.states.async_set(DISCHARGE_LIMIT, "unavailable")

    await blocking._on_window_end(WINDOW_END)

    assert blocking._pending_reset is True
    assert blocking._original_discharge_limit == 5000.0
    assert _runtime_state(blocking)["original_discharge_limit"] == 5000.0


@pytest.mark.asyncio
async def test_discharge_reset_reports_service_error(mock_hass, blocking, caplog):
    blocking._original_discharge_limit = 5000.0
    mock_hass.services.async_call = AsyncMock(side_effect=RuntimeError("modbus timeout"))

    assert await blocking._reset_discharge_limit() is False

    assert "Error writing Reset discharge limit" in caplog.text
    assert blocking._original_discharge_limit == 5000.0


# 14. Planned charge power --------------------------------------------------------

POWER_CONFIG = {
    **BRIDGE_CONFIG,
    CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
    CONF_MIN_CHARGE_POWER_W: 500,
    CONF_MAX_CHARGE_POWER_W: 10000,
}
BEST_3000 = {
    CONF_AUTO_EFFICIENCY_DATA: {"history": {"3000": 0.05}, "best_power_w": 3000, "best_loss": 0.05}
}
FOUR_HOURS_LEFT = datetime(2026, 1, 15, 1, 59)
NOW = "custom_components.inverter_charge_night.dt_util.now"


@pytest_asyncio.fixture
async def powered(mock_hass):
    """Bridge coordinator with an AC limit entity at 6000 W and a 3000 W efficiency optimum."""
    _real_task_runner(mock_hass)
    _register_inverter(mock_hass)
    _register_sun(mock_hass)
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, POWER_CONFIG, options=dict(BEST_3000))
    yield coordinator
    await coordinator._stop_periodic_verification()


async def _start_with_target(coordinator, target: float) -> None:
    await coordinator._on_window_start(WINDOW_START)
    # Replace the plan target for the power test; the replan keeps max(previous, plan)
    coordinator.initial_calculated_soc = target


@pytest.mark.asyncio
async def test_planned_power_two_kwh_in_four_hours_writes_556_w(mock_hass, powered):
    """2 kWh missing (40 -> 60 %) in 4 h at 0.9: 556 W, below the 3000 W optimum."""
    with patch(NOW, return_value=FOUR_HOURS_LEFT):
        await _start_with_target(powered, 60.0)
        data = await powered._async_update_data()

    assert powered.planned_charge_power_w == 556.0
    assert data["planned_charge_power_w"] == 556.0
    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": 60.0}),
        call("switch", "turn_on", {"entity_id": GRID}),
        call("number", "set_value", {"entity_id": AC_LIMIT, "value": 556.0}),
    ]
    assert powered._original_ac_charge_power == 6000.0
    assert powered._planned_setpoint_written_w == 556.0


@pytest.mark.asyncio
async def test_planned_power_thirty_minutes_left_writes_max(mock_hass, powered):
    """5 kWh missing in 30 min needs 11.1 kW: clamped to max_charge_power_w."""
    with patch(NOW, return_value=datetime(2026, 1, 15, 5, 29)):
        await _start_with_target(powered, 90.0)
        await powered._async_update_data()

    assert powered.planned_charge_power_w == 10000.0
    assert call("number", "set_value", {"entity_id": AC_LIMIT, "value": 10000.0}) in (
        mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_planned_power_small_change_is_not_written(mock_hass, powered):
    with patch(NOW, return_value=FOUR_HOURS_LEFT):
        await _start_with_target(powered, 60.0)
        await powered._async_update_data()
        mock_hass.states.async_set(MIN_SOC, "60")
        mock_hass.states.async_set(GRID, "on")
        mock_hass.services.async_call.reset_mock()

        # 1.9 kWh missing now: 528 W, within 100 W of the written 556 W
        mock_hass.states.async_set(BATTERY, "41")
        await powered._async_update_data()
        mock_hass.services.async_call.assert_not_awaited()
        assert powered.planned_charge_power_w == 528.0

    # Two hours later, still 1.9 kWh missing: 1056 W, 500 W above the written value
    with patch(NOW, return_value=datetime(2026, 1, 15, 3, 59)):
        await powered._async_update_data()
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": AC_LIMIT, "value": 1056.0}
    )


@pytest.mark.asyncio
async def test_planned_power_above_optimum_follows_deadline(mock_hass, powered):
    """Required 5.56 kW > 3000 W optimum: the deadline wins, the optimum only caps below it."""
    with patch(NOW, return_value=datetime(2026, 1, 15, 4, 59)):
        await _start_with_target(powered, 60.0)  # 2 kWh in 1 h -> 2222 W <= 3000
        await powered._async_update_data()
        assert powered.planned_charge_power_w == 2222.0
        mock_hass.states.async_set(MIN_SOC, "60")
        mock_hass.states.async_set(GRID, "on")
        mock_hass.services.async_call.reset_mock()
        powered.initial_calculated_soc = 90.0  # 5 kWh in 1 h -> 5556 W > 3000
        await powered._async_update_data()
    assert powered.planned_charge_power_w == 5556.0
    assert call("number", "set_value", {"entity_id": AC_LIMIT, "value": 5556.0}) in (
        mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_headroom_mode_reports_planned_power_but_does_not_write(mock_hass):
    _real_task_runner(mock_hass)
    _register_inverter(mock_hass)
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(
        mock_hass, {**CONFIG, CONF_CHARGE_POWER_ENTITY: AC_LIMIT, CONF_MIN_CHARGE_POWER_W: 500}
    )
    try:
        with patch(NOW, return_value=FOUR_HOURS_LEFT):
            await _start_with_target(coordinator, 60.0)
            data = await coordinator._async_update_data()

        assert coordinator.planned_charge_power_w == 556.0
        assert "planned_charge_power_w" not in data  # no plan attributes in headroom mode
        for awaited in mock_hass.services.async_call.await_args_list:
            assert awaited.args[2]["entity_id"] != AC_LIMIT
        assert coordinator._original_ac_charge_power is None
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_planned_power_not_written_while_finder_runs(mock_hass, powered):
    powered.auto_efficient_charge = True
    with patch(NOW, return_value=FOUR_HOURS_LEFT):
        await _start_with_target(powered, 60.0)
        await powered._async_update_data()

    assert powered.planned_charge_power_w == 556.0
    for awaited in mock_hass.services.async_call.await_args_list:
        assert awaited.args[2]["entity_id"] != AC_LIMIT


@pytest.mark.asyncio
async def test_planned_power_zero_when_target_reached_and_none_without_soc(mock_hass, powered):
    await powered._on_window_start(WINDOW_START)
    mock_hass.states.async_set(BATTERY, "40")  # above the 28.1 % target
    await powered._async_update_data()
    assert powered.target_reached is True
    assert powered.planned_charge_power_w == 0.0

    powered.target_reached = False
    mock_hass.states.async_set(BATTERY, "unavailable")
    await powered._async_update_data()
    assert powered.planned_charge_power_w is None


@pytest.mark.asyncio
async def test_window_end_restores_ac_limit_after_planned_writes(mock_hass, powered):
    with patch(NOW, return_value=FOUR_HOURS_LEFT):
        await _start_with_target(powered, 60.0)
        await powered._async_update_data()
    mock_hass.states.async_set(MIN_SOC, "60")
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(AC_LIMIT, "556", {"unit_of_measurement": "W"})
    mock_hass.services.async_call.reset_mock()

    await powered._on_window_end(WINDOW_END)

    assert call("number", "set_value", {"entity_id": AC_LIMIT, "value": 6000.0}) in (
        mock_hass.services.async_call.await_args_list
    )
    assert powered._planned_setpoint_written_w is None
    assert powered.planned_charge_power_w is None


@pytest.mark.asyncio
async def test_planned_power_invalid_efficiency_is_logged(mock_hass, powered, caplog):
    powered.config = {**POWER_CONFIG, CONF_CHARGE_EFFICIENCY: 0.0}
    with patch(NOW, return_value=FOUR_HOURS_LEFT):
        await _start_with_target(powered, 60.0)
        await powered._async_update_data()

    assert "Cannot plan the charge power" in caplog.text
    assert powered.planned_charge_power_w is None
