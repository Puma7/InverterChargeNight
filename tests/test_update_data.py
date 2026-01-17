"""Tests for _async_update_data behavior."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_PV_FORECAST_ENTITY,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    DEFAULT_SAFE_FALLBACK_SOC,
)


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_async_update_data_disabled(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.is_enabled = False
    coordinator.is_active = True
    mock_hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    data = await coordinator._async_update_data()
    assert data["is_active"] is False
    assert data["calculated_soc"] is None


@pytest.mark.asyncio
async def test_async_update_data_backup_active(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.is_enabled = True
    coordinator.is_active = True
    coordinator.calculated_soc = 60.0
    coordinator._is_backup_active = MagicMock(return_value=True)
    coordinator._on_window_end = AsyncMock()
    data = await coordinator._async_update_data()
    coordinator._on_window_end.assert_awaited()
    assert data["calculated_soc"] is None
    assert data["is_active"] is False


@pytest.mark.asyncio
async def test_async_update_data_outside_date_range_triggers_end(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.is_enabled = True
    coordinator.is_active = True
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=False)
    coordinator._on_window_end = AsyncMock()
    mock_hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    data = await coordinator._async_update_data()
    coordinator._on_window_end.assert_awaited()
    assert data["is_active"] is False


@pytest.mark.asyncio
async def test_async_update_data_fallback_when_forecast_unavailable(mock_hass):
    pv_state = MagicMock()
    pv_state.state = "unavailable"
    mock_hass.states.get.return_value = pv_state

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_PV_FORECAST_ENTITY: "sensor.pv",
            CONF_BATTERY_CAPACITY: 10.0,
            CONF_FORECAST_ERROR_MARGIN: 10.0,
            CONF_USER_MIN_SOC: 8.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator.is_enabled = True
    coordinator.is_active = True
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": MagicMock(state="50"),
    }.get(entity_id)

    data = await coordinator._async_update_data()

    assert data["calculated_soc"] == DEFAULT_SAFE_FALLBACK_SOC


@pytest.mark.asyncio
async def test_async_update_data_uses_initial_soc(mock_hass):
    pv_state = MagicMock()
    pv_state.state = "5"
    battery_state = MagicMock()
    battery_state.state = "30"

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_PV_FORECAST_ENTITY: "sensor.pv",
            CONF_BATTERY_CAPACITY: 10.0,
            CONF_FORECAST_ERROR_MARGIN: 10.0,
            CONF_USER_MIN_SOC: 8.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator.is_enabled = True
    coordinator.is_active = True
    coordinator.initial_calculated_soc = 40.0
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()

    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": battery_state,
    }.get(entity_id)

    data = await coordinator._async_update_data()

    assert data["calculated_soc"] == 40.0


@pytest.mark.asyncio
async def test_async_update_data_forecast_wh_conversion(mock_hass):
    pv_state = MagicMock()
    pv_state.state = "2000"
    battery_state = MagicMock()
    battery_state.state = "10"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": battery_state,
    }.get(entity_id)

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_PV_FORECAST_ENTITY: "sensor.pv",
            CONF_BATTERY_CAPACITY: 10.0,
            CONF_FORECAST_ERROR_MARGIN: 0.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_DEFAULT_MIN_SOC: 0.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator.is_enabled = True
    coordinator.is_active = True
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()

    data = await coordinator._async_update_data()

    assert data["calculated_soc"] is not None


@pytest.mark.asyncio
async def test_async_update_data_forecast_attributes_list(mock_hass):
    pv_state = MagicMock()
    pv_state.state = "unavailable"
    pv_state.attributes = {"forecast": [{"wh": 1000}, {"wh": 2000}]}
    battery_state = MagicMock()
    battery_state.state = "10"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": battery_state,
    }.get(entity_id)

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_PV_FORECAST_ENTITY: "sensor.pv",
            CONF_BATTERY_CAPACITY: 10.0,
            CONF_FORECAST_ERROR_MARGIN: 0.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_DEFAULT_MIN_SOC: 0.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator.is_enabled = True
    coordinator.is_active = True
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()

    data = await coordinator._async_update_data()

    assert data["calculated_soc"] is not None


@pytest.mark.asyncio
async def test_async_update_data_forecast_today_attribute(mock_hass):
    pv_state = MagicMock()
    pv_state.state = "unavailable"
    pv_state.attributes = {"today_forecast": "5"}
    battery_state = MagicMock()
    battery_state.state = "10"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": battery_state,
    }.get(entity_id)

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_PV_FORECAST_ENTITY: "sensor.pv",
            CONF_BATTERY_CAPACITY: 10.0,
            CONF_FORECAST_ERROR_MARGIN: 0.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_DEFAULT_MIN_SOC: 0.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator.is_enabled = True
    coordinator.is_active = True
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()

    data = await coordinator._async_update_data()

    assert data["calculated_soc"] is not None


@pytest.mark.asyncio
async def test_async_update_data_stops_when_already_at_target(mock_hass):
    pv_state = MagicMock()
    pv_state.state = "5"
    battery_state = MagicMock()
    battery_state.state = "60"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": battery_state,
    }.get(entity_id)

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_PV_FORECAST_ENTITY: "sensor.pv",
            CONF_BATTERY_CAPACITY: 10.0,
            CONF_FORECAST_ERROR_MARGIN: 0.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_DEFAULT_MIN_SOC: 0.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator.is_enabled = True
    coordinator.is_active = True
    coordinator.initial_calculated_soc = 50.0
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._stop_grid_charging = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()

    data = await coordinator._async_update_data()

    coordinator._stop_grid_charging.assert_awaited()
    assert data["target_reached"] is True
