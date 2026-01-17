"""Tests for window flow behavior."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import CONF_END_TIME, CONF_START_TIME


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_check_current_window_skips_on_backup(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    )
    coordinator._is_backup_active = MagicMock(return_value=True)
    coordinator._on_window_start = AsyncMock()

    await coordinator._check_current_window()

    coordinator._on_window_start.assert_not_called()


@pytest.mark.asyncio
async def test_check_current_window_stops_outside_date_range(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    )
    coordinator.is_active = True
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=False)
    coordinator._on_window_end = AsyncMock()

    await coordinator._check_current_window()

    coordinator._on_window_end.assert_awaited()


@pytest.mark.asyncio
async def test_check_current_window_starts_when_in_window(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    )
    coordinator.is_active = False
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._parse_time = MagicMock(side_effect=[(0, 0), (5, 59)])
    coordinator._is_time_between = MagicMock(return_value=True)
    coordinator._on_window_start = AsyncMock()

    await coordinator._check_current_window()

    coordinator._on_window_start.assert_awaited()


@pytest.mark.asyncio
async def test_check_current_window_ends_when_outside_window(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    )
    coordinator.is_active = True
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._parse_time = MagicMock(side_effect=[(0, 0), (5, 59)])
    coordinator._is_time_between = MagicMock(return_value=False)
    coordinator._on_window_end = AsyncMock()

    await coordinator._check_current_window()

    coordinator._on_window_end.assert_awaited()


@pytest.mark.asyncio
async def test_check_current_window_active_sets_listeners(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    )
    coordinator.is_active = True
    coordinator._battery_soc_listener = None
    coordinator._inverter_min_soc_listener = None
    coordinator._verification_task = None
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._is_within_date_range = MagicMock(return_value=True)
    coordinator._parse_time = MagicMock(side_effect=[(0, 0), (5, 59)])
    coordinator._is_time_between = MagicMock(return_value=True)
    coordinator._setup_battery_soc_listener = MagicMock()
    coordinator._setup_inverter_min_soc_listener = MagicMock()
    coordinator._start_periodic_verification = MagicMock()
    coordinator._verify_and_restore_min_soc = AsyncMock()

    await coordinator._check_current_window()

    coordinator._setup_battery_soc_listener.assert_called_once()
    coordinator._setup_inverter_min_soc_listener.assert_called_once()
    coordinator._start_periodic_verification.assert_called_once()
    coordinator._verify_and_restore_min_soc.assert_awaited()


@pytest.mark.asyncio
async def test_on_window_end_resets_state(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    )
    coordinator.is_active = True
    coordinator.target_reached = True
    coordinator.initial_calculated_soc = 50.0
    coordinator.minimum_calculated_soc = 45.0
    coordinator.override_soc = 60.0
    coordinator._finalize_auto_test = MagicMock()
    coordinator._reset_settings = AsyncMock()
    coordinator._remove_battery_soc_listener = MagicMock()
    coordinator._remove_inverter_min_soc_listener = MagicMock()
    coordinator._stop_periodic_verification = MagicMock()
    coordinator.async_request_refresh = AsyncMock()

    await coordinator._on_window_end(None)

    assert coordinator.is_active is False
    assert coordinator.target_reached is False
    assert coordinator.initial_calculated_soc is None
    assert coordinator.minimum_calculated_soc is None
    assert coordinator.override_soc is None
    coordinator._finalize_auto_test.assert_called_once()
    coordinator._reset_settings.assert_awaited()
    coordinator._remove_battery_soc_listener.assert_called_once()
    coordinator._remove_inverter_min_soc_listener.assert_called_once()
    coordinator._stop_periodic_verification.assert_called_once()
    coordinator.async_request_refresh.assert_awaited()
