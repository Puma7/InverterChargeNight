"""Tests for diagnostics output."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.inverter_charge_night import const
from custom_components.inverter_charge_night.planner import EveningOutlook
from custom_components.inverter_charge_night.diagnostics import (
    REDACT_KEYS,
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
    assert diagnostics["entry"]["min_soc_entity"] == "**REDACTED**"

def test_every_entity_config_key_is_redacted():
    """A config key that names an entity of the user's system must be redacted.

    The expected set is derived from ``const.py`` so that the next ``*_entity``
    or ``*_switch`` key cannot be forgotten in ``REDACT_KEYS`` (finding F14:
    ``discharge_limit_entity`` and ``house_load_entity`` were missing).
    """
    entity_keys = {
        value
        for name, value in vars(const).items()
        if name.startswith("CONF_")
        and isinstance(value, str)
        and (value.endswith("_entity") or value.endswith("_switch"))
    }

    assert entity_keys, "no entity config keys found - the derivation is broken"
    # Plus the names the two inverter keys carried before 3.0.2: a diagnostics
    # dump taken from an entry that has not been migrated yet still holds them.
    assert REDACT_KEYS == entity_keys | set(const.LEGACY_INVERTER_KEYS)


@pytest.mark.asyncio
async def test_diagnostics_explain_a_window_outside_the_schedule(mock_hass, mock_config_entry):
    """"Why is it doing something at three in the afternoon" has to be answerable.

    An ad-hoc window and the evening rescue drive the inverter outside any
    configured window, so a diagnostics download that does not mention them
    leaves the one question a support request actually asks unanswered.
    """
    coordinator = MagicMock()
    coordinator.entry = mock_config_entry
    coordinator._adhoc_until = datetime(2026, 6, 1, 18, 0)
    coordinator._adhoc_reason = "evening_rescue"
    coordinator._adhoc_target_soc = 62.0
    coordinator._adhoc_allow_grid_charge = True
    coordinator._rescue_stage = 2
    coordinator.evening_rescue_charge = True
    coordinator.last_evening_outlook = EveningOutlook(
        zone_start=datetime(2026, 6, 1, 18, 0),
        zone_end=datetime(2026, 6, 1, 21, 0),
        required_soc=62.0,
        projected_soc=38.0,
        missing_kwh=2.4,
        pv_to_come_kwh=0.3,
        load_to_come_kwh=2.0,
        forecast_available=True,
    )
    coordinator.get_auto_efficiency_data = MagicMock(
        return_value={"bands": {"40": {"best_power_w": 3000}}}
    )
    coordinator._auto_test_start = None
    mock_config_entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(mock_hass, mock_config_entry)

    state = diagnostics["state"]
    assert state["adhoc_reason"] == "evening_rescue"
    assert state["adhoc_until"] == "2026-06-01T18:00:00"
    assert state["rescue_stage"] == 2
    assert state["evening_outlook"]["missing_kwh"] == 2.4
    assert state["auto_efficiency_bands"] == {"40": {"best_power_w": 3000}}
