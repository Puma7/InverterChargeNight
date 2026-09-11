"""Fixtures for Inverter Charge Night tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_registry import EntityRegistry
from homeassistant.setup import async_setup_component

from custom_components.inverter_charge_night.const import (
    DOMAIN,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_PV_FORECAST_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_START_TIME,
    CONF_END_TIME,
    CONF_USER_MIN_SOC,
    CONF_USER_MAX_SOC,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_DEFAULT_MIN_SOC,
)


@pytest.fixture(autouse=True)
def _no_frame_report():
    """Silence HA's frame helper, which the MagicMock hass never sets up.

    Recent Home Assistant versions call ``frame.report_usage`` from the
    DataUpdateCoordinator constructor and raise if the helper is missing.
    Older versions do not have the function, so only patch it when present.
    """
    if hasattr(frame, "report_usage"):
        with patch.object(frame, "report_usage", lambda *args, **kwargs: None):
            yield
    else:
        yield


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    """Create a mock config entry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.title = "Inverter Charge Night"
    entry.data = {
        CONF_KOSTAL_MIN_SOC_ENTITY: "number.kostal_min_soc",
        CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.kostal_grid_charge",
        CONF_PV_FORECAST_ENTITY: "sensor.pv_forecast",
        CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
        CONF_BATTERY_CAPACITY: 10.0,
        CONF_START_TIME: "00:00",
        CONF_END_TIME: "05:59",
        CONF_USER_MIN_SOC: 8.0,
        CONF_USER_MAX_SOC: 100.0,
        CONF_FORECAST_ERROR_MARGIN: 10.0,
        CONF_DEFAULT_MIN_SOC: 8.0,
    }
    entry.options = {}
    return entry


@pytest.fixture
def mock_hass() -> HomeAssistant:
    """Create a mock Home Assistant instance."""
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {}
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro, **kwargs: coro.close())
    hass.config_entries = MagicMock()
    return hass


@pytest.fixture
def mock_entity_registry() -> EntityRegistry:
    """Create a mock entity registry."""
    return MagicMock(spec=EntityRegistry)




@pytest.fixture
def mock_coordinator(mock_hass: HomeAssistant, mock_config_entry: ConfigEntry):
    """Create a mock coordinator."""
    with patch(
        "custom_components.inverter_charge_night.InverterChargeNightCoordinator"
    ) as mock_coordinator_class:
        coordinator = mock_coordinator_class.return_value
        coordinator.hass = mock_hass
        coordinator.entry = mock_config_entry
        coordinator.config = mock_config_entry.data
        coordinator.is_enabled = True
        coordinator.is_active = False
        coordinator.calculated_soc = None
        coordinator.target_reached = False
        coordinator.original_min_soc = None
        coordinator._time_triggers = []
        coordinator.async_request_refresh = AsyncMock()
        coordinator._reset_settings = AsyncMock()
        coordinator._control_kostal = AsyncMock()
        coordinator._stop_grid_charging = AsyncMock()
        yield coordinator


@pytest.fixture
async def setup_integration(hass: HomeAssistant, mock_config_entry: ConfigEntry):
    """Set up the integration."""
    hass.data[DOMAIN] = {}
    with patch(
        "custom_components.inverter_charge_night.InverterChargeNightCoordinator"
    ) as mock_coordinator_class:
        coordinator = mock_coordinator_class.return_value
        coordinator.async_config_entry_first_refresh = AsyncMock()
        coordinator.setup_time_triggers = MagicMock()
        hass.data[DOMAIN][mock_config_entry.entry_id] = coordinator
        
        yield coordinator


def create_mock_state(entity_id: str, state: str, attributes: dict | None = None):
    """Create a mock state object."""
    mock_state = MagicMock()
    mock_state.entity_id = entity_id
    mock_state.state = state
    mock_state.attributes = attributes or {}
    return mock_state


def create_mock_service_call(domain: str, service: str, **kwargs):
    """Create a mock service call."""
    call = MagicMock()
    call.domain = domain
    call.service = service
    call.data = kwargs
    return call
