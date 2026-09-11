"""Tests for async_update_entry error handling."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night import async_update_entry
from custom_components.inverter_charge_night.const import DOMAIN


@pytest.mark.asyncio
async def test_async_update_entry_missing_coordinator(mock_hass, mock_config_entry):
    mock_hass.data = {DOMAIN: {}}
    await async_update_entry(mock_hass, mock_config_entry)


@pytest.mark.asyncio
async def test_async_update_entry_update_time_triggers_error(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.update_time_triggers.side_effect = Exception("boom")
    coordinator.async_request_refresh = AsyncMock()
    mock_hass.data = {DOMAIN: {mock_config_entry.entry_id: coordinator}}

    await async_update_entry(mock_hass, mock_config_entry)

    assert not coordinator.async_request_refresh.called


@pytest.mark.asyncio
async def test_async_update_entry_rearms_backup_mode_listener(mock_hass, mock_config_entry):
    """An options change re-registers the backup listener for the (possibly new) entity."""
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.async_request_refresh = AsyncMock()
    mock_hass.data = {DOMAIN: {mock_config_entry.entry_id: coordinator}}

    await async_update_entry(mock_hass, mock_config_entry)

    coordinator.update_time_triggers.assert_called_once()
    coordinator._setup_backup_mode_listener.assert_called_once()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_update_entry_backup_listener_error(mock_hass, mock_config_entry, caplog):
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator._setup_backup_mode_listener.side_effect = Exception("boom")
    coordinator.async_request_refresh = AsyncMock()
    mock_hass.data = {DOMAIN: {mock_config_entry.entry_id: coordinator}}

    await async_update_entry(mock_hass, mock_config_entry)

    assert "Error updating time triggers" in caplog.text
    assert not coordinator.async_request_refresh.called
