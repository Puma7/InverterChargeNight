"""Tests for backup mode listener."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import CONF_BACKUP_MODE_ENTITY


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_backup_mode_listener_triggers_window_check(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup"}
    )
    coordinator.is_enabled = True
    coordinator._check_current_window = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    captured = {}

    def _capture(hass, entity_id, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch(
        "custom_components.inverter_charge_night.coordinator.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_backup_mode_listener()

    await captured["callback"](MagicMock(data={"new_state": MagicMock(state="on")}))

    coordinator._check_current_window.assert_awaited()
    coordinator.async_request_refresh.assert_awaited()


def test_remove_backup_mode_listener(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup"}
    )
    listener = MagicMock()
    coordinator._backup_mode_listener = listener
    coordinator._remove_backup_mode_listener()
    listener.assert_called_once()
