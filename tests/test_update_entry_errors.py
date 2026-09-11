"""Tests for async_update_entry error handling."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night import async_update_entry


@pytest.mark.asyncio
async def test_async_update_entry_missing_coordinator(mock_hass, mock_config_entry):
    await async_update_entry(mock_hass, mock_config_entry)


@pytest.mark.asyncio
async def test_async_update_entry_update_time_triggers_error(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.update_time_triggers.side_effect = Exception("boom")
    coordinator.async_request_refresh = AsyncMock()
    mock_config_entry.runtime_data = coordinator

    await async_update_entry(mock_hass, mock_config_entry)

    assert not coordinator.async_request_refresh.called
