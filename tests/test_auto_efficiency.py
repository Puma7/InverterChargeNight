"""Tests for auto efficient charge logic."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
)


def _make_coordinator(hass: HomeAssistant, data: dict, options: dict | None = None):
    entry = MagicMock()
    entry.title = "Test"
    entry.entry_id = "test_entry"
    entry.data = data
    entry.options = options or {}
    return InverterChargeNightCoordinator(hass, entry)


def test_get_power_w_converts_kw(mock_hass: HomeAssistant):
    mock_hass.states.async_set("sensor.power", "2.5", {"unit_of_measurement": "kW"})

    coordinator = _make_coordinator(mock_hass, {CONF_MIN_CHARGE_POWER_W: 1000, CONF_MAX_CHARGE_POWER_W: 2000})
    assert coordinator._get_power_w("sensor.power") == 2500.0


def test_select_next_auto_test_power_within_range(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    candidate = coordinator._select_next_auto_test_power_w()
    assert candidate is not None
    assert 5000 <= candidate <= 15000
    assert candidate % 100 == 0


@pytest.mark.asyncio
async def test_apply_absolute_charge_power_limit_converts_units(mock_hass: HomeAssistant):
    mock_hass.services.async_call = AsyncMock()
    mock_hass.states.async_set("number.abs_max", "12.0", {"unit_of_measurement": "kW"})

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.abs_max",
            CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 10000,
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    await coordinator._apply_absolute_charge_power_limit()

    mock_hass.services.async_call.assert_called()
    args, kwargs = mock_hass.services.async_call.call_args
    data = kwargs.get("service_data") if kwargs else None
    if data is None and len(args) >= 3:
        data = args[2]
    assert data["value"] == 10.0


@pytest.mark.asyncio
async def test_reset_absolute_charge_power_restores_original(mock_hass: HomeAssistant):
    mock_hass.services.async_call = AsyncMock()
    mock_hass.states.async_set("number.abs_max", "12.0", {"unit_of_measurement": "kW"})

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.abs_max",
            CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 10000,
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._original_absolute_charge_power = 12.0
    await coordinator._reset_absolute_charge_power()

    mock_hass.services.async_call.assert_called()
    args, kwargs = mock_hass.services.async_call.call_args
    data = kwargs.get("service_data") if kwargs else None
    if data is None and len(args) >= 3:
        data = args[2]
    assert data["value"] == 12.0


@pytest.mark.asyncio
async def test_start_auto_test_sets_fields(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._set_ac_charge_limit_w = AsyncMock()
    await coordinator._start_auto_test(5000)

    assert coordinator._auto_test_active is True
    assert coordinator._auto_test_power_w == 5000
    coordinator._set_ac_charge_limit_w.assert_awaited()


def test_reset_auto_test_state_clears_values(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._auto_test_active = True
    coordinator._auto_test_power_w = 5000
    coordinator._auto_test_start = MagicMock()
    coordinator._auto_last_sample_time = MagicMock()
    coordinator._auto_energy_sent_wh = 1.0
    coordinator._auto_energy_received_wh = 1.0

    coordinator._reset_auto_test_state()

    assert coordinator._auto_test_active is False
    assert coordinator._auto_test_power_w is None


def test_save_auto_efficiency_data_updates_options(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    mock_hass.config_entries.async_update_entry = MagicMock()
    data = {"best_power_w": 5000}

    coordinator._save_auto_efficiency_data(data)

    mock_hass.config_entries.async_update_entry.assert_called_once()
