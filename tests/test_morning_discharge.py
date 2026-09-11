"""Tests for morning discharge mode."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import timedelta

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_OPERATION_MODE,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    DOMAIN,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
)
from tests.conftest import create_mock_state


def _make_coordinator(mock_hass, mock_config_entry, mode=MODE_MORNING_DISCHARGE):
    """Create a real coordinator configured for discharge mode."""
    data = dict(mock_config_entry.data)
    data[CONF_OPERATION_MODE] = mode
    mock_config_entry.data = data
    mock_hass.services.async_call = AsyncMock()

    with patch.object(InverterChargeNightCoordinator, "__init__", lambda self, *a, **kw: None):
        coord = InverterChargeNightCoordinator.__new__(InverterChargeNightCoordinator)
        coord.hass = mock_hass
        coord.entry = mock_config_entry
        coord.config = data
        coord.operation_mode = mode
        coord.skip_next = False
        coord._skip_next_unsub = None
        coord._skip_next_until = None
        coord.original_min_soc = None
        coord.is_active = True
        coord.is_enabled = True
        coord.calculated_soc = None
        coord.initial_calculated_soc = 35.0
        coord.minimum_calculated_soc = 35.0
        coord.target_reached = False
        coord._time_triggers = []
        coord._last_soc_set = None
        coord.override_soc = None
        coord._original_absolute_charge_power = None
        coord._original_ac_charge_power = None
        coord._pending_reset = False
        coord._reset_retry_unsub = None
        coord._reset_retry_count = 0
        coord._battery_soc_listener = None
        coord._inverter_min_soc_listener = None
        coord._verification_task = None
        coord._verifying_min_soc = False
        coord._backup_mode_listener = None
        coord.auto_efficient_charge = False
        coord._auto_test_active = False
        coord._auto_test_power_w = None
        coord._auto_test_start = None
        coord._auto_last_sample_time = None
        coord._auto_energy_sent_wh = 0.0
        coord._auto_energy_received_wh = 0.0
        coord._auto_missing_entities_logged = False
        coord.data = {}
        coord.update_interval = timedelta(seconds=900)
        coord.logger = MagicMock()
        coord.name = "test"
        coord.async_request_refresh = AsyncMock()
    return coord


@pytest.mark.asyncio
async def test_is_discharge_mode_property(mock_hass, mock_config_entry):
    """Test is_discharge_mode property."""
    coord = _make_coordinator(mock_hass, mock_config_entry, mode=MODE_MORNING_DISCHARGE)
    assert coord.is_discharge_mode is True

    coord.operation_mode = MODE_NIGHT_CHARGE
    assert coord.is_discharge_mode is False


@pytest.mark.asyncio
async def test_is_target_reached_discharge(mock_hass, mock_config_entry):
    """Test _is_target_reached for discharge mode (current <= target)."""
    coord = _make_coordinator(mock_hass, mock_config_entry, mode=MODE_MORNING_DISCHARGE)
    assert coord._is_target_reached(30.0, 35.0) is True
    assert coord._is_target_reached(35.0, 35.0) is True
    assert coord._is_target_reached(40.0, 35.0) is False


@pytest.mark.asyncio
async def test_is_target_reached_charge(mock_hass, mock_config_entry):
    """Test _is_target_reached for charge mode (current >= target)."""
    coord = _make_coordinator(mock_hass, mock_config_entry, mode=MODE_NIGHT_CHARGE)
    assert coord._is_target_reached(90.0, 85.0) is True
    assert coord._is_target_reached(85.0, 85.0) is True
    assert coord._is_target_reached(80.0, 85.0) is False


@pytest.mark.asyncio
async def test_control_discharge_sets_min_soc_and_turns_off_grid(mock_hass, mock_config_entry):
    """Test _control_discharge sets min SOC to target and turns off grid charge."""
    coord = _make_coordinator(mock_hass, mock_config_entry)

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "8.0"),
        "sensor.battery_soc": create_mock_state("sensor.battery_soc", "80.0"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "on"),
    }.get(eid))

    await coord._control_discharge(35.0)

    calls = mock_hass.services.async_call.call_args_list
    assert len(calls) >= 1
    set_value_call = calls[0]
    assert set_value_call[0] == ("number", "set_value", {"entity_id": "number.kostal_min_soc", "value": 35.0})
    turn_off_call = calls[1]
    assert turn_off_call[0] == ("switch", "turn_off", {"entity_id": "switch.kostal_grid_charge"})


@pytest.mark.asyncio
async def test_control_discharge_skips_when_soc_at_target(mock_hass, mock_config_entry):
    """Test _control_discharge skips when battery SOC already at or below target."""
    coord = _make_coordinator(mock_hass, mock_config_entry)

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "8.0"),
        "sensor.battery_soc": create_mock_state("sensor.battery_soc", "30.0"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "off"),
    }.get(eid))

    await coord._control_discharge(35.0)
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_control_discharge_skips_on_backup(mock_hass, mock_config_entry):
    """Test _control_discharge skips when backup mode is active."""
    coord = _make_coordinator(mock_hass, mock_config_entry)
    data = dict(coord.config)
    data["backup_mode_entity"] = "binary_sensor.backup"
    coord.config = data

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "binary_sensor.backup": create_mock_state("binary_sensor.backup", "on"),
    }.get(eid))

    await coord._control_discharge(35.0)
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_control_discharge_invalid_target(mock_hass, mock_config_entry):
    """Test _control_discharge rejects target outside user bounds."""
    coord = _make_coordinator(mock_hass, mock_config_entry)
    await coord._control_discharge(5.0)
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_control_discharge_stores_original_min_soc(mock_hass, mock_config_entry):
    """Test _control_discharge stores original min SOC on first call."""
    coord = _make_coordinator(mock_hass, mock_config_entry)
    assert coord.original_min_soc is None

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "8.0"),
        "sensor.battery_soc": create_mock_state("sensor.battery_soc", "80.0"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "off"),
    }.get(eid))

    await coord._control_discharge(35.0)
    assert coord.original_min_soc == 8.0


@pytest.mark.asyncio
async def test_control_discharge_skips_when_battery_unavailable(mock_hass, mock_config_entry):
    """Test _control_discharge skips when battery SOC is unavailable."""
    coord = _make_coordinator(mock_hass, mock_config_entry)

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "8.0"),
        "sensor.battery_soc": create_mock_state("sensor.battery_soc", "unavailable"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "off"),
    }.get(eid))

    await coord._control_discharge(35.0)
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_control_discharge_does_not_set_min_soc_when_already_at_target(mock_hass, mock_config_entry):
    """Test _control_discharge does not update min SOC if already close to target."""
    coord = _make_coordinator(mock_hass, mock_config_entry)

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "35.0"),
        "sensor.battery_soc": create_mock_state("sensor.battery_soc", "80.0"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "off"),
    }.get(eid))

    await coord._control_discharge(35.0)
    mock_hass.services.async_call.assert_not_called()
