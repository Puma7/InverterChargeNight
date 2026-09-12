"""Fixtures for Inverter Charge Night tests."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_registry import EntityRegistry

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
    # update_listeners is an instance attribute, so spec= does not provide it.
    # HA 2026.9's async_update_reload_and_abort reads it.
    entry.update_listeners = []
    entry.entry_id = "test_entry_id"
    entry.title = "Inverter Charge Night"
    # The min SOC entity identifies the inverter and is the entry's unique id.
    entry.unique_id = "number.kostal_min_soc"
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
    """Create a strict mock Home Assistant instance.

    ``hass.states.get`` returns ``None`` for every entity that a test has not
    registered via ``hass.states.async_set(entity_id, state, attributes)``.
    A plain ``MagicMock`` state would let ``float(state.state)`` raise a
    ``TypeError`` that product code swallows, making a broken test look green.
    Tests may still override ``hass.states.get.side_effect`` for special cases.
    """
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {}
    states: dict[str, Any] = {}
    hass.states = MagicMock()
    hass.states.get = MagicMock(side_effect=states.get)
    hass.states.async_set = MagicMock(
        side_effect=lambda eid, st, attributes=None: states.__setitem__(
            eid, create_mock_state(eid, str(st), attributes)
        )
    )
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.async_create_task = MagicMock(side_effect=lambda coro, **kwargs: coro.close())
    hass.config_entries = MagicMock()
    # No other entry is configured unless a test says so; the config and options
    # flows iterate this to detect a second entry pointed at the same inverter.
    hass.config_entries.async_entries.return_value = []
    # ``bus`` and ``loop`` are instance attributes of HomeAssistant, so the spec
    # does not provide them. HA's event helpers (async_track_state_change_event,
    # async_track_time_change) need both and then return real unsubscribe callables.
    hass.bus = MagicMock()
    hass.loop = MagicMock()
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
