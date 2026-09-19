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
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BACKUP_MODE_ENTITY,
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

    coordinator._control_kostal = AsyncMock(side_effect=_charge_past_the_target)

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
    config = {k: v for k, v in CONFIG.items() if k != CONF_KOSTAL_MIN_SOC_ENTITY}
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
