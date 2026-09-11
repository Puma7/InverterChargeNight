"""Tests for reset settings."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_DEFAULT_MIN_SOC,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
)


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_reset_settings_sets_min_soc_and_turns_off_grid(mock_hass):
    min_soc_state = MagicMock()
    min_soc_state.state = "8"
    grid_state = MagicMock()
    grid_state.state = "on"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_soc_state,
        "switch.grid": grid_state,
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_DEFAULT_MIN_SOC: 8.0,
        },
    )
    coordinator.original_min_soc = 5.0
    coordinator._reset_absolute_charge_power = AsyncMock()

    await coordinator._reset_settings()

    assert coordinator.original_min_soc is None
    calls = [call.args for call in mock_hass.services.async_call.call_args_list]
    assert ("number", "set_value") in [(c[0], c[1]) for c in calls]
    assert ("switch", "turn_off") in [(c[0], c[1]) for c in calls]


@pytest.mark.asyncio
async def test_reset_settings_warns_without_entities(mock_hass, caplog):
    mock_hass.services.async_call = AsyncMock()
    coordinator = _make_coordinator(mock_hass, {CONF_DEFAULT_MIN_SOC: 8.0})
    coordinator._reset_absolute_charge_power = AsyncMock()

    await coordinator._reset_settings()

    assert "No min SOC entity configured - cannot reset" in caplog.text
    assert "No grid charge switch configured - cannot reset" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()
    coordinator._reset_absolute_charge_power.assert_awaited_once()
