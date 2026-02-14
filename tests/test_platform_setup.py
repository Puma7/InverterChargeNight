"""Tests for platform setup functions."""
from unittest.mock import MagicMock

import pytest

from custom_components.inverter_charge_night import binary_sensor, number, sensor, switch


@pytest.mark.asyncio
async def test_binary_sensor_async_setup_entry(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    mock_config_entry.runtime_data = coordinator
    mock_config_entry.title = "Test"
    add_entities = MagicMock()
    await binary_sensor.async_setup_entry(mock_hass, mock_config_entry, add_entities)
    add_entities.assert_called_once()


@pytest.mark.asyncio
async def test_sensor_async_setup_entry(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    mock_config_entry.runtime_data = coordinator
    mock_config_entry.title = "Test"
    add_entities = MagicMock()
    await sensor.async_setup_entry(mock_hass, mock_config_entry, add_entities)
    add_entities.assert_called_once()


@pytest.mark.asyncio
async def test_number_async_setup_entry(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    mock_config_entry.runtime_data = coordinator
    mock_config_entry.title = "Test"
    add_entities = MagicMock()
    await number.async_setup_entry(mock_hass, mock_config_entry, add_entities)
    add_entities.assert_called_once()


@pytest.mark.asyncio
async def test_switch_async_setup_entry(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    mock_config_entry.runtime_data = coordinator
    mock_config_entry.title = "Test"
    add_entities = MagicMock()
    await switch.async_setup_entry(mock_hass, mock_config_entry, add_entities)
    add_entities.assert_called_once()
