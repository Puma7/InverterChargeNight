"""Tests for time parsing and triggers."""
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_END_TIME,
    CONF_START_TIME,
    CONF_ACTIVE_START_DATE,
    CONF_ACTIVE_END_DATE,
)


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


def test_parse_time_valid(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    hour, minute = coordinator._parse_time("23:15", "00:00")
    assert (hour, minute) == (23, 15)


def test_parse_time_invalid_uses_default(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    hour, minute = coordinator._parse_time("99:99", "01:30")
    assert (hour, minute) == (1, 30)


def test_parse_time_invalid_default_fallback(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    hour, minute = coordinator._parse_time("bad", "also-bad")
    assert (hour, minute) == (0, 0)


def test_parse_date_optional_invalid_and_range(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._parse_date_optional("2025-13-01") is None
    assert coordinator._parse_date_optional(None) is None
    assert coordinator._parse_date_optional("") is None


def test_is_within_date_range_invalid_range_returns_true(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_ACTIVE_START_DATE: "2025-12-31", CONF_ACTIVE_END_DATE: "2025-01-01"},
    )
    assert coordinator._is_within_date_range() is True


def test_is_within_date_range_before_start(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_ACTIVE_START_DATE: "2025-12-31"}
    )
    with patch(
        "custom_components.inverter_charge_night.dt_util.now",
        return_value=datetime(2025, 1, 1),
    ):
        assert coordinator._is_within_date_range() is False


def test_is_time_between_normal_range(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._is_time_between(
        check_time=time(12, 0),
        start_time=time(8, 0),
        end_time=time(18, 0),
    ) is True


def test_is_time_between_overnight(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._is_time_between(
        check_time=time(1, 0),
        start_time=time(22, 0),
        end_time=time(5, 0),
    ) is True


def test_setup_and_remove_time_triggers(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    )
    trigger = MagicMock()
    mock_hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())

    with patch(
        "custom_components.inverter_charge_night.async_track_time_change",
        return_value=trigger,
    ):
        coordinator.setup_time_triggers()

    assert len(coordinator._time_triggers) == 2
    coordinator.remove_time_triggers()
    trigger.assert_called()


def test_update_time_triggers_updates_listener(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_START_TIME: "00:00",
            CONF_END_TIME: "05:59",
            CONF_BATTERY_SOC_ENTITY: "sensor.old",
        },
    )
    coordinator.is_active = True
    coordinator._setup_battery_soc_listener = MagicMock()
    coordinator.remove_time_triggers = MagicMock()
    coordinator.setup_time_triggers = MagicMock()
    coordinator._check_current_window = AsyncMock()
    mock_hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())

    coordinator.entry.data = {
        CONF_START_TIME: "01:00",
        CONF_END_TIME: "06:00",
        CONF_BATTERY_SOC_ENTITY: "sensor.new",
    }

    coordinator.update_time_triggers()

    coordinator._setup_battery_soc_listener.assert_called_once()
    mock_hass.async_create_task.assert_called_once()
