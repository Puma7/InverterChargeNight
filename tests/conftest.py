"""Fixtures for Inverter Charge Night tests."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import UTC, datetime, time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_registry import EntityRegistry

from custom_components.inverter_charge_night.const import (
    DOMAIN,
    CONF_MIN_SOC_ENTITY,
    CONF_GRID_CHARGE_SWITCH,
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
def _no_feedback_wait():
    """A write KORE reports as unconfirmed waits for its fresh reading in real
    seconds; a test that wants the wait sets the constant itself."""
    with patch("custom_components.inverter_charge_night.coordinator.LIMIT_FEEDBACK_WAIT_S", 0):
        yield


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
        CONF_MIN_SOC_ENTITY: "number.kostal_min_soc",
        CONF_GRID_CHARGE_SWITCH: "switch.kostal_grid_charge",
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
    async def complete_service(domain, service, data, **kwargs):
        # A normal blocking write completes with actual feedback. Fault tests
        # replace this actor to model ignored commands or transport failures.
        if domain in ("switch", "input_boolean") and service in ("turn_on", "turn_off") and kwargs.get("blocking"):
            state = states.get(data.get("entity_id"))
            if state is not None:
                state.state = "on" if service == "turn_on" else "off"
                state.last_reported = datetime.now(UTC)
        if domain == "number" and service == "set_value" and kwargs.get("blocking"):
            state = states.get(data.get("entity_id"))
            if state is not None:
                state.state = str(data["value"])
                state.last_reported = datetime.now(UTC)

    hass.services.async_call = AsyncMock(side_effect=complete_service)
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
    hass.config = MagicMock()
    hass.config.time_zone = "Europe/Berlin"
    hass.config.language = "de"
    return hass


@pytest.fixture(autouse=True)
def local_translation_catalog():
    """Supply HA's translation boundary without a loader in the strict fake."""
    import json
    from pathlib import Path
    strings = json.loads((Path(__file__).parents[1] / "custom_components/inverter_charge_night/strings.json").read_text(encoding="utf-8"))
    translations = {
        f"component.inverter_charge_night.issues.{issue}.{key}": value
        for issue, texts in strings["issues"].items() for key, value in texts.items()
    }
    with patch("homeassistant.helpers.translation.async_get_translations", AsyncMock(return_value=translations)):
        yield


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
        coordinator._control_charge = AsyncMock()
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


@pytest.fixture
def draft_store(monkeypatch):
    """Real draft repository with a durable-memory stand-in for HA storage I/O."""
    from copy import deepcopy
    from custom_components.inverter_charge_night import setup_drafts
    files = {}
    class MemoryStore:
        def __init__(self, hass, version, key, **kwargs):
            self.key = key
        async def async_load(self):
            return deepcopy(files.get(self.key))
        async def async_save(self, data):
            files[self.key] = deepcopy(data)
    monkeypatch.setattr(setup_drafts, "Store", MemoryStore)
    return files
