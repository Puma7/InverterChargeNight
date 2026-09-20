"""Tests for the house connection limit (plan 008).

Two wallboxes (22 kW + 11 kW) and the battery run in the same six-hour window on
a 63 A three-phase connection. The battery is the only load this integration
controls, so it is the one that has to give way. Everything here checks one
direction: when anything is uncertain the setpoint goes *down*, never up.

The pure arithmetic lives in ``planner.py`` and is tested first; the coordinator
tests then run a real ``InverterChargeNightCoordinator`` against the strict
``mock_hass`` fixture, in the same style as ``test_window_lifecycle.py``.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio

from homeassistant.helpers import entity_registry as er

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night import config_flow
from custom_components.inverter_charge_night.const import (
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BRIDGE_RESERVE_KWH,
    CONF_CHARGE_EFFICIENCY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_BACKUP_MODE_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_GRID_CONTINUOUS_PCT,
    CONF_GRID_HEADROOM_W,
    CONF_GRID_IMPORT_ENTITY,
    CONF_GRID_MAX_CONTINUOUS_W,
    CONF_GRID_PHASES,
    CONF_GRID_VOLTAGE_V,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_MAIN_FUSE_A,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_OPERATION_MODE,
    CONF_PLANNER_MODE,
    CONF_PV_CROSSOVER_DELAY_MIN,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    DEFAULT_GRID_CONTINUOUS_PCT,
    DEFAULT_GRID_VOLTAGE_V,
    GRID_LIMIT_STALE_AFTER_S,
    MODE_MORNING_DISCHARGE,
    PLANNER_MODE_BRIDGE,
    PLANNER_MODE_HEADROOM,
)
from custom_components.inverter_charge_night.planner import (
    allowed_charge_power_w,
    grid_budget_w,
)
from custom_components.inverter_charge_night.sensor import GridChargeHeadroomSensor

# --- 1. The pure arithmetic -------------------------------------------------


def test_budget_63_a_three_phase_at_80_percent():
    """The owner's connection: 3 * 230 V * 63 A = 43 470 W, 80 % of it."""
    assert grid_budget_w(63, 3, 230, 80, None) == pytest.approx(34776.0)


def test_budget_single_phase():
    assert grid_budget_w(35, 1, 230, 80, None) == pytest.approx(6440.0)


def test_budget_explicit_value_wins_over_the_fuse():
    assert grid_budget_w(63, 3, 230, 80, 20000) == 20000.0


def test_budget_without_a_fuse_or_an_explicit_value_is_none():
    assert grid_budget_w(None, 3, 230, 80, None) is None
    assert grid_budget_w(0, 3, 230, 80, None) is None
    assert grid_budget_w(-63, 3, 230, 80, None) is None
    # A zero or negative explicit value is not a budget either
    assert grid_budget_w(None, 3, 230, 80, 0) is None


def test_budget_uses_a_lower_phase_count_when_the_configured_one_is_odd():
    """An unknown phase count must not widen the budget."""
    assert grid_budget_w(63, 2, 230, 80, None) == grid_budget_w(63, 1, 230, 80, None)
    assert grid_budget_w(63, 0, 230, 80, None) == grid_budget_w(63, 1, 230, 80, None)


def test_budget_falls_back_to_the_defaults_for_implausible_inputs():
    default = grid_budget_w(63, 3, DEFAULT_GRID_VOLTAGE_V, DEFAULT_GRID_CONTINUOUS_PCT, None)
    assert grid_budget_w(63, 3, 0, 80, None) == default
    assert grid_budget_w(63, 3, -230, 80, None) == default
    assert grid_budget_w(63, 3, 230, 0, None) == default
    assert grid_budget_w(63, 3, 230, 150, None) == default


def test_a_line_to_line_voltage_does_not_widen_the_three_phase_budget():
    """400 V is the voltage between two phases, not the phase voltage.

    Somebody reading the number off the meter cabinet must not end up with a
    budget that is sqrt(3) too large - the limit would then never engage
    before the real connection limit is passed.
    """
    at_230 = grid_budget_w(63, 3, 230, 80, None)
    at_400 = grid_budget_w(63, 3, 400, 80, None)
    assert at_400 == pytest.approx(400 / math.sqrt(3) * 3 * 63 * 0.8)
    assert at_400 == pytest.approx(at_230, rel=0.01)
    assert at_400 < at_230 * 1.05


def test_a_voltage_that_is_no_phase_voltage_falls_back_to_the_default():
    """400 V on one phase is not a house connection, whatever was meant.

    Taking it at face value would hand the battery a budget 74 % too large, so
    the documented default is used instead - the only answer that can make the
    budget smaller rather than larger.
    """
    assert grid_budget_w(63, 1, 400, 80, None) == pytest.approx(
        DEFAULT_GRID_VOLTAGE_V * 63 * 0.8
    )
    # Same for a value far below anything a grid does
    assert grid_budget_w(63, 3, 12, 80, None) == pytest.approx(
        3 * DEFAULT_GRID_VOLTAGE_V * 63 * 0.8
    )


def test_budget_rejects_non_finite_inputs():
    assert grid_budget_w(math.nan, 3, 230, 80, None) is None
    assert grid_budget_w(math.inf, 3, 230, 80, None) is None
    assert grid_budget_w(63, 3, 230, 80, math.inf) is not math.inf


def test_allowed_subtracts_the_other_load_and_the_headroom():
    # 34 776 W budget, 500 W headroom, house at 20 000 W of which 5 000 W is ours
    allowed = allowed_charge_power_w(34776, 20000, 5000, 500)
    assert allowed == pytest.approx(34776 - 500 - 15000)


def test_allowed_own_charge_is_not_counted_as_foreign_load():
    """Without subtracting our own charge the setpoint would collapse each poll."""
    with_own = allowed_charge_power_w(34776, 20000, 5000, 500)
    without_own = allowed_charge_power_w(34776, 15000, 0, 500)
    assert with_own == without_own


def test_allowed_is_zero_when_the_house_already_uses_the_budget():
    assert allowed_charge_power_w(34776, 40000, 0, 500) == 0.0
    assert allowed_charge_power_w(34776, 34776, 0, 500) == 0.0


def test_allowed_never_exceeds_the_budget_minus_the_headroom():
    # Feeding in (a negative import) must not earn the battery extra budget
    assert allowed_charge_power_w(10000, -5000, 0, 500) == 9500.0


def test_allowed_treats_nonsense_inputs_as_no_room():
    assert allowed_charge_power_w(math.nan, 1000, 0, 500) == 0.0
    assert allowed_charge_power_w(10000, math.nan, 0, 500) == 0.0
    assert allowed_charge_power_w(10000, 1000, math.nan, 500) == 0.0
    assert allowed_charge_power_w(10000, 1000, 0, math.nan) == 0.0


def test_allowed_ignores_a_negative_headroom_or_own_charge():
    assert allowed_charge_power_w(10000, 1000, -100, -500) == 9000.0


# --- 2. Coordinator fixtures ------------------------------------------------

MIN_SOC = "number.min_soc"
GRID = "switch.grid"
BATTERY = "sensor.soc"
PV = "sensor.pv"
SUN = "sun.sun"
AC_LIMIT = "number.ac_limit"
GRID_IMPORT = "sensor.grid_import"
BACKUP = "binary_sensor.backup_mode"
SENT = "sensor.charge_sent"
RECEIVED = "sensor.charge_received"

WINDOW_START = datetime(2026, 1, 15, 0, 0)
WINDOW_END = datetime(2026, 1, 15, 5, 59)
FOUR_HOURS_LEFT = datetime(2026, 1, 15, 1, 59)
# The debounce counts from the last write, and the regular poll writes too.
LATER = FOUR_HOURS_LEFT + timedelta(minutes=1)
MUCH_LATER = FOUR_HOURS_LEFT + timedelta(minutes=2)
# 40 -> 100 % of a 10 kWh battery in 4 h at 0.9 efficiency
PLANNED_W = 1667.0
NOW = "custom_components.inverter_charge_night.coordinator.dt_util.now"
TRACK = "custom_components.inverter_charge_night.coordinator.async_track_state_change_event"

BASE_CONFIG = {
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
    CONF_PLANNER_MODE: PLANNER_MODE_BRIDGE,
    CONF_AVG_HOUSE_LOAD_KW: 0.5,
    CONF_PV_CROSSOVER_DELAY_MIN: 90,
    CONF_BRIDGE_RESERVE_KWH: 0.5,
    CONF_CHARGE_EFFICIENCY: 0.9,
    CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
    CONF_MIN_CHARGE_POWER_W: 500,
    CONF_MAX_CHARGE_POWER_W: 10000,
}
# 63 A, three phases, 80 %: 34 776 W budget, 500 W headroom
LIMITED_CONFIG = {
    **BASE_CONFIG,
    CONF_GRID_IMPORT_ENTITY: GRID_IMPORT,
    CONF_MAIN_FUSE_A: 63,
    CONF_GRID_PHASES: 3,
    CONF_GRID_VOLTAGE_V: 230,
    CONF_GRID_CONTINUOUS_PCT: 80,
    CONF_GRID_HEADROOM_W: 500,
}
BUDGET_W = 34776.0


@pytest.fixture(autouse=True)
def _inside_window():
    with patch(NOW, return_value=FOUR_HOURS_LEFT):
        yield


def _make_coordinator(hass, config, options=None) -> InverterChargeNightCoordinator:
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = config
    entry.options = options or {}
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _register(hass, *, import_w="1000", unit="W", age_s=0.0):
    hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    hass.states.async_set(MIN_SOC, "8")
    hass.states.async_set(GRID, "off")
    hass.states.async_set(BATTERY, "40")
    hass.states.async_set(PV, "5", {"unit_of_measurement": "kWh"})
    hass.states.async_set(
        SUN,
        "below_horizon",
        {"next_rising": "2026-01-15T07:30:00+00:00", "next_setting": "2026-01-15T17:00:00+00:00"},
    )
    hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    if import_w is not None:
        _set_import(hass, import_w, unit=unit, age_s=age_s)


def _set_import(hass, value, *, unit="W", age_s=0.0):
    """Register the grid import with a real ``last_reported`` timestamp."""
    attributes = {"unit_of_measurement": unit} if unit is not None else {}
    hass.states.async_set(GRID_IMPORT, value, attributes)
    state = hass.states.get(GRID_IMPORT)
    state.last_reported = FOUR_HOURS_LEFT - timedelta(seconds=age_s)
    state.last_updated = state.last_reported
    state.last_changed = state.last_reported
    return state


@pytest_asyncio.fixture
async def limited(mock_hass):
    """Bridge coordinator with the house connection limit configured."""
    _register(mock_hass)
    coordinator = _make_coordinator(mock_hass, LIMITED_CONFIG)
    yield coordinator
    await coordinator._stop_periodic_verification()


@pytest_asyncio.fixture
async def unlimited(mock_hass):
    """The same coordinator without any house connection configuration."""
    _register(mock_hass, import_w=None)
    coordinator = _make_coordinator(mock_hass, BASE_CONFIG)
    yield coordinator
    await coordinator._stop_periodic_verification()


async def _start_with_target(coordinator, target: float) -> None:
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = target


def _ac_writes(hass) -> list[float]:
    return [
        c.args[2]["value"]
        for c in hass.services.async_call.await_args_list
        if c.args[0] == "number" and c.args[2].get("entity_id") == AC_LIMIT
    ]


# --- 3. The limit in the coordinator ----------------------------------------


@pytest.mark.asyncio
async def test_without_a_grid_entity_the_behaviour_is_unchanged(mock_hass, unlimited):
    """The completion criterion of plan 008: no grid entity, no limiting."""
    await _start_with_target(unlimited, 60.0)
    await unlimited._async_update_data()

    # 2 kWh missing in 4 h at 0.9 efficiency -> 556 W, exactly as before
    assert unlimited.planned_charge_power_w == 556.0
    assert _ac_writes(mock_hass) == [556.0]
    assert unlimited.grid_charge_headroom_w is None
    assert unlimited.grid_limit_attributes()["limited"] is False


@pytest.mark.asyncio
async def test_a_quiet_house_does_not_hold_the_battery_back(mock_hass, limited):
    """1 kW of other load leaves far more room than the planner asks for."""
    await _start_with_target(limited, 60.0)
    await limited._async_update_data()

    assert limited.planned_charge_power_w == 556.0
    assert _ac_writes(mock_hass) == [556.0]
    assert limited.grid_charge_headroom_w == pytest.approx(BUDGET_W - 500 - 1000)
    attributes = limited.grid_limit_attributes()
    assert attributes["limited"] is False
    assert attributes["budget_w"] == BUDGET_W
    assert attributes["other_load_w"] == 1000.0


@pytest.mark.asyncio
async def test_two_wallboxes_push_the_battery_down(mock_hass, limited):
    """33 kW of wallboxes leave 1 276 W of the 34 776 W budget after the headroom."""
    _set_import(mock_hass, "33000")
    # A target that would otherwise order the maximum
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()

    expected = BUDGET_W - 500 - 33000
    assert limited.planned_charge_power_w == pytest.approx(expected)
    assert _ac_writes(mock_hass) == [pytest.approx(expected)]
    assert limited.grid_limit_attributes()["limited"] is True


@pytest.mark.asyncio
async def test_no_room_at_all_stops_the_charge(mock_hass, limited, caplog):
    """Below the minimum charge power the setpoint is 0, not the minimum."""
    caplog.set_level(logging.INFO)
    _set_import(mock_hass, "40000")
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()

    assert limited.planned_charge_power_w == 0.0
    assert _ac_writes(mock_hass) == [0.0]
    assert "no room for the battery" in caplog.text


@pytest.mark.asyncio
async def test_the_own_charge_power_is_not_counted_twice(mock_hass, limited):
    """The import already contains our own charge; without subtracting it the
    setpoint would ratchet down to zero over consecutive polls."""
    _set_import(mock_hass, "33000")
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    first = limited.planned_charge_power_w
    assert first is not None

    # The inverter now really draws that much on top of the wallboxes
    _set_import(mock_hass, str(33000 + first))
    await limited._async_update_data()

    assert limited.planned_charge_power_w == pytest.approx(first)


def _set_sent(hass, value, *, unit="W", age_s=0.0):
    """Register the measured charge power with a real ``last_reported``."""
    hass.states.async_set(SENT, value, {"unit_of_measurement": unit})
    state = hass.states.get(SENT)
    state.last_reported = FOUR_HOURS_LEFT - timedelta(seconds=age_s)
    state.last_updated = state.last_reported
    state.last_changed = state.last_reported
    return state


@pytest_asyncio.fixture
async def measured(mock_hass):
    """The limited coordinator with the measured charge power configured."""
    _register(mock_hass)
    _set_sent(mock_hass, "0")
    coordinator = _make_coordinator(
        mock_hass, {**LIMITED_CONFIG, CONF_CHARGE_POWER_SENT_ENTITY: SENT}
    )
    yield coordinator
    await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_the_setpoint_is_not_mistaken_for_the_real_draw(mock_hass, measured):
    """A setpoint the inverter has not followed must not hide foreign load.

    The written value is a request. While the battery is still ramping up - or
    is not charging at all - the wallboxes account for the whole import, and
    subtracting the request would leave room that the connection does not have.
    """
    _set_import(mock_hass, "33000")
    await _start_with_target(measured, 100.0)
    await measured._async_update_data()
    first = BUDGET_W - 500 - 33000
    assert measured.planned_charge_power_w == pytest.approx(first)

    # The import is unchanged: the battery has not started drawing anything yet
    _set_sent(mock_hass, "0")
    await measured._async_update_data()

    assert measured.planned_charge_power_w == pytest.approx(first)
    assert measured.grid_limit_attributes()["other_load_w"] == pytest.approx(33000.0)


@pytest.mark.asyncio
async def test_the_measured_draw_is_subtracted_once_it_is_real(mock_hass, measured):
    """Once the battery really draws its setpoint, that share is ours again."""
    _set_import(mock_hass, "33000")
    await _start_with_target(measured, 100.0)
    await measured._async_update_data()
    first = measured.planned_charge_power_w
    assert first is not None

    _set_import(mock_hass, str(33000 + first))
    _set_sent(mock_hass, str(first))
    await measured._async_update_data()

    assert measured.planned_charge_power_w == pytest.approx(first)
    assert measured.grid_limit_attributes()["other_load_w"] == pytest.approx(33000.0)


@pytest.mark.asyncio
async def test_a_draw_above_the_setpoint_is_taken_at_face_value(mock_hass, measured):
    """The smaller of the two never overstates our own share of the import."""
    _set_import(mock_hass, "33000")
    await _start_with_target(measured, 100.0)
    await measured._async_update_data()
    written = measured._planned_setpoint_written_w
    assert written is not None

    _set_sent(mock_hass, str(written + 2000))
    assert measured._own_charge_draw_w() == pytest.approx(written)


@pytest.mark.asyncio
async def test_a_stale_measurement_falls_back_to_the_setpoint(mock_hass, measured):
    """An old reading says nothing about now; the request is the better guess."""
    _set_import(mock_hass, "33000")
    await _start_with_target(measured, 100.0)
    await measured._async_update_data()
    written = measured._planned_setpoint_written_w
    assert written is not None

    _set_sent(mock_hass, "0", age_s=GRID_LIMIT_STALE_AFTER_S + 60)
    assert measured._own_charge_draw_w() == pytest.approx(written)

    mock_hass.states.async_set(SENT, "unavailable")
    assert measured._own_charge_draw_w() == pytest.approx(written)


@pytest.mark.asyncio
async def test_backup_mode_keeps_the_integration_off_the_inverter(mock_hass):
    """Island mode: no grid import to limit, and the inverter is not ours."""
    _register(mock_hass, import_w="33000")
    mock_hass.states.async_set(BACKUP, "on")
    coordinator = _make_coordinator(
        mock_hass, {**LIMITED_CONFIG, CONF_BACKUP_MODE_ENTITY: BACKUP}
    )
    try:
        await _start_with_target(coordinator, 100.0)
        await coordinator._async_update_data()

        # The poll ends the window instead of driving the inverter
        assert _ac_writes(mock_hass) == []
        assert coordinator.is_active is False

        # A grid reading arriving before the window end has propagated is
        # answered by the listener's own backup check, not by a write
        coordinator.is_active = True
        _set_import(mock_hass, "40000")
        with patch(NOW, return_value=LATER):
            await coordinator._react_to_grid_import()

        assert _ac_writes(mock_hass) == []
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_kilowatt_units_are_understood(mock_hass, limited):
    _set_import(mock_hass, "33", unit="kW")
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()

    assert limited.planned_charge_power_w == pytest.approx(BUDGET_W - 500 - 33000)


@pytest.mark.asyncio
async def test_an_unusable_unit_falls_back_to_the_minimum(mock_hass, limited, caplog):
    """Reading megawatts as watts would remove the limit entirely."""
    _set_import(mock_hass, "0.033", unit="MW")
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()

    assert limited.planned_charge_power_w == 500.0
    assert _ac_writes(mock_hass) == [500.0]
    assert "unavailable or stale" in caplog.text


@pytest.mark.asyncio
async def test_an_unavailable_sensor_falls_back_to_the_minimum(mock_hass, limited, caplog):
    _set_import(mock_hass, "unavailable")
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()

    assert limited.planned_charge_power_w == 500.0
    assert limited.grid_limit_attributes()["grid_import_w"] is None
    assert "unavailable or stale" in caplog.text
    # Warned once per window, not on every poll
    caplog.clear()
    await limited._async_update_data()
    assert "unavailable or stale" not in caplog.text


@pytest.mark.asyncio
async def test_a_stale_reading_falls_back_to_the_minimum(mock_hass, limited):
    _set_import(mock_hass, "500", age_s=GRID_LIMIT_STALE_AFTER_S + 60)
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()

    assert limited.planned_charge_power_w == 500.0


@pytest.mark.asyncio
async def test_a_missing_reading_never_raises_the_setpoint(mock_hass, limited):
    """The plan's second STOP condition, checked directly."""
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    with_reading = limited.planned_charge_power_w
    assert with_reading == PLANNED_W

    _set_import(mock_hass, "unknown")
    await limited._async_update_data()

    assert limited.planned_charge_power_w is not None
    assert limited.planned_charge_power_w < with_reading


@pytest.mark.asyncio
async def test_an_unreadable_value_falls_back_to_the_minimum(mock_hass, limited):
    _set_import(mock_hass, "n/a")
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()

    assert limited.planned_charge_power_w == 500.0


@pytest.mark.asyncio
async def test_a_sensor_without_a_unit_is_not_trusted(mock_hass, limited):
    """A kW template sensor without a unit would read 33 as 33 W and lift the cap.

    Every unusable unit, including none at all, falls back to the minimum charge
    power instead of charging at whatever the missing information would allow.
    """
    _set_import(mock_hass, "33", unit=None)
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()

    assert limited.planned_charge_power_w == pytest.approx(
        float(limited.config[CONF_MIN_CHARGE_POWER_W])
    )
    assert limited._grid_import_w is None


@pytest.mark.asyncio
async def test_an_explicit_budget_takes_precedence_over_the_fuse(mock_hass):
    """With the 63 A fuse there would be room for the planner's full request."""
    _register(mock_hass, import_w="3500")
    coordinator = _make_coordinator(
        mock_hass, {**LIMITED_CONFIG, CONF_GRID_MAX_CONTINUOUS_W: 5000}
    )
    try:
        await _start_with_target(coordinator, 100.0)
        await coordinator._async_update_data()
        assert coordinator.planned_charge_power_w == pytest.approx(5000 - 500 - 3500)
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_grid_entity_without_a_budget_does_not_limit(mock_hass):
    """Without a fuse size or an explicit budget there is nothing to measure against."""
    _register(mock_hass, import_w="40000")
    config = {**BASE_CONFIG, CONF_GRID_IMPORT_ENTITY: GRID_IMPORT}
    coordinator = _make_coordinator(mock_hass, config)
    try:
        await _start_with_target(coordinator, 60.0)
        await coordinator._async_update_data()
        assert coordinator.planned_charge_power_w == 556.0
        assert coordinator.grid_charge_headroom_w is None
    finally:
        await coordinator._stop_periodic_verification()


# --- 4. The limit applies in both planner modes -----------------------------


@pytest.mark.asyncio
async def test_headroom_mode_writes_the_limited_setpoint(mock_hass):
    """The limit is a protection: it must not depend on the planner mode."""
    _register(mock_hass, import_w="33000")
    config = {**LIMITED_CONFIG, CONF_PLANNER_MODE: PLANNER_MODE_HEADROOM}
    coordinator = _make_coordinator(mock_hass, config)
    try:
        await _start_with_target(coordinator, 100.0)
        await coordinator._async_update_data()
        expected = BUDGET_W - 500 - 33000
        assert coordinator.planned_charge_power_w == pytest.approx(expected)
        assert _ac_writes(mock_hass) == [pytest.approx(expected)]
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_headroom_mode_without_the_limit_writes_nothing(mock_hass):
    """Unchanged behaviour: in headroom mode the planner does not own the setpoint."""
    _register(mock_hass, import_w=None)
    config = {**BASE_CONFIG, CONF_PLANNER_MODE: PLANNER_MODE_HEADROOM}
    coordinator = _make_coordinator(mock_hass, config)
    try:
        await _start_with_target(coordinator, 60.0)
        await coordinator._async_update_data()
        assert coordinator.planned_charge_power_w == 556.0
        assert _ac_writes(mock_hass) == []
    finally:
        await coordinator._stop_periodic_verification()


# --- 5. The efficiency finder is capped too ---------------------------------


def _finder_config() -> dict:
    return {
        **LIMITED_CONFIG,
        CONF_AUTO_EFFICIENT_CHARGE: True,
        CONF_CHARGE_POWER_SENT_ENTITY: SENT,
        CONF_CHARGE_POWER_RECEIVED_ENTITY: RECEIVED,
    }


@pytest.mark.asyncio
async def test_the_efficiency_finder_cannot_exceed_the_connection(mock_hass, caplog):
    """A test the connection cannot carry is not started at all.

    Starting it anyway would file the measured loss under the requested power
    while the battery drew the limited one, and that wrong number would steer
    every later night.
    """
    caplog.set_level(logging.INFO)
    _register(mock_hass, import_w="33000")
    mock_hass.states.async_set(SENT, "0", {"unit_of_measurement": "W"})
    mock_hass.states.async_set(RECEIVED, "0", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, _finder_config())
    try:
        await _start_with_target(coordinator, 100.0)
        mock_hass.states.async_set(GRID, "on")
        await coordinator._async_update_data()

        assert coordinator._auto_test_active is False
        written = _ac_writes(mock_hass)
        assert written, "the connection limit must still reach the inverter"
        assert written[-1] <= BUDGET_W - 500 - 33000
        assert "Not testing the efficiency" in caplog.text
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_the_efficiency_finder_runs_when_there_is_room(mock_hass):
    """With a quiet house the finder works exactly as before."""
    _register(mock_hass, import_w="1000")
    mock_hass.states.async_set(SENT, "0", {"unit_of_measurement": "W"})
    mock_hass.states.async_set(RECEIVED, "0", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, _finder_config())
    try:
        await _start_with_target(coordinator, 100.0)
        mock_hass.states.async_set(GRID, "on")
        await coordinator._async_update_data()

        assert coordinator._auto_test_active is True
        assert coordinator._auto_test_power_w == _ac_writes(mock_hass)[-1]
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_running_efficiency_test_is_abandoned_when_the_house_load_rises(
    mock_hass, caplog
):
    """The finder owns the setpoint; the connection outranks it.

    The sample is dropped rather than continued at a lower power: its counters
    belong to the test power, and the finder can try again another night.
    """
    caplog.set_level(logging.INFO)
    _register(mock_hass, import_w="1000")
    mock_hass.states.async_set(SENT, "0", {"unit_of_measurement": "W"})
    mock_hass.states.async_set(RECEIVED, "0", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, _finder_config())
    try:
        await _start_with_target(coordinator, 100.0)
        mock_hass.states.async_set(GRID, "on")
        await coordinator._async_update_data()
        assert coordinator._auto_test_active is True
        mock_hass.services.async_call.reset_mock()

        _set_import(mock_hass, "34000")  # both wallboxes start mid-test
        with patch(NOW, return_value=LATER):
            await coordinator._react_to_grid_import()

        assert coordinator._auto_test_active is False
        assert _ac_writes(mock_hass) == [0.0]
        assert "abandoning the efficiency test" in caplog.text
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_direct_write_above_the_limit_is_capped(mock_hass, limited):
    """``_set_ac_charge_limit_w`` is the single write path and caps there too."""
    _set_import(mock_hass, "33000")
    await _start_with_target(limited, 100.0)
    mock_hass.services.async_call.reset_mock()

    await limited._set_ac_charge_limit_w(10000)

    assert _ac_writes(mock_hass) == [pytest.approx(BUDGET_W - 500 - 33000)]


# --- 6. Never outside a window ----------------------------------------------


@pytest.mark.asyncio
async def test_the_limit_never_engages_without_an_active_window(mock_hass, limited):
    """First STOP condition: outside a window the integration controls nothing."""
    _set_import(mock_hass, "40000")
    assert limited.is_active is False

    assert limited._grid_limited_setpoint(9000.0) == 9000.0
    assert limited.grid_charge_headroom_w is None


@pytest.mark.asyncio
async def test_the_listener_is_armed_at_the_window_start_and_dropped_at_the_end(
    mock_hass, limited
):
    await limited._on_window_start(WINDOW_START)
    assert limited._grid_limit_listener is not None

    await limited._on_window_end(WINDOW_END)
    assert limited._grid_limit_listener is None


@pytest.mark.asyncio
async def test_no_listener_without_a_configured_limit(mock_hass, unlimited):
    await unlimited._on_window_start(WINDOW_START)
    assert unlimited._grid_limit_listener is None


@pytest.mark.asyncio
async def test_no_listener_in_a_discharge_window(mock_hass):
    """A discharge window feeds the house from the battery, not from the grid."""
    _register(mock_hass)
    config = {**LIMITED_CONFIG, CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE}
    coordinator = _make_coordinator(mock_hass, config)
    try:
        coordinator._setup_grid_import_listener()
        assert coordinator._grid_limit_listener is None
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_mode_switch_drops_the_listener(mock_hass, limited):
    await limited._on_window_start(WINDOW_START)
    assert limited._grid_limit_listener is not None

    await limited.async_apply_operation_mode(MODE_MORNING_DISCHARGE)

    assert limited._grid_limit_listener is None


# --- 7. Reacting between polls ----------------------------------------------


@pytest.mark.asyncio
async def test_a_rising_house_load_lowers_the_setpoint_immediately(mock_hass, limited):
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    assert limited._planned_setpoint_written_w == PLANNED_W
    mock_hass.services.async_call.reset_mock()

    # A wallbox starts: 34.5 kW on the meter, our own charge included
    _set_import(mock_hass, "34500")
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()

    expected = BUDGET_W - 500 - (34500 - PLANNED_W)
    assert limited._planned_setpoint_written_w == pytest.approx(expected)
    assert limited.planned_charge_power_w == pytest.approx(expected)
    assert _ac_writes(mock_hass) == [pytest.approx(expected)]


@pytest.mark.asyncio
async def test_a_falling_house_load_waits_for_the_next_poll(mock_hass, limited):
    """Upwards only on the regular poll, so a short dip cannot raise the setpoint."""
    _set_import(mock_hass, "33000")
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    written = limited._planned_setpoint_written_w
    mock_hass.services.async_call.reset_mock()

    _set_import(mock_hass, "1000")
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()

    assert limited._planned_setpoint_written_w == written
    assert _ac_writes(mock_hass) == []


@pytest.mark.asyncio
async def test_a_small_change_does_not_trigger_a_write(mock_hass, limited):
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    mock_hass.services.async_call.reset_mock()

    # 50 W more load is below PLANNED_POWER_WRITE_THRESHOLD_W
    _set_import(mock_hass, str(1000 + PLANNED_W + 50))
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()

    assert _ac_writes(mock_hass) == []


@pytest.mark.asyncio
async def test_the_listener_is_debounced(mock_hass, limited):
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    _set_import(mock_hass, "34500")
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()
        mock_hass.services.async_call.reset_mock()

        _set_import(mock_hass, "40000")
        await limited._react_to_grid_import()

    assert _ac_writes(mock_hass) == []


@pytest.mark.asyncio
async def test_the_debounce_expires(mock_hass, limited):
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    _set_import(mock_hass, "34500")
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()
    mock_hass.services.async_call.reset_mock()

    _set_import(mock_hass, "42000")
    with patch(NOW, return_value=MUCH_LATER):
        await limited._react_to_grid_import()

    assert _ac_writes(mock_hass) == [0.0]


@pytest.mark.asyncio
async def test_the_listener_does_nothing_outside_a_window(mock_hass, limited):
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    mock_hass.services.async_call.reset_mock()
    limited.is_active = False

    _set_import(mock_hass, "40000")
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()

    assert _ac_writes(mock_hass) == []


@pytest.mark.asyncio
async def test_the_listener_does_nothing_before_the_first_write(mock_hass, limited):
    await limited._on_window_start(WINDOW_START)
    assert limited._planned_setpoint_written_w is None

    _set_import(mock_hass, "40000")
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()

    assert _ac_writes(mock_hass) == []


@pytest.mark.asyncio
async def test_the_limit_stays_on_the_inverter_after_the_target_is_reached(
    mock_hass, limited
):
    """Reaching the charge target does not end the window.

    The raised min SOC floor can still make the inverter buy power, and the
    wallboxes may still be running, so the connection limit has to stay on the
    inverter until the window really ends - it must not be handed back to the
    user's own value hours early.
    """
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    mock_hass.services.async_call.reset_mock()
    limited.target_reached = True

    _set_import(mock_hass, "40000")  # the house alone is over the budget
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()

    assert _ac_writes(mock_hass) == [0.0]
    # Holding the cap is the limit doing its job, so it must still say so
    assert limited.grid_limit_attributes()["limited"] is True


@pytest.mark.asyncio
async def test_a_cap_on_a_battery_that_draws_nothing_is_not_subtracted(
    mock_hass, limited
):
    """After the target the written limit caps nothing, so it is not our load.

    Subtracting it would hide that much of the house load and leave the
    connection carrying more than the budget.
    """
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    assert limited._planned_setpoint_written_w is not None
    limited.target_reached = True

    assert limited._own_charge_draw_w() == 0.0

    _set_import(mock_hass, "34600")
    mock_hass.services.async_call.reset_mock()
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()

    assert _ac_writes(mock_hass) == [0.0]


@pytest.mark.asyncio
async def test_holding_the_cap_does_not_write_on_every_reading(mock_hass, limited):
    """A power sensor reports every few seconds; the inverter must not follow."""
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    limited.target_reached = True
    mock_hass.services.async_call.reset_mock()

    _set_import(mock_hass, "34000")
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()
    first = _ac_writes(mock_hass)
    assert len(first) == 1

    # Two more readings inside the debounce, and one with the same load after it
    for offset in (2, 4):
        _set_import(mock_hass, "34000")
        with patch(NOW, return_value=LATER + timedelta(seconds=offset)):
            await limited._react_to_grid_import()
    assert _ac_writes(mock_hass) == first

    with patch(NOW, return_value=LATER + timedelta(minutes=5)):
        await limited._react_to_grid_import()
    assert _ac_writes(mock_hass) == first, "an unchanged load needs no second write"


@pytest.mark.asyncio
async def test_stopping_grid_charging_keeps_the_limit_while_the_window_runs(
    mock_hass, limited
):
    """_stop_grid_charging must not restore the user's full charge limit mid-window."""
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    original = float(limited._original_ac_charge_power or 0)
    mock_hass.services.async_call.reset_mock()
    _set_import(mock_hass, "40000")

    with patch(NOW, return_value=LATER):
        await limited._stop_grid_charging()

    assert original not in _ac_writes(mock_hass)
    assert limited._original_ac_charge_power == original  # still restorable at window end


@pytest.mark.asyncio
async def test_the_listener_leaves_the_finder_alone_while_there_is_room(
    mock_hass, limited
):
    """Nothing to correct: the finder's value fits inside the budget."""
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()
    mock_hass.services.async_call.reset_mock()
    limited._auto_test_active = True
    limited._auto_test_power_w = 2000

    _set_import(mock_hass, "1000")
    with patch(NOW, return_value=LATER):
        await limited._react_to_grid_import()

    assert _ac_writes(mock_hass) == []
    assert limited._auto_test_active is True


@pytest.mark.asyncio
async def test_a_listener_error_is_logged_and_swallowed(mock_hass, limited, caplog):
    """A broken callback must not take the event loop down with it."""
    with patch(TRACK) as track:
        limited._setup_grid_import_listener()
    callback = track.call_args.args[2]
    limited._react_to_grid_import = AsyncMock(side_effect=RuntimeError("boom"))

    await callback(MagicMock())

    assert "Unexpected error in grid import listener" in caplog.text


@pytest.mark.asyncio
async def test_the_listener_forwards_a_state_change(mock_hass, limited):
    """The registered callback really reaches the throttling code."""
    with patch(TRACK) as track:
        limited._setup_grid_import_listener()
    assert track.call_args.args[1] == GRID_IMPORT
    callback = track.call_args.args[2]
    limited._react_to_grid_import = AsyncMock()

    await callback(MagicMock())

    limited._react_to_grid_import.assert_awaited_once()


# --- 8. The sensor ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sensor_shows_the_headroom_and_its_inputs(mock_hass, limited):
    _set_import(mock_hass, "33000")
    await _start_with_target(limited, 100.0)
    await limited._async_update_data()

    sensor = GridChargeHeadroomSensor(limited, limited.entry)
    assert sensor.native_value == pytest.approx(BUDGET_W - 500 - 33000)
    attributes = sensor.extra_state_attributes
    assert attributes["budget_w"] == BUDGET_W
    assert attributes["grid_import_w"] == 33000.0
    assert attributes["other_load_w"] == 33000.0
    assert attributes["limited"] is True


@pytest.mark.asyncio
async def test_the_sensor_reports_limited_against_what_was_asked_for(mock_hass, limited):
    """``limited`` means the connection is holding the battery back.

    A connection that carries less than the configured maximum is not by
    itself holding anything back: as long as the battery gets the power the
    plan asked for, nothing is being taken away.
    """
    # 20 kW of other load leaves 14 276 W: less than the 10 kW maximum the
    # battery could take, but far more than the 556 W the plan asks for.
    _set_import(mock_hass, "20000")
    await _start_with_target(limited, 60.0)
    await limited._async_update_data()

    assert limited.planned_charge_power_w == 556.0
    assert limited.grid_limit_attributes()["limited"] is False

    # Now the wallboxes start and the plan cannot be met any more
    _set_import(mock_hass, "34600")
    await limited._async_update_data()

    assert limited.planned_charge_power_w == 0.0
    assert limited.grid_limit_attributes()["limited"] is True


@pytest.mark.asyncio
async def test_the_sensor_is_none_without_a_window(mock_hass, limited):
    sensor = GridChargeHeadroomSensor(limited, limited.entry)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["limited"] is False


@pytest.mark.asyncio
async def test_the_state_age_helper_ignores_non_datetime_stamps(mock_hass, limited):
    """A test double's MagicMock timestamps must not be subtracted from ``now``."""
    state = MagicMock()
    assert InverterChargeNightCoordinator._state_age_s(state, FOUR_HOURS_LEFT) is None

    state = MagicMock()
    state.last_reported = datetime(2026, 1, 15, 1, 58, tzinfo=timezone.utc)
    aware_now = datetime(2026, 1, 15, 1, 59, tzinfo=timezone.utc)
    assert InverterChargeNightCoordinator._state_age_s(state, aware_now) == 60.0

    # Mixing naive and aware raises TypeError, which the helper answers with None
    state.last_reported = datetime(2026, 1, 15, 1, 58)
    assert InverterChargeNightCoordinator._state_age_s(state, aware_now) is None


# --- 9. The configuration wizard --------------------------------------------

_VALID_WIZARD_INPUT = {
    CONF_START_TIME: "00:00",
    CONF_END_TIME: "05:59",
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_MIN_CHARGE_POWER_W: 500,
    CONF_MAX_CHARGE_POWER_W: 10000,
}


def _wizard_errors(mock_hass, **overrides) -> dict:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: MagicMock())
        return config_flow._validate_user_input(
            {**_VALID_WIZARD_INPUT, **overrides}, mock_hass
        )


def test_a_complete_house_connection_is_accepted(mock_hass):
    errors = _wizard_errors(
        mock_hass,
        **{
            CONF_MAIN_FUSE_A: 63,
            CONF_GRID_PHASES: 3,
            CONF_GRID_VOLTAGE_V: 230,
            CONF_GRID_CONTINUOUS_PCT: 80,
            CONF_GRID_HEADROOM_W: 500,
        },
    )
    assert errors == {}


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({CONF_GRID_PHASES: 2}, {CONF_GRID_PHASES: "invalid_phases"}),
        ({CONF_MAIN_FUSE_A: 0}, {CONF_MAIN_FUSE_A: "invalid_current"}),
        ({CONF_GRID_MAX_CONTINUOUS_W: 0}, {CONF_GRID_MAX_CONTINUOUS_W: "invalid_power"}),
        ({CONF_GRID_HEADROOM_W: -1}, {CONF_GRID_HEADROOM_W: "invalid_power"}),
        ({CONF_GRID_VOLTAGE_V: 0}, {CONF_GRID_VOLTAGE_V: "invalid_voltage"}),
        ({CONF_GRID_CONTINUOUS_PCT: 0}, {CONF_GRID_CONTINUOUS_PCT: "invalid_soc"}),
        ({CONF_GRID_CONTINUOUS_PCT: 120}, {CONF_GRID_CONTINUOUS_PCT: "invalid_soc"}),
    ],
)
def test_implausible_house_connection_values_are_rejected(mock_hass, overrides, expected):
    assert _wizard_errors(mock_hass, **overrides) == expected


def test_a_grid_entity_without_a_budget_is_rejected(mock_hass):
    """The entity alone would be read on every poll and never act."""
    errors = _wizard_errors(
        mock_hass,
        **{CONF_GRID_IMPORT_ENTITY: GRID_IMPORT, CONF_CHARGE_POWER_ENTITY: AC_LIMIT},
    )
    assert errors == {CONF_MAIN_FUSE_A: "required_value"}

    assert (
        _wizard_errors(
            mock_hass,
            **{
                CONF_GRID_IMPORT_ENTITY: GRID_IMPORT,
                CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
                CONF_GRID_MAX_CONTINUOUS_W: 20000,
            },
        )
        == {}
    )


def test_a_grid_entity_without_a_charge_power_entity_is_rejected(mock_hass):
    """The limit works by writing a lower setpoint; without the entity it is inert."""
    errors = _wizard_errors(
        mock_hass,
        **{CONF_GRID_IMPORT_ENTITY: GRID_IMPORT, CONF_MAIN_FUSE_A: 63},
    )
    assert errors == {CONF_CHARGE_POWER_ENTITY: "required_entity"}


def test_the_power_step_offers_every_house_connection_field():
    keys = set(config_flow.STEP_POWER_KEYS)
    assert {
        CONF_GRID_IMPORT_ENTITY,
        CONF_MAIN_FUSE_A,
        CONF_GRID_PHASES,
        CONF_GRID_VOLTAGE_V,
        CONF_GRID_CONTINUOUS_PCT,
        CONF_GRID_MAX_CONTINUOUS_W,
        CONF_GRID_HEADROOM_W,
    } <= keys


def test_the_phase_count_is_stored_as_an_integer():
    """A number selector returns floats; the budget formula compares to 3."""
    stored = config_flow._finalize_data(
        {**_VALID_WIZARD_INPUT, CONF_GRID_PHASES: 3.0, CONF_GRID_HEADROOM_W: 500.0}
    )
    assert stored[CONF_GRID_PHASES] == 3
    assert isinstance(stored[CONF_GRID_PHASES], int)
    assert isinstance(stored[CONF_GRID_HEADROOM_W], int)


# --- 10. Both protections in one window -------------------------------------


def _min_soc_writes(hass) -> list[float]:
    return [
        c.args[2]["value"]
        for c in hass.services.async_call.await_args_list
        if c.args[0] == "number" and c.args[2].get("entity_id") == MIN_SOC
    ]


@pytest.mark.asyncio
async def test_the_connection_limit_and_the_discharge_block_run_together(mock_hass):
    """The combination this integration is actually installed for.

    The battery starts the window fuller than the plan needs, so the discharge
    block raises the min SOC to keep that charge - and a raised floor is exactly
    what can make the inverter keep buying after the charge target is reached.
    At the same time both wallboxes are running, so the house connection has
    almost nothing left. The limit has to hold while the block holds, and the
    window end has to give both settings back.
    """
    _register(mock_hass, import_w="33000")
    mock_hass.states.async_set(BATTERY, "70")  # more than the plan asks for
    coordinator = _make_coordinator(mock_hass, LIMITED_CONFIG)
    try:
        await coordinator._on_window_start(WINDOW_START)
        coordinator.initial_calculated_soc = 60.0
        await coordinator._async_update_data()

        # The block keeps the charge: the inverter carries the floor, not the target
        assert coordinator._window_floor_soc == 70.0
        assert coordinator.current_target_soc() == 60.0
        assert coordinator.inverter_floor_soc() == 70.0
        assert _min_soc_writes(mock_hass)[-1] == 70.0
        assert coordinator.target_reached is True

        # ... and the connection still holds the charge power down, because the
        # raised floor can make the inverter buy power at any moment
        mock_hass.services.async_call.reset_mock()
        _set_import(mock_hass, "34600")
        with patch(NOW, return_value=LATER):
            await coordinator._react_to_grid_import()

        assert _ac_writes(mock_hass) == [0.0]
        assert coordinator.grid_limit_attributes()["limited"] is True

        # The window end gives the inverter back what it had
        mock_hass.services.async_call.reset_mock()
        with patch(NOW, return_value=WINDOW_END):
            await coordinator._on_window_end(WINDOW_END)

        assert _min_soc_writes(mock_hass)[-1] == 8.0
        assert _ac_writes(mock_hass)[-1] == 6000.0
        assert coordinator._window_floor_soc is None
    finally:
        await coordinator._stop_periodic_verification()
