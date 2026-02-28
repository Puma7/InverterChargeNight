"""Extra coverage tests for __init__.py."""
from __future__ import annotations

import asyncio

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.inverter_charge_night import (
    InverterChargeNightCoordinator,
    _forecast_state_to_kwh,
    async_unload_entry,
    async_update_entry,
)
from custom_components.inverter_charge_night.const import (
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_START_DATE,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_BATTERY_CHARGE_ENERGY_ENTITY,
    CONF_HOME_CONSUMPTION_ENERGY_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_UPDATE_INTERVAL,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_BACKUP_MODE_ENTITY,
)


BASE_CONFIG = {
    CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
    CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
    CONF_PV_FORECAST_ENTITY: "sensor.pv",
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_START_TIME: "00:00",
    CONF_END_TIME: "05:59",
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_UPDATE_INTERVAL: 900,
    CONF_COMMAND_DELAY: 0.1,
    CONF_MIN_CHARGE_POWER_W: 1000,
    CONF_MAX_CHARGE_POWER_W: 2000,
    CONF_ACTIVE_START_DATE: "",
    CONF_ACTIVE_END_DATE: "",
}


def _make_coordinator(hass: HomeAssistant, data: dict | None = None, options: dict | None = None):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = dict(data or BASE_CONFIG)
    entry.options = options or {}
    return InverterChargeNightCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_async_update_entry_window_changed(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.config = {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    coordinator.entry = mock_config_entry
    coordinator.update_time_triggers = MagicMock()
    coordinator.async_request_refresh = AsyncMock()

    mock_config_entry.runtime_data = coordinator
    mock_config_entry.data = dict(mock_config_entry.data)
    mock_config_entry.data[CONF_START_TIME] = "01:00"
    mock_config_entry.data[CONF_END_TIME] = "06:00"

    await async_update_entry(mock_hass, mock_config_entry)

    coordinator.update_time_triggers.assert_called_once()


@pytest.mark.asyncio
async def test_async_unload_entry_active_reset_error(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.is_active = True
    coordinator.remove_time_triggers = MagicMock()
    coordinator._remove_battery_soc_listener = MagicMock()
    coordinator._remove_inverter_min_soc_listener = MagicMock()
    coordinator._stop_periodic_verification = MagicMock()
    coordinator._reset_settings = AsyncMock(side_effect=Exception("boom"))

    mock_config_entry.runtime_data = coordinator
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    result = await async_unload_entry(mock_hass, mock_config_entry)

    assert result is True
    coordinator.remove_time_triggers.assert_called_once()


def test_parse_time_invalid_default(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    assert coordinator._parse_time("bad", "bad") == (0, 0)


def test_parse_date_optional_invalid(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    assert coordinator._parse_date_optional("bad") is None
    assert coordinator._parse_date_optional(date(2025, 1, 1)) == date(2025, 1, 1)


def test_forecast_state_to_kwh_unknown_returns_none():
    state = MagicMock()
    state.state = "unknown"
    state.attributes = {}
    assert _forecast_state_to_kwh(state) is None


def test_is_within_date_range_invalid_range(mock_hass: HomeAssistant):
    data = dict(BASE_CONFIG)
    data[CONF_ACTIVE_START_DATE] = "2025-12-31"
    data[CONF_ACTIVE_END_DATE] = "2025-01-01"
    coordinator = _make_coordinator(mock_hass, data)
    assert coordinator._is_within_date_range() is True


def test_is_within_date_range_before_after(mock_hass: HomeAssistant):
    data = dict(BASE_CONFIG)
    data[CONF_ACTIVE_START_DATE] = "2099-01-01"
    data[CONF_ACTIVE_END_DATE] = "2099-12-31"
    coordinator = _make_coordinator(mock_hass, data)
    assert coordinator._is_within_date_range() is False


def test_is_within_date_range_after_end(mock_hass: HomeAssistant):
    data = dict(BASE_CONFIG)
    data[CONF_ACTIVE_START_DATE] = "2000-01-01"
    data[CONF_ACTIVE_END_DATE] = "2000-01-02"
    coordinator = _make_coordinator(mock_hass, data)
    assert coordinator._is_within_date_range() is False


def test_is_backup_active_values(mock_hass: HomeAssistant):
    data = dict(BASE_CONFIG)
    data[CONF_BACKUP_MODE_ENTITY] = "binary_sensor.backup"
    coordinator = _make_coordinator(mock_hass, data)

    state = MagicMock()
    state.state = "on"
    mock_hass.states.get.return_value = state
    assert coordinator._is_backup_active() is True

    state.state = "grid"
    assert coordinator._is_backup_active() is False

    state.state = "unknown"
    assert coordinator._is_backup_active() is False

    state.state = "weird"
    assert coordinator._is_backup_active() is False


@pytest.mark.asyncio
async def test_set_ac_charge_limit_no_entity(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass, {**BASE_CONFIG, CONF_CHARGE_POWER_ENTITY: None})
    await coordinator._set_ac_charge_limit_w(1000)


def test_get_power_w_variants(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    assert coordinator._get_power_w(None) is None

    state = MagicMock()
    state.state = "bad"
    state.attributes = {}
    mock_hass.states.get.return_value = state
    assert coordinator._get_power_w("sensor.power") is None

    state.state = "unavailable"
    mock_hass.states.get.return_value = state
    assert coordinator._get_power_w("sensor.power") is None

    state.state = "2"
    state.attributes = {"unit_of_measurement": "kW"}
    assert coordinator._get_power_w("sensor.power") == 2000.0

    state.attributes = {"unit_of_measurement": "W"}
    assert coordinator._get_power_w("sensor.power") == 2.0

    state.attributes = {"unit_of_measurement": "foo"}
    assert coordinator._get_power_w("sensor.power") == 2.0


@pytest.mark.asyncio
async def test_set_ac_charge_limit_unsupported_domain(mock_hass: HomeAssistant):
    data = dict(BASE_CONFIG)
    data[CONF_CHARGE_POWER_ENTITY] = "sensor.bad"
    coordinator = _make_coordinator(mock_hass, data)
    await coordinator._set_ac_charge_limit_w(1000)


@pytest.mark.asyncio
async def test_set_ac_charge_limit_error(mock_hass: HomeAssistant):
    data = dict(BASE_CONFIG)
    data[CONF_CHARGE_POWER_ENTITY] = "number.ac"
    coordinator = _make_coordinator(mock_hass, data)
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))
    state = MagicMock()
    state.state = "1"
    state.attributes = {"unit_of_measurement": "kW"}
    mock_hass.states.get.return_value = state
    await coordinator._set_ac_charge_limit_w(1000)


@pytest.mark.asyncio
async def test_apply_absolute_charge_power_limit_branches(mock_hass: HomeAssistant):
    data = dict(BASE_CONFIG)
    data[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] = "number.abs"
    data[CONF_ABSOLUTE_MAX_CHARGE_POWER_W] = "bad"
    coordinator = _make_coordinator(mock_hass, data)
    await coordinator._apply_absolute_charge_power_limit()

    data[CONF_ABSOLUTE_MAX_CHARGE_POWER_W] = 0
    coordinator = _make_coordinator(mock_hass, data)
    await coordinator._apply_absolute_charge_power_limit()

    data[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] = "sensor.abs"
    data[CONF_ABSOLUTE_MAX_CHARGE_POWER_W] = 1000
    coordinator = _make_coordinator(mock_hass, data)
    await coordinator._apply_absolute_charge_power_limit()

    data[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] = "number.abs"
    coordinator = _make_coordinator(mock_hass, data)
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))
    state = MagicMock()
    state.state = "bad"
    state.attributes = {}
    mock_hass.states.get.return_value = state
    await coordinator._apply_absolute_charge_power_limit()


@pytest.mark.asyncio
async def test_reset_absolute_charge_power_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    await coordinator._reset_absolute_charge_power()

    data = dict(BASE_CONFIG)
    data[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] = None
    coordinator = _make_coordinator(mock_hass, data)
    coordinator._original_absolute_charge_power = 5.0
    await coordinator._reset_absolute_charge_power()

    data[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] = "switch.abs"
    coordinator = _make_coordinator(mock_hass, data)
    coordinator._original_absolute_charge_power = 5.0
    await coordinator._reset_absolute_charge_power()

    data[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] = "number.abs"
    coordinator = _make_coordinator(mock_hass, data)
    coordinator._original_absolute_charge_power = 5.0
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))
    await coordinator._reset_absolute_charge_power()


def test_select_next_auto_test_power_w_branches(mock_hass: HomeAssistant):
    data = dict(BASE_CONFIG)
    data[CONF_MIN_CHARGE_POWER_W] = 2000
    data[CONF_MAX_CHARGE_POWER_W] = 2000
    coordinator = _make_coordinator(mock_hass, data)
    assert coordinator._select_next_auto_test_power_w() is None

    data[CONF_MIN_CHARGE_POWER_W] = 1000
    data[CONF_MAX_CHARGE_POWER_W] = 1050
    coordinator = _make_coordinator(mock_hass, data, options={"auto_efficiency_data": {"range_min_w": 1000, "range_max_w": 1050}})
    assert coordinator._select_next_auto_test_power_w() is None

    data[CONF_MIN_CHARGE_POWER_W] = 1000
    data[CONF_MAX_CHARGE_POWER_W] = 1200
    coordinator = _make_coordinator(
        mock_hass,
        data,
        options={"auto_efficiency_data": {"history": {"1000": 0.1, "1100": 0.2}, "range_min_w": 1000, "range_max_w": 1200}},
    )
    assert coordinator._select_next_auto_test_power_w() == 1200

    coordinator = _make_coordinator(
        mock_hass,
        data,
        options={"auto_efficiency_data": {"history": {"1000": 0.1, "1100": 0.05}, "range_min_w": 1000, "range_max_w": 1200}},
    )
    assert coordinator._select_next_auto_test_power_w() == 1200

    coordinator = _make_coordinator(
        mock_hass,
        data,
        options={"auto_efficiency_data": {"history": {"1000": 0.1}}},
    )
    coordinator._round_power_step = MagicMock(side_effect=[1000, 1100])
    assert coordinator._select_next_auto_test_power_w() == 1100


def test_snapshot_based_auto_test_init(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    # Verify snapshot attributes exist and are None by default
    assert coordinator._auto_test_start_snapshot is None
    assert coordinator._session_start_snapshot is None


def test_finalize_auto_test_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator._finalize_auto_test()

    coordinator._auto_test_active = True
    coordinator._auto_test_start = datetime(2025, 1, 1, 0, 0, 0)
    coordinator._auto_test_power_w = 1000
    coordinator._auto_test_start_snapshot = {
        "grid_import_kwh": 100.0,
        "battery_charge_kwh": 50.0,
        "home_consumption_kwh": 30.0,
    }
    with patch("homeassistant.util.dt.now", return_value=coordinator._auto_test_start + timedelta(seconds=60)):
        coordinator._finalize_auto_test()

    coordinator._auto_test_active = True
    coordinator._auto_test_start = datetime(2025, 1, 1, 0, 0, 0)
    coordinator._auto_test_power_w = 1000
    coordinator._auto_test_start_snapshot = {
        "grid_import_kwh": 100.0,
        "battery_charge_kwh": 50.0,
        "home_consumption_kwh": 30.0,
    }
    coordinator._auto_efficiency._snapshot_meters = MagicMock(return_value={
        "grid_import_kwh": 110.0,
        "battery_charge_kwh": 59.0,
        "home_consumption_kwh": 31.0,
    })
    coordinator.entry.options = {"auto_efficiency_data": {"best_loss": 0.05}}
    mock_hass.config_entries.async_update_entry = MagicMock()
    with patch("homeassistant.util.dt.now", return_value=coordinator._auto_test_start + timedelta(hours=1)):
        coordinator._finalize_auto_test()


@pytest.mark.asyncio
async def test_handle_auto_charge_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator._auto_test_active = True
    await coordinator._handle_auto_charge()
    assert coordinator._auto_test_active is False

    coordinator = _make_coordinator(mock_hass)
    coordinator.auto_efficient_charge = True
    coordinator._auto_test_active = True
    coordinator._is_backup_active = MagicMock(return_value=True)
    await coordinator._handle_auto_charge()
    assert coordinator._auto_test_active is False

    coordinator = _make_coordinator(mock_hass)
    coordinator.auto_efficient_charge = True
    await coordinator._handle_auto_charge()
    assert coordinator._auto_missing_entities_logged is True

    coordinator = _make_coordinator(mock_hass, {
        **BASE_CONFIG,
        CONF_CHARGE_POWER_ENTITY: "number.ac",
        CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
        CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
        CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
    })
    coordinator.auto_efficient_charge = True
    coordinator._auto_test_active = True
    coordinator.target_reached = True
    await coordinator._handle_auto_charge()
    assert coordinator._auto_test_active is False

    state = MagicMock()
    state.state = "on"
    mock_hass.states.get.return_value = state
    coordinator = _make_coordinator(mock_hass, {
        **BASE_CONFIG,
        CONF_CHARGE_POWER_ENTITY: "number.ac",
        CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
        CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
        CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
        CONF_MIN_CHARGE_POWER_W: 1500,
        CONF_MAX_CHARGE_POWER_W: 1500,
    }, options={"auto_efficiency_data": {"best_power_w": 1500}})
    coordinator.auto_efficient_charge = True
    coordinator._set_ac_charge_limit_w = AsyncMock()
    def _update_entry(entry, data=None, options=None):
        if options is not None:
            entry.options = options
        if data is not None:
            entry.data = data
    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)
    await coordinator._handle_auto_charge()
    coordinator._set_ac_charge_limit_w.assert_awaited()
    assert coordinator.auto_efficient_charge is False

    coordinator = _make_coordinator(mock_hass, {
        **BASE_CONFIG,
        CONF_CHARGE_POWER_ENTITY: "number.ac",
        CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
        CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
        CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
    })
    coordinator.auto_efficient_charge = True
    coordinator.config[CONF_MIN_CHARGE_POWER_W] = 1000
    coordinator.config[CONF_MAX_CHARGE_POWER_W] = 2000
    mock_hass.services.async_call = AsyncMock()
    state = MagicMock()
    state.state = "on"
    mock_hass.states.get.return_value = state
    await coordinator._handle_auto_charge()
    assert coordinator._auto_test_active is True

    # When auto_test_active, handle_auto_charge just returns (snapshot-based, no accumulate)
    coordinator = _make_coordinator(mock_hass, {
        **BASE_CONFIG,
        CONF_CHARGE_POWER_ENTITY: "number.ac",
        CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
        CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
        CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
    })
    coordinator.auto_efficient_charge = True
    coordinator._auto_test_active = True
    mock_hass.states.get.return_value = MagicMock(state="on")
    await coordinator._handle_auto_charge()
    assert coordinator._auto_test_active is True


def test_is_time_between_variants(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    assert coordinator._is_time_between(datetime(2025, 1, 1, 1, 0).time(), datetime(2025, 1, 1, 0, 0).time(), datetime(2025, 1, 1, 2, 0).time()) is True
    assert coordinator._is_time_between(datetime(2025, 1, 1, 23, 0).time(), datetime(2025, 1, 1, 22, 0).time(), datetime(2025, 1, 1, 1, 0).time()) is True


def test_setup_time_triggers_error_path(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    with patch("custom_components.inverter_charge_night.async_track_time_change", side_effect=Exception("boom")):
        coordinator.setup_time_triggers()

def test_ensure_time_triggers_registered_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator._time_triggers_bootstrapped = True
    coordinator._time_triggers = [lambda: None, lambda: None]
    coordinator.setup_time_triggers = MagicMock()
    coordinator._ensure_time_triggers_registered()
    coordinator.setup_time_triggers.assert_not_called()

    coordinator = _make_coordinator(mock_hass)
    coordinator._time_triggers_bootstrapped = True
    coordinator._time_triggers = []
    def _setup_success():
        coordinator._time_triggers = [lambda: None, lambda: None]
    coordinator.setup_time_triggers = MagicMock(side_effect=_setup_success)
    coordinator._ensure_time_triggers_registered()
    coordinator.setup_time_triggers.assert_called_once()

    coordinator = _make_coordinator(mock_hass)
    coordinator._time_triggers_bootstrapped = True
    coordinator._time_triggers = []
    coordinator.setup_time_triggers = MagicMock()
    coordinator._ensure_time_triggers_registered()
    coordinator.setup_time_triggers.assert_called_once()

    coordinator = _make_coordinator(mock_hass)
    coordinator._time_triggers_bootstrapped = False
    coordinator._time_triggers = []
    coordinator.setup_time_triggers = MagicMock()
    coordinator._ensure_time_triggers_registered()
    coordinator.setup_time_triggers.assert_not_called()


def test_update_time_triggers_updates_listener(mock_hass: HomeAssistant):
    data = dict(BASE_CONFIG)
    data[CONF_BATTERY_SOC_ENTITY] = "sensor.old"
    coordinator = _make_coordinator(mock_hass, data)
    coordinator.is_active = True
    coordinator._setup_battery_soc_listener = MagicMock()
    coordinator.setup_time_triggers = MagicMock()
    mock_hass.async_create_background_task = MagicMock(side_effect=lambda coro, name=None: coro.close())
    coordinator.entry.data = dict(BASE_CONFIG)
    coordinator.entry.data[CONF_BATTERY_SOC_ENTITY] = "sensor.new"
    coordinator.update_time_triggers()
    coordinator._setup_battery_soc_listener.assert_called_once()


@pytest.mark.asyncio
async def test_check_current_window_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=True)
    coordinator.is_active = True
    coordinator._on_window_end = AsyncMock()
    await coordinator._check_current_window()
    coordinator._on_window_end.assert_awaited()

    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=False)
    coordinator.is_active = False
    await coordinator._check_current_window()

    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator.is_active = False
    coordinator._is_time_between = MagicMock(return_value=True)
    coordinator._on_window_start = AsyncMock()
    await coordinator._check_current_window()
    coordinator._on_window_start.assert_awaited()

    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator.is_active = True
    coordinator._is_time_between = MagicMock(return_value=False)
    coordinator._on_window_end = AsyncMock()
    await coordinator._check_current_window()
    coordinator._on_window_end.assert_awaited()

    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator.is_active = True
    coordinator._is_time_between = MagicMock(return_value=True)
    coordinator._setup_battery_soc_listener = MagicMock()
    coordinator._setup_inverter_min_soc_listener = MagicMock()
    coordinator._start_periodic_verification = MagicMock()
    coordinator._verify_and_restore_min_soc = AsyncMock()
    await coordinator._check_current_window()
    coordinator._verify_and_restore_min_soc.assert_awaited()

    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._is_time_between = MagicMock(side_effect=Exception("boom"))
    await coordinator._check_current_window()


@pytest.mark.asyncio
async def test_on_window_start_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_enabled = False
    await coordinator._on_window_start(datetime.now())

    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=True)
    await coordinator._on_window_start(datetime.now())

    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=False)
    await coordinator._on_window_start(datetime.now())

    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._calculate_initial_soc = AsyncMock()
    coordinator._setup_battery_soc_listener = MagicMock()
    coordinator._setup_inverter_min_soc_listener = MagicMock()
    coordinator._start_periodic_verification = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    await coordinator._on_window_start(datetime.now())
    coordinator._calculate_initial_soc.assert_awaited()

    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._calculate_initial_soc = AsyncMock()
    coordinator.minimum_calculated_soc = 37.0
    coordinator._ensure_min_soc_target = AsyncMock(return_value=True)
    coordinator._setup_battery_soc_listener = MagicMock()
    coordinator._setup_inverter_min_soc_listener = MagicMock()
    coordinator._start_periodic_verification = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    await coordinator._on_window_start(datetime.now())
    coordinator._ensure_min_soc_target.assert_awaited_with(37.0)


@pytest.mark.asyncio
async def test_window_target_and_ensure_min_soc_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator.minimum_calculated_soc = None
    coordinator.override_soc = 35.0
    assert coordinator._get_window_target_soc() == 35.0

    coordinator.override_soc = None
    coordinator.initial_calculated_soc = 33.0
    assert coordinator._get_window_target_soc() == 33.0

    coordinator.initial_calculated_soc = None
    coordinator.calculated_soc = 31.0
    assert coordinator._get_window_target_soc() == 31.0

    coordinator = _make_coordinator(mock_hass, {**BASE_CONFIG, CONF_KOSTAL_MIN_SOC_ENTITY: None})
    assert await coordinator._ensure_min_soc_target(40.0) is False

    coordinator = _make_coordinator(mock_hass)
    state = MagicMock()
    state.state = "40.0"
    mock_hass.states.get.return_value = state
    assert await coordinator._ensure_min_soc_target(40.0) is True

    coordinator = _make_coordinator(mock_hass)
    coordinator._last_soc_set = 40.0
    coordinator._is_within_min_soc_cooldown = MagicMock(return_value=True)
    state = MagicMock()
    state.state = "10.0"
    mock_hass.states.get.return_value = state
    assert await coordinator._ensure_min_soc_target(40.0) is True

    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.get.return_value = MagicMock(state="8")
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))
    assert await coordinator._ensure_min_soc_target(40.0) is False


@pytest.mark.asyncio
async def test_calculate_initial_soc_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    state = MagicMock()
    state.state = "50"
    mock_hass.states.get.return_value = state
    await coordinator._calculate_initial_soc()
    assert coordinator.initial_calculated_soc == 50.0

    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.get.return_value = MagicMock(state="unavailable", attributes={})
    with patch("asyncio.sleep", new=AsyncMock()), patch(
        "custom_components.inverter_charge_night.calculate_required_soc", return_value=42.0
    ):
        await coordinator._calculate_initial_soc()
    assert coordinator.initial_calculated_soc == 42.0

    pv_state = MagicMock()
    pv_state.state = "unavailable"
    pv_state.attributes = {"forecast": [{"wh": 1000}, {"wh": 2000}]}
    mock_hass.states.get.return_value = pv_state
    coordinator = _make_coordinator(mock_hass)
    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._calculate_initial_soc()

    pv_state = MagicMock()
    pv_state.state = "2000"
    pv_state.attributes = {}
    mock_hass.states.get.return_value = pv_state
    coordinator = _make_coordinator(mock_hass)
    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._calculate_initial_soc()

    pv_state = MagicMock()
    pv_state.state = "unavailable"
    pv_state.attributes = {"today_forecast": "bad"}
    mock_hass.states.get.return_value = pv_state
    coordinator = _make_coordinator(mock_hass)
    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._calculate_initial_soc()

    pv_state = MagicMock()
    pv_state.state = "unavailable"
    pv_state.attributes = {"forecast_today": "bad"}
    mock_hass.states.get.return_value = pv_state
    coordinator = _make_coordinator(mock_hass)
    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._calculate_initial_soc()

    pv_state = MagicMock()
    pv_state.state = "bad"
    pv_state.attributes = {}
    mock_hass.states.get.return_value = pv_state
    coordinator = _make_coordinator(mock_hass)
    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._calculate_initial_soc()

    pv_state = MagicMock()
    pv_state.state = "unavailable"
    pv_state.attributes = {"today_forecast": "5"}
    mock_hass.states.get.return_value = pv_state
    coordinator = _make_coordinator(mock_hass)
    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._calculate_initial_soc()

    pv_state = MagicMock()
    pv_state.state = "unavailable"
    pv_state.attributes = {"forecast_today": "5"}
    mock_hass.states.get.return_value = pv_state
    coordinator = _make_coordinator(mock_hass)
    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._calculate_initial_soc()

    coordinator = _make_coordinator(mock_hass, {**BASE_CONFIG, CONF_BATTERY_CAPACITY: "bad"})
    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._calculate_initial_soc()

    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.get.return_value = MagicMock(state="unavailable", attributes={})
    with patch("asyncio.sleep", new=AsyncMock()), patch(
        "custom_components.inverter_charge_night.calculate_required_soc", return_value=None
    ):
        await coordinator._calculate_initial_soc()


@pytest.mark.asyncio
async def test_calculate_initial_soc_retry_paths(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    states = [
        MagicMock(state="unavailable", attributes={}),
        MagicMock(state="8", attributes={}),
    ]

    def _get(entity_id):
        if entity_id == "number.min_soc":
            return states.pop(0)
        return MagicMock(state="1", attributes={})

    mock_hass.states.get.side_effect = _get
    with patch("asyncio.sleep", new=AsyncMock()), patch(
        "custom_components.inverter_charge_night.calculate_required_soc", return_value=42.0
    ):
        await coordinator._calculate_initial_soc()

    coordinator = _make_coordinator(mock_hass)

    def _get_raise(entity_id):
        if entity_id == "number.min_soc":
            raise Exception("boom")
        return MagicMock(state="1", attributes={})

    mock_hass.states.get.side_effect = _get_raise
    with patch("asyncio.sleep", new=AsyncMock()), patch(
        "custom_components.inverter_charge_night.calculate_required_soc", return_value=42.0
    ):
        await coordinator._calculate_initial_soc()

    coordinator = _make_coordinator(mock_hass)
    bad_state = MagicMock(state="bad", attributes={})
    mock_hass.states.get.return_value = bad_state
    with patch("asyncio.sleep", new=AsyncMock()), patch(
        "custom_components.inverter_charge_night.calculate_required_soc", return_value=42.0
    ):
        await coordinator._calculate_initial_soc()

    coordinator = _make_coordinator(mock_hass)

    def _get_bad_min_soc(entity_id):
        if entity_id == "number.min_soc":
            return MagicMock(state="bad", attributes={})
        return MagicMock(state="1", attributes={})

    mock_hass.states.get.side_effect = _get_bad_min_soc
    with patch("asyncio.sleep", new=AsyncMock()), patch(
        "custom_components.inverter_charge_night.calculate_required_soc", return_value=42.0
    ):
        await coordinator._calculate_initial_soc()


@pytest.mark.asyncio
async def test_on_window_end_error_path(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator._reset_settings = AsyncMock(side_effect=Exception("boom"))
    coordinator.async_request_refresh = AsyncMock()
    await coordinator._on_window_end(datetime.now())
    assert coordinator.is_active is False


@pytest.mark.asyncio
async def test_reset_settings_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass, {**BASE_CONFIG, CONF_KOSTAL_MIN_SOC_ENTITY: None, CONF_KOSTAL_GRID_CHARGE_SWITCH: None})
    await coordinator._reset_settings()

    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.get.return_value = None
    await coordinator._reset_settings()

    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock()
    min_state = MagicMock(state="8")
    switch_state = MagicMock(state="on")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_state,
        "switch.grid": switch_state,
    }.get(entity_id)
    await coordinator._reset_settings()
    assert coordinator.original_min_soc is None

    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.get.return_value = None
    await coordinator._reset_settings()

    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.get.return_value = MagicMock(state="off")
    mock_hass.services.async_call = AsyncMock()
    await coordinator._reset_settings()

    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock()
    min_state = MagicMock(state="8")
    grid_state = MagicMock(state="off")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_state,
        "switch.grid": grid_state,
    }.get(entity_id)
    await coordinator._reset_settings()

    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.get.return_value = MagicMock(state="on")
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))
    await coordinator._reset_settings()

    coordinator = _make_coordinator(
        mock_hass,
        {**BASE_CONFIG, CONF_KOSTAL_MIN_SOC_ENTITY: None, CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid"},
    )
    mock_hass.states.get.side_effect = None
    mock_hass.states.get.return_value = MagicMock(state="on")
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))
    await coordinator._reset_settings()


@pytest.mark.asyncio
async def test_reset_settings_grid_charge_exception(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {**BASE_CONFIG, CONF_KOSTAL_MIN_SOC_ENTITY: None, CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid"},
    )
    mock_hass.states.get.return_value = MagicMock(state="on")
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))
    await coordinator._reset_settings()


@pytest.mark.asyncio
async def test_async_update_data_error_parsing_and_override(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass, {**BASE_CONFIG, CONF_BATTERY_CAPACITY: "bad"})
    coordinator.is_active = True
    coordinator.is_enabled = True
    data = await coordinator._async_update_data()
    assert data["calculated_soc"] is None

    pv_state = MagicMock()
    pv_state.state = "bad"
    pv_state.attributes = {}
    soc_state = MagicMock(state="10")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": soc_state,
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.override_soc = 20.0
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    await coordinator._async_update_data()
    assert coordinator.minimum_calculated_soc == 20.0

    pv_state = MagicMock()
    pv_state.state = "unavailable"
    pv_state.attributes = {"today_forecast": "bad"}
    soc_state = MagicMock(state="10")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": soc_state,
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    await coordinator._async_update_data()

    pv_state.attributes = {"forecast_today": "bad"}
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    await coordinator._async_update_data()

    with patch("custom_components.inverter_charge_night.calculate_required_soc", return_value=None):
        coordinator = _make_coordinator(mock_hass)
        coordinator.is_active = True
        coordinator.is_enabled = True
        coordinator._control_kostal = AsyncMock()
        coordinator._handle_auto_charge = AsyncMock()
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_forecast_today_valid(mock_hass: HomeAssistant):
    pv_state = MagicMock()
    pv_state.state = "unavailable"
    pv_state.attributes = {"forecast_today": "5"}
    soc_state = MagicMock(state="10")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": soc_state,
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_warns_when_soc_unavailable(mock_hass: HomeAssistant):
    pv_state = MagicMock()
    pv_state.state = "5"
    soc_state = MagicMock(state="unavailable")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": soc_state,
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    data = await coordinator._async_update_data()
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_async_update_data_invalid_battery_soc_value(mock_hass: HomeAssistant):
    pv_state = MagicMock()
    pv_state.state = "5"
    soc_state = MagicMock(state="bad")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": soc_state,
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._control_kostal = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_target_reached_and_safety(mock_hass: HomeAssistant):
    pv_state = MagicMock()
    pv_state.state = "5"
    soc_state = MagicMock(state="50")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.pv": pv_state,
        "sensor.soc": soc_state,
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.initial_calculated_soc = 40.0
    coordinator.minimum_calculated_soc = 40.0
    coordinator._stop_grid_charging = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    data = await coordinator._async_update_data()
    assert data["target_reached"] is True

    soc_state.state = "60"
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.initial_calculated_soc = 40.0
    coordinator.minimum_calculated_soc = 40.0
    coordinator._stop_grid_charging = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    data = await coordinator._async_update_data()
    assert data["target_reached"] is True

    soc_state.state = "90"
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.initial_calculated_soc = 50.0
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    data = await coordinator._async_update_data()
    assert data["target_reached"] is True


@pytest.mark.asyncio
async def test_async_update_data_final_target_check(mock_hass: HomeAssistant):
    pv_state = MagicMock()
    pv_state.state = "5"
    battery_states = [
        MagicMock(state="unavailable"),  # pre-check skips
        MagicMock(state="70"),  # final check hits warning/stop
    ]
    call_count = {"soc": 0}

    def _get(entity_id):
        if entity_id == "sensor.pv":
            return pv_state
        if entity_id == "sensor.soc":
            idx = call_count["soc"]
            call_count["soc"] += 1
            return battery_states[min(idx, len(battery_states) - 1)]
        return MagicMock(state="8")

    mock_hass.states.get.side_effect = _get
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.initial_calculated_soc = 50.0
    coordinator.minimum_calculated_soc = 50.0
    coordinator._control_kostal = AsyncMock()
    coordinator._stop_grid_charging = AsyncMock()
    coordinator._handle_auto_charge = AsyncMock()
    data = await coordinator._async_update_data()
    assert data["target_reached"] is True


@pytest.mark.asyncio
async def test_control_kostal_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator._is_backup_active = MagicMock(return_value=True)
    await coordinator._control_kostal(50.0)

    coordinator = _make_coordinator(mock_hass)
    await coordinator._control_kostal(1000.0)

    soc_state = MagicMock(state="unavailable")
    mock_hass.states.get.return_value = soc_state
    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock()
    await coordinator._control_kostal(50.0)

    min_state = MagicMock(state="50")
    grid_state = MagicMock(state="on")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_state,
        "switch.grid": grid_state,
        "sensor.soc": MagicMock(state="10"),
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock()
    coordinator = _make_coordinator(mock_hass)
    coordinator._last_soc_set = 50.0
    await coordinator._control_kostal(50.0)

    grid_state.state = "off"
    coordinator = _make_coordinator(mock_hass)
    coordinator.original_min_soc = 8.0
    coordinator._last_soc_set = None
    await coordinator._control_kostal(60.0)

    bad_state = MagicMock(state="bad")
    mock_hass.states.get.return_value = bad_state
    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock()
    await coordinator._control_kostal(50.0)

    bad_batt = MagicMock(state="bad")
    min_state = MagicMock(state="10")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.soc": bad_batt,
        "number.min_soc": min_state,
        "switch.grid": MagicMock(state="off"),
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock()
    await coordinator._control_kostal(50.0)

    def _get_for_prepare(entity_id):
        if entity_id == "sensor.soc":
            return MagicMock(state="10")
        if entity_id == "number.min_soc":
            raise Exception("boom")
        return MagicMock(state="off")

    mock_hass.states.get.side_effect = _get_for_prepare
    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock()
    await coordinator._control_kostal(50.0)

    min_state = MagicMock(state="10")
    grid_state = MagicMock(state="off")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_state,
        "switch.grid": grid_state,
        "sensor.soc": MagicMock(state="10"),
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock(side_effect=[None, Exception("boom")])
    coordinator = _make_coordinator(mock_hass)
    await coordinator._control_kostal(60.0)

    def _get_for_prepare(entity_id):
        if entity_id == "sensor.soc":
            return MagicMock(state="10")
        if entity_id == "number.min_soc":
            raise Exception("boom")
        return MagicMock(state="off")

    mock_hass.states.get.side_effect = _get_for_prepare
    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock()
    await coordinator._control_kostal(50.0)

    min_state = MagicMock(state="10")
    grid_state = MagicMock(state="off")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_state,
        "switch.grid": grid_state,
        "sensor.soc": MagicMock(state="10"),
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock(side_effect=[None, Exception("boom")])
    coordinator = _make_coordinator(mock_hass)
    await coordinator._control_kostal(60.0)

    min_state = MagicMock(state="bad")
    grid_state = MagicMock(state="on")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_state,
        "switch.grid": grid_state,
        "sensor.soc": MagicMock(state="10"),
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator._last_soc_set = 50.0
    mock_hass.services.async_call = AsyncMock()
    await coordinator._control_kostal(50.0)

    min_state = MagicMock(state="20")
    grid_state = MagicMock(state="on")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_state,
        "switch.grid": grid_state,
        "sensor.soc": MagicMock(state="10"),
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock()
    await coordinator._control_kostal(30.0)

    min_state = MagicMock(state="30")
    grid_state = MagicMock(state="off")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_state,
        "switch.grid": grid_state,
        "sensor.soc": MagicMock(state="10"),
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock()
    await coordinator._control_kostal(30.0)

    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))
    await coordinator._control_kostal(30.0)


@pytest.mark.asyncio
async def test_stop_grid_charging_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))
    mock_hass.states.get.return_value = MagicMock(state="on")
    await coordinator._stop_grid_charging()

    mock_hass.services.async_call = AsyncMock()
    mock_hass.states.get.return_value = MagicMock(state="off")
    await coordinator._stop_grid_charging()

    mock_hass.states.get.return_value = None
    await coordinator._stop_grid_charging()


@pytest.mark.asyncio
async def test_battery_soc_listener_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"})
    coordinator.is_active = False
    captured = {}

    def _capture(hass, entity_id, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_battery_soc_listener()

    event = MagicMock()
    event.data = {"new_state": MagicMock(state="10")}
    await captured["callback"](event)

    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_SOC_ENTITY: None})
    coordinator._setup_battery_soc_listener()

    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = None
    coordinator.calculated_soc = None
    coordinator._stop_grid_charging = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_battery_soc_listener()
    event.data = {"new_state": MagicMock(state="unknown")}
    await captured["callback"](event)
    event.data = {"new_state": MagicMock(state="10")}
    await captured["callback"](event)

    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock(side_effect=Exception("boom"))
    coordinator.async_request_refresh = AsyncMock()

    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_battery_soc_listener()

    event.data = {"new_state": MagicMock(state="60")}
    await captured["callback"](event)

    event.data = {"new_state": MagicMock(state="100")}
    await captured["callback"](event)

    class _BadFloat:
        def __float__(self):
            raise RuntimeError("boom")

    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_battery_soc_listener()
    event.data = {"new_state": MagicMock(state=_BadFloat())}
    await captured["callback"](event)

    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_battery_soc_listener()
    event.data = {"new_state": MagicMock(state="bad")}
    await captured["callback"](event)

    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock(side_effect=Exception("boom"))
    coordinator.async_request_refresh = AsyncMock()
    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_battery_soc_listener()
    event.data = {"new_state": MagicMock(state="60")}
    await captured["callback"](event)


@pytest.mark.asyncio
async def test_inverter_min_soc_listener_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: None})
    coordinator._setup_inverter_min_soc_listener()

    coordinator = _make_coordinator(mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc"})
    coordinator.is_active = False
    captured = {}

    def _capture(hass, entity_id, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_inverter_min_soc_listener()

    event = MagicMock()
    event.data = {"new_state": MagicMock(state="unknown")}
    await captured["callback"](event)

    coordinator = _make_coordinator(mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc"})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    coordinator._verify_and_restore_min_soc = AsyncMock()
    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_inverter_min_soc_listener()
    event.data = {"new_state": MagicMock(state="unavailable")}
    await captured["callback"](event)

    coordinator = _make_coordinator(mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc"})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = None
    coordinator.initial_calculated_soc = None
    coordinator._verify_and_restore_min_soc = AsyncMock()
    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_inverter_min_soc_listener()
    event.data = {"new_state": MagicMock(state="10")}
    await captured["callback"](event)

    coordinator = _make_coordinator(mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc"})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    coordinator._verify_and_restore_min_soc = AsyncMock()
    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_inverter_min_soc_listener()
    event.data = {"new_state": MagicMock(state="bad")}
    await captured["callback"](event)
    event.data = {"new_state": MagicMock(state="10")}
    coordinator._verify_and_restore_min_soc = AsyncMock(side_effect=Exception("boom"))
    await captured["callback"](event)

    listener = MagicMock()
    coordinator._inverter_min_soc_listener = listener
    coordinator._remove_inverter_min_soc_listener()
    listener.assert_called_once()


@pytest.mark.asyncio
async def test_periodic_verification_task_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True

    task_holder = {}

    def _create_task(coro, name=None):
        task = asyncio.create_task(coro)
        task_holder["task"] = task
        return task

    mock_hass.async_create_background_task = _create_task
    coordinator._verify_and_restore_min_soc = AsyncMock(side_effect=Exception("boom"))

    call_count = {"count": 0}

    async def _fake_sleep(_):
        call_count["count"] += 1
        if call_count["count"] > 1:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", new=_fake_sleep):
        coordinator._start_periodic_verification()
        await task_holder["task"]

    coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_verify_and_restore_min_soc_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._verifying_min_soc = True
    await coordinator._verify_and_restore_min_soc()

    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = False
    coordinator.is_enabled = True
    await coordinator._verify_and_restore_min_soc()

    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._is_backup_active = MagicMock(return_value=True)
    await coordinator._verify_and_restore_min_soc()

    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._verifying_min_soc = True
    await coordinator._verify_and_restore_min_soc()

    coordinator = _make_coordinator(mock_hass, {**BASE_CONFIG, CONF_KOSTAL_MIN_SOC_ENTITY: None})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    await coordinator._verify_and_restore_min_soc()
    coordinator.minimum_calculated_soc = 50.0
    mock_hass.states.get.return_value = MagicMock(state="10")
    mock_hass.services.async_call = AsyncMock()
    coordinator._stop_grid_charging = AsyncMock()
    await coordinator._verify_and_restore_min_soc()

    coordinator = _make_coordinator(mock_hass, {**BASE_CONFIG, CONF_KOSTAL_MIN_SOC_ENTITY: None})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = None
    coordinator.initial_calculated_soc = None
    await coordinator._verify_and_restore_min_soc()

    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    mock_hass.states.get.return_value = MagicMock(state="unavailable")
    await coordinator._verify_and_restore_min_soc()

    state = MagicMock(state="10")
    soc_state = MagicMock(state="60")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": state,
        "sensor.soc": soc_state,
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock()
    mock_hass.services.async_call = AsyncMock()
    await coordinator._verify_and_restore_min_soc()

    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    mock_hass.states.get.side_effect = Exception("boom")
    await coordinator._verify_and_restore_min_soc()

    state = MagicMock(state="10")
    soc_state = MagicMock(state="bad")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": state,
        "sensor.soc": soc_state,
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock()
    mock_hass.services.async_call = AsyncMock()
    await coordinator._verify_and_restore_min_soc()

    state = MagicMock(state="50")
    soc_state = MagicMock(state="bad")
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": state,
        "sensor.soc": soc_state,
    }.get(entity_id)
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock()
    mock_hass.services.async_call = AsyncMock()
    await coordinator._verify_and_restore_min_soc()


@pytest.mark.asyncio
async def test_backup_mode_listener_branches(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass, {CONF_BACKUP_MODE_ENTITY: None})
    coordinator._setup_backup_mode_listener()

    coordinator = _make_coordinator(mock_hass, {CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup"})
    coordinator.is_enabled = False
    coordinator._check_current_window = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    captured = {}

    def _capture(hass, entity_id, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_backup_mode_listener()

    event = MagicMock()
    event.data = {"new_state": MagicMock(state="on")}
    await captured["callback"](event)

    coordinator.is_enabled = True
    await captured["callback"](event)

    listener = MagicMock()
    coordinator._backup_mode_listener = listener
    coordinator._remove_backup_mode_listener()
    listener.assert_called_once()
