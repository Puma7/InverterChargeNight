"""The ad-hoc window (plan 013).

A window like any other - same capture, restore, retry ladder and interlocks -
whose start comes from a call and whose end comes from an argument. It exists
so that daytime interventions do not need a second capture-and-restore contract
beside the one the window lifecycle already has.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BACKUP_MODE_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_RUNTIME_STATE,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)

COORDINATOR = "custom_components.inverter_charge_night.coordinator"
CALL_LATER = f"{COORDINATOR}.async_call_later"

MIN_SOC = "number.min_soc"
GRID = "switch.grid"
BATTERY = "sensor.soc"
BACKUP = "binary_sensor.backup"

# A night window, so the afternoon is outside it
CONFIG = {
    CONF_MIN_SOC_ENTITY: MIN_SOC,
    CONF_GRID_CHARGE_SWITCH: GRID,
    CONF_BATTERY_SOC_ENTITY: BATTERY,
    CONF_PV_FORECAST_ENTITY: "sensor.pv",
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_START_TIME: "23:00",
    CONF_END_TIME: "05:00",
}
AFTERNOON = datetime(2026, 6, 1, 14, 0)
ZONE_START = datetime(2026, 6, 1, 18, 0)


def _make_coordinator(hass, extra=None, options=None):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = CONFIG | (extra or {})
    entry.options = options or {}

    def _update(entry_, options=None, **kwargs):
        if options is not None:
            entry_.options = options
        return True

    hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    hass.states.async_set(MIN_SOC, "8", {"unit_of_measurement": "%"})
    hass.states.async_set(GRID, "off")
    hass.states.async_set(BATTERY, "40", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.pv", "5", {"unit_of_measurement": "kWh"})
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    coordinator._start_periodic_verification = AsyncMock()
    coordinator._stop_periodic_verification = AsyncMock()
    return coordinator


async def _open(coordinator, *, target=60.0, until=ZONE_START, grid=False, now=AFTERNOON):
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=now
    ):
        return await coordinator.async_open_adhoc_window(
            target_soc=target, until=until, reason="evening_rescue", allow_grid_charge=grid
        )


# Opening ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_one_starts_a_window_with_the_given_target(mock_hass):
    coordinator = _make_coordinator(mock_hass)

    assert await _open(coordinator) is True

    assert coordinator.is_active is True
    assert coordinator.initial_calculated_soc == 60.0
    assert coordinator._adhoc_until == ZONE_START
    assert coordinator._adhoc_reason == "evening_rescue"


@pytest.mark.asyncio
async def test_the_window_ends_when_it_was_told_to_not_when_the_clock_says(mock_hass):
    """Everything that asks how long the window has left has to get this answer."""
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator)

    with patch(f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON):
        assert coordinator._window_end_datetime(AFTERNOON) == ZONE_START


@pytest.mark.asyncio
async def test_the_target_is_clamped_into_the_user_range(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator, target=140.0)
    assert coordinator.initial_calculated_soc == 100.0


@pytest.mark.asyncio
async def test_an_end_in_the_past_is_refused(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    assert await _open(coordinator, until=AFTERNOON - timedelta(hours=1)) is False
    assert coordinator.is_active is False


@pytest.mark.asyncio
async def test_backup_mode_refuses(mock_hass):
    """The house is running on the battery; the inverter is not ours to write to."""
    mock_hass.states.async_set(BACKUP, "on")
    coordinator = _make_coordinator(mock_hass, {CONF_BACKUP_MODE_ENTITY: BACKUP})

    assert await _open(coordinator) is False
    assert coordinator.is_active is False


@pytest.mark.asyncio
async def test_a_switched_off_integration_refuses(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_enabled = False

    assert await _open(coordinator) is False


@pytest.mark.asyncio
async def test_the_configured_window_wins_over_an_ad_hoc_one(mock_hass):
    """That one has the tariff contract behind it."""
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True  # a regular window is running

    assert await _open(coordinator) is False
    assert coordinator._adhoc_until is None


@pytest.mark.asyncio
async def test_opening_it_again_extends_the_running_one(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator, target=60.0)

    assert await _open(coordinator, target=75.0, until=ZONE_START + timedelta(hours=1)) is True
    assert coordinator._adhoc_until == ZONE_START + timedelta(hours=1)
    assert coordinator.initial_calculated_soc == 75.0


# Grid charging ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_permission_it_holds_but_does_not_buy(mock_hass):
    """Stage one of the rescue: keep what is there, buy nothing."""
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator, target=60.0, grid=False)
    mock_hass.services.async_call.reset_mock()

    with patch(f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON):
        await coordinator._control_charge(60.0)

    assert not any(
        call.args[:2] == ("switch", "turn_on")
        for call in mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_with_permission_it_charges_from_the_grid(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator, target=60.0, grid=True)
    mock_hass.services.async_call.reset_mock()

    with patch(f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON):
        await coordinator._control_charge(60.0)

    assert any(
        call.args[:2] == ("switch", "turn_on")
        for call in mock_hass.services.async_call.await_args_list
    )


# Ending -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_it_ends_at_its_own_time_and_resets_the_inverter(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator)
    coordinator.original_min_soc = 8.0
    coordinator._reset_absolute_charge_power = AsyncMock()
    mock_hass.services.async_call.reset_mock()

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=ZONE_START
    ):
        await coordinator._check_current_window()

    assert coordinator.is_active is False
    assert coordinator._adhoc_until is None
    assert any(
        call.args[:2] == ("number", "set_value")
        for call in mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_it_is_not_ended_early_by_the_configured_times(mock_hass):
    """It runs outside them by definition - that check would kill it at once."""
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator)

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON + timedelta(minutes=30)
    ):
        await coordinator._check_current_window()

    assert coordinator.is_active is True
    assert coordinator._adhoc_until == ZONE_START


@pytest.mark.asyncio
async def test_the_configured_window_starting_ends_it(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator, until=datetime(2026, 6, 2, 2, 0))
    coordinator._reset_absolute_charge_power = AsyncMock()

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=datetime(2026, 6, 1, 23, 30)
    ):
        await coordinator._check_current_window()

    # The ad-hoc window is gone and the configured one has taken over
    assert coordinator._adhoc_until is None
    assert coordinator.is_active is True


@pytest.mark.asyncio
async def test_the_polling_update_ends_it_when_its_time_has_passed(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator)
    coordinator._reset_absolute_charge_power = AsyncMock()

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=ZONE_START + timedelta(minutes=5)
    ):
        data = await coordinator._async_update_data()

    assert data["is_active"] is False
    assert coordinator._adhoc_until is None


# The one that matters most ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_restart_in_the_middle_still_ends_it(mock_hass):
    """A battery whose release a restart swallowed stays blocked.

    That is the worst failure this design has, so the deadline is persisted
    and a new coordinator picks it up.
    """
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator)
    stored = coordinator.entry.options[CONF_RUNTIME_STATE]
    assert stored["adhoc_until"] == ZONE_START.isoformat()
    assert stored["adhoc_reason"] == "evening_rescue"

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON + timedelta(minutes=10)
    ):
        restarted = _make_coordinator(mock_hass, options=dict(coordinator.entry.options))

    assert restarted._adhoc_until == ZONE_START
    assert restarted._adhoc_reason == "evening_rescue"
    assert restarted._adhoc_target_soc == 60.0


@pytest.mark.asyncio
async def test_a_deadline_that_passed_during_downtime_is_not_resumed(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator)

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=ZONE_START + timedelta(hours=2)
    ):
        restarted = _make_coordinator(mock_hass, options=dict(coordinator.entry.options))

    assert restarted._adhoc_until is None


@pytest.mark.asyncio
async def test_a_malformed_persisted_deadline_is_ignored(mock_hass, caplog):
    coordinator = _make_coordinator(
        mock_hass, options={CONF_RUNTIME_STATE: {"adhoc_until": "not a timestamp"}}
    )
    assert coordinator._adhoc_until is None
    assert "adhoc_until" in caplog.text


# The actions that open one (plan 013) -------------------------------------------


@pytest.mark.asyncio
async def test_charge_to_opens_a_window_that_may_buy(mock_hass):
    coordinator = _make_coordinator(mock_hass)

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON
    ):
        assert await coordinator.async_charge_to(62.0, timedelta(hours=2)) is True

    assert coordinator.initial_calculated_soc == 62.0
    assert coordinator._adhoc_allow_grid_charge is True
    assert coordinator._adhoc_until == AFTERNOON + timedelta(hours=2)
    assert coordinator._adhoc_reason == "service"


@pytest.mark.asyncio
async def test_block_discharge_holds_at_the_level_it_finds(mock_hass):
    coordinator = _make_coordinator(mock_hass)  # battery at 40 %

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON
    ):
        assert await coordinator.async_block_discharge(timedelta(hours=1)) is True

    assert coordinator.initial_calculated_soc == 40.0
    assert coordinator._adhoc_allow_grid_charge is False


@pytest.mark.asyncio
async def test_block_discharge_refuses_an_unreadable_battery(mock_hass):
    """Holding at a level nobody can read is not holding, it is guessing."""
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set(BATTERY, "unavailable")

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON
    ):
        assert await coordinator.async_block_discharge(timedelta(hours=1)) is False

    assert coordinator.is_active is False


@pytest.mark.asyncio
async def test_allow_discharge_ends_it_early_and_resets(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _open(coordinator)
    coordinator.original_min_soc = 8.0
    coordinator._reset_absolute_charge_power = AsyncMock()
    mock_hass.services.async_call.reset_mock()

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON + timedelta(minutes=20)
    ):
        assert await coordinator.async_allow_discharge() is True

    assert coordinator.is_active is False
    assert coordinator._adhoc_until is None
    assert any(
        call.args[:2] == ("number", "set_value")
        for call in mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_allow_discharge_leaves_a_configured_window_alone(mock_hass):
    """That is the schedule doing its job; reset_inverter is what ends it."""
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_active = True  # a configured window, no ad-hoc state

    with patch(f"{COORDINATOR}.dt_util.now", return_value=AFTERNOON):
        assert await coordinator.async_allow_discharge() is False

    assert coordinator.is_active is True
