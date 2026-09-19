"""Tests for the periodic verification task.

Plan 005 made ``_stop_periodic_verification`` await the cancelled task
(finding F11) and removed the waiting loop from the window start (finding F6):
the verification applies the target once the min SOC entity is available.
"""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)

MIN_SOC = "number.min_soc"
GRID = "switch.grid"
BATTERY = "sensor.soc"
PV = "sensor.pv"

CONFIG = {
    CONF_KOSTAL_MIN_SOC_ENTITY: MIN_SOC,
    CONF_KOSTAL_GRID_CHARGE_SWITCH: GRID,
    CONF_BATTERY_SOC_ENTITY: BATTERY,
    CONF_PV_FORECAST_ENTITY: PV,
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_START_TIME: "00:00",
    CONF_END_TIME: "05:59",
}
INSIDE_WINDOW = datetime(2026, 1, 15, 2, 0)


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


async def _blocking_task(finished: asyncio.Event) -> "asyncio.Task[None]":
    """A task that blocks until cancelled and records that it has finished."""

    async def _run() -> None:
        try:
            await asyncio.sleep(3600)
        finally:
            finished.set()

    task = asyncio.ensure_future(_run())
    await asyncio.sleep(0)  # let it reach the sleep
    return task


# Task lifecycle ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_periodic_verification_uses_background_task(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.is_active = True
    coordinator.is_enabled = True
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None: coro.close()
    )

    await coordinator._start_periodic_verification()

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

    await coordinator._start_periodic_verification()

    with patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError())):
        await captured["coro"]


@pytest.mark.asyncio
async def test_start_periodic_verification_passes_task_name(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.is_active = True
    coordinator.is_enabled = True
    captured_name = {}

    def _capture(coro, name=None):
        captured_name["name"] = name
        coro.close()
        return MagicMock()

    mock_hass.async_create_background_task = MagicMock(side_effect=_capture)

    await coordinator._start_periodic_verification()

    assert captured_name["name"] == "inverter_charge_night_periodic_verification"


@pytest.mark.asyncio
async def test_stop_periodic_verification_cancels_and_awaits_task(mock_hass):
    """Finding F11: stopping returns only after the task has actually finished."""
    coordinator = _make_coordinator(mock_hass, {})
    finished = asyncio.Event()
    task = await _blocking_task(finished)
    coordinator._verification_task = task

    await coordinator._stop_periodic_verification()

    assert task.cancelled()
    assert finished.is_set()
    assert coordinator._verification_task is None


@pytest.mark.asyncio
async def test_stop_periodic_verification_without_task_is_noop(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})

    await coordinator._stop_periodic_verification()

    assert coordinator._verification_task is None


@pytest.mark.asyncio
async def test_start_periodic_verification_replaces_running_task(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.is_active = True
    coordinator.is_enabled = True
    finished = asyncio.Event()
    old_task = await _blocking_task(finished)
    coordinator._verification_task = old_task
    new_task = MagicMock()

    def _replace(coro, name=None):
        coro.close()
        return new_task

    mock_hass.async_create_background_task = MagicMock(side_effect=_replace)

    await coordinator._start_periodic_verification()

    assert old_task.cancelled()
    assert finished.is_set()
    assert coordinator._verification_task is new_task


# Window start without the inverter (finding F6) -------------------------------


@pytest.mark.asyncio
async def test_verification_applies_target_once_min_soc_entity_appears(mock_hass):
    """The window start no longer waits for the inverter; the verification catches up.

    With the min SOC entity unknown at window start the target is still
    calculated, but neither the floor nor grid charging is written (no floor,
    no charging). When the entity reports its value the verification captures
    it as the original and writes the target.
    """
    mock_hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    mock_hass.states.async_set(GRID, "off")
    mock_hass.states.async_set(BATTERY, "40")
    mock_hass.states.async_set(PV, "5", {"unit_of_measurement": "kWh"})
    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.async_request_refresh = AsyncMock()
    try:
        with patch(
            "custom_components.inverter_charge_night.coordinator.dt_util.now", return_value=INSIDE_WINDOW
        ):
            await coordinator._on_window_start(INSIDE_WINDOW)
            assert coordinator.is_active is True
            assert coordinator.initial_calculated_soc == 45.0
            assert coordinator._verification_task is not None

            data = await coordinator._async_update_data()

        assert data["is_active"] is True
        mock_hass.services.async_call.assert_not_awaited()
        assert coordinator.original_min_soc is None

        # The inverter comes online with its original value
        mock_hass.states.async_set(MIN_SOC, "8")
        await coordinator._verify_and_restore_min_soc()

        mock_hass.services.async_call.assert_awaited_once_with(
            "number", "set_value", {"entity_id": MIN_SOC, "value": 45.0}
        )
        assert coordinator.original_min_soc == 8.0
        assert coordinator._last_soc_set == 45.0
    finally:
        await coordinator._stop_periodic_verification()


@pytest.mark.asyncio
async def test_verify_does_not_write_when_window_ends_during_run(mock_hass):
    """Finding F11: the window end between the read and the write cancels the write."""
    coordinator = _make_coordinator(
        mock_hass, {CONF_KOSTAL_MIN_SOC_ENTITY: MIN_SOC, CONF_BATTERY_SOC_ENTITY: BATTERY}
    )
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.initial_calculated_soc = 45.0
    inverter = MagicMock()
    inverter.state = "8"

    def _read(entity_id):
        # The window end trigger fires while this run reads the inverter
        coordinator.is_active = False
        return inverter if entity_id == MIN_SOC else None

    mock_hass.states.get.side_effect = _read

    await coordinator._verify_and_restore_min_soc()

    mock_hass.services.async_call.assert_not_awaited()
    assert coordinator.original_min_soc is None
    assert coordinator._verifying_min_soc is False
