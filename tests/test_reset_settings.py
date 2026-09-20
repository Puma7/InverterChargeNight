"""Tests for reset settings, the reset retry (finding F3) and the AC charge limit restore (F7)."""
import asyncio
import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
    RESET_RETRY_DELAYS,
    RESET_RETRY_INTERVAL,
)
from custom_components.inverter_charge_night.const import (
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_BACKUP_MODE_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORCE_DISCHARGE_SWITCH,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_RUNTIME_STATE,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)
from custom_components.inverter_charge_night.switch import (
    AutoEfficientChargeSwitch,
    InverterChargeNightSwitch,
)

MIN_SOC = "number.min_soc"
GRID = "switch.grid"
FORCE = "switch.force_discharge"
AC_LIMIT = "number.ac_limit"
BATTERY = "sensor.soc"
PV = "sensor.pv"
BACKUP = "binary_sensor.backup"

WINDOW_END = datetime(2026, 1, 15, 5, 59)
INSIDE_WINDOW = datetime(2026, 1, 15, 2, 0)

CONFIG = {
    CONF_MIN_SOC_ENTITY: MIN_SOC,
    CONF_GRID_CHARGE_SWITCH: GRID,
    CONF_DEFAULT_MIN_SOC: 8.0,
}
WINDOW_CONFIG = {
    **CONFIG,
    CONF_BATTERY_SOC_ENTITY: BATTERY,
    CONF_PV_FORECAST_ENTITY: PV,
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_START_TIME: "00:00",
    CONF_END_TIME: "05:59",
}
CALL_LATER = "custom_components.inverter_charge_night.coordinator.async_call_later"


def _make_coordinator(hass, data, options=None):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = options or {}

    # Persist for real so the tests can read back what would survive a restart
    def _update(entry_, options=None, **kwargs):
        if options is not None:
            entry_.options = options
        return True

    hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _runtime_state(coordinator):
    return coordinator.entry.options[CONF_RUNTIME_STATE]


# _reset_settings ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_settings_sets_min_soc_and_turns_off_grid(mock_hass):
    min_soc_state = MagicMock()
    min_soc_state.state = "8"
    grid_state = MagicMock()
    grid_state.state = "on"
    mock_hass.states.get.side_effect = lambda entity_id: {
        MIN_SOC: min_soc_state,
        GRID: grid_state,
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.original_min_soc = 5.0
    coordinator._reset_absolute_charge_power = AsyncMock()

    await coordinator._reset_settings()

    assert coordinator.original_min_soc is None
    calls = [call.args for call in mock_hass.services.async_call.call_args_list]
    assert ("number", "set_value") in [(c[0], c[1]) for c in calls]
    assert ("switch", "turn_off") in [(c[0], c[1]) for c in calls]


@pytest.mark.asyncio
async def test_reset_settings_warns_without_entities(mock_hass, caplog):
    mock_hass.services.async_call = AsyncMock()
    coordinator = _make_coordinator(mock_hass, {CONF_DEFAULT_MIN_SOC: 8.0})
    coordinator._reset_absolute_charge_power = AsyncMock()

    ok = await coordinator._reset_settings()

    assert ok is True  # nothing configured, nothing left to do
    assert "No min SOC entity configured - cannot reset" in caplog.text
    assert "No grid charge switch configured - cannot reset" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()
    coordinator._reset_absolute_charge_power.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_settings_success_clears_originals_and_persists(mock_hass):
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.original_min_soc = 5.0
    coordinator.override_soc = 70.0
    coordinator._pending_reset = True

    ok = await coordinator._reset_settings()

    assert ok is True
    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": 5.0}),
        call("switch", "turn_off", {"entity_id": GRID}),
    ]
    assert coordinator.original_min_soc is None
    assert coordinator.override_soc is None
    assert coordinator._pending_reset is False
    assert _runtime_state(coordinator)["original_min_soc"] is None
    assert _runtime_state(coordinator)["pending_reset"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [MIN_SOC, GRID])
async def test_reset_settings_fails_when_entity_unavailable(mock_hass, missing):
    """An unavailable entity makes the reset fail and keeps the values for a retry."""
    for entity_id, state in ((MIN_SOC, "45"), (GRID, "on")):
        mock_hass.states.async_set(entity_id, "unavailable" if entity_id == missing else state)
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.original_min_soc = 5.0
    coordinator.override_soc = 70.0

    ok = await coordinator._reset_settings()

    assert ok is False
    assert coordinator.original_min_soc == 5.0
    assert coordinator.override_soc == 70.0
    assert coordinator._last_soc_set is None


@pytest.mark.asyncio
async def test_reset_settings_fails_when_force_discharge_unavailable(mock_hass):
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "off")
    coordinator = _make_coordinator(
        mock_hass, {**CONFIG, CONF_FORCE_DISCHARGE_SWITCH: FORCE}
    )
    coordinator.original_min_soc = 5.0

    ok = await coordinator._reset_settings()

    assert ok is False
    # The reachable part is still done
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": MIN_SOC, "value": 5.0}
    )
    assert coordinator.original_min_soc == 5.0


# Reset retry (finding F3) ----------------------------------------------------


@pytest.mark.asyncio
async def test_window_end_schedules_retry_and_retry_resets_when_entity_returns(mock_hass):
    """Min SOC entity gone at 05:59: retry is scheduled; it resets once the entity is back."""
    mock_hass.states.async_set(GRID, "on")  # the min SOC entity is not known at all
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.is_active = True
    coordinator.original_min_soc = 5.0
    coordinator.initial_calculated_soc = 45.0
    unsub = MagicMock()

    with patch(CALL_LATER, return_value=unsub) as later:
        await coordinator._on_window_end(WINDOW_END)

    # Grid charging is off, but the min SOC could not be restored
    mock_hass.services.async_call.assert_awaited_once_with(
        "switch", "turn_off", {"entity_id": GRID}
    )
    assert coordinator.is_active is False
    assert coordinator._pending_reset is True
    assert coordinator.original_min_soc == 5.0
    assert coordinator._reset_retry_unsub is unsub
    later.assert_called_once()
    _hass, delay, retry = later.call_args.args
    assert delay == RESET_RETRY_DELAYS[0]
    assert _runtime_state(coordinator)["pending_reset"] is True
    assert _runtime_state(coordinator)["original_min_soc"] == 5.0

    # The inverter is back; the retry fires
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "off")
    mock_hass.services.async_call.reset_mock()
    with patch(CALL_LATER) as later:
        await retry(WINDOW_END)

    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": MIN_SOC, "value": 5.0}
    )
    later.assert_not_called()
    assert coordinator._pending_reset is False
    assert coordinator.original_min_soc is None
    assert coordinator._reset_retry_unsub is None
    assert _runtime_state(coordinator)["pending_reset"] is False
    assert _runtime_state(coordinator)["original_min_soc"] is None


def test_retry_backs_off_then_every_15_minutes(mock_hass):
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator._pending_reset = True

    with patch(CALL_LATER, return_value=MagicMock()) as later:
        for _ in range(5):
            coordinator._schedule_reset_retry()

    assert [c.args[1] for c in later.call_args_list] == [
        *RESET_RETRY_DELAYS,
        RESET_RETRY_INTERVAL,
        RESET_RETRY_INTERVAL,
    ]


@pytest.mark.asyncio
async def test_retry_reschedules_while_reset_keeps_failing(mock_hass):
    coordinator = _make_coordinator(mock_hass, CONFIG)  # no entities known
    coordinator._pending_reset = True
    coordinator.original_min_soc = 5.0

    with patch(CALL_LATER, return_value=MagicMock()) as later:
        await coordinator._retry_reset()

    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator._pending_reset is True
    assert coordinator.original_min_soc == 5.0
    later.assert_called_once()
    assert _runtime_state(coordinator)["pending_reset"] is True


@pytest.mark.asyncio
async def test_retry_defers_in_backup_mode(mock_hass):
    """The retry never overrides backup mode: it waits instead."""
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(BACKUP, "on")
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_BACKUP_MODE_ENTITY: BACKUP})
    coordinator._pending_reset = True

    with patch(CALL_LATER, return_value=MagicMock()) as later:
        await coordinator._retry_reset()

    mock_hass.services.async_call.assert_not_awaited()
    later.assert_called_once()
    assert coordinator._pending_reset is True


@pytest.mark.asyncio
async def test_retry_is_noop_without_pending_reset_or_during_window(mock_hass):
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, CONFIG)

    coordinator._pending_reset = False
    await coordinator._retry_reset()
    mock_hass.services.async_call.assert_not_awaited()

    coordinator._pending_reset = True
    coordinator.is_active = True
    with patch(CALL_LATER) as later:
        await coordinator._retry_reset()
    mock_hass.services.async_call.assert_not_awaited()
    later.assert_not_called()
    assert coordinator._pending_reset is True


@pytest.mark.asyncio
async def test_window_start_during_pending_reset_keeps_original(mock_hass):
    """A new window after a failed reset captures no new original (the night target)."""
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    mock_hass.states.async_set(MIN_SOC, "45")  # still last night's target
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(BATTERY, "40")
    mock_hass.states.async_set(PV, "5", {"unit_of_measurement": "kWh"})
    coordinator = _make_coordinator(mock_hass, WINDOW_CONFIG)
    coordinator.original_min_soc = 5.0
    coordinator._pending_reset = True
    unsub = MagicMock()
    coordinator._reset_retry_unsub = unsub
    try:
        with patch(
            "custom_components.inverter_charge_night.coordinator.dt_util.now", return_value=INSIDE_WINDOW
        ):
            await coordinator._on_window_start(INSIDE_WINDOW)
            # The window supersedes the pending retry
            unsub.assert_called_once()
            assert coordinator._reset_retry_unsub is None
            assert coordinator._pending_reset is False
            # No persisted target: the plan is recalculated from the forecast
            assert coordinator.initial_calculated_soc == 45.0

            await coordinator._async_update_data()

        # The inverter already shows the target, so only charging is switched on ...
        mock_hass.services.async_call.assert_awaited_once_with(
            "switch", "turn_on", {"entity_id": GRID}
        )
        # ... and 45 % was not taken for the original
        assert coordinator.original_min_soc == 5.0
        assert _runtime_state(coordinator)["original_min_soc"] == 5.0
    finally:
        await coordinator._stop_periodic_verification()


def test_restart_with_pending_reset_schedules_retry(mock_hass):
    """A reset still pending at shutdown is retried after the next setup.

    The constructor only restores the flag (finding B10): arming the timer there
    would run it on a coordinator that ``entry.runtime_data`` does not point at
    yet and whose setup may still fail. ``async_setup_entry`` arms it afterwards.
    """
    with patch(CALL_LATER, return_value=MagicMock()) as later:
        coordinator = _make_coordinator(
            mock_hass,
            CONFIG,
            options={CONF_RUNTIME_STATE: {"pending_reset": True, "original_min_soc": 5.0}},
        )

    assert coordinator._pending_reset is True
    assert coordinator.original_min_soc == 5.0
    later.assert_not_called()

    with patch(CALL_LATER, return_value=MagicMock()) as later:
        coordinator.async_start_pending_reset_retry()

    later.assert_called_once()
    assert later.call_args.args[1] == RESET_RETRY_DELAYS[0]


def test_restart_without_pending_reset_arms_nothing(mock_hass):
    """Nothing is armed when the previous run left the inverter in order."""
    coordinator = _make_coordinator(mock_hass, CONFIG)
    with patch(CALL_LATER, return_value=MagicMock()) as later:
        coordinator.async_start_pending_reset_retry()
    later.assert_not_called()


# AC charge limit (finding F7) ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "unit", "test_value", "restored_value"),
    [("6000", "W", 1000.0, 6000.0), ("6", "kW", 1.0, 6.0)],
)
async def test_window_end_restores_ac_charge_limit_set_by_finder(
    mock_hass, state, unit, test_value, restored_value
):
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(AC_LIMIT, state, {"unit_of_measurement": unit})
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_CHARGE_POWER_ENTITY: AC_LIMIT})
    coordinator.is_active = True
    coordinator.original_min_soc = 8.0

    await coordinator._start_auto_test(1000)

    assert coordinator._original_ac_charge_power == 6000.0
    assert _runtime_state(coordinator)["original_ac_charge_power"] == 6000.0
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": AC_LIMIT, "value": test_value}
    )
    mock_hass.services.async_call.reset_mock()

    await coordinator._on_window_end(WINDOW_END)

    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": 8.0}),
        call("switch", "turn_off", {"entity_id": GRID}),
        call("number", "set_value", {"entity_id": AC_LIMIT, "value": restored_value}),
    ]
    assert coordinator._original_ac_charge_power is None
    assert coordinator._pending_reset is False
    assert _runtime_state(coordinator)["original_ac_charge_power"] is None


@pytest.mark.asyncio
async def test_finder_switch_off_restores_ac_charge_limit(mock_hass):
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_CHARGE_POWER_ENTITY: AC_LIMIT})
    coordinator.auto_efficient_charge = True
    await coordinator._start_auto_test(1000)
    mock_hass.services.async_call.reset_mock()

    switch = AutoEfficientChargeSwitch(coordinator, coordinator.entry)
    switch.hass = mock_hass
    switch.async_write_ha_state = MagicMock()
    await switch.async_turn_off()

    assert coordinator.auto_efficient_charge is False
    assert coordinator._auto_test_active is False
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": AC_LIMIT, "value": 6000.0}
    )
    assert coordinator._original_ac_charge_power is None


@pytest.mark.asyncio
async def test_reset_ac_charge_limit_keeps_original_when_entity_unavailable(mock_hass):
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "off")
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_CHARGE_POWER_ENTITY: AC_LIMIT})
    coordinator._original_ac_charge_power = 6000.0

    assert await coordinator._reset_ac_charge_limit() is False
    assert coordinator._original_ac_charge_power == 6000.0
    mock_hass.services.async_call.assert_not_awaited()

    # The whole reset reports failure, so it is retried
    assert await coordinator._reset_settings() is False


@pytest.mark.asyncio
async def test_reset_ac_charge_limit_with_nothing_to_restore(mock_hass):
    coordinator = _make_coordinator(mock_hass, CONFIG)
    assert await coordinator._reset_ac_charge_limit() is True

    # Entity no longer usable: the stored value cannot be restored and is dropped
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_CHARGE_POWER_ENTITY: "sensor.x"})
    coordinator._original_ac_charge_power = 6000.0
    assert await coordinator._reset_ac_charge_limit() is True
    assert coordinator._original_ac_charge_power is None
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_ac_charge_limit_skips_write_without_readable_original(mock_hass, caplog):
    mock_hass.states.async_set(AC_LIMIT, "unavailable")
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_CHARGE_POWER_ENTITY: AC_LIMIT})

    await coordinator._set_ac_charge_limit_w(1000)

    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator._original_ac_charge_power is None
    assert "not writing a test value" in caplog.text


@pytest.mark.asyncio
async def test_finder_completion_writes_the_best_power_and_keeps_the_original(mock_hass):
    """The result is written, and the user's own limit stays recoverable.

    It used to be dropped ("the best value is meant to stay on the inverter"),
    which had a nasty consequence: that final write goes through the house
    connection limit like every other one, so a wallbox running at the wrong
    moment left a throttled value on the inverter for good - with the user's
    own value gone. The result now lives on as the planner's ceiling instead.
    """
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(AC_LIMIT, "8000", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(
        mock_hass,
        {
            **CONFIG,
            CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
            CONF_CHARGE_POWER_SENT_ENTITY: "sensor.sent",
            CONF_CHARGE_POWER_RECEIVED_ENTITY: "sensor.received",
        },
    )
    coordinator.auto_efficient_charge = True
    coordinator._select_next_auto_test_power_w = MagicMock(return_value=None)
    coordinator.get_auto_efficiency_data = MagicMock(return_value={"best_power_w": 6000})

    await coordinator._handle_auto_charge()

    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": AC_LIMIT, "value": 6000.0}
    )
    assert coordinator.auto_efficient_charge is False
    assert coordinator._original_ac_charge_power == 8000.0, "the user's value is still known"

    # ... and the window end really puts it back
    assert await coordinator._reset_ac_charge_limit() is True
    assert mock_hass.services.async_call.await_args.args[2] == {
        "entity_id": AC_LIMIT,
        "value": 8000.0,
    }


# Absolute charge power and self-marking resets (findings B3, B4, B5, B11) ------

ABS_LIMIT = "number.absolute_charge_power"
ABS_CONFIG = {
    **CONFIG,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: ABS_LIMIT,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 5000,
}


@pytest.mark.asyncio
async def test_absolute_charge_power_is_kept_when_the_reset_fails(mock_hass):
    """A failed service call used to clear the original in a ``finally`` (finding B4)."""
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(ABS_LIMIT, "9000", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, ABS_CONFIG)
    coordinator._original_absolute_charge_power = 9000.0
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("inverter busy"))

    assert await coordinator._reset_absolute_charge_power() is False
    assert coordinator._original_absolute_charge_power == 9000.0

    # ... and the whole reset reports failure, so it is retried
    with patch(CALL_LATER, return_value=MagicMock()):
        assert await coordinator._reset_settings() is False


@pytest.mark.asyncio
async def test_absolute_charge_power_reset_clears_only_on_success(mock_hass):
    mock_hass.states.async_set(ABS_LIMIT, "9000", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, ABS_CONFIG)
    coordinator._original_absolute_charge_power = 9000.0

    assert await coordinator._reset_absolute_charge_power() is True
    assert coordinator._original_absolute_charge_power is None
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": ABS_LIMIT, "value": 9000.0}
    )

    # Nothing captured, or an entity that is gone: nothing left to restore
    assert await coordinator._reset_absolute_charge_power() is True
    coordinator.config = {**CONFIG, CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "sensor.x"}
    coordinator._original_absolute_charge_power = 9000.0
    assert await coordinator._reset_absolute_charge_power() is True
    assert coordinator._original_absolute_charge_power is None
    mock_hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_absolute_charge_power_is_not_written_without_a_readable_original(
    mock_hass, caplog
):
    """A limit that cannot be restored must never be left on the inverter (finding B5)."""
    mock_hass.states.async_set(ABS_LIMIT, "unavailable")
    coordinator = _make_coordinator(mock_hass, ABS_CONFIG)

    await coordinator._apply_absolute_charge_power_limit()

    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator._original_absolute_charge_power is None
    assert "not writing a limit that could not be restored" in caplog.text


@pytest.mark.asyncio
async def test_absolute_charge_power_survives_a_restart(mock_hass):
    """The captured original was missing from the persisted state (finding B5)."""
    mock_hass.states.async_set(ABS_LIMIT, "9000", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, ABS_CONFIG)

    await coordinator._apply_absolute_charge_power_limit()

    assert coordinator._original_absolute_charge_power == 9000.0
    assert _runtime_state(coordinator)["original_absolute_charge_power"] == 9000.0

    restarted = InverterChargeNightCoordinator(mock_hass, coordinator.entry)
    assert restarted._original_absolute_charge_power == 9000.0


@pytest.mark.asyncio
async def test_partial_reset_persists_the_cleared_capture_values(mock_hass):
    """The reset only persisted on success, so a restart resurrected day-old limits."""
    mock_hass.states.async_set(MIN_SOC, "unavailable")  # min SOC reset fails
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(ABS_LIMIT, "9000", {"unit_of_measurement": "W"})
    coordinator = _make_coordinator(mock_hass, ABS_CONFIG)
    coordinator.original_min_soc = 5.0
    coordinator._original_absolute_charge_power = 9000.0
    coordinator._persist_state()

    with patch(CALL_LATER, return_value=MagicMock()):
        assert await coordinator._reset_settings() is False

    state = _runtime_state(coordinator)
    # The absolute limit went back and must not come back after a restart ...
    assert state["original_absolute_charge_power"] is None
    # ... while the min SOC is still owed and stays for the retry
    assert state["original_min_soc"] == 5.0
    assert state["pending_reset"] is True


@pytest.mark.asyncio
async def test_failed_reset_marks_itself_pending_and_schedules_the_retry(mock_hass):
    """Every caller gets the retry; only the window end used to arrange it (B3)."""
    mock_hass.states.async_set(MIN_SOC, "unavailable")
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.original_min_soc = 5.0

    with patch(CALL_LATER, return_value=MagicMock()) as later:
        assert await coordinator._reset_settings() is False

    assert coordinator._pending_reset is True
    later.assert_called_once()
    assert later.call_args.args[1] == RESET_RETRY_DELAYS[0]
    assert _runtime_state(coordinator)["pending_reset"] is True


@pytest.mark.asyncio
async def test_failed_reset_during_unload_persists_but_arms_no_timer(mock_hass, caplog):
    """An async_call_later armed in the unload would fire on a dead coordinator."""
    mock_hass.states.async_set(MIN_SOC, "unavailable")
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.original_min_soc = 5.0
    coordinator._unloading = True

    with patch(CALL_LATER, return_value=MagicMock()) as later:
        assert await coordinator._reset_settings() is False

    later.assert_not_called()
    assert coordinator._reset_retry_unsub is None
    assert coordinator._pending_reset is True
    # ... but the next setup picks it up from the persisted state
    assert _runtime_state(coordinator)["pending_reset"] is True
    assert "retrying after the next setup" in caplog.text


@pytest.mark.asyncio
async def test_turning_the_integration_off_retries_a_failed_reset(mock_hass):
    """The enable switch ignored the result and left grid charging on (finding B3)."""
    mock_hass.states.async_set(MIN_SOC, "unavailable")
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.is_active = True
    coordinator.original_min_soc = 5.0

    switch = InverterChargeNightSwitch(coordinator, coordinator.entry)
    switch.hass = mock_hass
    switch.async_write_ha_state = MagicMock()

    with patch(CALL_LATER, return_value=MagicMock()) as later:
        await switch.async_turn_off()

    assert coordinator._pending_reset is True
    later.assert_called_once()


@pytest.mark.asyncio
async def test_successful_reset_clears_the_pending_flag_and_the_timer(mock_hass):
    mock_hass.states.async_set(MIN_SOC, "45")
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.original_min_soc = 5.0
    coordinator._pending_reset = True
    unsub = MagicMock()
    coordinator._reset_retry_unsub = unsub
    coordinator._reset_retry_count = 3

    assert await coordinator._reset_settings() is True

    assert coordinator._pending_reset is False
    assert coordinator._reset_retry_count == 0
    unsub.assert_called_once()
    assert _runtime_state(coordinator)["pending_reset"] is False


@pytest.mark.asyncio
async def test_reset_leaves_an_untouched_min_soc_alone(mock_hass, caplog):
    """Disabling the integration outside a window must not write the default.

    Nothing was captured, so the integration never changed the floor; writing
    the configured default would clobber a value the user set by hand, and a
    failed write would then arm a retry chain for a reset nobody asked for.
    """
    mock_hass.states.async_set(MIN_SOC, "42", {"unit_of_measurement": "%"})
    mock_hass.states.async_set(GRID, "off")
    coordinator = _make_coordinator(mock_hass, CONFIG)
    assert coordinator.original_min_soc is None

    ok = await coordinator._reset_settings()

    assert ok is True
    assert coordinator._pending_reset is False
    assert not any(
        call.args[:2] == ("number", "set_value")
        for call in mock_hass.services.async_call.await_args_list
    )
    assert float(mock_hass.states.get(MIN_SOC).state) == 42
    # The entity IS configured, so the "not configured" warning must stay away
    assert "No min SOC entity configured" not in caplog.text


@pytest.mark.asyncio
async def test_another_capture_does_not_re_enable_the_default_write(mock_hass, caplog):
    """Only a captured floor may be restored, not a captured charge limit.

    A window that starts while the min SOC entity is unavailable captures no
    floor, but the planner still captures the AC charge limit. That must not
    make the reset write the configured default over an untouched floor.
    """
    mock_hass.states.async_set(MIN_SOC, "42", {"unit_of_measurement": "%"})
    mock_hass.states.async_set(GRID, "off")
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.original_min_soc = None
    coordinator._original_ac_charge_power = 5000.0

    await coordinator._reset_settings()

    assert not any(
        c.args[:2] == ("number", "set_value") and c.args[2]["entity_id"] == MIN_SOC
        for c in mock_hass.services.async_call.await_args_list
    )
    assert float(mock_hass.states.get(MIN_SOC).state) == 42


@pytest.mark.asyncio
async def test_reset_still_switches_grid_charging_off_without_a_capture(mock_hass):
    """Switching off is always safe, so it happens even with nothing captured."""
    mock_hass.states.async_set(MIN_SOC, "42", {"unit_of_measurement": "%"})
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, CONFIG)

    await coordinator._reset_settings()

    assert (
        call("switch", "turn_off", {"entity_id": GRID})
        in mock_hass.services.async_call.await_args_list
    )


# async_reset_inverter (the reset_inverter action) --------------------------------


def _window_coordinator(mock_hass):
    """A coordinator with a running window, ready to be reset from outside."""
    mock_hass.states.async_set(MIN_SOC, "45", {"unit_of_measurement": "%"})
    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set(BATTERY, "99", {"unit_of_measurement": "%"})
    coordinator = _make_coordinator(mock_hass, WINDOW_CONFIG)
    coordinator.is_active = True
    coordinator.original_min_soc = 8.0
    coordinator.initial_calculated_soc = 45.0
    coordinator._reset_absolute_charge_power = AsyncMock()
    return coordinator


@pytest.mark.asyncio
async def test_the_reset_action_ends_the_running_window(mock_hass):
    """A bare reset would not survive: the min SOC watchdog writes it back.

    Ending the window is what makes the action mean anything - it takes down
    the listeners and the verification task that put the floor back.
    """
    coordinator = _window_coordinator(mock_hass)

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=INSIDE_WINDOW,
    ):
        ok = await coordinator.async_reset_inverter()

    assert ok is True
    assert coordinator.is_active is False
    awaited = mock_hass.services.async_call.await_args_list
    assert call("number", "set_value", {"entity_id": MIN_SOC, "value": 8.0}) in awaited
    assert call("switch", "turn_off", {"entity_id": GRID}) in awaited


@pytest.mark.asyncio
async def test_the_reset_action_keeps_the_window_down_until_its_end(mock_hass):
    """Without this the next update would start the window again within minutes."""
    coordinator = _window_coordinator(mock_hass)

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=INSIDE_WINDOW,
    ):
        await coordinator.async_reset_inverter()
        assert coordinator._hands_off_until == WINDOW_END
        # The deadline survives a restart, so a reload does not restart the window
        assert _runtime_state(coordinator)["hands_off_until"] == WINDOW_END.isoformat()

        await coordinator._check_current_window()
        assert coordinator.is_active is False

    # Past the deadline the next window runs as usual
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=WINDOW_END,
    ):
        coordinator._on_window_start = AsyncMock()
        await coordinator._check_current_window()

    assert coordinator._hands_off_until is None
    coordinator._on_window_start.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_restored_hands_off_deadline_in_the_past_is_dropped(mock_hass):
    """It expires by the clock, so a stale one must not block the next window."""
    options = {
        CONF_RUNTIME_STATE: {"hands_off_until": datetime(2026, 1, 14, 5, 59).isoformat()}
    }
    with patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=INSIDE_WINDOW,
    ):
        coordinator = _make_coordinator(mock_hass, WINDOW_CONFIG, options=options)

    assert coordinator._hands_off_until is None


@pytest.mark.asyncio
async def test_a_malformed_hands_off_deadline_is_ignored(mock_hass, caplog):
    options = {CONF_RUNTIME_STATE: {"hands_off_until": "not a timestamp"}}

    coordinator = _make_coordinator(mock_hass, WINDOW_CONFIG, options=options)

    assert coordinator._hands_off_until is None
    assert "hands_off_until" in caplog.text


@pytest.mark.asyncio
async def test_switching_the_integration_back_on_takes_the_inverter_back(mock_hass):
    """The action hands the inverter over; the switch is how the user takes it back."""
    coordinator = _window_coordinator(mock_hass)
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=INSIDE_WINDOW,
    ):
        await coordinator.async_reset_inverter()
    assert coordinator._hands_off_until == WINDOW_END

    coordinator.is_enabled = False
    switch = InverterChargeNightSwitch(coordinator, coordinator.entry)
    switch.async_write_ha_state = MagicMock()
    await switch.async_turn_on()

    assert coordinator._hands_off_until is None
    assert _runtime_state(coordinator)["hands_off_until"] is None


@pytest.mark.asyncio
async def test_the_reset_action_without_a_window_just_resets(mock_hass):
    """No window means nothing to end - and nothing to keep down afterwards."""
    mock_hass.states.async_set(MIN_SOC, "45", {"unit_of_measurement": "%"})
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.original_min_soc = 8.0
    coordinator._reset_absolute_charge_power = AsyncMock()

    ok = await coordinator.async_reset_inverter()

    assert ok is True
    assert coordinator._hands_off_until is None
    assert (
        call("number", "set_value", {"entity_id": MIN_SOC, "value": 8.0})
        in mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_the_reset_action_refuses_during_backup_mode(mock_hass, caplog):
    """The house runs on the battery; the inverter is not ours to write to."""
    caplog.set_level(logging.INFO)
    mock_hass.states.async_set(BACKUP, "on")
    mock_hass.states.async_set(MIN_SOC, "45", {"unit_of_measurement": "%"})
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_BACKUP_MODE_ENTITY: BACKUP})
    coordinator.is_active = True
    coordinator.original_min_soc = 8.0

    assert await coordinator.async_reset_inverter() is False

    assert coordinator.is_active is True
    assert coordinator._hands_off_until is None
    mock_hass.services.async_call.assert_not_awaited()
    assert "Backup mode active" in caplog.text


@pytest.mark.asyncio
async def test_a_failed_reset_from_the_action_is_reported(mock_hass):
    """The caller gets an error, and the retry ladder is armed all the same."""
    coordinator = _window_coordinator(mock_hass)
    mock_hass.services.async_call = AsyncMock(side_effect=RuntimeError("inverter says no"))

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=INSIDE_WINDOW,
    ):
        ok = await coordinator.async_reset_inverter()

    assert ok is False
    assert coordinator._pending_reset is True
