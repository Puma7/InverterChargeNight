"""The paths that only a misbehaving inverter reaches.

Everything here is a failure the integration has to survive without leaving
the battery in a state the user did not ask for: an entity that stops
answering, a service call that raises, a meter that reports a unit nobody
expected. A real ``InverterChargeNightCoordinator`` runs against the strict
``mock_hass`` fixture, as in ``test_window_lifecycle.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.sensor import PlannedChargePowerSensor
from custom_components.inverter_charge_night.const import (
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_AVG_HOUSE_LOAD_KW,
    CONF_BACKUP_MODE_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_END_TIME,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_GRID_IMPORT_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
    CONF_CHARGE_POWER_ENTITY,
    CONF_MIN_SOC_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_OPERATION_MODE,
    CONF_PLANNER_MODE,
    PLANNER_MODE_BRIDGE,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    MODE_MORNING_DISCHARGE,
)

MIN_SOC = "number.min_soc"
GRID = "switch.grid_charge"
BATTERY = "sensor.battery_soc"
PV = "sensor.pv"
FORCE = "switch.force_discharge"
BACKUP = "binary_sensor.backup"
METER = "sensor.grid_import_energy"

CONFIG = {
    CONF_MIN_SOC_ENTITY: MIN_SOC,
    CONF_GRID_CHARGE_SWITCH: GRID,
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
}

WINDOW_START = datetime(2026, 1, 15, 0, 0)
WINDOW_END = datetime(2026, 1, 15, 5, 59)
INSIDE_WINDOW = datetime(2026, 1, 15, 2, 0)


@pytest.fixture(autouse=True)
def _inside_window():
    with patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=INSIDE_WINDOW,
    ):
        yield


def _make_coordinator(hass, config=None) -> InverterChargeNightCoordinator:
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = config or CONFIG
    entry.options = {}
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest_asyncio.fixture
async def coordinator(mock_hass):
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    mock_hass.states.async_set(MIN_SOC, "8")
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(BATTERY, "40")
    mock_hass.states.async_set(PV, "5", {"unit_of_measurement": "kWh"})
    coordinator = _make_coordinator(mock_hass)
    yield coordinator
    await coordinator._stop_periodic_verification()


# --- the target is reached mid-window ----------------------------------------


@pytest.mark.asyncio
async def test_reaching_the_target_stops_grid_charging_once(mock_hass, coordinator):
    """The battery is full enough: charging stops, and stays stopped quietly."""
    await coordinator._on_window_start(WINDOW_START)
    await coordinator._async_update_data()
    target = coordinator.initial_calculated_soc
    assert target is not None

    # The inverter applied the commands and the battery has reached the target
    mock_hass.states.async_set(MIN_SOC, str(target))
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(BATTERY, str(target + 1.0))
    mock_hass.services.async_call.reset_mock()

    data = await coordinator._async_update_data()

    assert coordinator.target_reached is True
    assert data["target_reached"] is True
    assert call("switch", "turn_off", {"entity_id": GRID}) in (
        mock_hass.services.async_call.await_args_list
    )

    # A second update must not write again - with the switch still on, a
    # repeated stop would show up here.
    mock_hass.services.async_call.reset_mock()
    await coordinator._async_update_data()
    assert mock_hass.services.async_call.await_args_list == []


# --- a reset that raises at window end ---------------------------------------


@pytest.mark.asyncio
async def test_a_reset_that_raises_at_window_end_still_clears_the_window(
    mock_hass, coordinator
):
    """The inverter refuses the reset: the window still ends and the retry is armed."""
    await coordinator._on_window_start(WINDOW_START)
    await coordinator._async_update_data()

    coordinator._reset_settings = AsyncMock(side_effect=RuntimeError("inverter offline"))
    coordinator._record_reset_outcome = MagicMock()

    await coordinator._on_window_end(WINDOW_END)

    coordinator._record_reset_outcome.assert_called_once_with(False)
    # A stuck _ending flag would make every later window check a no-op
    assert coordinator._ending is False
    assert coordinator.is_active is False
    assert coordinator.target_reached is False
    assert coordinator._window_floor_soc is None


# --- energy meters -----------------------------------------------------------


@pytest.mark.parametrize(
    "unit,reading,expected",
    [
        ("kWh", "1.5", 1500.0),
        ("Wh", "1500", 1500.0),
        ("MWh", "0.0015", 1500.0),
    ],
)
def test_an_energy_meter_is_read_in_its_own_unit(mock_hass, unit, reading, expected):
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set(METER, reading, {"unit_of_measurement": unit})
    assert coordinator._read_energy_wh(METER) == pytest.approx(expected)


@pytest.mark.parametrize("attributes", [{}, {"unit_of_measurement": "kvarh"}])
def test_an_energy_meter_without_a_usable_unit_is_refused(mock_hass, attributes):
    """Guessing the scale would put the measurement out by a factor of a thousand."""
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set(METER, "1500", attributes)
    assert coordinator._read_energy_wh(METER) is None


# --- an unreadable backup entity ---------------------------------------------


def test_an_unreadable_backup_entity_warns_once(mock_hass, caplog):
    """The integration cannot tell whether the house is on the battery - say so once."""
    config = dict(CONFIG) | {CONF_BACKUP_MODE_ENTITY: BACKUP}
    coordinator = _make_coordinator(mock_hass, config)
    mock_hass.states.async_set(BACKUP, "unavailable")

    with caplog.at_level("WARNING"):
        assert coordinator._is_backup_active() is False
        assert coordinator._is_backup_active() is False

    assert sum("is unavailable" in r.getMessage() for r in caplog.records) == 1

    # Once it answers again the next outage is reported afresh
    mock_hass.states.async_set(BACKUP, "off")
    assert coordinator._is_backup_active() is False
    assert coordinator._backup_unreadable_logged is False


# --- discharge mode without a usable floor -----------------------------------


DISCHARGE_CONFIG = dict(CONFIG) | {
    CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE,
    CONF_FORCE_DISCHARGE_SWITCH: FORCE,
}


def _discharge_coordinator(mock_hass, *, min_soc_state="8"):
    coordinator = _make_coordinator(mock_hass, DISCHARGE_CONFIG)
    mock_hass.states.async_set(MIN_SOC, min_soc_state)
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(BATTERY, "80")
    mock_hass.states.async_set(FORCE, "off")
    return coordinator


@pytest.mark.asyncio
async def test_discharge_with_a_usable_floor_starts_the_force_discharge(mock_hass):
    """The control case for the two tests below: this is what success looks like."""
    coordinator = _discharge_coordinator(mock_hass)

    await coordinator._control_discharge(35.0)

    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": 35.0}),
        call("switch", "turn_on", {"entity_id": FORCE}),
    ]


@pytest.mark.asyncio
async def test_discharge_waits_when_the_floor_entity_is_unavailable(mock_hass):
    """Discharging against an unknown floor could empty the battery past the user minimum."""
    coordinator = _discharge_coordinator(mock_hass, min_soc_state="unavailable")

    await coordinator._control_discharge(35.0)

    # Neither the floor nor the force discharge: nothing is written at all
    assert mock_hass.services.async_call.await_args_list == []


@pytest.mark.asyncio
async def test_discharge_waits_when_the_floor_cannot_be_written(mock_hass):
    """The write failed, so the floor on the inverter is not the one we planned for."""
    coordinator = _discharge_coordinator(mock_hass)

    async def _fail(domain, service, data, *args, **kwargs):
        if domain == "number":
            raise RuntimeError("inverter refused the write")

    mock_hass.services.async_call = AsyncMock(side_effect=_fail)

    await coordinator._control_discharge(35.0)

    # The floor write was attempted and failed, so the discharge does not start
    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": 35.0}),
    ]


# --- the target is crossed while we are writing -------------------------------


@pytest.mark.asyncio
async def test_a_target_crossed_during_the_update_stops_charging(mock_hass, coordinator):
    """The battery passes the target between the two SOC reads of one update."""
    await coordinator._on_window_start(WINDOW_START)
    await coordinator._async_update_data()
    target = coordinator.initial_calculated_soc
    assert target is not None

    mock_hass.states.async_set(MIN_SOC, str(target))
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(BATTERY, str(target - 1.0))
    coordinator.target_reached = False
    mock_hass.services.async_call.reset_mock()

    async def _charge_past_the_target(_target_soc):
        mock_hass.states.async_set(BATTERY, str(target + 0.5))

    coordinator._control_charge = AsyncMock(side_effect=_charge_past_the_target)

    data = await coordinator._async_update_data()

    assert coordinator.target_reached is True
    assert data["target_reached"] is True
    assert call("switch", "turn_off", {"entity_id": GRID}) in (
        mock_hass.services.async_call.await_args_list
    )


# --- the guards in front of the periodic verification -------------------------


def _armed_for_verification(mock_hass, config=None):
    coordinator = _make_coordinator(mock_hass, config)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._ending = False
    coordinator.initial_calculated_soc = 45.0
    coordinator.minimum_calculated_soc = 45.0
    coordinator.calculated_soc = 45.0
    return coordinator


@pytest.mark.asyncio
async def test_verification_stands_down_during_backup(mock_hass):
    """On battery power the inverter belongs to the house, not to us."""
    config = dict(CONFIG) | {CONF_BACKUP_MODE_ENTITY: BACKUP}
    coordinator = _armed_for_verification(mock_hass, config)
    mock_hass.states.async_set(BACKUP, "on")
    mock_hass.states.async_set(MIN_SOC, "8")

    await coordinator._verify_and_restore_min_soc()

    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_second_verification_does_not_run_alongside_the_first(mock_hass):
    """The listener and the periodic task can arrive together; one is enough."""
    coordinator = _armed_for_verification(mock_hass)
    coordinator._verifying_min_soc = True
    mock_hass.states.async_set(MIN_SOC, "8")

    await coordinator._verify_and_restore_min_soc()

    mock_hass.services.async_call.assert_not_awaited()
    # The flag belongs to the run that set it and must survive the second call
    assert coordinator._verifying_min_soc is True


@pytest.mark.asyncio
async def test_verification_without_a_target_writes_nothing(mock_hass):
    coordinator = _armed_for_verification(mock_hass)
    coordinator.initial_calculated_soc = None
    coordinator.minimum_calculated_soc = None
    coordinator.calculated_soc = None
    coordinator.override_soc = None
    mock_hass.states.async_set(MIN_SOC, "8")

    await coordinator._verify_and_restore_min_soc()

    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator._verifying_min_soc is False


@pytest.mark.asyncio
async def test_verification_without_a_min_soc_entity_writes_nothing(mock_hass):
    config = {k: v for k, v in CONFIG.items() if k != CONF_MIN_SOC_ENTITY}
    coordinator = _armed_for_verification(mock_hass, config)

    await coordinator._verify_and_restore_min_soc()

    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("reading", ["unavailable", "unknown", "not a number"])
@pytest.mark.asyncio
async def test_verification_needs_a_readable_min_soc(mock_hass, reading):
    """Without a reading there is nothing to compare, so nothing is rewritten."""
    coordinator = _armed_for_verification(mock_hass)
    mock_hass.states.async_set(MIN_SOC, reading)

    await coordinator._verify_and_restore_min_soc()

    mock_hass.services.async_call.assert_not_awaited()


# --- reading back the AC charge limit -----------------------------------------


@pytest.mark.asyncio
async def test_the_charge_limit_is_not_reread_without_a_target(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator._planned_setpoint_written_w = 5000.0
    coordinator._ac_charge_limit_target = MagicMock(return_value=None)

    await coordinator._verify_ac_charge_limit()

    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_charge_limit_is_not_rewritten(mock_hass):
    """A missing reading is not proof of drift; rewriting blindly would fight the inverter."""
    coordinator = _make_coordinator(mock_hass)
    coordinator._planned_setpoint_written_w = 5000.0
    coordinator._ac_charge_limit_target = MagicMock(return_value=("number.ac_limit", "W"))
    coordinator._get_power_w = MagicMock(return_value=None)

    await coordinator._verify_ac_charge_limit()

    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_charge_limit_that_matches_is_left_alone(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator._planned_setpoint_written_w = 5000.0
    coordinator._ac_charge_limit_target = MagicMock(return_value=("number.ac_limit", "W"))
    coordinator._get_power_w = MagicMock(return_value=5000.0)

    await coordinator._verify_ac_charge_limit()

    mock_hass.services.async_call.assert_not_awaited()


# --- a plan the inverter never receives ---------------------------------------

BRIDGE_CONFIG = dict(CONFIG) | {
    CONF_PLANNER_MODE: PLANNER_MODE_BRIDGE,
    CONF_AVG_HOUSE_LOAD_KW: 0.5,
    CONF_MIN_CHARGE_POWER_W: 1000,
    CONF_MAX_CHARGE_POWER_W: 10000,
}


def _bridge_coordinator(mock_hass, *, charge_power_entity: str | None = None):
    config = dict(BRIDGE_CONFIG)
    if charge_power_entity:
        config[CONF_CHARGE_POWER_ENTITY] = charge_power_entity
        mock_hass.states.async_set(charge_power_entity, "10000", {"unit_of_measurement": "W"})
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    mock_hass.states.async_set(MIN_SOC, "8")
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(BATTERY, "10")
    mock_hass.states.async_set(PV, "5", {"unit_of_measurement": "kWh"})
    mock_hass.states.async_set(
        "sun.sun",
        "below_horizon",
        {
            "next_rising": "2026-01-15T07:30:00+00:00",
            "next_setting": "2026-01-15T17:00:00+00:00",
        },
    )
    coordinator = _make_coordinator(mock_hass, config)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.mark.asyncio
async def test_a_plan_without_a_charge_limit_entity_is_not_marked_applied(mock_hass):
    """Bridge mode plans a charge power even with nothing to write it to.

    The number is worth showing - it is what the window would need - but the
    sensor has to say that the inverter is not being told, or the user reads a
    plan as a command.
    """
    coordinator = _bridge_coordinator(mock_hass)
    try:
        await coordinator._on_window_start(WINDOW_START)
        await coordinator._async_update_data()

        assert coordinator.planned_charge_power_w is not None
        assert coordinator.planned_charge_power_w > 0
        assert coordinator.planned_power_is_applied is False
        # Nothing was written to a number entity: only the min SOC and the switch
        assert [
            c for c in mock_hass.services.async_call.await_args_list if c.args[0] == "number"
        ] == [call("number", "set_value", {"entity_id": MIN_SOC, "value": ANY})]
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_plan_with_a_charge_limit_entity_is_written_and_marked_applied(mock_hass):
    """The control case: with somewhere to write, the same plan reaches the inverter."""
    ac_limit = "number.ac_charge_limit"
    coordinator = _bridge_coordinator(mock_hass, charge_power_entity=ac_limit)
    try:
        await coordinator._on_window_start(WINDOW_START)
        await coordinator._async_update_data()

        planned = coordinator.planned_charge_power_w
        assert planned is not None
        assert coordinator.planned_power_is_applied is True
        assert (
            call("number", "set_value", {"entity_id": ac_limit, "value": int(planned)})
            in mock_hass.services.async_call.await_args_list
        )
    finally:
        await coordinator._stop_periodic_verification()


def test_the_planned_power_sensor_publishes_whether_it_is_applied(mock_hass):
    """The flag has to be visible in Home Assistant, not just on the coordinator."""
    coordinator = _make_coordinator(mock_hass)
    entry = coordinator.entry
    sensor = PlannedChargePowerSensor(coordinator, entry)

    coordinator.planned_charge_power_w = 4500.0
    coordinator.planned_power_is_applied = False
    assert sensor.native_value == 4500.0
    assert sensor.extra_state_attributes == {"applied": False}

    coordinator.planned_power_is_applied = True
    assert sensor.extra_state_attributes == {"applied": True}


# --- the reset has to admit it failed ----------------------------------------
#
# _reset_settings returning True while a write failed is the worst outcome the
# integration has: the retry chain never arms, and the raised min SOC floor
# stays on the inverter until somebody notices by hand.


@pytest.mark.asyncio
async def test_a_failing_grid_charge_write_makes_the_reset_report_failure(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator.original_min_soc = 8.0
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "on")

    async def _fail(domain, service, data, *args, **kwargs):
        if domain == "switch":
            raise RuntimeError("inverter refused the switch")

    mock_hass.services.async_call = AsyncMock(side_effect=_fail)

    assert await coordinator._reset_settings() is False


@pytest.mark.asyncio
async def test_a_failing_force_discharge_write_makes_the_reset_report_failure(mock_hass):
    config = dict(CONFIG) | {CONF_FORCE_DISCHARGE_SWITCH: FORCE}
    coordinator = _make_coordinator(mock_hass, config)
    coordinator.original_min_soc = 8.0
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(FORCE, "on")

    async def _fail(domain, service, data, *args, **kwargs):
        if data.get("entity_id") == FORCE:
            raise RuntimeError("inverter refused the switch")

    mock_hass.services.async_call = AsyncMock(side_effect=_fail)

    assert await coordinator._reset_settings() is False


@pytest.mark.asyncio
async def test_an_unavailable_force_discharge_switch_makes_the_reset_report_failure(mock_hass):
    """Unreadable is not "already off": the switch may still be discharging."""
    config = dict(CONFIG) | {CONF_FORCE_DISCHARGE_SWITCH: FORCE}
    coordinator = _make_coordinator(mock_hass, config)
    coordinator.original_min_soc = 8.0
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(FORCE, "unavailable")

    assert await coordinator._reset_settings() is False


@pytest.mark.asyncio
async def test_a_clean_reset_reports_success(mock_hass):
    """The control case, so the three above cannot pass for the wrong reason."""
    config = dict(CONFIG) | {CONF_FORCE_DISCHARGE_SWITCH: FORCE}
    coordinator = _make_coordinator(mock_hass, config)
    coordinator.original_min_soc = 8.0
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(FORCE, "on")

    assert await coordinator._reset_settings() is True
    assert call("switch", "turn_off", {"entity_id": GRID}) in (
        mock_hass.services.async_call.await_args_list
    )
    assert call("switch", "turn_off", {"entity_id": FORCE}) in (
        mock_hass.services.async_call.await_args_list
    )


# --- the house connection limit never assumes a write landed ------------------


@pytest.mark.asyncio
async def test_a_failed_limit_write_does_not_move_the_reference(mock_hass):
    """The reference is what stands on the inverter. A failed write changed nothing.

    Moving it anyway would make the next reading believe the battery is already
    capped, and the limit would stop writing while the load is still there.
    """
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator._ending = False
    coordinator._unloading = False
    coordinator._planned_setpoint_written_w = 6000.0
    coordinator._grid_budget = MagicMock(return_value=8000.0)
    coordinator.config = dict(CONFIG) | {CONF_GRID_IMPORT_ENTITY: "sensor.grid_import"}
    coordinator._grid_limited_setpoint = MagicMock(return_value=2000.0)
    coordinator._grid_write_is_debounced = MagicMock(return_value=False)
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=False)

    await coordinator._enforce_grid_limit_now()

    coordinator._set_ac_charge_limit_w.assert_awaited_once_with(2000)
    assert coordinator._planned_setpoint_written_w == 6000.0
    assert coordinator.planned_power_is_applied is False


@pytest.mark.asyncio
async def test_a_successful_limit_write_moves_the_reference(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator._ending = False
    coordinator._unloading = False
    coordinator._planned_setpoint_written_w = 6000.0
    coordinator._grid_budget = MagicMock(return_value=8000.0)
    coordinator.config = dict(CONFIG) | {CONF_GRID_IMPORT_ENTITY: "sensor.grid_import"}
    coordinator._grid_limited_setpoint = MagicMock(return_value=2000.0)
    coordinator._grid_write_is_debounced = MagicMock(return_value=False)
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)

    await coordinator._enforce_grid_limit_now()

    assert coordinator._planned_setpoint_written_w == 2000.0
    assert coordinator.planned_power_is_applied is True


@pytest.mark.parametrize(
    "state,reason",
    [
        ("ending", "a window that is being torn down"),
        ("unloading", "an entry that is going away"),
        ("debounced", "a write that is too recent"),
    ],
)
@pytest.mark.asyncio
async def test_the_limit_does_not_write_while_it_must_not(mock_hass, state, reason):
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator._ending = state == "ending"
    coordinator._unloading = state == "unloading"
    coordinator._planned_setpoint_written_w = 6000.0
    coordinator._grid_budget = MagicMock(return_value=8000.0)
    coordinator.config = dict(CONFIG) | {CONF_GRID_IMPORT_ENTITY: "sensor.grid_import"}
    coordinator._grid_limited_setpoint = MagicMock(return_value=2000.0)
    coordinator._grid_write_is_debounced = MagicMock(return_value=state == "debounced")
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)

    await coordinator._enforce_grid_limit_now()

    coordinator._set_ac_charge_limit_w.assert_not_awaited()


# --- the absolute charge power limit -----------------------------------------


@pytest.mark.parametrize("limit", ["not a number", 0, -500])
@pytest.mark.asyncio
async def test_an_unusable_absolute_limit_is_not_written(mock_hass, limit):
    """Writing 0 or a garbage value would order "charge with at most nothing"."""
    config = dict(CONFIG) | {
        CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.absolute_max",
        CONF_ABSOLUTE_MAX_CHARGE_POWER_W: limit,
    }
    coordinator = _make_coordinator(mock_hass, config)
    mock_hass.states.async_set("number.absolute_max", "10000", {"unit_of_measurement": "W"})

    await coordinator._apply_absolute_charge_power_limit()

    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_absolute_limit_write_is_survived(mock_hass):
    """It is a cap, not the charge command: a failure must not take the window down."""
    config = dict(CONFIG) | {
        CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.absolute_max",
        CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 7000,
    }
    coordinator = _make_coordinator(mock_hass, config)
    mock_hass.states.async_set("number.absolute_max", "10000", {"unit_of_measurement": "W"})
    mock_hass.services.async_call = AsyncMock(side_effect=RuntimeError("inverter refused"))

    await coordinator._apply_absolute_charge_power_limit()  # does not raise

    assert coordinator._original_absolute_charge_power == 10000.0


@pytest.mark.asyncio
async def test_stopping_grid_charging_survives_a_failing_switch(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set(GRID, "on")
    mock_hass.services.async_call = AsyncMock(side_effect=RuntimeError("inverter refused"))

    await coordinator._stop_grid_charging()  # does not raise


@pytest.mark.asyncio
async def test_a_charge_limit_entity_without_unit_or_maximum_is_written_as_watts(mock_hass):
    """Stated in the log, because the scale is an assumption at that point."""
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set("number.ac_limit", "5000", {})

    assert await coordinator._write_ac_charge_limit("number.ac_limit", "number", 4000.0, "test")
    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": "number.ac_limit", "value": 4000.0})
    ]


@pytest.mark.asyncio
async def test_the_limit_stays_out_of_it_without_a_grid_import_entity(mock_hass):
    """No entity, no budget, no business writing a charge limit."""
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator._ending = False
    coordinator._unloading = False
    coordinator._planned_setpoint_written_w = 6000.0
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)

    await coordinator._enforce_grid_limit_now()

    coordinator._set_ac_charge_limit_w.assert_not_awaited()


# --- entities pointed at the wrong kind of thing ------------------------------


@pytest.mark.parametrize("entity_id", ["switch.discharge_limit", "sensor.discharge_limit"])
def test_a_discharge_limit_in_the_wrong_domain_is_refused(mock_hass, entity_id):
    """set_value on a switch does nothing; the block would be configured and inert."""
    config = dict(CONFIG) | {CONF_DISCHARGE_LIMIT_ENTITY: entity_id}
    coordinator = _make_coordinator(mock_hass, config)

    assert coordinator._discharge_limit_target() is None


def test_a_discharge_limit_number_is_accepted(mock_hass):
    config = dict(CONFIG) | {CONF_DISCHARGE_LIMIT_ENTITY: "number.discharge_limit"}
    coordinator = _make_coordinator(mock_hass, config)

    assert coordinator._discharge_limit_target() == ("number.discharge_limit", "number")


@pytest.mark.asyncio
async def test_an_unavailable_charge_limit_entity_fails_the_reset(mock_hass):
    """Reporting success would drop the captured original and leave the cap on."""
    config = dict(CONFIG) | {CONF_CHARGE_POWER_ENTITY: "number.ac_limit"}
    coordinator = _make_coordinator(mock_hass, config)
    coordinator._original_ac_charge_power = 10000.0
    mock_hass.states.async_set("number.ac_limit", "unavailable")

    assert await coordinator._reset_ac_charge_limit() is False
    # The capture survives, so the retry can still put it back
    assert coordinator._original_ac_charge_power == 10000.0


@pytest.mark.asyncio
async def test_stopping_force_discharge_survives_a_failing_switch(mock_hass):
    config = dict(CONFIG) | {CONF_FORCE_DISCHARGE_SWITCH: FORCE}
    coordinator = _make_coordinator(mock_hass, config)
    mock_hass.states.async_set(FORCE, "on")
    mock_hass.services.async_call = AsyncMock(side_effect=RuntimeError("inverter refused"))

    await coordinator._stop_force_discharge()  # does not raise


@pytest.mark.asyncio
async def test_a_retry_that_raises_is_recorded_as_another_failure(mock_hass):
    """Otherwise the retry chain ends on the one attempt that threw."""
    coordinator = _make_coordinator(mock_hass)
    coordinator._reset_settings = AsyncMock(side_effect=RuntimeError("inverter offline"))
    coordinator._record_reset_outcome = MagicMock()
    coordinator._pending_reset = True

    await coordinator._retry_reset()

    coordinator._record_reset_outcome.assert_called_once_with(False)


def test_no_discharge_limit_entity_means_no_target(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    assert coordinator._discharge_limit_target() is None


@pytest.mark.asyncio
async def test_a_refused_charge_limit_write_fails_the_reset(mock_hass):
    """Dropping the capture on a refused write would leave the cap on for good."""
    config = dict(CONFIG) | {CONF_CHARGE_POWER_ENTITY: "number.ac_limit"}
    coordinator = _make_coordinator(mock_hass, config)
    coordinator._original_ac_charge_power = 10000.0
    mock_hass.states.async_set("number.ac_limit", "5000", {"unit_of_measurement": "W"})
    coordinator._write_ac_charge_limit = AsyncMock(return_value=False)

    assert await coordinator._reset_ac_charge_limit() is False
    assert coordinator._original_ac_charge_power == 10000.0


@pytest.mark.asyncio
async def test_the_grid_import_reaction_stays_out_of_discharge_mode(mock_hass):
    """Discharging exports; there is no charge setpoint of ours to lower."""
    config = dict(CONFIG) | {
        CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE,
        CONF_GRID_IMPORT_ENTITY: "sensor.grid_import",
    }
    coordinator = _make_coordinator(mock_hass, config)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._ending = False
    coordinator._unloading = False
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)

    await coordinator._react_to_grid_import()

    coordinator._set_ac_charge_limit_w.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_lowering_write_leaves_the_reference_alone(mock_hass):
    config = dict(CONFIG) | {CONF_GRID_IMPORT_ENTITY: "sensor.grid_import"}
    coordinator = _make_coordinator(mock_hass, config)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._ending = False
    coordinator._unloading = False
    coordinator._planned_setpoint_written_w = 6000.0
    coordinator.planned_charge_power_w = 6000.0
    coordinator._grid_limited_setpoint = MagicMock(return_value=1500.0)
    coordinator._grid_write_is_debounced = MagicMock(return_value=False)
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=False)

    await coordinator._react_to_grid_import()

    coordinator._set_ac_charge_limit_w.assert_awaited_once_with(1500)
    assert coordinator._planned_setpoint_written_w == 6000.0


@pytest.mark.asyncio
async def test_a_backup_state_change_is_ignored_while_the_integration_is_off(mock_hass):
    config = dict(CONFIG) | {CONF_BACKUP_MODE_ENTITY: BACKUP}
    coordinator = _make_coordinator(mock_hass, config)
    coordinator.is_enabled = False
    coordinator._check_current_window = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    captured = {}
    with patch(
        "custom_components.inverter_charge_night.coordinator.async_track_state_change_event",
        side_effect=lambda hass, entity_id, cb: captured.setdefault("cb", cb) and MagicMock(),
    ):
        coordinator._setup_backup_mode_listener()

    await captured["cb"](MagicMock(data={"new_state": MagicMock(state="on")}))

    coordinator._check_current_window.assert_not_awaited()


@pytest.mark.asyncio
async def test_verification_without_a_floor_writes_nothing(mock_hass):
    coordinator = _armed_for_verification(mock_hass)
    coordinator.inverter_floor_soc = MagicMock(return_value=None)
    mock_hass.states.async_set(MIN_SOC, "8")

    await coordinator._verify_and_restore_min_soc()

    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator._verifying_min_soc is False


@pytest.mark.asyncio
async def test_a_verification_that_raises_still_releases_its_flag(mock_hass):
    """A stuck flag would make every later verification a no-op for the entry's life."""
    coordinator = _armed_for_verification(mock_hass)
    coordinator._apply_discharge_block = AsyncMock(side_effect=RuntimeError("inverter offline"))
    mock_hass.states.async_set(MIN_SOC, "8")

    await coordinator._verify_and_restore_min_soc()  # does not raise

    assert coordinator._verifying_min_soc is False


# --- every service call in the discharge path is survivable -------------------
#
# None of these five handlers may let an exception out: _control_discharge runs
# from the polling update, and a raise there would take the whole window down
# with it - including the parts that still worked.


@pytest.mark.asyncio
async def test_an_unparsable_battery_soc_does_not_stop_the_discharge_control(mock_hass):
    coordinator = _discharge_coordinator(mock_hass)
    mock_hass.states.async_set(BATTERY, "n/a")

    await coordinator._control_discharge(35.0)

    # Unreadable is not "already at target": the floor is still written
    assert call("number", "set_value", {"entity_id": MIN_SOC, "value": 35.0}) in (
        mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_a_failing_stop_of_force_discharge_is_survived(mock_hass):
    """The target is already reached; turning the switch off fails."""
    coordinator = _discharge_coordinator(mock_hass)
    mock_hass.states.async_set(BATTERY, "20")  # at or below the 35 % target
    mock_hass.states.async_set(FORCE, "on")
    mock_hass.services.async_call = AsyncMock(side_effect=RuntimeError("inverter refused"))

    await coordinator._control_discharge(35.0)  # does not raise


@pytest.mark.asyncio
async def test_a_failing_min_soc_preparation_is_survived(mock_hass):
    coordinator = _discharge_coordinator(mock_hass)
    coordinator._capture_original_min_soc = MagicMock(side_effect=RuntimeError("registry gone"))

    await coordinator._control_discharge(35.0)  # does not raise

    # The floor could not be prepared, so the discharge does not start
    assert (
        call("switch", "turn_on", {"entity_id": FORCE})
        not in mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_a_failing_grid_charge_turn_off_does_not_stop_the_discharge(mock_hass):
    coordinator = _discharge_coordinator(mock_hass)
    mock_hass.states.async_set(GRID, "on")

    async def _fail(domain, service, data, *args, **kwargs):
        if data.get("entity_id") == GRID:
            raise RuntimeError("inverter refused")

    mock_hass.services.async_call = AsyncMock(side_effect=_fail)

    await coordinator._control_discharge(35.0)

    assert call("switch", "turn_on", {"entity_id": FORCE}) in (
        mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_a_failing_force_discharge_turn_on_is_survived(mock_hass):
    coordinator = _discharge_coordinator(mock_hass)

    async def _fail(domain, service, data, *args, **kwargs):
        if data.get("entity_id") == FORCE:
            raise RuntimeError("inverter refused")

    mock_hass.services.async_call = AsyncMock(side_effect=_fail)

    await coordinator._control_discharge(35.0)  # does not raise


# --- the connection limit and the efficiency finder ---------------------------


@pytest.mark.asyncio
async def test_the_finder_is_not_limited_without_a_connection_limit(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)

    await coordinator._limit_the_efficiency_finder()

    coordinator._set_ac_charge_limit_w.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_a_running_test_the_finder_limit_uses_what_was_written(mock_hass):
    config = dict(CONFIG) | {CONF_GRID_IMPORT_ENTITY: "sensor.grid_import"}
    coordinator = _make_coordinator(mock_hass, config)
    coordinator._grid_limit_configured = MagicMock(return_value=True)
    coordinator._auto_test_active = False
    coordinator._planned_setpoint_written_w = 6000.0
    coordinator._grid_limited_setpoint = MagicMock(return_value=1500.0)
    coordinator._grid_write_is_debounced = MagicMock(return_value=False)
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)

    await coordinator._limit_the_efficiency_finder()

    coordinator._grid_limited_setpoint.assert_called_once_with(6000.0)
    coordinator._set_ac_charge_limit_w.assert_awaited_once_with(1500)


@pytest.mark.asyncio
async def test_a_debounced_write_does_not_touch_the_finder(mock_hass):
    config = dict(CONFIG) | {CONF_GRID_IMPORT_ENTITY: "sensor.grid_import"}
    coordinator = _make_coordinator(mock_hass, config)
    coordinator._grid_limit_configured = MagicMock(return_value=True)
    coordinator._auto_test_active = True
    coordinator._auto_test_power_w = 6000
    coordinator._grid_limited_setpoint = MagicMock(return_value=1500.0)
    coordinator._grid_write_is_debounced = MagicMock(return_value=True)
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)

    await coordinator._limit_the_efficiency_finder()

    coordinator._set_ac_charge_limit_w.assert_not_awaited()
    assert coordinator._auto_test_active is True


@pytest.mark.asyncio
async def test_an_unparsable_soc_during_verification_is_ignored(mock_hass):
    coordinator = _armed_for_verification(mock_hass)
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(BATTERY, "n/a")

    await coordinator._verify_and_restore_min_soc()  # does not raise

    assert coordinator.target_reached is False
    assert coordinator._verifying_min_soc is False
