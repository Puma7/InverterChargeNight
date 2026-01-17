"""Tests for periodic verification task setup."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


def test_start_periodic_verification_uses_background_task(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.is_active = True
    coordinator.is_enabled = True
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None: coro.close()
    )

    coordinator._start_periodic_verification()

    mock_hass.async_create_background_task.assert_called_once()


@pytest.mark.asyncio
async def test_periodic_verification_loop_handles_cancel(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator._verify_and_restore_min_soc = AsyncMock()
    captured = {}

    def _capture(coro, name=None):
        captured["coro"] = coro
        return MagicMock()

    mock_hass.async_create_background_task = MagicMock(side_effect=_capture)

    coordinator._start_periodic_verification()

    with patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError())):
        await captured["coro"]


def test_stop_periodic_verification_cancels_task(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    task = MagicMock()
    task.done.return_value = False
    coordinator._verification_task = task

    coordinator._stop_periodic_verification()

    task.cancel.assert_called_once()


def test_start_periodic_verification_fallback_task(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.is_active = True
    coordinator.is_enabled = True
    if hasattr(mock_hass, "async_create_background_task"):
        delattr(mock_hass, "async_create_background_task")
    mock_hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())

    coordinator._start_periodic_verification()

    mock_hass.async_create_task.assert_called_once()
