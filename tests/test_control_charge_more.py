"""More tests for control and stop behavior."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
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
        mock_hass, {CONF_GRID_CHARGE_SWITCH: "switch.grid"}
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
        mock_hass, {CONF_GRID_CHARGE_SWITCH: "switch.grid"}
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
            CONF_MIN_SOC_ENTITY: "number.min_soc",
            CONF_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)

    await coordinator._control_charge(50.0)

    # Inverter already at 50 % and the switch already on: nothing to send, but the
    # coordinator records the value as set and captures the original min SOC.
    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator._last_soc_set == 50.0
    # The live value is the original: since plan 005 a restart inside the window
    # restores the original from the persisted state instead of guessing it.
    assert coordinator.original_min_soc == 50.0


@pytest.mark.asyncio
async def test_control_kostal_defers_when_min_soc_unavailable(mock_hass, caplog):
    """Plan 005 (finding F6): no floor, no charging - and no original captured yet."""
    mock_hass.states.async_set("number.min_soc", "unavailable")
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.states.async_set("sensor.soc", "10")
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_SOC_ENTITY: "number.min_soc",
            CONF_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)

    await coordinator._control_charge(50.0)

    # Neither the min SOC nor grid charging is written; the periodic
    # verification applies the target once the entity reports a value
    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator.original_min_soc is None
    assert "number.min_soc is unavailable - deferring" in caplog.text


@pytest.mark.asyncio
async def test_control_kostal_keeps_high_live_value_as_original(mock_hass):
    """Plan 005 removed the "more than 10 points above default" heuristic (finding F4).

    An original of 50 % is stored as 50 %. A restart inside the window no
    longer reaches this code with the night target as live value, because the
    persisted original is restored first (see test_window_lifecycle).
    """
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
            CONF_MIN_SOC_ENTITY: "number.min_soc",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)

    await coordinator._control_charge(30.0)

    assert coordinator.original_min_soc == 50.0
    mock_hass.services.async_call.assert_awaited_with(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 30.0}
    )


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
            CONF_MIN_SOC_ENTITY: "number.min_soc",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)

    await coordinator._control_charge(30.0)

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
            CONF_MIN_SOC_ENTITY: "number.min_soc",
            CONF_GRID_CHARGE_SWITCH: "switch.grid",
            CONF_DEFAULT_MIN_SOC: 8.0,
            CONF_USER_MIN_SOC: 0.0,
            CONF_USER_MAX_SOC: 100.0,
            CONF_COMMAND_DELAY: 0.0,
        },
    )
    coordinator._is_backup_active = MagicMock(return_value=False)
    coordinator._last_soc_set = 60.0

    await coordinator._control_charge(60.0)

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
            CONF_MIN_SOC_ENTITY: "number.min_soc",
            CONF_GRID_CHARGE_SWITCH: "switch.grid",
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
            CONF_MIN_SOC_ENTITY: "number.min_soc",
            CONF_GRID_CHARGE_SWITCH: "switch.grid",
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
