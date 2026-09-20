"""Additional tests for Kostal control paths."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_control_kostal_skips_on_backup(mock_hass, caplog):
    caplog.set_level("INFO")
    coordinator = _make_coordinator(mock_hass, {})
    coordinator._is_backup_active = MagicMock(return_value=True)
    mock_hass.services.async_call = AsyncMock()

    await coordinator._control_charge(50.0)

    assert "Backup mode active - skipping inverter control" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_kostal_invalid_target(mock_hass, caplog):
    coordinator = _make_coordinator(
        mock_hass, {CONF_USER_MIN_SOC: 10.0, CONF_USER_MAX_SOC: 90.0}
    )
    coordinator._is_backup_active = MagicMock(return_value=False)
    mock_hass.services.async_call = AsyncMock()

    await coordinator._control_charge(5.0)

    assert "Target SOC 5.0% is outside allowed range [10.0%, 90.0%]" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_kostal_sets_min_and_grid(mock_hass):
    battery_state = MagicMock()
    battery_state.state = "10"
    min_soc_state = MagicMock()
    min_soc_state.state = "8"
    grid_state = MagicMock()
    grid_state.state = "off"

    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.soc": battery_state,
        "number.min_soc": min_soc_state,
        "switch.grid": grid_state,
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
            CONF_MIN_SOC_ENTITY: "number.min_soc",
            CONF_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 8.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_COMMAND_DELAY: 0.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._apply_absolute_charge_power_limit = AsyncMock()

    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._control_charge(50.0)

    calls = [call.args for call in mock_hass.services.async_call.call_args_list]
    assert ("number", "set_value") in [(c[0], c[1]) for c in calls]
    assert ("switch", "turn_on") in [(c[0], c[1]) for c in calls]
    coordinator._apply_absolute_charge_power_limit.assert_awaited()


@pytest.mark.asyncio
async def test_stop_grid_charging_turns_off_and_resets(mock_hass):
    mock_hass.states.async_set("switch.grid", "on")
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass, {CONF_GRID_CHARGE_SWITCH: "switch.grid"}
    )
    coordinator._reset_absolute_charge_power = AsyncMock()
    coordinator._finalize_auto_test = MagicMock()

    await coordinator._stop_grid_charging()

    mock_hass.services.async_call.assert_awaited_once_with(
        "switch", "turn_off", {"entity_id": "switch.grid"}
    )
    coordinator._reset_absolute_charge_power.assert_awaited()
    coordinator._finalize_auto_test.assert_called_once()


_CONTROL_CONFIG = {
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_MIN_SOC_ENTITY: "number.min_soc",
    CONF_GRID_CHARGE_SWITCH: "switch.grid",
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_COMMAND_DELAY: 0.0,
}


@pytest.mark.asyncio
async def test_control_kostal_min_soc_service_error_keeps_grid_charge_off(mock_hass, caplog):
    """A failed min SOC write must not switch grid charging on (finding F10)."""
    mock_hass.states.async_set("sensor.soc", "10")
    mock_hass.states.async_set("number.min_soc", "8")
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.services.async_call = AsyncMock(side_effect=HomeAssistantError("inverter busy"))

    coordinator = _make_coordinator(mock_hass, _CONTROL_CONFIG)
    coordinator._is_backup_active = MagicMock(return_value=False)

    await coordinator._control_charge(50.0)

    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 50.0}
    )
    assert coordinator._last_soc_set is None
    assert "Error setting min SOC" in caplog.text


@pytest.mark.asyncio
async def test_update_data_survives_min_soc_service_error(mock_hass):
    """The polling update returns normally when the min SOC write fails."""
    mock_hass.states.async_set("sensor.soc", "10")
    mock_hass.states.async_set("number.min_soc", "8")
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.services.async_call = AsyncMock(side_effect=[HomeAssistantError("boom")])

    coordinator = _make_coordinator(mock_hass, _CONTROL_CONFIG)
    coordinator.is_enabled = True
    coordinator.is_active = True
    coordinator.initial_calculated_soc = 50.0
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._handle_auto_charge = AsyncMock()

    with patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=datetime(2026, 1, 15, 2, 0),
    ):
        data = await coordinator._async_update_data()

    assert isinstance(data, dict)
    assert data["is_active"] is True
    assert data["target_reached"] is False
    assert data["current_soc"] == 10.0
    # Only the failed min SOC write was attempted; no grid charge command
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 50.0}
    )
