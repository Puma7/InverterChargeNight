"""Test coordinator functionality."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_START_DATE,
    CONF_BACKUP_MODE_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_BATTERY_CHARGE_ENERGY_ENTITY,
    CONF_HOME_CONSUMPTION_ENERGY_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    DEFAULT_SAFE_FALLBACK_SOC,
)


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


def test_is_within_date_range_true(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ACTIVE_START_DATE: "2025-01-01",
            CONF_ACTIVE_END_DATE: "2025-12-31",
        },
    )
    with patch(
        "custom_components.inverter_charge_night.dt_util.now",
        return_value=datetime(2025, 6, 1),
    ):
        assert coordinator._is_within_date_range() is True


def test_is_within_date_range_false(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ACTIVE_START_DATE: "2025-06-01",
            CONF_ACTIVE_END_DATE: "2025-06-30",
        },
    )
    with patch(
        "custom_components.inverter_charge_night.dt_util.now",
        return_value=datetime(2025, 5, 1),
    ):
        assert coordinator._is_within_date_range() is False


def test_is_backup_active(mock_hass):
    state = MagicMock()
    state.state = "on"
    state.attributes = {}
    mock_hass.states.get.return_value = state

    coordinator = _make_coordinator(
        mock_hass,
        {CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup_mode"},
    )
    assert coordinator._is_backup_active() is True


def test_snapshot_based_auto_test_attributes(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    # Verify snapshot attributes exist and are None by default
    assert coordinator._auto_test_start_snapshot is None
    assert coordinator._session_start_snapshot is None


def test_finalize_auto_test_records_best(mock_hass):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    coordinator = _make_coordinator(mock_hass, {})
    def _update_entry(entry, data=None, options=None):
        if options is not None:
            entry.options = options
        if data is not None:
            entry.data = data
    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)
    coordinator._auto_test_active = True
    coordinator._auto_test_start = now - timedelta(hours=2)
    coordinator._auto_test_power_w = 5000
    # Set up start snapshot and mock end snapshot for finalize
    coordinator._auto_test_start_snapshot = {
        "grid_import_kwh": 100.0,
        "battery_charge_kwh": 50.0,
        "home_consumption_kwh": 30.0,
    }
    # Mock _snapshot_meters to return end snapshot
    coordinator._auto_efficiency._snapshot_meters = MagicMock(return_value={
        "grid_import_kwh": 110.0,
        "battery_charge_kwh": 59.0,
        "home_consumption_kwh": 31.0,
    })

    with patch("custom_components.inverter_charge_night.dt_util.now", return_value=now):
        coordinator._finalize_auto_test()

    saved = coordinator.entry.options["auto_efficiency_data"]
    assert saved["best_power_w"] == 5000
    # delta: grid=10kWh, battery=9kWh, home=1kWh → loss=0kWh, eff=100%
    assert saved["best_loss"] == pytest.approx(0.0)


def test_min_soc_cooldown_helper(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._is_within_min_soc_cooldown() is False

    with patch(
        "custom_components.inverter_charge_night.time_module.monotonic",
        return_value=1000.0,
    ):
        coordinator._last_soc_set_at = 1000.0
        assert coordinator._is_within_min_soc_cooldown() is True
        coordinator._last_soc_set_at = 900.0
        assert coordinator._is_within_min_soc_cooldown() is False


def test_finalize_auto_test_discards_short_duration(mock_hass):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    coordinator = _make_coordinator(mock_hass, {})
    coordinator._auto_test_active = True
    coordinator._auto_test_start = now - timedelta(minutes=10)
    coordinator._auto_test_power_w = 5000
    coordinator._auto_test_start_snapshot = {
        "grid_import_kwh": 100.0,
        "battery_charge_kwh": 50.0,
        "home_consumption_kwh": 30.0,
    }
    coordinator._save_auto_efficiency_data = MagicMock()

    with patch("custom_components.inverter_charge_night.dt_util.now", return_value=now):
        coordinator._finalize_auto_test()

    assert coordinator._auto_test_active is False
    assert not coordinator._save_auto_efficiency_data.called


@pytest.mark.asyncio
async def test_handle_auto_charge_uses_best_power_and_disables(mock_hass):
    grid_state = MagicMock()
    grid_state.state = "on"
    mock_hass.states.get.return_value = grid_state
    def _update_entry(entry, data=None, options=None):
        if options is not None:
            entry.options = options
        if data is not None:
            entry.data = data
    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_CHARGE_POWER_ENTITY: "number.setpoint",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
            CONF_MIN_CHARGE_POWER_W: 2000,
            CONF_MAX_CHARGE_POWER_W: 2000,
        },
    )
    coordinator.auto_efficient_charge = True
    coordinator.target_reached = False
    coordinator._set_ac_charge_limit_w = AsyncMock()
    coordinator.entry.options = {"auto_efficiency_data": {"best_power_w": 6000}}

    await coordinator._handle_auto_charge()

    coordinator._set_ac_charge_limit_w.assert_awaited_with(6000)
    assert coordinator.auto_efficient_charge is False


@pytest.mark.asyncio
async def test_handle_auto_charge_missing_entities_no_action(mock_hass):
    mock_hass.services.async_call = AsyncMock()
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_CHARGE_POWER_ENTITY: "number.setpoint",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    coordinator.auto_efficient_charge = True
    await coordinator._handle_auto_charge()
    assert not mock_hass.services.async_call.called


@pytest.mark.asyncio
async def test_handle_auto_charge_grid_off_finalizes_test(mock_hass):
    mock_hass.services.async_call = AsyncMock()

    grid_state = MagicMock()
    grid_state.state = "off"
    mock_hass.states.get.return_value = grid_state

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_CHARGE_POWER_ENTITY: "number.setpoint",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    coordinator.auto_efficient_charge = True
    coordinator._auto_test_active = True
    coordinator._auto_test_start = datetime(2025, 1, 1)
    await coordinator._handle_auto_charge()
    assert coordinator._auto_test_active is False


@pytest.mark.asyncio
async def test_calculate_initial_soc_preserves_inverter_value(mock_hass):
    min_soc_state = MagicMock()
    min_soc_state.state = "70"
    pv_state = MagicMock()
    pv_state.state = "unavailable"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_soc_state,
        "sensor.pv": pv_state,
    }.get(entity_id)

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_PV_FORECAST_ENTITY: "sensor.pv",
            CONF_USER_MIN_SOC: 8.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_FORECAST_ERROR_MARGIN: 10.0,
        },
    )
    await coordinator._calculate_initial_soc()
    assert coordinator.initial_calculated_soc == 70.0


@pytest.mark.asyncio
async def test_calculate_initial_soc_uses_safe_fallback_when_forecast_unavailable(mock_hass):
    pv_state = MagicMock()
    pv_state.state = "unavailable"
    mock_hass.states.get.return_value = pv_state

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_PV_FORECAST_ENTITY: "sensor.pv",
            CONF_USER_MIN_SOC: 8.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_FORECAST_ERROR_MARGIN: 10.0,
        },
    )
    await coordinator._calculate_initial_soc()
    assert coordinator.initial_calculated_soc == DEFAULT_SAFE_FALLBACK_SOC


@pytest.mark.asyncio
async def test_verify_and_restore_min_soc_sets_value(mock_hass):
    min_soc_state = MagicMock()
    min_soc_state.state = "8"
    battery_state = MagicMock()
    battery_state.state = "20"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_soc_state,
        "sensor.soc": battery_state,
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 70.0

    await coordinator._verify_and_restore_min_soc()

    args, kwargs = mock_hass.services.async_call.call_args
    assert args[0] == "number"
    assert args[1] == "set_value"
    assert args[2]["value"] == 70.0


@pytest.mark.asyncio
async def test_verify_and_restore_min_soc_respects_cooldown(mock_hass):
    min_soc_state = MagicMock()
    min_soc_state.state = "8"
    mock_hass.states.get.return_value = min_soc_state
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc"},
    )
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 70.0
    coordinator._last_soc_set_at = 1000.0

    with patch(
        "custom_components.inverter_charge_night.time_module.monotonic",
        return_value=1000.0,
    ):
        await coordinator._verify_and_restore_min_soc()

    mock_hass.services.async_call.assert_not_awaited()