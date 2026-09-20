"""Tests for discharge-specific forecast entity routing and force discharge switch."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_OPERATION_MODE,
    CONF_PV_FORECAST_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
)
from tests.conftest import create_mock_state


def _make_coordinator(mock_hass, mock_config_entry, mode=MODE_MORNING_DISCHARGE, extra_config=None):
    """Create a real coordinator."""
    data = dict(mock_config_entry.data)
    data[CONF_OPERATION_MODE] = mode
    if extra_config:
        data.update(extra_config)
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
        coord.snow_nights = 0
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


def test_get_active_forecast_entity_before_noon_uses_today(mock_hass, mock_config_entry):
    """Before noon (e.g. 05:00) uses today's forecast entity."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_PV_FORECAST_TODAY_ENTITY: "sensor.solcast_today"},
    )
    with patch("custom_components.inverter_charge_night.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 6, 15, 5, 0)
        assert coord._get_active_forecast_entity() == "sensor.solcast_today"


def test_get_active_forecast_entity_after_noon_uses_tomorrow(mock_hass, mock_config_entry):
    """After noon (e.g. 23:00) uses tomorrow's forecast entity."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_PV_FORECAST_TODAY_ENTITY: "sensor.solcast_today"},
    )
    with patch("custom_components.inverter_charge_night.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 12, 15, 23, 0)
        assert coord._get_active_forecast_entity() == "sensor.pv_forecast"


def test_get_active_forecast_entity_before_noon_fallback_to_tomorrow(mock_hass, mock_config_entry):
    """Before noon falls back to tomorrow entity when today is not configured."""
    coord = _make_coordinator(mock_hass, mock_config_entry)
    with patch("custom_components.inverter_charge_night.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 6, 15, 3, 0)
        assert coord._get_active_forecast_entity() == "sensor.pv_forecast"


def test_get_active_forecast_entity_after_noon_fallback_to_today(mock_hass, mock_config_entry):
    """After noon falls back to today entity when tomorrow is not configured."""
    data = dict(mock_config_entry.data)
    del data[CONF_PV_FORECAST_ENTITY]
    data[CONF_PV_FORECAST_TODAY_ENTITY] = "sensor.solcast_today"
    coord = _make_coordinator(mock_hass, mock_config_entry, extra_config=data)
    coord.config[CONF_PV_FORECAST_ENTITY] = None
    coord.config[CONF_PV_FORECAST_TODAY_ENTITY] = "sensor.solcast_today"
    with patch("custom_components.inverter_charge_night.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 12, 15, 22, 0)
        assert coord._get_active_forecast_entity() == "sensor.solcast_today"


def test_get_active_forecast_entity_midnight_boundary(mock_hass, mock_config_entry):
    """At exactly 00:01 uses today's forecast."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_PV_FORECAST_TODAY_ENTITY: "sensor.solcast_today"},
    )
    with patch("custom_components.inverter_charge_night.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 1, 15, 0, 1)
        assert coord._get_active_forecast_entity() == "sensor.solcast_today"


def test_get_active_forecast_entity_noon_boundary(mock_hass, mock_config_entry):
    """At exactly 12:00 uses tomorrow's forecast."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_PV_FORECAST_TODAY_ENTITY: "sensor.solcast_today"},
    )
    with patch("custom_components.inverter_charge_night.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 6, 15, 12, 0)
        assert coord._get_active_forecast_entity() == "sensor.pv_forecast"


@pytest.mark.asyncio
async def test_control_discharge_activates_force_discharge_switch(mock_hass, mock_config_entry):
    """Test _control_discharge turns on the force discharge switch."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_FORCE_DISCHARGE_SWITCH: "switch.force_discharge"},
    )

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "8.0"),
        "sensor.battery_soc": create_mock_state("sensor.battery_soc", "80.0"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "off"),
        "switch.force_discharge": create_mock_state("switch.force_discharge", "off"),
    }.get(eid))

    await coord._control_discharge(35.0)

    calls = mock_hass.services.async_call.call_args_list
    domains_services = [(c[0][0], c[0][1]) for c in calls]
    assert ("number", "set_value") in domains_services
    assert ("switch", "turn_on") in domains_services
    turn_on_call = [c for c in calls if c[0][1] == "turn_on"][0]
    assert turn_on_call[0][2]["entity_id"] == "switch.force_discharge"


@pytest.mark.asyncio
async def test_control_discharge_skips_force_discharge_already_on(mock_hass, mock_config_entry):
    """Test _control_discharge doesn't re-turn-on force discharge if already on."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_FORCE_DISCHARGE_SWITCH: "switch.force_discharge"},
    )

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "35.0"),
        "sensor.battery_soc": create_mock_state("sensor.battery_soc", "80.0"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "off"),
        "switch.force_discharge": create_mock_state("switch.force_discharge", "on"),
    }.get(eid))

    await coord._control_discharge(35.0)
    for call in mock_hass.services.async_call.call_args_list:
        assert call[0][1] != "turn_on"


@pytest.mark.asyncio
async def test_control_discharge_turns_off_force_discharge_when_target_reached(mock_hass, mock_config_entry):
    """Test _control_discharge turns off force discharge when SOC already at target."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_FORCE_DISCHARGE_SWITCH: "switch.force_discharge"},
    )

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "35.0"),
        "sensor.battery_soc": create_mock_state("sensor.battery_soc", "30.0"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "off"),
        "switch.force_discharge": create_mock_state("switch.force_discharge", "on"),
    }.get(eid))

    await coord._control_discharge(35.0)
    calls = mock_hass.services.async_call.call_args_list
    assert len(calls) == 1
    assert calls[0][0] == ("switch", "turn_off", {"entity_id": "switch.force_discharge"})


@pytest.mark.asyncio
async def test_control_discharge_no_force_switch_configured(mock_hass, mock_config_entry):
    """Test _control_discharge works without force discharge switch."""
    coord = _make_coordinator(mock_hass, mock_config_entry)

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "8.0"),
        "sensor.battery_soc": create_mock_state("sensor.battery_soc", "80.0"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "off"),
    }.get(eid))

    await coord._control_discharge(35.0)
    calls = mock_hass.services.async_call.call_args_list
    assert len(calls) == 1
    assert calls[0][0][0] == "number"


@pytest.mark.asyncio
async def test_stop_force_discharge(mock_hass, mock_config_entry):
    """Test _stop_force_discharge turns off the switch."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_FORCE_DISCHARGE_SWITCH: "switch.force_discharge"},
    )

    mock_hass.states.get = MagicMock(return_value=create_mock_state("switch.force_discharge", "on"))
    await coord._stop_force_discharge()
    mock_hass.services.async_call.assert_called_once_with(
        "switch", "turn_off", {"entity_id": "switch.force_discharge"},
    )


@pytest.mark.asyncio
async def test_stop_force_discharge_already_off(mock_hass, mock_config_entry):
    """Test _stop_force_discharge is noop when switch already off."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_FORCE_DISCHARGE_SWITCH: "switch.force_discharge"},
    )

    mock_hass.states.get = MagicMock(return_value=create_mock_state("switch.force_discharge", "off"))
    await coord._stop_force_discharge()
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_stop_force_discharge_no_switch(mock_hass, mock_config_entry):
    """Test _stop_force_discharge is safe when no switch configured."""
    coord = _make_coordinator(mock_hass, mock_config_entry)
    await coord._stop_force_discharge()
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_reset_settings_turns_off_force_discharge(mock_hass, mock_config_entry):
    """Test _reset_settings turns off force discharge switch."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={CONF_FORCE_DISCHARGE_SWITCH: "switch.force_discharge"},
    )
    coord.original_min_soc = 8.0

    mock_hass.states.get = MagicMock(side_effect=lambda eid: {
        "number.kostal_min_soc": create_mock_state("number.kostal_min_soc", "35.0"),
        "switch.kostal_grid_charge": create_mock_state("switch.kostal_grid_charge", "off"),
        "switch.force_discharge": create_mock_state("switch.force_discharge", "on"),
    }.get(eid))

    await coord._reset_settings()

    calls = mock_hass.services.async_call.call_args_list
    force_off_calls = [c for c in calls if c[0][2].get("entity_id") == "switch.force_discharge"]
    assert len(force_off_calls) == 1
    assert force_off_calls[0][0][1] == "turn_off"


# --- Plan 006: the solar day is the calendar day of the window end -----------


def test_forecast_entity_window_not_over_midnight_uses_today_after_noon(mock_hass, mock_config_entry):
    """Window 22:00-23:30 at 22:30: the window ends today, so today's entity is used (was: tomorrow)."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry, mode=MODE_NIGHT_CHARGE,
        extra_config={
            CONF_PV_FORECAST_TODAY_ENTITY: "sensor.solcast_today",
            CONF_START_TIME: "22:00",
            CONF_END_TIME: "23:30",
        },
    )
    with patch("custom_components.inverter_charge_night.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 12, 15, 22, 30)
        assert coord._get_active_forecast_entity() == "sensor.solcast_today"


def test_forecast_entity_window_over_midnight_uses_tomorrow_in_evening(mock_hass, mock_config_entry):
    """Window 22:00-05:59 at 23:00: the window ends tomorrow."""
    coord = _make_coordinator(
        mock_hass, mock_config_entry, mode=MODE_NIGHT_CHARGE,
        extra_config={
            CONF_PV_FORECAST_TODAY_ENTITY: "sensor.solcast_today",
            CONF_START_TIME: "22:00",
            CONF_END_TIME: "05:59",
        },
    )
    with patch("custom_components.inverter_charge_night.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 12, 15, 23, 0)
        assert coord._get_active_forecast_entity() == "sensor.pv_forecast"
        # In the end minute the window is still today's
        mock_dt.now.return_value = datetime(2025, 12, 16, 5, 59, 30)
        assert coord._get_active_forecast_entity() == "sensor.solcast_today"


def test_forecast_entity_morning_discharge_window_uses_today(mock_hass, mock_config_entry):
    coord = _make_coordinator(
        mock_hass, mock_config_entry,
        extra_config={
            CONF_PV_FORECAST_TODAY_ENTITY: "sensor.solcast_today",
            CONF_START_TIME: "06:00",
            CONF_END_TIME: "08:00",
        },
    )
    with patch("custom_components.inverter_charge_night.coordinator.dt_util") as mock_dt:
        mock_dt.now.return_value = datetime(2025, 6, 15, 6, 0)
        assert coord._get_active_forecast_entity() == "sensor.solcast_today"
