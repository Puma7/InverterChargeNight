"""Tests for setup and unload flows."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.inverter_charge_night import (
    async_setup_entry,
    async_unload_entry,
    async_update_entry,
)
from custom_components.inverter_charge_night.const import (
    CONF_BACKUP_MODE_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)

REQUIRED_ENTITY_KEYS = (
    CONF_BATTERY_SOC_ENTITY,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
)


def _register_required_entities(mock_hass, mock_config_entry, skip: str | None = None) -> None:
    """Make the strict mock_hass know the required entities (except ``skip``)."""
    for key in REQUIRED_ENTITY_KEYS:
        entity_id = mock_config_entry.data[key]
        if entity_id != skip:
            mock_hass.states.async_set(entity_id, "50")


@pytest.mark.asyncio
async def test_async_setup_entry_registers_coordinator(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.setup_time_triggers = MagicMock()
    coordinator._setup_backup_mode_listener = MagicMock()

    mock_config_entry.add_update_listener = MagicMock(return_value="unload_listener")
    mock_config_entry.async_on_unload = MagicMock()
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
    mock_hass.config_entries.async_update_entry = MagicMock()
    _register_required_entities(mock_hass, mock_config_entry)

    with patch(
        "custom_components.inverter_charge_night.InverterChargeNightCoordinator",
        return_value=coordinator,
    ), patch("custom_components.inverter_charge_night.ir.async_delete_issue"):
        result = await async_setup_entry(mock_hass, mock_config_entry)

    assert result is True
    assert mock_config_entry.runtime_data is coordinator
    assert mock_hass.data == {}
    coordinator.async_config_entry_first_refresh.assert_awaited()
    mock_hass.config_entries.async_forward_entry_setups.assert_awaited()
    coordinator.setup_time_triggers.assert_called_once()
    coordinator._setup_backup_mode_listener.assert_called_once()
    mock_config_entry.async_on_unload.assert_called_once()


@pytest.mark.asyncio
async def test_async_unload_entry_cleans_up(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.is_active = False
    coordinator.remove_time_triggers = MagicMock()
    coordinator._remove_battery_soc_listener = MagicMock()
    coordinator._remove_inverter_min_soc_listener = MagicMock()
    coordinator._stop_periodic_verification = MagicMock()
    coordinator._remove_backup_mode_listener = MagicMock()
    coordinator._reset_settings = AsyncMock()

    mock_config_entry.runtime_data = coordinator
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    result = await async_unload_entry(mock_hass, mock_config_entry)

    assert result is True
    coordinator.remove_time_triggers.assert_called_once()
    coordinator._remove_battery_soc_listener.assert_called_once()
    coordinator._remove_inverter_min_soc_listener.assert_called_once()
    coordinator._stop_periodic_verification.assert_called_once()
    coordinator._reset_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_update_entry_updates_backup_listener(mock_hass, mock_config_entry):
    coordinator = MagicMock()
    coordinator.config = dict(mock_config_entry.data)
    coordinator.entry = mock_config_entry
    coordinator.update_time_triggers = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator._setup_backup_mode_listener = MagicMock()

    mock_config_entry.runtime_data = coordinator

    mock_config_entry.data = dict(mock_config_entry.data)
    mock_config_entry.data[CONF_BACKUP_MODE_ENTITY] = "binary_sensor.backup_mode"
    mock_config_entry.data[CONF_UPDATE_INTERVAL] = 900

    await async_update_entry(mock_hass, mock_config_entry)

    coordinator.update_time_triggers.assert_called_once()
    coordinator.async_request_refresh.assert_awaited()


@pytest.mark.asyncio
async def test_async_setup_entry_missing_entity_creates_issue_and_raises(mock_hass, mock_config_entry):
    """A required entity unknown to HA raises ConfigEntryNotReady and files a repair issue."""
    missing = mock_config_entry.data[CONF_KOSTAL_MIN_SOC_ENTITY]
    _register_required_entities(mock_hass, mock_config_entry, skip=missing)
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()

    with patch(
        "custom_components.inverter_charge_night.InverterChargeNightCoordinator"
    ) as coordinator_cls, patch(
        "custom_components.inverter_charge_night.ir.async_create_issue"
    ) as create_issue, patch(
        "custom_components.inverter_charge_night.ir.async_delete_issue"
    ) as delete_issue:
        with pytest.raises(ConfigEntryNotReady, match=missing):
            await async_setup_entry(mock_hass, mock_config_entry)

    create_issue.assert_called_once()
    args, kwargs = create_issue.call_args
    assert args == (mock_hass, DOMAIN, f"entity_not_available_{missing}")
    assert kwargs["translation_key"] == "entity_not_available"
    assert kwargs["translation_placeholders"] == {"entity_id": missing}
    assert kwargs["is_fixable"] is False
    assert kwargs["issue_domain"] == DOMAIN
    # Nothing was set up: no coordinator, no platforms, no issue cleared
    coordinator_cls.assert_not_called()
    mock_hass.config_entries.async_forward_entry_setups.assert_not_awaited()
    delete_issue.assert_not_called()
    assert not hasattr(mock_config_entry, "runtime_data")


@pytest.mark.asyncio
async def test_async_setup_entry_all_entities_present_clears_issues(mock_hass, mock_config_entry):
    """With all required entities known, earlier repair issues are cleared and setup succeeds."""
    _register_required_entities(mock_hass, mock_config_entry)
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    mock_config_entry.add_update_listener = MagicMock(return_value=lambda: None)
    mock_config_entry.async_on_unload = MagicMock()
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock()

    with patch(
        "custom_components.inverter_charge_night.InverterChargeNightCoordinator",
        return_value=coordinator,
    ), patch(
        "custom_components.inverter_charge_night.ir.async_create_issue"
    ) as create_issue, patch(
        "custom_components.inverter_charge_night.ir.async_delete_issue"
    ) as delete_issue:
        result = await async_setup_entry(mock_hass, mock_config_entry)

    assert result is True
    create_issue.assert_not_called()
    assert delete_issue.call_count == 3
    assert [c.args for c in delete_issue.call_args_list] == [
        (mock_hass, DOMAIN, f"entity_not_available_{mock_config_entry.data[key]}")
        for key in REQUIRED_ENTITY_KEYS
    ]
    assert mock_config_entry.runtime_data is coordinator
