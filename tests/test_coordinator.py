"""Test coordinator functionality."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import (
    InverterChargeNightCoordinator,
    _as_float,
    async_unload_entry,
    async_update_entry,
)
from custom_components.inverter_charge_night.const import (
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_RUNTIME_STATE,
    CONF_PV_FORECAST_ENTITY,
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_START_DATE,
    CONF_BACKUP_MODE_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    DEFAULT_SAFE_FALLBACK_SOC,
)


def _make_coordinator(hass, data, options=None):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.title = "Test"
    entry.data = data
    entry.options = options or {}
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
    mock_hass.states.async_set("binary_sensor.backup_mode", "on")

    coordinator = _make_coordinator(
        mock_hass,
        {CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup_mode"},
    )
    assert coordinator._is_backup_active() is True


def test_accumulate_auto_energy_updates_totals(mock_hass):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_CHARGE_POWER_SENT_ENTITY: "sensor.sent",
            CONF_CHARGE_POWER_RECEIVED_ENTITY: "sensor.received",
        },
    )
    coordinator._auto_test_active = True
    coordinator._auto_last_sample_time = now - timedelta(hours=1)
    coordinator._auto_energy_sent_wh = 0.0
    coordinator._auto_energy_received_wh = 0.0
    coordinator._get_power_w = MagicMock(side_effect=[1000, 900])

    with patch("custom_components.inverter_charge_night.dt_util.now", return_value=now):
        coordinator._accumulate_auto_energy()

    assert coordinator._auto_energy_sent_wh == 1000.0
    assert coordinator._auto_energy_received_wh == 900.0


def test_finalize_auto_test_records_best(mock_hass):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    coordinator = _make_coordinator(mock_hass, {})
    coordinator._auto_test_active = True
    coordinator._auto_test_start = now - timedelta(hours=2)
    coordinator._auto_test_power_w = 5000
    coordinator._auto_energy_sent_wh = 1000.0
    coordinator._auto_energy_received_wh = 900.0
    coordinator.get_auto_efficiency_data = MagicMock(return_value={})
    coordinator._save_auto_efficiency_data = MagicMock()

    with patch("custom_components.inverter_charge_night.dt_util.now", return_value=now):
        coordinator._finalize_auto_test()

    saved = coordinator._save_auto_efficiency_data.call_args.args[0]
    assert saved["best_power_w"] == 5000
    assert saved["best_loss"] == pytest.approx(0.1)
    assert saved["history"]["5000"] == pytest.approx(0.1)
    assert coordinator._auto_test_active is False


def test_finalize_auto_test_discards_short_duration(mock_hass):
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    coordinator = _make_coordinator(mock_hass, {})
    coordinator._auto_test_active = True
    coordinator._auto_test_start = now - timedelta(minutes=10)
    coordinator._auto_test_power_w = 5000
    coordinator._auto_energy_sent_wh = 1000.0
    coordinator._auto_energy_received_wh = 900.0
    coordinator._save_auto_efficiency_data = MagicMock()

    with patch("custom_components.inverter_charge_night.dt_util.now", return_value=now):
        coordinator._finalize_auto_test()

    assert coordinator._auto_test_active is False
    assert not coordinator._save_auto_efficiency_data.called


@pytest.mark.asyncio
async def test_handle_auto_charge_uses_best_power_and_disables(mock_hass):
    mock_hass.states.async_set("switch.grid", "on")
    mock_hass.config_entries.async_update_entry = MagicMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_CHARGE_POWER_ENTITY: "number.setpoint",
            CONF_CHARGE_POWER_SENT_ENTITY: "sensor.sent",
            CONF_CHARGE_POWER_RECEIVED_ENTITY: "sensor.received",
        },
    )
    coordinator.auto_efficient_charge = True
    coordinator.target_reached = False
    coordinator._select_next_auto_test_power_w = MagicMock(return_value=None)
    coordinator.get_auto_efficiency_data = MagicMock(return_value={"best_power_w": 6000})
    coordinator._set_ac_charge_limit_w = AsyncMock()

    await coordinator._handle_auto_charge()

    coordinator._set_ac_charge_limit_w.assert_awaited_with(6000)
    assert coordinator.auto_efficient_charge is False


def test_get_power_w_returns_none_on_unavailable(mock_hass):
    state = MagicMock()
    state.state = "unavailable"
    state.attributes = {}
    mock_hass.states.get.return_value = state
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 1000, CONF_MAX_CHARGE_POWER_W: 2000},
    )
    assert coordinator._get_power_w("sensor.power") is None


def test_select_next_auto_test_power_none_when_range_invalid(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_MIN_CHARGE_POWER_W: 5000, CONF_MAX_CHARGE_POWER_W: 5000},
    )
    assert coordinator._select_next_auto_test_power_w() is None


@pytest.mark.asyncio
async def test_handle_auto_charge_missing_entities_no_action(mock_hass):
    mock_hass.services.async_call = AsyncMock()
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator.auto_efficient_charge = True
    await coordinator._handle_auto_charge()
    assert coordinator._auto_missing_entities_logged is True
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_auto_charge_grid_off_finalizes_test(mock_hass):
    mock_hass.services.async_call = AsyncMock()
    mock_hass.states.async_set("switch.grid", "off")

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_CHARGE_POWER_ENTITY: "number.setpoint",
            CONF_CHARGE_POWER_SENT_ENTITY: "sensor.sent",
            CONF_CHARGE_POWER_RECEIVED_ENTITY: "sensor.received",
        },
    )
    coordinator.auto_efficient_charge = True
    coordinator._auto_test_active = True
    coordinator._auto_test_start = datetime(2025, 1, 1)
    await coordinator._handle_auto_charge()
    assert coordinator._auto_test_active is False


@pytest.mark.asyncio
async def test_calculate_initial_soc_uses_persisted_target_after_restart(mock_hass):
    """Plan 005 (finding F4): the persisted target is continued, the live value is ignored."""
    mock_hass.states.async_set("number.min_soc", "70")
    mock_hass.states.async_set("sensor.pv", "unavailable")

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
        options={
            CONF_RUNTIME_STATE: {"original_min_soc": 8.0, "initial_calculated_soc": 65.0}
        },
    )
    assert coordinator.original_min_soc == 8.0
    assert coordinator.initial_calculated_soc == 65.0

    await coordinator._calculate_initial_soc()

    assert coordinator.initial_calculated_soc == 65.0
    assert coordinator.minimum_calculated_soc == 65.0
    assert coordinator.calculated_soc == 65.0


@pytest.mark.asyncio
async def test_calculate_initial_soc_ignores_live_value_without_persisted_state(mock_hass):
    """Without a persisted target the inverter's live value is never adopted (no waiting loop)."""
    mock_hass.states.async_set("number.min_soc", "70")
    mock_hass.states.async_set("sensor.pv", "unavailable")

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
    with patch("custom_components.inverter_charge_night.asyncio.sleep", new=AsyncMock()) as sleep:
        await coordinator._calculate_initial_soc()

    sleep.assert_not_awaited()
    assert coordinator.initial_calculated_soc == DEFAULT_SAFE_FALLBACK_SOC


@pytest.mark.asyncio
async def test_calculate_initial_soc_uses_safe_fallback_when_forecast_unavailable(mock_hass):
    mock_hass.states.async_set("sensor.pv", "unavailable")

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
    coordinator.initial_calculated_soc = 70.0

    await coordinator._verify_and_restore_min_soc()

    args, kwargs = mock_hass.services.async_call.call_args
    assert args[0] == "number"
    assert args[1] == "set_value"
    assert args[2]["value"] == 70.0


# Runtime state persistence (plan 005, finding F5) ----------------------------

CALL_LATER = "custom_components.inverter_charge_night.async_call_later"


def _persisting_hass(mock_hass):
    """Make async_update_entry store the options on the entry like Home Assistant does."""

    def _update(entry, options=None, **kwargs):
        if options is not None:
            entry.options = options
        return True

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update)


def test_restore_state_from_options(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {},
        options={
            CONF_RUNTIME_STATE: {
                "is_enabled": False,
                "skip_next_until": None,
                "override_soc": 70,
                "original_min_soc": 5,
                "initial_calculated_soc": 65,
                "original_ac_charge_power": 6000,
                "pending_reset": False,
            }
        },
    )

    assert coordinator.is_enabled is False
    assert coordinator.skip_next is False
    assert coordinator.override_soc == 70.0
    assert coordinator.original_min_soc == 5.0
    assert coordinator.initial_calculated_soc == 65.0
    assert coordinator._original_ac_charge_power == 6000.0
    assert coordinator._pending_reset is False


def test_persist_state_round_trip_next_to_auto_efficiency_data(mock_hass):
    _persisting_hass(mock_hass)
    history = {CONF_AUTO_EFFICIENCY_DATA: {"history": {"5000": 0.1}, "best_power_w": 5000}}
    first = _make_coordinator(mock_hass, {}, options=dict(history))
    first.is_enabled = False
    first.override_soc = 70.0
    first.original_min_soc = 5.0
    first.is_active = True
    first.initial_calculated_soc = 65.0
    first._original_ac_charge_power = 6000.0
    first._pending_reset = True
    first.snow_nights = 3

    first._persist_state()

    options = first.entry.options
    assert options[CONF_AUTO_EFFICIENCY_DATA] == history[CONF_AUTO_EFFICIENCY_DATA]
    assert options[CONF_RUNTIME_STATE] == {
        "is_enabled": False,
        "skip_next_until": None,
        "override_soc": 70.0,
        "original_min_soc": 5.0,
        "initial_calculated_soc": 65.0,
        "original_ac_charge_power": 6000.0,
        "original_discharge_limit": None,
        "window_floor_soc": None,
        "original_discharge_block": None,
        "original_absolute_charge_power": None,
        "pending_reset": True,
        "snow_nights": 3,
    }

    # "Restart": a new coordinator on the same entry gets everything back
    with patch(CALL_LATER, return_value=MagicMock()):
        second = InverterChargeNightCoordinator(mock_hass, first.entry)
    assert second.is_enabled is False
    assert second.override_soc == 70.0
    assert second.original_min_soc == 5.0
    assert second.initial_calculated_soc == 65.0
    assert second._original_ac_charge_power == 6000.0
    assert second._pending_reset is True
    assert second.snow_nights == 3

    # Saving the auto-efficiency history keeps the runtime state, and vice versa
    second._save_auto_efficiency_data({"history": {"5000": 0.1, "6000": 0.2}})
    assert second.entry.options[CONF_RUNTIME_STATE]["original_min_soc"] == 5.0
    assert second.entry.options[CONF_AUTO_EFFICIENCY_DATA]["history"]["6000"] == 0.2
    second.original_min_soc = None
    second._persist_state()
    assert second.entry.options[CONF_AUTO_EFFICIENCY_DATA]["history"]["6000"] == 0.2
    assert second.entry.options[CONF_RUNTIME_STATE]["original_min_soc"] is None


def test_persist_state_stores_window_target_only_while_active(mock_hass):
    _persisting_hass(mock_hass)
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.initial_calculated_soc = 45.0

    coordinator.is_active = False
    coordinator._persist_state()
    assert coordinator.entry.options[CONF_RUNTIME_STATE]["initial_calculated_soc"] is None

    coordinator.is_active = True
    coordinator._persist_state()
    assert coordinator.entry.options[CONF_RUNTIME_STATE]["initial_calculated_soc"] == 45.0


def test_restore_state_tolerates_missing_and_invalid_values(mock_hass):
    with patch(CALL_LATER) as later:
        coordinator = _make_coordinator(
            mock_hass,
            {},
            options={
                CONF_RUNTIME_STATE: {
                    "is_enabled": "no",
                    "override_soc": "abc",
                    "original_min_soc": True,
                    "pending_reset": 1,
                    "skip_next_until": "not-a-date",
                }
            },
        )

    assert coordinator.is_enabled is True
    assert coordinator.override_soc is None
    assert coordinator.original_min_soc is None
    assert coordinator.initial_calculated_soc is None
    assert coordinator._pending_reset is False
    assert coordinator.skip_next is False
    later.assert_not_called()

    # A block that is not a dict is ignored entirely
    coordinator = _make_coordinator(mock_hass, {}, options={CONF_RUNTIME_STATE: "garbage"})
    assert coordinator.is_enabled is True
    assert coordinator.original_min_soc is None


@pytest.mark.asyncio
async def test_async_update_entry_ignores_options_only_change(mock_hass, mock_config_entry):
    """Persisting runtime state fires the update listener; without a data change
    it must not re-register triggers, listeners or refresh."""
    coordinator = MagicMock()
    coordinator.config = mock_config_entry.data
    coordinator.async_request_refresh = AsyncMock()
    mock_config_entry.runtime_data = coordinator
    mock_config_entry.options = {CONF_RUNTIME_STATE: {"is_enabled": False}}

    await async_update_entry(mock_hass, mock_config_entry)

    coordinator.update_time_triggers.assert_not_called()
    coordinator._setup_backup_mode_listener.assert_not_called()
    coordinator.async_request_refresh.assert_not_awaited()

# Non-finite numbers and out-of-range restores (finding B12) -------------------


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "Infinity", float("nan"), float("inf")])
def test_as_float_rejects_non_finite_values(value):
    """A nan SOC makes target_reached unreachable, so grid charging never stops."""
    assert _as_float(value) is None


@pytest.mark.parametrize(("value", "expected"), [("42.5", 42.5), (0, 0.0), (-3, -3.0)])
def test_as_float_still_accepts_ordinary_numbers(value, expected):
    assert _as_float(value) == expected


def test_battery_soc_ignores_a_non_finite_state(mock_hass):
    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"})
    mock_hass.states.async_set("sensor.soc", "nan")
    assert coordinator._current_battery_soc() is None

    mock_hass.states.async_set("sensor.soc", "37.5")
    assert coordinator._current_battery_soc() == 37.5


@pytest.mark.parametrize("bad", ["nan", "inf", 150.0, -1.0, "not a number"])
def test_restore_state_drops_unusable_soc_values(mock_hass, bad, caplog):
    """A restored target outside 0-100 % would be compared against the battery all window."""
    coordinator = _make_coordinator(
        mock_hass,
        {},
        options={
            CONF_RUNTIME_STATE: {
                "override_soc": bad,
                "original_min_soc": bad,
                "initial_calculated_soc": bad,
            }
        },
    )
    assert coordinator.override_soc is None
    assert coordinator.original_min_soc is None
    assert coordinator.initial_calculated_soc is None


def test_restore_state_keeps_valid_soc_bounds(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {},
        options={CONF_RUNTIME_STATE: {"override_soc": 0, "original_min_soc": 100}},
    )
    assert coordinator.override_soc == 0.0
    assert coordinator.original_min_soc == 100.0


# The window check task handle (finding B10) ----------------------------------


@pytest.mark.asyncio
async def test_window_check_task_is_kept_and_cancellable(mock_hass):
    """setup_time_triggers dropped the handle, so the unload could not cancel it."""
    created = []

    def _create_task(coro, name=None, **kwargs):
        task = asyncio.ensure_future(coro)
        created.append(task)
        return task

    mock_hass.async_create_task = MagicMock(side_effect=_create_task)
    coordinator = _make_coordinator(mock_hass, {})

    coordinator.setup_time_triggers()
    assert coordinator._window_check_task is created[-1]

    # Re-registering the triggers does not leak the previous check either
    coordinator.setup_time_triggers()
    assert len(created) == 2

    coordinator._cancel_window_check()
    assert coordinator._window_check_task is None
    await asyncio.gather(*created, return_exceptions=True)
    assert [task.cancelled() for task in created] == [True, True]


@pytest.mark.asyncio
async def test_unload_cancels_the_pending_window_check(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.is_active = False
    coordinator._stop_periodic_verification = AsyncMock()
    mock_config_entry.runtime_data = coordinator
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await async_unload_entry(mock_hass, mock_config_entry) is True

    coordinator._cancel_window_check.assert_called_once()
    assert coordinator._unloading is True
