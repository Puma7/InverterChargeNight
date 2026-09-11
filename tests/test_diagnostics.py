"""Tests for diagnostics output."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.inverter_charge_night.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.mark.asyncio
async def test_diagnostics_redacts_entities(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.target_reached = False
    coordinator.calculated_soc = 50.0
    coordinator.initial_calculated_soc = 50.0
    coordinator.minimum_calculated_soc = 45.0
    coordinator.override_soc = None
    coordinator.auto_efficient_charge = False
    coordinator._auto_test_active = True
    coordinator._auto_test_power_w = 6000
    coordinator._auto_test_start = datetime.now(timezone.utc)
    coordinator._auto_energy_sent_wh = 1000.0
    coordinator._auto_energy_received_wh = 950.0
    coordinator.get_auto_efficiency_data.return_value = {"best_power_w": 6000, "best_loss": 0.05, "history": {"6000": 0.05}}

    mock_config_entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(
        mock_hass, mock_config_entry
    )

    assert "entry" in diagnostics
    assert "options" in diagnostics
    assert "state" in diagnostics
    # Ensure entity IDs are redacted
    entry = diagnostics["entry"]
    assert entry["pv_forecast_entity"] == "**REDACTED**"
    assert entry["battery_soc_entity"] == "**REDACTED**"
    assert diagnostics["state"]["auto_efficiency_best_power_w"] == 6000
    assert diagnostics["state"]["auto_test_active"] is True
    assert diagnostics["state"]["auto_test_power_w"] == 6000

@pytest.mark.asyncio
async def test_diagnostics_without_coordinator_omits_state(mock_hass, mock_config_entry):
    """An entry whose setup never finished has no runtime_data and no state block."""
    assert not hasattr(mock_config_entry, "runtime_data")

    diagnostics = await async_get_config_entry_diagnostics(mock_hass, mock_config_entry)

    assert set(diagnostics) == {"entry", "options"}
    assert diagnostics["entry"]["kostal_min_soc_entity"] == "**REDACTED**"
