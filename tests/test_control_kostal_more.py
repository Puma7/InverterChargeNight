"""More tests for control and stop behavior."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_OPERATION_MODE,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    MODE_MORNING_DISCHARGE,
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


@pytest.mark.asyncio
async def test_control_kostal_sets_min_soc_even_if_last_set_matches_target(mock_hass):
    """The inverter's reported value wins over _last_soc_set (finding F10).

    We believe we already set 60 %, but the inverter reports 8 %: the value must
    be written again instead of being vetoed by the bookkeeping.
    """
    mock_hass.states.async_set("sensor.soc", "10")
    mock_hass.states.async_set("number.min_soc", "8")
    mock_hass.states.async_set("switch.grid", "on")
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
            CONF_COMMAND_DELAY: 0.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._last_soc_set = 60.0

    await coordinator._control_kostal(60.0)

    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 60.0}
    )
    assert coordinator._last_soc_set == 60.0


@pytest.mark.asyncio
async def test_control_discharge_sets_min_soc_even_if_last_set_matches_target(mock_hass):
    """Same for the discharge path: the reported 8 % is corrected to the 35 % floor."""
    mock_hass.states.async_set("sensor.soc", "40")
    mock_hass.states.async_set("number.min_soc", "8")
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._last_soc_set = 35.0

    await coordinator._control_discharge(35.0)

    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 35.0}
    )
    assert coordinator._last_soc_set == 35.0


@pytest.mark.asyncio
async def test_reset_settings_always_forgets_last_soc_set(mock_hass):
    """Even when the min SOC reset fails, _last_soc_set is cleared (it is log info only)."""
    mock_hass.states.async_set("number.min_soc", "45")
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.services.async_call = AsyncMock(side_effect=Exception("boom"))

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
            CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_DEFAULT_MIN_SOC: 8.0,
        },
    )
    coordinator._reset_absolute_charge_power = AsyncMock()
    coordinator._last_soc_set = 45.0
    coordinator.original_min_soc = 8.0
    coordinator.override_soc = 70.0

    await coordinator._reset_settings()

    assert coordinator._last_soc_set is None
    # The reset did not succeed, so the values needed for a retry are kept
    assert coordinator.original_min_soc == 8.0
    assert coordinator.override_soc == 70.0
