"""Characterization tests: ``async_setup_entry`` with a real coordinator.

Unlike ``tests/test_setup.py`` the coordinator is not patched. The strict
``mock_hass`` fixture provides ``bus``/``loop`` mocks so Home Assistant's own
``async_track_time_change`` / ``async_track_state_change_event`` run for real.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState, current_entry

from custom_components.inverter_charge_night import (
    PLATFORMS,
    async_setup_entry,
)
from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BACKUP_MODE_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
)


def _prepare_entry(mock_config_entry, extra: dict | None = None):
    """Give the MagicMock entry what a real entry has during setup."""
    mock_config_entry.data = {**mock_config_entry.data, **(extra or {})}
    # DataUpdateCoordinator.async_config_entry_first_refresh insists on this state
    mock_config_entry.state = ConfigEntryState.SETUP_IN_PROGRESS
    mock_config_entry.add_update_listener = MagicMock(return_value=lambda: None)
    mock_config_entry.async_on_unload = MagicMock()
    return mock_config_entry


async def _setup(mock_hass, entry) -> InverterChargeNightCoordinator:
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    # The startup check needs the required entities to be known to hass
    for key in (CONF_BATTERY_SOC_ENTITY, CONF_KOSTAL_MIN_SOC_ENTITY, CONF_KOSTAL_GRID_CHARGE_SWITCH):
        mock_hass.states.async_set(entry.data[key], "50")
    # HA sets this context variable while an entry is being set up; the
    # coordinator picks its config entry up from it.
    token = current_entry.set(entry)
    try:
        with patch("custom_components.inverter_charge_night.ir.async_delete_issue"
    ), patch(
        "custom_components.inverter_charge_night.ir.async_get"):
            assert await async_setup_entry(mock_hass, entry) is True
    finally:
        current_entry.reset(token)
    coordinator = entry.runtime_data
    assert isinstance(coordinator, InverterChargeNightCoordinator)
    return coordinator


@pytest.mark.asyncio
async def test_setup_entry_registers_two_time_triggers_and_backup_listener(
    mock_hass, mock_config_entry
):
    entry = _prepare_entry(
        mock_config_entry, {CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup_mode"}
    )

    coordinator = await _setup(mock_hass, entry)

    # First refresh ran against an inactive window and touched nothing
    assert coordinator.config_entry is entry
    assert coordinator.data == {
        "calculated_soc": None,
        "is_active": False,
        "target_reached": False,
        "operation_mode": "night_charge",
        "skip_next": False,
    }
    mock_hass.services.async_call.assert_not_awaited()

    # Platforms forwarded, update listener registered for unload
    mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, PLATFORMS
    )
    entry.add_update_listener.assert_called_once()
    # Two unload hooks: the DataUpdateCoordinator registers its own shutdown,
    # async_setup_entry registers the update-listener unsubscribe
    unload_hooks = [c.args[0] for c in entry.async_on_unload.call_args_list]
    assert unload_hooks == [
        coordinator.async_shutdown,
        entry.add_update_listener.return_value,
    ]

    # setup_time_triggers registered window start and end
    assert len(coordinator._time_triggers) == 2
    assert all(callable(unsub) for unsub in coordinator._time_triggers)
    mock_hass.async_create_task.assert_called_once()
    assert (
        mock_hass.async_create_task.call_args.kwargs["name"]
        == "inverter_charge_night_check_window"
    )

    # Backup listener is set because backup_mode_entity is configured
    assert coordinator._backup_mode_listener is not None
    coordinator._remove_backup_mode_listener()
    assert coordinator._backup_mode_listener is None


@pytest.mark.asyncio
async def test_setup_entry_without_backup_entity_sets_no_backup_listener(
    mock_hass, mock_config_entry
):
    entry = _prepare_entry(mock_config_entry)

    coordinator = await _setup(mock_hass, entry)

    assert len(coordinator._time_triggers) == 2
    assert coordinator._backup_mode_listener is None
    mock_hass.services.async_call.assert_not_awaited()
