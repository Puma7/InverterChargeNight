"""More tests for control and stop behavior."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_SOC_ENTITY,
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
async def test_stop_grid_charging_when_off(mock_hass, caplog):
    caplog.set_level("DEBUG")
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.services.async_call = AsyncMock()
    coordinator = _make_coordinator(
        mock_hass, {CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid"}
    )
    coordinator._reset_absolute_charge_power = AsyncMock()
    coordinator._finalize_auto_test = MagicMock()

    await coordinator._stop_grid_charging()

    assert "Grid charge already off" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()
    coordinator._reset_absolute_charge_power.assert_awaited_once()
    coordinator._finalize_auto_test.assert_called_once()


@pytest.mark.asyncio
async def test_stop_grid_charging_state_unavailable(mock_hass, caplog):
    # The strict hass returns None for the unregistered switch.grid
    mock_hass.services.async_call = AsyncMock()
    coordinator = _make_coordinator(
        mock_hass, {CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid"}
    )
    coordinator._reset_absolute_charge_power = AsyncMock()
    coordinator._finalize_auto_test = MagicMock()

    await coordinator._stop_grid_charging()

    assert "Cannot stop grid charge - entity state unavailable" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()
    coordinator._reset_absolute_charge_power.assert_awaited_once()


@pytest.mark.asyncio
async def test_control_kostal_min_soc_already_set(mock_hass):
    battery_state = MagicMock()
    battery_state.state = "10"
    min_soc_state = MagicMock()
    min_soc_state.state = "50"
    grid_state = MagicMock()
    grid_state.state = "on"

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
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)

    await coordinator._control_kostal(50.0)

    # Inverter already at 50 % and the switch already on: nothing to send, but the
    # coordinator records the value as set and captures the original min SOC.
    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator._last_soc_set == 50.0
    # 50 % is more than 10 points above the 8 % default, so the default is stored
    assert coordinator.original_min_soc == 8.0


@pytest.mark.asyncio
async def test_control_kostal_stores_default_when_min_soc_unavailable(mock_hass):
    mock_hass.states.async_set("number.min_soc", "unavailable")
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)

    await coordinator._control_kostal(50.0)

    assert coordinator.original_min_soc == 8.0


@pytest.mark.asyncio
async def test_control_kostal_original_min_soc_high_resets_to_default(mock_hass):
    min_soc_state = MagicMock()
    min_soc_state.state = "50"
    battery_state = MagicMock()
    battery_state.state = "10"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_soc_state,
        "sensor.soc": battery_state,
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)

    await coordinator._control_kostal(30.0)

    assert coordinator.original_min_soc == 8.0


@pytest.mark.asyncio
async def test_control_kostal_handles_bad_min_soc_state(mock_hass):
    min_soc_state = MagicMock()
    min_soc_state.state = "bad"
    battery_state = MagicMock()
    battery_state.state = "10"
    mock_hass.states.get.side_effect = lambda entity_id: {
        "number.min_soc": min_soc_state,
        "sensor.soc": battery_state,
    }.get(entity_id)
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)

    await coordinator._control_kostal(30.0)

    assert coordinator.original_min_soc == 8.0
