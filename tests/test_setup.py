"""Tests for setup and unload flows."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import (
    async_setup_entry,
    async_unload_entry,
    async_update_entry,
)
from custom_components.inverter_charge_night.const import CONF_BACKUP_MODE_ENTITY, CONF_UPDATE_INTERVAL
from custom_components.inverter_charge_night.const import DOMAIN


@pytest.mark.asyncio
async def test_async_setup_entry_registers_coordinator(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.setup_time_triggers = MagicMock()
    coordinator._setup_backup_mode_listener = MagicMock()

    mock_config_entry.add_update_listener = MagicMock(return_value="unload_listener")
    mock_config_entry.async_on_unload = MagicMock()
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.config_entries.async_update_entry = MagicMock()

    with patch(
        "custom_components.inverter_charge_night.InverterChargeNightCoordinator",
        return_value=coordinator,
    ):
        result = await async_setup_entry(mock_hass, mock_config_entry)

    assert result is True
    assert DOMAIN in mock_hass.data
    assert mock_config_entry.entry_id in mock_hass.data[DOMAIN]
    coordinator.async_config_entry_first_refresh.assert_awaited()
    mock_hass.config_entries.async_forward_entry_setups.assert_awaited()
    coordinator.setup_time_triggers.assert_called_once()
    coordinator._setup_backup_mode_listener.assert_called_once()
    mock_config_entry.async_on_unload.assert_called_once()


@pytest.mark.asyncio
async def test_async_unload_entry_cleans_up(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.is_active = False
    coordinator.remove_time_triggers = MagicMock()
    coordinator._remove_battery_soc_listener = MagicMock()
    coordinator._remove_inverter_min_soc_listener = MagicMock()
    coordinator._stop_periodic_verification = MagicMock()
    coordinator._remove_backup_mode_listener = MagicMock()
    coordinator._reset_settings = AsyncMock()

    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    result = await async_unload_entry(mock_hass, mock_config_entry)

    assert result is True
    coordinator.remove_time_triggers.assert_called_once()
    coordinator._remove_battery_soc_listener.assert_called_once()
    coordinator._remove_inverter_min_soc_listener.assert_called_once()
    coordinator._stop_periodic_verification.assert_called_once()
    assert mock_config_entry.entry_id not in mock_hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_update_entry_updates_backup_listener(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.config = dict(mock_config_entry.data)
    coordinator.entry = mock_config_entry
    coordinator.update_time_triggers = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator._setup_backup_mode_listener = MagicMock()

    mock_hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}

    mock_config_entry.data = dict(mock_config_entry.data)
    mock_config_entry.data[CONF_BACKUP_MODE_ENTITY] = "binary_sensor.backup_mode"
    mock_config_entry.data[CONF_UPDATE_INTERVAL] = 900

    await async_update_entry(mock_hass, mock_config_entry)

    coordinator.update_time_triggers.assert_called_once()
    coordinator.async_request_refresh.assert_awaited()
