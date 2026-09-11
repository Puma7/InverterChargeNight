"""Tests for skip_next functionality."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.util import dt as dt_util

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.switch import SkipNextSwitch
from custom_components.inverter_charge_night.const import (
    CONF_OPERATION_MODE,
    CONF_RUNTIME_STATE,
    DOMAIN,
    MODE_NIGHT_CHARGE,
)

CALL_LATER = "custom_components.inverter_charge_night.async_call_later"


def _make_coordinator(mock_hass, mock_config_entry):
    """Create a real coordinator for skip_next testing."""
    data = dict(mock_config_entry.data)
    data[CONF_OPERATION_MODE] = MODE_NIGHT_CHARGE
    mock_config_entry.data = data
    mock_hass.services.async_call = AsyncMock()

    with patch.object(InverterChargeNightCoordinator, "__init__", lambda self, *a, **kw: None):
        coord = InverterChargeNightCoordinator.__new__(InverterChargeNightCoordinator)
        coord.hass = mock_hass
        coord.entry = mock_config_entry
        coord.config = data
        coord.operation_mode = MODE_NIGHT_CHARGE
        coord.skip_next = False
        coord._skip_next_unsub = None
        coord._skip_next_until = None
        coord.original_min_soc = None
        coord.is_active = False
        coord.is_enabled = True
        coord.calculated_soc = None
        coord.initial_calculated_soc = None
        coord.minimum_calculated_soc = None
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


def test_skip_next_default_false(mock_hass, mock_config_entry):
    """Test skip_next defaults to False."""
    coord = _make_coordinator(mock_hass, mock_config_entry)
    assert coord.skip_next is False


@pytest.mark.asyncio
async def test_schedule_and_cancel_skip_next_expiry(mock_hass, mock_config_entry):
    """Test scheduling and cancelling skip_next expiry."""
    coord = _make_coordinator(mock_hass, mock_config_entry)

    mock_unsub = MagicMock()
    with patch("custom_components.inverter_charge_night.async_call_later", return_value=mock_unsub) as mock_call_later:
        coord._schedule_skip_next_expiry()
        mock_call_later.assert_called_once()
        assert coord._skip_next_unsub is mock_unsub

        coord._cancel_skip_next_expiry()
        mock_unsub.assert_called_once()
        assert coord._skip_next_unsub is None


@pytest.mark.asyncio
async def test_cancel_skip_next_when_no_timer(mock_hass, mock_config_entry):
    """Test _cancel_skip_next_expiry is safe when no timer is set."""
    coord = _make_coordinator(mock_hass, mock_config_entry)
    coord._skip_next_unsub = None
    coord._cancel_skip_next_expiry()
    assert coord._skip_next_unsub is None


@pytest.mark.asyncio
async def test_window_start_skipped_when_skip_next(mock_hass, mock_config_entry):
    """Test _on_window_start returns early when skip_next is True."""
    coord = _make_coordinator(mock_hass, mock_config_entry)
    coord.skip_next = True
    coord.is_enabled = True

    from datetime import datetime
    await coord._on_window_start(datetime.now())
    assert coord.is_active is False


@pytest.mark.asyncio
async def test_check_current_window_stops_on_skip_next(mock_hass, mock_config_entry):
    """Test _check_current_window ends active window when skip_next."""
    coord = _make_coordinator(mock_hass, mock_config_entry)
    coord.skip_next = True
    coord.is_active = True
    coord._reset_settings = AsyncMock()
    coord._remove_battery_soc_listener = MagicMock()
    coord._remove_inverter_min_soc_listener = MagicMock()
    coord._stop_periodic_verification = AsyncMock()
    coord._finalize_auto_test = MagicMock()

    await coord._check_current_window()
    assert coord.is_active is False


@pytest.mark.asyncio
async def test_skip_next_switch_turn_on(mock_config_entry, mock_coordinator):
    """Test SkipNextSwitch.async_turn_on activates skip and ends window."""
    mock_coordinator.skip_next = False
    mock_coordinator.is_active = True
    mock_coordinator._schedule_skip_next_expiry = MagicMock()
    mock_coordinator._on_window_end = AsyncMock()

    switch = SkipNextSwitch(mock_coordinator, mock_config_entry)
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    assert mock_coordinator.skip_next is True
    mock_coordinator._schedule_skip_next_expiry.assert_called_once()
    mock_coordinator._persist_state.assert_called_once()
    mock_coordinator._on_window_end.assert_called_once()


@pytest.mark.asyncio
async def test_skip_next_switch_turn_on_already_active(mock_config_entry, mock_coordinator):
    """Test SkipNextSwitch.async_turn_on is noop when already active."""
    mock_coordinator.skip_next = True
    mock_coordinator._schedule_skip_next_expiry = MagicMock()
    mock_coordinator._on_window_end = AsyncMock()

    switch = SkipNextSwitch(mock_coordinator, mock_config_entry)
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    mock_coordinator._schedule_skip_next_expiry.assert_not_called()


@pytest.mark.asyncio
async def test_skip_next_switch_turn_on_not_active(mock_config_entry, mock_coordinator):
    """Test SkipNextSwitch.async_turn_on without active window."""
    mock_coordinator.skip_next = False
    mock_coordinator.is_active = False
    mock_coordinator._schedule_skip_next_expiry = MagicMock()
    mock_coordinator._on_window_end = AsyncMock()

    switch = SkipNextSwitch(mock_coordinator, mock_config_entry)
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    assert mock_coordinator.skip_next is True
    mock_coordinator._on_window_end.assert_not_called()


@pytest.mark.asyncio
async def test_skip_next_switch_turn_off(mock_config_entry, mock_coordinator):
    """Test SkipNextSwitch.async_turn_off deactivates skip."""
    mock_coordinator.skip_next = True
    mock_coordinator._cancel_skip_next_expiry = MagicMock()
    mock_coordinator._check_current_window = AsyncMock()

    switch = SkipNextSwitch(mock_coordinator, mock_config_entry)
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()
    assert mock_coordinator.skip_next is False
    mock_coordinator._cancel_skip_next_expiry.assert_called_once()
    mock_coordinator._persist_state.assert_called_once()
    mock_coordinator._check_current_window.assert_called_once()


@pytest.mark.asyncio
async def test_skip_next_switch_turn_off_already_inactive(mock_config_entry, mock_coordinator):
    """Test SkipNextSwitch.async_turn_off is noop when already inactive."""
    mock_coordinator.skip_next = False
    mock_coordinator._cancel_skip_next_expiry = MagicMock()
    mock_coordinator._check_current_window = AsyncMock()

    switch = SkipNextSwitch(mock_coordinator, mock_config_entry)
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()
    mock_coordinator._cancel_skip_next_expiry.assert_not_called()


@pytest.mark.asyncio
async def test_skip_next_switch_is_on(mock_config_entry, mock_coordinator):
    """Test SkipNextSwitch.is_on reflects coordinator state."""
    mock_coordinator.skip_next = False
    switch = SkipNextSwitch(mock_coordinator, mock_config_entry)
    assert switch.is_on is False

    mock_coordinator.skip_next = True
    assert switch.is_on is True


# Persistence of skip next across restarts (plan 005, finding F5) ------------


def _restored_coordinator(mock_hass, mock_config_entry, runtime_state):
    mock_config_entry.options = {CONF_RUNTIME_STATE: runtime_state}
    return InverterChargeNightCoordinator(mock_hass, mock_config_entry)


def test_restore_skip_next_rearms_timer_for_remaining_time(mock_hass, mock_config_entry):
    until = dt_util.now() + timedelta(hours=2)

    with patch(CALL_LATER, return_value=MagicMock()) as later:
        coord = _restored_coordinator(
            mock_hass, mock_config_entry, {"skip_next_until": until.isoformat()}
        )

    assert coord.skip_next is True
    assert coord._skip_next_until == until
    later.assert_called_once()
    assert 7100 <= later.call_args.args[1] <= 7200


def test_restore_skip_next_expired_during_downtime(mock_hass, mock_config_entry):
    until = dt_util.now() - timedelta(minutes=1)

    with patch(CALL_LATER) as later:
        coord = _restored_coordinator(
            mock_hass, mock_config_entry, {"skip_next_until": until.isoformat()}
        )

    assert coord.skip_next is False
    assert coord._skip_next_until is None
    later.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_skip_next_expiry_persists_deadline_and_expiry(mock_hass, mock_config_entry):
    def _update(entry, options=None, **kwargs):
        if options is not None:
            entry.options = options
        return True

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    coord = InverterChargeNightCoordinator(mock_hass, mock_config_entry)
    coord._check_current_window = AsyncMock()
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    deadline = now + timedelta(hours=24)

    with patch("custom_components.inverter_charge_night.dt_util.now", return_value=now), patch(
        CALL_LATER, return_value=MagicMock()
    ) as later:
        coord.skip_next = True
        coord._schedule_skip_next_expiry()
        coord._persist_state()

    assert coord._skip_next_until == deadline
    assert later.call_args.args[1] == 24 * 3600
    assert mock_config_entry.options[CONF_RUNTIME_STATE]["skip_next_until"] == deadline.isoformat()

    expire = later.call_args.args[2]
    await expire(deadline)

    assert coord.skip_next is False
    assert coord._skip_next_until is None
    assert coord._skip_next_unsub is None
    assert mock_config_entry.options[CONF_RUNTIME_STATE]["skip_next_until"] is None
    coord._check_current_window.assert_awaited_once()
