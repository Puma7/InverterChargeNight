"""Tests for state change listeners."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_KOSTAL_MIN_SOC_ENTITY,
)


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_battery_soc_listener_stops_on_target(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"}
    )
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.target_reached = False
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    captured = {}

    def _capture(hass, entity_id, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_battery_soc_listener()

    event = MagicMock()
    event.data = {"new_state": MagicMock(state="55")}
    await captured["callback"](event)

    coordinator._stop_grid_charging.assert_awaited()
    assert coordinator.target_reached is True


@pytest.mark.asyncio
async def test_battery_soc_listener_handles_stop_error(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"}
    )
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.target_reached = False
    coordinator.minimum_calculated_soc = 50.0
    coordinator._stop_grid_charging = AsyncMock(side_effect=Exception("boom"))
    coordinator.async_request_refresh = AsyncMock()

    captured = {}

    def _capture(hass, entity_id, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_battery_soc_listener()

    event = MagicMock()
    event.data = {"new_state": MagicMock(state="55")}
    await captured["callback"](event)

    assert coordinator.target_reached is True


def test_remove_battery_soc_listener(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"}
    )
    listener = MagicMock()
    coordinator._battery_soc_listener = listener
    coordinator._remove_battery_soc_listener()
    listener.assert_called_once()


@pytest.mark.asyncio
async def test_inverter_min_soc_listener_triggers_restore(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc"}
    )
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 70.0
    coordinator._verify_and_restore_min_soc = AsyncMock()

    captured = {}

    def _capture(hass, entity_id, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_inverter_min_soc_listener()

    event = MagicMock()
    event.data = {"new_state": MagicMock(state="8")}
    await captured["callback"](event)

    coordinator._verify_and_restore_min_soc.assert_awaited()


@pytest.mark.asyncio
async def test_inverter_min_soc_listener_respects_cooldown(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc"}
    )
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = 70.0
    coordinator._verify_and_restore_min_soc = AsyncMock()
    coordinator._last_soc_set_at = 1000.0

    captured = {}

    def _capture(hass, entity_id, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch(
        "custom_components.inverter_charge_night.async_track_state_change_event",
        side_effect=_capture,
    ), patch(
        "custom_components.inverter_charge_night.time_module.monotonic",
        return_value=1000.0,
    ):
        coordinator._setup_inverter_min_soc_listener()

        event = MagicMock()
        event.data = {"new_state": MagicMock(state="8")}
        await captured["callback"](event)

    coordinator._verify_and_restore_min_soc.assert_not_awaited()
