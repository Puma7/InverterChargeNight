"""Tests for auto efficient charge logic."""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import os
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.auto_efficiency import _build_record
from custom_components.inverter_charge_night.const import (
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_CHARGE_POWER_ENTITY,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_BATTERY_CHARGE_ENERGY_ENTITY,
    CONF_HOME_CONSUMPTION_ENERGY_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
)


def _make_coordinator(hass: HomeAssistant, data: dict, options: dict | None = None):
    entry = MagicMock()
    entry.title = "Test"
    entry.entry_id = "test_entry"
    entry.data = data
    entry.options = options or {}
    return InverterChargeNightCoordinator(hass, entry)


def test_get_power_w_converts_kw(mock_hass: HomeAssistant):
    state = MagicMock()
    state.state = "2.5"
    state.attributes = {"unit_of_measurement": "kW"}
    mock_hass.states.get.return_value = state

    coordinator = _make_coordinator(mock_hass, {CONF_MIN_CHARGE_POWER_W: 1000, CONF_MAX_CHARGE_POWER_W: 2000})
    assert coordinator._get_power_w("sensor.power") == 2500.0


def test_get_auto_efficiency_data_defaults_history(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    data = coordinator._get_auto_efficiency_data()
    assert data["history"] == {}


def test_select_next_auto_test_power_within_range(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    candidate = coordinator._select_next_auto_test_power_w()
    assert candidate is not None
    assert 5000 <= candidate <= 15000
    assert candidate % 100 == 0


def test_select_next_auto_test_power_updates_range(mock_hass: HomeAssistant):
    def _update_entry(entry, data=None, options=None):
        if options is not None:
            entry.options = options

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 1000, CONF_MAX_CHARGE_POWER_W: 1400},
        options={"auto_efficiency_data": {"history": {"1200": 0.1, "1300": 0.2}, "range_min_w": 1000, "range_max_w": 1400}},
    )

    result = coordinator._select_next_auto_test_power_w()

    assert result is not None
    updated = coordinator.entry.options["auto_efficiency_data"]
    assert updated["range_max_w"] == 1300


def test_select_next_auto_test_power_returns_d_when_missing(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 1000, CONF_MAX_CHARGE_POWER_W: 1200},
        options={"auto_efficiency_data": {"history": {"1100": 0.1}, "range_min_w": 1000, "range_max_w": 1200}},
    )

    assert coordinator._select_next_auto_test_power_w() == 1200


def test_select_next_auto_test_power_returns_none_when_history_full(mock_hass: HomeAssistant):
    def _update_entry(entry, data=None, options=None):
        if options is not None:
            entry.options = options

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 1000, CONF_MAX_CHARGE_POWER_W: 1400},
        options={
            "auto_efficiency_data": {
                "history": {
                    "1000": 0.3,
                    "1100": 0.2,
                    "1200": 0.1,
                    "1300": 0.15,
                    "1400": 0.25,
                },
                "range_min_w": 1000,
                "range_max_w": 1400,
            }
        },
    )

    assert coordinator._select_next_auto_test_power_w() is None


def test_select_next_auto_test_power_updates_min_range(mock_hass: HomeAssistant):
    def _update_entry(entry, data=None, options=None):
        if options is not None:
            entry.options = options

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 1000, CONF_MAX_CHARGE_POWER_W: 1400},
        options={"auto_efficiency_data": {"history": {"1200": 0.3, "1300": 0.1}, "range_min_w": 1000, "range_max_w": 1400}},
    )

    result = coordinator._select_next_auto_test_power_w()

    assert result is not None
    updated = coordinator.entry.options["auto_efficiency_data"]
    assert updated["range_min_w"] == 1200


def test_finalize_auto_test_no_energy_flow(mock_hass: HomeAssistant):
    """Cover auto_efficiency.py:357-359 – discard when no energy flow."""
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    coordinator._auto_test_active = True
    coordinator._auto_test_start = dt_util.now() - timedelta(hours=2)
    coordinator._auto_test_power_w = 5000
    # Start and end snapshots identical → no energy flow
    coordinator._auto_test_start_snapshot = {
        "grid_import_kwh": 100.0,
        "battery_charge_kwh": 50.0,
        "home_consumption_kwh": 30.0,
    }
    coordinator._auto_efficiency._snapshot_meters = MagicMock(return_value={
        "grid_import_kwh": 100.0,
        "battery_charge_kwh": 50.0,
        "home_consumption_kwh": 30.0,
    })
    coordinator._finalize_auto_test()
    assert coordinator._auto_test_active is False


def test_finalize_auto_test_not_new_best(mock_hass: HomeAssistant):
    """Cover auto_efficiency.py:392 – else branch when not new best."""
    def _update_entry(entry, data=None, options=None):
        if options is not None:
            entry.options = options
    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)
    mock_hass.config.path = MagicMock(return_value="/tmp")
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    coordinator._auto_test_active = True
    coordinator._auto_test_start = dt_util.now() - timedelta(hours=2)
    coordinator._auto_test_power_w = 5000
    coordinator._auto_test_start_snapshot = {
        "grid_import_kwh": 100.0,
        "battery_charge_kwh": 50.0,
        "home_consumption_kwh": 30.0,
    }
    coordinator._auto_efficiency._snapshot_meters = MagicMock(return_value={
        "grid_import_kwh": 110.0,
        "battery_charge_kwh": 58.0,
        "home_consumption_kwh": 31.0,
    })
    # Pre-set a very good best_loss so the new result is NOT better
    coordinator.entry.options = {"auto_efficiency_data": {"best_loss": 0.001, "best_power_w": 6000, "history": {}, "detailed_log": []}}
    coordinator._finalize_auto_test()
    assert coordinator._auto_test_active is False
    # best_power_w should remain 6000 (not updated)
    assert coordinator.entry.options["auto_efficiency_data"]["best_power_w"] == 6000


@pytest.mark.asyncio
async def test_handle_auto_charge_resets_on_backup(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator.auto_efficient_charge = True
    coordinator._auto_test_active = True
    coordinator._is_backup_active = MagicMock(return_value=True)

    await coordinator._handle_auto_charge()

    assert coordinator._auto_test_active is False


@pytest.mark.asyncio
async def test_handle_auto_charge_returns_when_test_active(mock_hass: HomeAssistant):
    grid_state = MagicMock()
    grid_state.state = "on"
    mock_hass.states.get.return_value = grid_state
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_CHARGE_POWER_ENTITY: "number.setpoint",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator.auto_efficient_charge = True
    coordinator.target_reached = False
    coordinator._auto_test_active = True

    await coordinator._handle_auto_charge()

    # When test is active, handle_auto_charge just returns (snapshot-based, no accumulate)
    assert coordinator._auto_test_active is True


@pytest.mark.asyncio
async def test_handle_auto_charge_starts_test_when_candidate(mock_hass: HomeAssistant):
    grid_state = MagicMock()
    grid_state.state = "on"
    mock_hass.states.get.return_value = grid_state
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_CHARGE_POWER_ENTITY: "number.setpoint",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator.auto_efficient_charge = True
    coordinator.target_reached = False
    coordinator._set_ac_charge_limit_w = AsyncMock()

    await coordinator._handle_auto_charge()

    coordinator._set_ac_charge_limit_w.assert_awaited()
    assert coordinator._auto_test_active is True


def test_round_power_step_wrapper(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    assert coordinator._round_power_step(1550.0, 100) == 1600


@pytest.mark.asyncio
async def test_apply_absolute_charge_power_limit_converts_units(mock_hass: HomeAssistant):
    mock_hass.services.async_call = AsyncMock()
    state = MagicMock()
    state.state = "12.0"
    state.attributes = {"unit_of_measurement": "kW"}
    mock_hass.states.get.return_value = state

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.abs_max",
            CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 10000,
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    await coordinator._apply_absolute_charge_power_limit()

    mock_hass.services.async_call.assert_called()
    args, kwargs = mock_hass.services.async_call.call_args
    data = kwargs.get("service_data") if kwargs else None
    if data is None and len(args) >= 3:
        data = args[2]
    assert data["value"] == 10.0


@pytest.mark.asyncio
async def test_reset_absolute_charge_power_restores_original(mock_hass: HomeAssistant):
    mock_hass.services.async_call = AsyncMock()
    state = MagicMock()
    state.state = "12.0"
    state.attributes = {"unit_of_measurement": "kW"}
    mock_hass.states.get.return_value = state

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.abs_max",
            CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 10000,
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._original_absolute_charge_power = 12.0
    await coordinator._reset_absolute_charge_power()

    mock_hass.services.async_call.assert_called()
    args, kwargs = mock_hass.services.async_call.call_args
    data = kwargs.get("service_data") if kwargs else None
    if data is None and len(args) >= 3:
        data = args[2]
    assert data["value"] == 12.0


@pytest.mark.asyncio
async def test_start_auto_test_sets_fields(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._set_ac_charge_limit_w = AsyncMock()
    await coordinator._start_auto_test(5000)

    assert coordinator._auto_test_active is True
    assert coordinator._auto_test_power_w == 5000
    coordinator._set_ac_charge_limit_w.assert_awaited()


def test_reset_auto_test_state_clears_values(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._auto_test_active = True
    coordinator._auto_test_power_w = 5000
    coordinator._auto_test_start = MagicMock()
    coordinator._auto_last_sample_time = MagicMock()
    coordinator._auto_energy_sent_wh = 1.0
    coordinator._auto_energy_received_wh = 1.0

    coordinator._reset_auto_test_state()

    assert coordinator._auto_test_active is False
    assert coordinator._auto_test_power_w is None


def test_save_auto_efficiency_data_updates_options(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    mock_hass.config_entries.async_update_entry = MagicMock()
    data = {"best_power_w": 5000}

    coordinator._save_auto_efficiency_data(data)

    mock_hass.config_entries.async_update_entry.assert_called_once()


def test_build_record_basic():
    # grid=1000, battery=900, home=50 → loss=50, eff=900/(900+50)=94.74%
    record = _build_record("auto_test", 5000, 1000.0, 900.0, 50.0, 3600.0)
    assert record["type"] == "auto_test"
    assert record["power_setpoint_w"] == 5000
    assert record["grid_import_wh"] == 1000.0
    assert record["battery_charge_wh"] == 900.0
    assert record["home_consumption_wh"] == 50.0
    assert record["loss_wh"] == 50.0
    assert record["duration_s"] == 3600
    assert record["efficiency_pct"] == 94.74
    assert record["loss_pct"] == 5.26


def test_build_record_zero_energy():
    record = _build_record("auto_test", 5000, 0.0, 0.0, 0.0, 3600.0)
    assert record["efficiency_pct"] == 0.0
    assert record["loss_pct"] == 0.0
    assert record["loss_wh"] == 0.0


def test_build_record_zero_duration():
    record = _build_record("auto_test", 5000, 100.0, 90.0, 5.0, 0.0)
    assert record["duration_s"] == 0


def test_build_record_clamps_efficiency():
    # battery > grid (impossible but test clamping): loss clamped to 0
    record = _build_record("auto_test", 5000, 100.0, 200.0, 0.0, 3600.0)
    assert record["efficiency_pct"] == 100.0
    assert record["loss_pct"] == 0.0
    assert record["loss_wh"] == 0.0


def test_get_data_defaults_detailed_log(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    data = coordinator._get_auto_efficiency_data()
    assert data["detailed_log"] == []


def test_save_data_trims_detailed_log(mock_hass: HomeAssistant):
    mock_hass.config_entries.async_update_entry = MagicMock()
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    data = coordinator._get_auto_efficiency_data()
    data["detailed_log"] = [{"i": i} for i in range(600)]
    coordinator._save_auto_efficiency_data(data)
    saved_options = mock_hass.config_entries.async_update_entry.call_args
    saved_data = saved_options[1]["options"]["auto_efficiency_data"]
    assert len(saved_data["detailed_log"]) == 500


def test_get_session_data_defaults_empty(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    assert coordinator._get_session_data() == {}


def test_save_session_data(mock_hass: HomeAssistant):
    mock_hass.config_entries.async_update_entry = MagicMock()
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    coordinator._auto_efficiency.save_session_data({"last_session": {"efficiency_pct": 95.0}})
    mock_hass.config_entries.async_update_entry.assert_called_once()


def test_csv_append_creates_file(mock_hass: HomeAssistant, tmp_path):
    mock_hass.config.path = MagicMock(return_value=str(tmp_path))
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    record = _build_record("auto_test", 5000, 1000.0, 900.0, 50.0, 3600.0)
    coordinator._auto_efficiency.append_csv(record)
    csv_path = os.path.join(str(tmp_path), "efficiency_log.csv")
    assert os.path.exists(csv_path)
    with open(csv_path, encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 2  # header + 1 row
    assert "auto_test" in lines[1]


def test_csv_append_appends_without_header(mock_hass: HomeAssistant, tmp_path):
    mock_hass.config.path = MagicMock(return_value=str(tmp_path))
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    record1 = _build_record("auto_test", 5000, 1000.0, 900.0, 50.0, 3600.0)
    record2 = _build_record("regular_charge", 6000, 2000.0, 1800.0, 100.0, 7200.0)
    coordinator._auto_efficiency.append_csv(record1)
    coordinator._auto_efficiency.append_csv(record2)
    csv_path = os.path.join(str(tmp_path), "efficiency_log.csv")
    with open(csv_path, encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 3  # header + 2 rows


def test_csv_append_handles_os_error(mock_hass: HomeAssistant):
    mock_hass.config.path = MagicMock(return_value="/nonexistent/path/that/does/not/exist")
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    record = _build_record("auto_test", 5000, 1000.0, 900.0, 50.0, 3600.0)
    coordinator._auto_efficiency.append_csv(record)


def _mock_energy_states(mock_hass, grid_kwh, battery_kwh, home_kwh):
    """Helper to set up mock states for energy meter entities."""
    def _get(entity_id):
        state = MagicMock()
        if entity_id == "sensor.grid":
            state.state = str(grid_kwh)
        elif entity_id == "sensor.battery":
            state.state = str(battery_kwh)
        elif entity_id == "sensor.home":
            state.state = str(home_kwh)
        elif entity_id == "number.setpoint":
            state.state = "5000"
            state.attributes = {"unit_of_measurement": "W"}
        else:
            state.state = "unknown"
        return state
    mock_hass.states.get = MagicMock(side_effect=_get)


def test_start_charge_session(mock_hass: HomeAssistant):
    _mock_energy_states(mock_hass, 100.0, 50.0, 30.0)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    coordinator._start_charge_session()
    assert coordinator._session_active is True
    assert coordinator._session_start is not None
    assert coordinator._session_start_snapshot is not None
    assert coordinator._session_start_snapshot["grid_import_kwh"] == 100.0


def test_start_charge_session_no_entities(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    coordinator._start_charge_session()
    assert coordinator._session_active is True
    assert coordinator._session_start_snapshot is None


def test_read_energy_kwh_unavailable(mock_hass: HomeAssistant):
    state = MagicMock()
    state.state = "unavailable"
    mock_hass.states.get = MagicMock(return_value=state)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    result = coordinator._auto_efficiency._read_energy_kwh("sensor.grid")
    assert result is None


def test_read_energy_kwh_invalid_value(mock_hass: HomeAssistant):
    state = MagicMock()
    state.state = "not_a_number"
    mock_hass.states.get = MagicMock(return_value=state)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    result = coordinator._auto_efficiency._read_energy_kwh("sensor.grid")
    assert result is None


def test_read_energy_kwh_none_entity(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(mock_hass, {})
    result = coordinator._auto_efficiency._read_energy_kwh(None)
    assert result is None


def test_read_energy_kwh_missing_state(mock_hass: HomeAssistant):
    mock_hass.states.get = MagicMock(return_value=None)
    coordinator = _make_coordinator(mock_hass, {})
    result = coordinator._auto_efficiency._read_energy_kwh("sensor.missing")
    assert result is None


def test_snapshot_meters_partial_unavailable(mock_hass: HomeAssistant):
    def _get(entity_id):
        if entity_id == "sensor.grid":
            state = MagicMock()
            state.state = "100.0"
            return state
        return None
    mock_hass.states.get = MagicMock(side_effect=_get)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    assert coordinator._auto_efficiency._snapshot_meters() is None


def test_finalize_charge_session_persists(mock_hass: HomeAssistant, tmp_path):
    mock_hass.config.path = MagicMock(return_value=str(tmp_path))
    mock_hass.config_entries.async_update_entry = MagicMock()
    # Start snapshot: grid=100, battery=50, home=30
    _mock_energy_states(mock_hass, 100.0, 50.0, 30.0)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_CHARGE_POWER_ENTITY: "number.setpoint",
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    coordinator._start_charge_session()
    coordinator._session_start = dt_util.now() - timedelta(hours=2)
    # End snapshot: grid=110, battery=58, home=31 → delta: grid=10kWh, battery=8kWh, home=1kWh
    _mock_energy_states(mock_hass, 110.0, 58.0, 31.0)
    coordinator._finalize_charge_session()
    assert coordinator._session_active is False
    csv_path = os.path.join(str(tmp_path), "efficiency_log.csv")
    assert os.path.exists(csv_path)


def test_finalize_charge_session_discards_short(mock_hass: HomeAssistant):
    _mock_energy_states(mock_hass, 100.0, 50.0, 30.0)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    coordinator._start_charge_session()
    # Session just started, duration < 60s
    coordinator._finalize_charge_session()
    assert coordinator._session_active is False


def test_finalize_charge_session_discards_no_energy(mock_hass: HomeAssistant):
    _mock_energy_states(mock_hass, 100.0, 50.0, 30.0)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    coordinator._start_charge_session()
    coordinator._session_start = dt_util.now() - timedelta(hours=2)
    # End snapshot same as start → no energy flow
    coordinator._finalize_charge_session()
    assert coordinator._session_active is False


def test_finalize_charge_session_not_active(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    coordinator._finalize_charge_session()
    assert coordinator._session_active is False


def test_finalize_charge_session_no_power_entity(mock_hass: HomeAssistant, tmp_path):
    mock_hass.config.path = MagicMock(return_value=str(tmp_path))
    mock_hass.config_entries.async_update_entry = MagicMock()
    _mock_energy_states(mock_hass, 100.0, 50.0, 30.0)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    coordinator._start_charge_session()
    coordinator._session_start = dt_util.now() - timedelta(hours=2)
    _mock_energy_states(mock_hass, 110.0, 58.0, 31.0)
    coordinator._finalize_charge_session()
    assert coordinator._session_active is False


def test_finalize_charge_session_no_start_snapshot(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 15000},
    )
    coordinator._start_charge_session()
    coordinator._session_start = dt_util.now() - timedelta(hours=2)
    # start_snapshot is None (no energy entities configured)
    coordinator._finalize_charge_session()
    assert coordinator._session_active is False


def test_reset_charge_session(mock_hass: HomeAssistant):
    _mock_energy_states(mock_hass, 100.0, 50.0, 30.0)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_GRID_IMPORT_ENERGY_ENTITY: "sensor.grid",
            CONF_BATTERY_CHARGE_ENERGY_ENTITY: "sensor.battery",
            CONF_HOME_CONSUMPTION_ENERGY_ENTITY: "sensor.home",
        },
    )
    coordinator._start_charge_session()
    coordinator._auto_efficiency.reset_charge_session()
    assert coordinator._session_active is False
    assert coordinator._session_start is None
    assert coordinator._session_start_snapshot is None
