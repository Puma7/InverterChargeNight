"""The paths where a mistake is a physical hazard, not a bug.

This integration drives a battery charger of up to 20 kW on a 63 A house
connection that also feeds two wallboxes. Every test here asserts the same
property from a different direction: **when anything is unknown, unreadable or
failed, the result must be less current, never more** - and nothing the
integration put on the inverter may outlive the window that put it there.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_BACKUP_MODE_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CHARGE_EFFICIENCY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_GRID_CONTINUOUS_PCT,
    CONF_GRID_HEADROOM_W,
    CONF_GRID_IMPORT_ENTITY,
    CONF_GRID_PHASES,
    CONF_GRID_VOLTAGE_V,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_MAIN_FUSE_A,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_PV_FORECAST_ENTITY,
    CONF_RUNTIME_STATE,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)

MIN_SOC = "number.min_soc"
GRID = "switch.grid_charge"
BATTERY = "sensor.battery_soc"
PV = "sensor.pv_forecast"
AC_LIMIT = "number.ac_limit"
GRID_IMPORT = "sensor.grid_import"
BACKUP = "binary_sensor.backup"

WINDOW_START = datetime(2026, 1, 15, 0, 0)
INSIDE = datetime(2026, 1, 15, 1, 59)
AFTER_WINDOW = datetime(2026, 1, 15, 9, 0)
NOW = "custom_components.inverter_charge_night.dt_util.now"

CONFIG = {
    CONF_KOSTAL_MIN_SOC_ENTITY: MIN_SOC,
    CONF_KOSTAL_GRID_CHARGE_SWITCH: GRID,
    CONF_BATTERY_SOC_ENTITY: BATTERY,
    CONF_PV_FORECAST_ENTITY: PV,
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_START_TIME: "00:00",
    CONF_END_TIME: "05:59",
    CONF_COMMAND_DELAY: 0,
    CONF_CHARGE_EFFICIENCY: 0.9,
    CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
    CONF_MIN_CHARGE_POWER_W: 500,
    CONF_MAX_CHARGE_POWER_W: 20000,
    CONF_GRID_IMPORT_ENTITY: GRID_IMPORT,
    CONF_MAIN_FUSE_A: 63,
    CONF_GRID_PHASES: 3,
    CONF_GRID_VOLTAGE_V: 230,
    CONF_GRID_CONTINUOUS_PCT: 80,
    CONF_GRID_HEADROOM_W: 500,
}
BUDGET_W = 34776.0


def _register(hass, *, battery="40", grid_import="1000", ac_limit_attrs=None):
    hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: __import__("asyncio").ensure_future(coro)
    )
    hass.states.async_set(MIN_SOC, "8")
    hass.states.async_set(GRID, "off")
    hass.states.async_set(BATTERY, battery, {"unit_of_measurement": "%"})
    hass.states.async_set(PV, "5", {"unit_of_measurement": "kWh"})
    hass.states.async_set(AC_LIMIT, "6000", ac_limit_attrs or {"unit_of_measurement": "W"})
    if grid_import is not None:
        state = hass.states.async_set(GRID_IMPORT, grid_import, {"unit_of_measurement": "W"})
        state = hass.states.get(GRID_IMPORT)
        state.last_reported = INSIDE
        state.last_updated = state.last_reported
        state.last_changed = state.last_reported


def _make(hass, config=None, options=None) -> InverterChargeNightCoordinator:
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = config or CONFIG
    entry.options = options or {}

    def _update(config_entry, options=None, data=None, **kwargs):
        if options is not None:
            entry.options = options
        if data is not None:
            entry.data = data

    hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _ac_writes(hass) -> list[float]:
    return [
        c.args[2]["value"]
        for c in hass.services.async_call.await_args_list
        if c.args[0] == "number" and c.args[2].get("entity_id") == AC_LIMIT
    ]


def _service_calls(hass) -> list[tuple]:
    return [(c.args[0], c.args[1], c.args[2].get("entity_id")) for c in hass.services.async_call.await_args_list]


@pytest.fixture(autouse=True)
def _inside_window():
    with patch(NOW, return_value=INSIDE):
        yield


# --- 1. Nothing of ours may outlive its window ------------------------------


@pytest.mark.asyncio
async def test_a_restart_past_the_window_end_resets_the_inverter(mock_hass):
    """Home Assistant updated overnight and came back after the window.

    Nothing ended that window: grid charging is still on and the min SOC still
    carries the night's floor. Left alone, the battery is bought full from the
    grid in broad daylight, every single day, until somebody notices.
    """
    _register(mock_hass, battery="70")
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(MIN_SOC, "70")
    stored = {
        CONF_RUNTIME_STATE: {
            "is_enabled": True,
            "original_min_soc": 8.0,
            "original_ac_charge_power": 6000.0,
            "initial_calculated_soc": 60.0,
            "window_floor_soc": 70.0,
            "window_started_at": WINDOW_START.isoformat(),
        }
    }
    coordinator = _make(mock_hass, options=stored)
    assert coordinator.original_min_soc == 8.0
    assert coordinator.is_active is False

    with patch(NOW, return_value=AFTER_WINDOW):
        await coordinator._check_current_window()

    calls = _service_calls(mock_hass)
    assert ("number", "set_value", MIN_SOC) in calls, "the min SOC must go back to 8 %"
    assert ("switch", "turn_off", GRID) in calls, "grid charging must be switched off"
    assert coordinator.original_min_soc is None
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_target_from_an_old_window_is_not_reused(mock_hass):
    """Yesterday's target must not decide how much is bought tonight."""
    _register(mock_hass)
    stored = {
        CONF_RUNTIME_STATE: {
            "is_enabled": True,
            "original_min_soc": 8.0,
            "initial_calculated_soc": 95.0,
            "window_started_at": (WINDOW_START - timedelta(days=1)).isoformat(),
        }
    }
    coordinator = _make(mock_hass, options=stored)
    assert coordinator.initial_calculated_soc == 95.0

    with patch(NOW, return_value=WINDOW_START):
        await coordinator._on_window_start(WINDOW_START)

    assert coordinator.initial_calculated_soc != 95.0, "the planner must start fresh"
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_restart_inside_the_window_keeps_its_target(mock_hass):
    """The other direction: a restart during the window must not replan."""
    _register(mock_hass)
    stored = {
        CONF_RUNTIME_STATE: {
            "is_enabled": True,
            "original_min_soc": 8.0,
            "initial_calculated_soc": 65.0,
            "window_started_at": WINDOW_START.isoformat(),
        }
    }
    coordinator = _make(mock_hass, options=stored)

    with patch(NOW, return_value=INSIDE):
        await coordinator._on_window_start(INSIDE)

    assert coordinator.initial_calculated_soc == 65.0
    await coordinator._stop_periodic_verification()


# --- 2. A failed write is not a written value -------------------------------


@pytest.mark.asyncio
async def test_a_charge_limit_that_failed_to_write_is_retried(mock_hass):
    """Believing a failed protective write suppresses every later attempt.

    All the comparisons afterwards are "only write when it is lower than what
    stands there", so one silently failed write would mean the limit never
    reaches the inverter again for the rest of the window.
    """
    _register(mock_hass)
    coordinator = _make(mock_hass)
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 100.0
    mock_hass.services.async_call = AsyncMock(side_effect=RuntimeError("modbus timeout"))

    await coordinator._async_update_data()

    assert coordinator._planned_setpoint_written_w is None, "a failed write is not a setpoint"

    # The next round succeeds and the value does reach the inverter
    mock_hass.services.async_call = AsyncMock()
    await coordinator._async_update_data()

    assert _ac_writes(mock_hass), "the write must be retried"
    assert coordinator._planned_setpoint_written_w is not None
    await coordinator._stop_periodic_verification()


# --- 3. Unknown scale, unknown value: never write more -----------------------


@pytest.mark.asyncio
async def test_a_unitless_kilowatt_entity_is_not_written_in_watts(mock_hass, caplog):
    """5000 written to a number that counts kilowatts asks for 5 MW.

    An inverter that clamps that to its maximum turns a protective limit into
    full power - the exact opposite of what the limit is for.
    """
    caplog.set_level(logging.DEBUG)
    _register(mock_hass, ac_limit_attrs={"max": 20.0, "min": 0.0})
    coordinator = _make(mock_hass)
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 100.0
    mock_hass.services.async_call.reset_mock()

    await coordinator._set_ac_charge_limit_w(5000)

    written = _ac_writes(mock_hass)
    assert written == [5.0], "the entity's own maximum says it counts kilowatts"
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_an_unwritable_value_on_an_unitless_entity_is_refused(mock_hass, caplog):
    """No unit, and the value is above what the entity accepts: do not guess."""
    caplog.set_level(logging.ERROR)
    _register(mock_hass, ac_limit_attrs={"max": 3000.0, "min": 0.0})
    coordinator = _make(mock_hass)
    await coordinator._on_window_start(WINDOW_START)
    mock_hass.services.async_call.reset_mock()

    assert await coordinator._set_ac_charge_limit_w(5000) is False
    assert _ac_writes(mock_hass) == []
    assert "the scale is unknown" in caplog.text
    await coordinator._stop_periodic_verification()


def test_a_voltage_that_is_not_a_phase_voltage_never_widens_the_budget():
    from custom_components.inverter_charge_night.planner import grid_budget_w

    at_230 = grid_budget_w(63, 3, 230, 80, None)
    assert grid_budget_w(63, 1, 400, 80, None) <= at_230
    assert grid_budget_w(63, 3, 400, 80, None) == pytest.approx(at_230, rel=0.01)
    assert grid_budget_w(63, 3, 99, 80, None) == at_230


# --- 4. A poisoned reading must not disable a stop condition -----------------


@pytest.mark.asyncio
async def test_a_nan_battery_soc_does_not_keep_grid_charging_on(mock_hass, caplog):
    """`nan` parses as a float and then makes every comparison False.

    The target would never count as reached, so grid charging would run to the
    end of the window no matter how full the battery is.
    """
    _register(mock_hass, battery="70")
    coordinator = _make(mock_hass)
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 60.0
    mock_hass.states.async_set(BATTERY, "nan", {"unit_of_measurement": "%"})
    mock_hass.services.async_call.reset_mock()

    await coordinator._async_update_data()

    # A nan SOC is treated like no SOC: the integration does not charge on it
    assert ("switch", "turn_on", GRID) not in _service_calls(mock_hass)
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_nan_min_soc_is_never_captured_as_the_original(mock_hass):
    """A captured nan would be written back to the inverter at the window end."""
    _register(mock_hass)
    mock_hass.states.async_set(MIN_SOC, "nan")
    coordinator = _make(mock_hass)

    await coordinator._on_window_start(WINDOW_START)

    assert coordinator.original_min_soc is None or coordinator.original_min_soc == coordinator.original_min_soc
    assert coordinator.original_min_soc != float("nan")
    await coordinator._stop_periodic_verification()


# --- 5. The connection limit may never stop watching ------------------------


@pytest.mark.asyncio
async def test_the_limit_still_reacts_when_the_planner_produced_nothing(mock_hass):
    """An unreadable battery SOC must not switch the protection off.

    The planner returns early and clears its setpoint then; if the listener
    gave up with it, the wallboxes could start and nothing would answer for a
    quarter of an hour.
    """
    _register(mock_hass)
    coordinator = _make(mock_hass)
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 100.0
    await coordinator._async_update_data()
    assert coordinator._planned_setpoint_written_w is not None
    mock_hass.services.async_call.reset_mock()

    coordinator.planned_charge_power_w = None  # what the early returns leave behind
    mock_hass.states.async_set(GRID_IMPORT, "34600", {"unit_of_measurement": "W"})
    state = mock_hass.states.get(GRID_IMPORT)
    state.last_reported = INSIDE + timedelta(minutes=1)
    state.last_updated = state.last_reported
    state.last_changed = state.last_reported

    with patch(NOW, return_value=INSIDE + timedelta(minutes=1)):
        await coordinator._react_to_grid_import()

    written = _ac_writes(mock_hass)
    assert written, "the limit has to answer the rising load"
    # 34.6 kW measured, at most our own 1 667 W of it: what is left of the
    # budget after the safety margin is well under what was running before
    assert written[-1] < 1667.0
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_backup_mode_stops_the_limit_from_writing_after_the_target(mock_hass):
    """Island mode owns the inverter; the limit has nothing to do off-grid."""
    _register(mock_hass, battery="70")
    mock_hass.states.async_set(BACKUP, "on")
    coordinator = _make(mock_hass, {**CONFIG, CONF_BACKUP_MODE_ENTITY: BACKUP})
    coordinator.is_active = True
    coordinator.target_reached = True
    coordinator._planned_setpoint_written_w = 5000.0
    mock_hass.states.async_set(GRID_IMPORT, "40000", {"unit_of_measurement": "W"})
    state = mock_hass.states.get(GRID_IMPORT)
    state.last_reported = INSIDE
    state.last_updated = state.last_reported
    state.last_changed = state.last_reported
    mock_hass.services.async_call.reset_mock()

    await coordinator._enforce_grid_limit_now()

    assert _ac_writes(mock_hass) == []
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_our_own_share_can_never_exceed_the_whole_import(mock_hass):
    """Crediting more than flows would make the foreign load look negative."""
    _register(mock_hass)
    coordinator = _make(mock_hass)
    coordinator.is_active = True
    coordinator._planned_setpoint_written_w = 10000.0
    mock_hass.states.async_set(GRID_IMPORT, "800", {"unit_of_measurement": "W"})
    state = mock_hass.states.get(GRID_IMPORT)
    state.last_reported = INSIDE
    state.last_updated = state.last_reported
    state.last_changed = state.last_reported

    coordinator._grid_limited_setpoint(20000.0)

    # 800 W import, at most all of it ours: no foreign load, full budget free
    assert coordinator.grid_charge_headroom_w == pytest.approx(BUDGET_W - 500)
    assert coordinator.grid_limit_attributes()["other_load_w"] == 0.0
    await coordinator._stop_periodic_verification()
