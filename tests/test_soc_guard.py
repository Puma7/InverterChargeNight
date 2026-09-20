"""Test SOC guard logic for charging."""

import pytest
from unittest.mock import Mock, AsyncMock
from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)


@pytest.mark.asyncio
async def test_skip_charging_when_current_soc_equals_target():
    """Test that charging is skipped when current SOC equals target SOC."""
    # Setup
    hass = Mock()
    entry = Mock()
    entry.data = {
        "battery_soc_entity": "sensor.battery_soc",
        "grid_charge_switch": "switch.grid_charge",
        "min_soc_entity": "number.min_soc",
        "battery_capacity": 10.0,
        "forecast_error_margin": 10.0,
        "user_min_soc": 8.0,
        "user_max_soc": 100.0,
        "default_min_soc": 8.0,
    }

    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.original_min_soc = 8.0

    # Mock battery SOC state (75%)
    battery_soc_state = Mock()
    battery_soc_state.state = "75"

    # Mock grid charge switch state (off)
    grid_charge_state = Mock()
    grid_charge_state.state = "off"

    # Mock min SOC state
    min_soc_state = Mock()
    min_soc_state.state = "8"

    hass.states.get.side_effect = lambda entity_id: {
        "sensor.battery_soc": battery_soc_state,
        "switch.grid_charge": grid_charge_state,
        "number.min_soc": min_soc_state,
    }.get(entity_id)

    # Mock service calls
    hass.services.async_call = AsyncMock()

    # Test: currentSOC = 75, targetSOC = 75 → should skip charging
    target_soc = 75.0
    await coordinator._control_charge(target_soc)

    # Verify grid charge was NOT turned on
    grid_charge_calls = [
        call for call in hass.services.async_call.call_args_list
        if call[0][0] == "switch" and call[0][1] == "turn_on"
    ]
    assert len(grid_charge_calls) == 0, "Grid charge should not be turned on when currentSOC >= targetSOC"

    # Verify min SOC was still set (this happens before SOC check)
    min_soc_calls = [
        call for call in hass.services.async_call.call_args_list
        if call.args and call.args[0] == "number" and call.args[1] == "set_value"
    ]
    assert len(min_soc_calls) == 1, "Min SOC should still be set"
    data = min_soc_calls[0].kwargs.get("service_data")
    if data is None and len(min_soc_calls[0].args) >= 3:
        data = min_soc_calls[0].args[2]
    assert data["value"] == target_soc


@pytest.mark.asyncio
async def test_skip_charging_when_current_soc_greater_than_target():
    """Test that charging is skipped when current SOC is greater than target SOC."""
    # Setup
    hass = Mock()
    entry = Mock()
    entry.data = {
        "battery_soc_entity": "sensor.battery_soc",
        "grid_charge_switch": "switch.grid_charge",
        "min_soc_entity": "number.min_soc",
        "battery_capacity": 10.0,
        "forecast_error_margin": 10.0,
        "user_min_soc": 8.0,
        "user_max_soc": 100.0,
        "default_min_soc": 8.0,
    }

    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.original_min_soc = 8.0

    # Mock battery SOC state (80%)
    battery_soc_state = Mock()
    battery_soc_state.state = "80"

    # Mock grid charge switch state (off)
    grid_charge_state = Mock()
    grid_charge_state.state = "off"

    # Mock min SOC state
    min_soc_state = Mock()
    min_soc_state.state = "8"

    hass.states.get.side_effect = lambda entity_id: {
        "sensor.battery_soc": battery_soc_state,
        "switch.grid_charge": grid_charge_state,
        "number.min_soc": min_soc_state,
    }.get(entity_id)

    # Mock service calls
    hass.services.async_call = AsyncMock()

    # Test: currentSOC = 80, targetSOC = 75 → should skip charging
    target_soc = 75.0
    await coordinator._control_charge(target_soc)

    # Verify grid charge was NOT turned on
    grid_charge_calls = [
        call for call in hass.services.async_call.call_args_list
        if call[0][0] == "switch" and call[0][1] == "turn_on"
    ]
    assert len(grid_charge_calls) == 0, "Grid charge should not be turned on when currentSOC >= targetSOC"


@pytest.mark.asyncio
async def test_start_charging_when_current_soc_less_than_target():
    """Test that charging starts normally when current SOC is less than target SOC."""
    # Setup
    hass = Mock()
    entry = Mock()
    entry.data = {
        "battery_soc_entity": "sensor.battery_soc",
        "grid_charge_switch": "switch.grid_charge",
        "min_soc_entity": "number.min_soc",
        "battery_capacity": 10.0,
        "forecast_error_margin": 10.0,
        "user_min_soc": 8.0,
        "user_max_soc": 100.0,
        "default_min_soc": 8.0,
    }

    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.original_min_soc = 8.0

    # Mock battery SOC state (70%)
    battery_soc_state = Mock()
    battery_soc_state.state = "70"

    # Mock grid charge switch state (off)
    grid_charge_state = Mock()
    grid_charge_state.state = "off"

    # Mock min SOC state
    min_soc_state = Mock()
    min_soc_state.state = "8"

    hass.states.get.side_effect = lambda entity_id: {
        "sensor.battery_soc": battery_soc_state,
        "switch.grid_charge": grid_charge_state,
        "number.min_soc": min_soc_state,
    }.get(entity_id)

    # Mock service calls
    hass.services.async_call = AsyncMock()

    # Test: currentSOC = 70, targetSOC = 75 → should start charging
    target_soc = 75.0
    await coordinator._control_charge(target_soc)

    # Verify both min SOC was set AND grid charge was turned on
    assert hass.services.async_call.call_count == 2, "Both min SOC and grid charge should be called"

    # Check min SOC call
    min_soc_calls = [
        call for call in hass.services.async_call.call_args_list
        if call.args and call.args[0] == "number" and call.args[1] == "set_value"
    ]
    assert len(min_soc_calls) == 1, "Min SOC should be set"
    data = min_soc_calls[0].kwargs.get("service_data")
    if data is None and len(min_soc_calls[0].args) >= 3:
        data = min_soc_calls[0].args[2]
    assert data["value"] == target_soc

    # Check grid charge call
    grid_charge_calls = [
        call for call in hass.services.async_call.call_args_list
        if call.args and call.args[0] == "switch" and call.args[1] == "turn_on"
    ]
    assert len(grid_charge_calls) == 1, "Grid charge should be turned on"
    data = grid_charge_calls[0].kwargs.get("service_data")
    if data is None and len(grid_charge_calls[0].args) >= 3:
        data = grid_charge_calls[0].args[2]
    assert data["entity_id"] == "switch.grid_charge"


@pytest.mark.asyncio
async def test_soc_guard_handles_unavailable_battery_soc():
    """Test that charging proceeds normally when battery SOC entity is unavailable."""
    # Setup
    hass = Mock()
    entry = Mock()
    entry.data = {
        "battery_soc_entity": "sensor.battery_soc",
        "grid_charge_switch": "switch.grid_charge",
        "min_soc_entity": "number.min_soc",
        "battery_capacity": 10.0,
        "forecast_error_margin": 10.0,
        "user_min_soc": 8.0,
        "user_max_soc": 100.0,
        "default_min_soc": 8.0,
    }

    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.original_min_soc = 8.0

    # Mock battery SOC state as unavailable
    battery_soc_state = Mock()
    battery_soc_state.state = "unavailable"

    # Mock grid charge switch state (off)
    grid_charge_state = Mock()
    grid_charge_state.state = "off"

    # Mock min SOC state
    min_soc_state = Mock()
    min_soc_state.state = "8"

    hass.states.get.side_effect = lambda entity_id: {
        "sensor.battery_soc": battery_soc_state,
        "switch.grid_charge": grid_charge_state,
        "number.min_soc": min_soc_state,
    }.get(entity_id)

    # Mock service calls
    hass.services.async_call = AsyncMock()

    # Test: unavailable SOC should proceed with charging
    target_soc = 75.0
    await coordinator._control_charge(target_soc)

    # Verify min SOC was set but grid charge was NOT turned on (safety on unavailable SOC)
    min_soc_calls = [
        call for call in hass.services.async_call.call_args_list
        if call.args and call.args[0] == "number" and call.args[1] == "set_value"
    ]
    grid_charge_calls = [
        call for call in hass.services.async_call.call_args_list
        if call.args and call.args[0] == "switch" and call.args[1] == "turn_on"
    ]
    assert len(min_soc_calls) == 1, "Min SOC should be set when SOC is unavailable"
    assert len(grid_charge_calls) == 0, "Grid charge should be skipped when SOC is unavailable"
