"""Tests for unload entry behavior."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night import async_unload_entry
from custom_components.inverter_charge_night.const import DOMAIN


@pytest.mark.asyncio
async def test_async_unload_entry_active_resets(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.is_active = True
    coordinator._reset_settings = AsyncMock()
    coordinator.remove_time_triggers = MagicMock()
    coordinator._remove_battery_soc_listener = MagicMock()
    coordinator._remove_inverter_min_soc_listener = MagicMock()
    coordinator._stop_periodic_verification = MagicMock()

    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    result = await async_unload_entry(mock_hass, mock_config_entry)

    assert result is True
    coordinator._reset_settings.assert_awaited()


@pytest.mark.asyncio
async def test_async_unload_entry_no_unload(mock_hass, mock_config_entry):
    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: MagicMock()}
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    result = await async_unload_entry(mock_hass, mock_config_entry)

    assert result is False
