"""Additional tests for Kostal control paths."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
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
async def test_control_kostal_skips_on_backup(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator._is_backup_active = MagicMock(return_value=True)
    mock_hass.services.async_call = AsyncMock()

    await coordinator._control_kostal(50.0)

    assert not mock_hass.services.async_call.called


@pytest.mark.asyncio
async def test_control_kostal_invalid_target(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_USER_MIN_SOC: 10.0, CONF_USER_MAX_SOC: 90.0}
    )
    coordinator._is_backup_active = MagicMock(return_value=False)
    mock_hass.services.async_call = AsyncMock()

    await coordinator._control_kostal(5.0)

    assert not mock_hass.services.async_call.called


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
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 8.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_COMMAND_DELAY: 0.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._apply_absolute_charge_power_limit = AsyncMock()

    with patch("asyncio.sleep", new=AsyncMock()):
        await coordinator._control_kostal(50.0)

    calls = [call.args for call in mock_hass.services.async_call.call_args_list]
    assert ("number", "set_value") in [(c[0], c[1]) for c in calls]
    assert ("switch", "turn_on") in [(c[0], c[1]) for c in calls]
    coordinator._apply_absolute_charge_power_limit.assert_awaited()


@pytest.mark.asyncio
async def test_control_kostal_skips_min_soc_when_in_cooldown(mock_hass):
    battery_state = MagicMock()
    battery_state.state = "10"
    min_soc_state = MagicMock()
    min_soc_state.state = "8"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "sensor.soc": battery_state,
        "number.min_soc": min_soc_state,
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 8.0,
            CONF_USER_MAX_SOC: 100.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._last_soc_set = 50.0
    coordinator._last_soc_set_at = 1000.0

    with patch(
        "custom_components.inverter_charge_night.time_module.monotonic",
        return_value=1000.0,
    ):
        await coordinator._control_kostal(50.0)

    assert not mock_hass.services.async_call.called


@pytest.mark.asyncio
async def test_stop_grid_charging_turns_off_and_resets(mock_hass):
    grid_state = MagicMock()
    grid_state.state = "on"
    mock_hass.states.get.return_value = grid_state
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass, {CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid"}
    )
    coordinator._reset_absolute_charge_power = AsyncMock()
    coordinator._finalize_auto_test = MagicMock()

    await coordinator._stop_grid_charging()

    mock_hass.services.async_call.assert_awaited()
    coordinator._reset_absolute_charge_power.assert_awaited()
    coordinator._finalize_auto_test.assert_called_once()
