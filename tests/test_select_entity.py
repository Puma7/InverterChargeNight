"""Tests for select platform (OperationModeSelect)."""
from __future__ import annotations

from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.select import (
    OperationModeSelect,
    async_setup_entry,
)
from custom_components.inverter_charge_night.const import (
    CONF_OPERATION_MODE,
    DOMAIN,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
)


def _real_mode_switch(coordinator) -> None:
    """Let the mock run the coordinator's real teardown for a mode change.

    The teardown moved into ``async_apply_operation_mode`` so the options flow
    goes through the same code (finding Q2); the select entity only calls it.
    """
    # Every attribute of a MagicMock is truthy, which the re-entrancy guard
    # would read as "a mode switch is already running".
    coordinator._switching_mode = False
    coordinator._ending = False
    coordinator.last_plan = None
    coordinator.planned_charge_power_w = None
    coordinator.async_apply_operation_mode = partial(
        InverterChargeNightCoordinator.async_apply_operation_mode, coordinator
    )


@pytest.mark.asyncio
async def test_select_async_setup_entry(mock_hass, mock_config_entry, mock_coordinator):
    """Test select platform setup creates OperationModeSelect."""
    mock_config_entry.runtime_data = mock_coordinator
    mock_coordinator.operation_mode = MODE_NIGHT_CHARGE
    added = []
    await async_setup_entry(mock_hass, mock_config_entry, lambda entities: added.extend(entities))
    assert len(added) == 1
    assert isinstance(added[0], OperationModeSelect)


@pytest.mark.asyncio
async def test_operation_mode_select_current_option(mock_config_entry, mock_coordinator):
    """Test current_option returns the coordinator's operation_mode."""
    mock_coordinator.operation_mode = MODE_NIGHT_CHARGE
    select = OperationModeSelect(mock_coordinator, mock_config_entry)
    assert select.current_option == MODE_NIGHT_CHARGE

    mock_coordinator.operation_mode = MODE_MORNING_DISCHARGE
    assert select.current_option == MODE_MORNING_DISCHARGE


@pytest.mark.asyncio
async def test_operation_mode_select_option_change(mock_config_entry, mock_coordinator):
    """Test async_select_option switches mode and resets active window."""
    mock_coordinator.operation_mode = MODE_NIGHT_CHARGE
    mock_coordinator.is_active = True
    mock_coordinator.target_reached = False
    mock_coordinator.initial_calculated_soc = 80.0
    mock_coordinator.minimum_calculated_soc = 80.0
    mock_coordinator.override_soc = 50.0
    mock_coordinator._remove_battery_soc_listener = MagicMock()
    mock_coordinator._remove_inverter_min_soc_listener = MagicMock()
    mock_coordinator._stop_periodic_verification = AsyncMock()
    mock_coordinator._check_current_window = AsyncMock()
    _real_mode_switch(mock_coordinator)

    select = OperationModeSelect(mock_coordinator, mock_config_entry)
    select.hass = MagicMock()
    select.async_write_ha_state = MagicMock()

    await select.async_select_option(MODE_MORNING_DISCHARGE)

    assert mock_coordinator.operation_mode == MODE_MORNING_DISCHARGE
    assert mock_coordinator.is_active is False
    mock_coordinator._reset_settings.assert_called_once()
    mock_coordinator._check_current_window.assert_called_once()


@pytest.mark.asyncio
async def test_operation_mode_select_same_option_noop(mock_config_entry, mock_coordinator):
    """Test async_select_option does nothing when selecting same mode."""
    mock_coordinator.operation_mode = MODE_NIGHT_CHARGE
    mock_coordinator._check_current_window = AsyncMock()

    select = OperationModeSelect(mock_coordinator, mock_config_entry)
    select.hass = MagicMock()
    select.async_write_ha_state = MagicMock()

    await select.async_select_option(MODE_NIGHT_CHARGE)
    mock_coordinator._reset_settings.assert_not_called()


@pytest.mark.asyncio
async def test_operation_mode_select_invalid_option(mock_config_entry, mock_coordinator):
    """Test async_select_option ignores invalid options."""
    mock_coordinator.operation_mode = MODE_NIGHT_CHARGE
    mock_coordinator._check_current_window = AsyncMock()

    select = OperationModeSelect(mock_coordinator, mock_config_entry)
    select.hass = MagicMock()
    select.async_write_ha_state = MagicMock()

    await select.async_select_option("invalid_mode")
    mock_coordinator._reset_settings.assert_not_called()
    assert mock_coordinator.operation_mode == MODE_NIGHT_CHARGE


@pytest.mark.asyncio
async def test_operation_mode_select_reset_error_handled(mock_config_entry, mock_coordinator):
    """Test mode switch handles error during reset gracefully."""
    mock_coordinator.operation_mode = MODE_NIGHT_CHARGE
    mock_coordinator.is_active = True
    mock_coordinator.target_reached = False
    mock_coordinator.initial_calculated_soc = 80.0
    mock_coordinator.minimum_calculated_soc = 80.0
    mock_coordinator.override_soc = None
    mock_coordinator._reset_settings = AsyncMock(side_effect=Exception("reset failed"))
    mock_coordinator._remove_battery_soc_listener = MagicMock()
    mock_coordinator._remove_inverter_min_soc_listener = MagicMock()
    mock_coordinator._stop_periodic_verification = AsyncMock()
    mock_coordinator._check_current_window = AsyncMock()
    _real_mode_switch(mock_coordinator)

    select = OperationModeSelect(mock_coordinator, mock_config_entry)
    select.hass = MagicMock()
    select.async_write_ha_state = MagicMock()

    await select.async_select_option(MODE_MORNING_DISCHARGE)
    assert mock_coordinator.is_active is False
    assert mock_coordinator.operation_mode == MODE_MORNING_DISCHARGE
