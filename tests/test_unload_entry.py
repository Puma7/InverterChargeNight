"""Tests for unload entry behavior."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night import (
    async_unload_entry,
)


@pytest.mark.asyncio
async def test_async_unload_entry_active_resets(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.is_active = True
    coordinator._reset_settings = AsyncMock()
    coordinator.remove_time_triggers = MagicMock()
    coordinator._remove_battery_soc_listener = MagicMock()
    coordinator._remove_inverter_min_soc_listener = MagicMock()
    coordinator._stop_periodic_verification = AsyncMock()

    mock_config_entry.runtime_data = coordinator
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    result = await async_unload_entry(mock_hass, mock_config_entry)

    assert result is True
    coordinator._reset_settings.assert_awaited()
    coordinator._stop_periodic_verification.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_unload_entry_no_unload(mock_hass, mock_config_entry):
    mock_config_entry.runtime_data = MagicMock()
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    result = await async_unload_entry(mock_hass, mock_config_entry)

    assert result is False


@pytest.mark.asyncio
async def test_async_unload_entry_removes_all_listeners(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.is_active = False
    coordinator._stop_periodic_verification = AsyncMock()

    mock_config_entry.runtime_data = coordinator
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    result = await async_unload_entry(mock_hass, mock_config_entry)

    assert result is True
    coordinator.remove_time_triggers.assert_called_once()
    coordinator._remove_battery_soc_listener.assert_called_once()
    coordinator._remove_inverter_min_soc_listener.assert_called_once()
    coordinator._remove_backup_mode_listener.assert_called_once()
    coordinator._stop_periodic_verification.assert_awaited_once()
    coordinator._cancel_skip_next_expiry.assert_called_once()
    # A pending reset retry is cancelled; it is re-armed from the persisted state on setup
    coordinator._cancel_reset_retry.assert_called_once()
