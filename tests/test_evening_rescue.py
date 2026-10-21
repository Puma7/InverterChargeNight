"""The evening rescue (plan 013, part two).

The day after a forecast that was too good: snow on the panels with nobody
having set the snow nights. By mid-afternoon it is decidable that the battery
will not carry the high-price period, and there is still time to do something
about it.

Two stages, separated by what they cost. Holding the battery back costs
nothing the same evening does not pay back, so it runs by itself. Buying
spends money against a forecast that might still turn, so it waits for the
switch.
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
    CONF_HIGH_PRICE_END,
    CONF_HIGH_PRICE_START,
    CONF_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)
from custom_components.inverter_charge_night.planner import EveningOutlook

COORDINATOR = "custom_components.inverter_charge_night.coordinator"
CALL_LATER = f"{COORDINATOR}.async_call_later"

ZONE_START = datetime(2026, 6, 1, 18, 0)
ZONE_END = datetime(2026, 6, 1, 21, 0)
EARLY = datetime(2026, 6, 1, 14, 0)   # more than two hours before the period
LATE = datetime(2026, 6, 1, 16, 30)   # inside the last two hours

CONFIG = {
    CONF_MIN_SOC_ENTITY: "number.min_soc",
    CONF_GRID_CHARGE_SWITCH: "switch.grid",
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_PV_FORECAST_ENTITY: "sensor.pv",
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_START_TIME: "23:00",
    CONF_END_TIME: "05:00",
    CONF_HIGH_PRICE_START: "18:00",
    CONF_HIGH_PRICE_END: "21:00",
}


def _make_coordinator(mock_hass, extra=None, *, rescue=True):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = CONFIG | (extra or {})
    entry.options = {}

    def _update(entry_, options=None, **kwargs):
        if options is not None:
            entry_.options = options
        return True

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    mock_hass.states.async_set("number.min_soc", "8", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.states.async_set("sensor.soc", "30", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("sensor.pv", "2", {"unit_of_measurement": "kWh"})
    coordinator = InverterChargeNightCoordinator(mock_hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    coordinator._start_periodic_verification = AsyncMock()
    coordinator._stop_periodic_verification = AsyncMock()
    coordinator._house_load_profile = AsyncMock(return_value=[0.5] * 24)
    # Opt-in since 21 October 2026; the tests here are about what it does when on
    coordinator.evening_rescue_charge = rescue
    return coordinator


def _short(missing=2.0, required=62.0, projected=38.0):
    """An outlook that says the battery will not make it."""
    return EveningOutlook(
        zone_start=ZONE_START,
        zone_end=ZONE_END,
        required_soc=required,
        projected_soc=projected,
        missing_kwh=missing,
        pv_to_come_kwh=0.3,
        load_to_come_kwh=2.0,
        forecast_available=True,
    )


async def _rescue(coordinator, outlook, now=EARLY):
    coordinator.last_evening_outlook = outlook
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=now
    ):
        await coordinator._maybe_rescue_the_evening()


# Stage one: hold ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_shortfall_holds_what_is_in_the_battery(mock_hass):
    coordinator = _make_coordinator(mock_hass)

    await _rescue(coordinator, _short())

    assert coordinator._rescue_stage == 1
    assert coordinator.is_active is True
    assert coordinator._adhoc_reason == "evening_rescue"
    assert coordinator._adhoc_until == ZONE_START
    # Holding means the level that is there, not the one that is wanted
    assert coordinator.initial_calculated_soc == 30.0
    assert coordinator._adhoc_allow_grid_charge is False


@pytest.mark.asyncio
async def test_holding_does_not_buy_from_the_grid(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    mock_hass.services.async_call.reset_mock()

    with patch(f"{COORDINATOR}.dt_util.now", return_value=EARLY):
        await coordinator._control_charge(30.0)

    assert not any(
        call.args[:2] == ("switch", "turn_on")
        for call in mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_an_outlook_that_is_fine_changes_nothing(mock_hass):
    coordinator = _make_coordinator(mock_hass)

    await _rescue(coordinator, _short(missing=0.0))

    assert coordinator._rescue_stage == 0
    assert coordinator.is_active is False


@pytest.mark.asyncio
async def test_no_outlook_changes_nothing(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, None)
    assert coordinator.is_active is False


# Stage two: buy -----------------------------------------------------------------


@pytest.mark.parametrize("now", [EARLY, LATE], ids=["early", "late"])
@pytest.mark.asyncio
async def test_without_the_switch_it_does_nothing_at_all(mock_hass, now):
    """Pascal's decision of 21 October 2026: only on request, the hold included.

    Held at 35 % from the morning on, the min SOC went back up every time it
    was set by hand - an integration for the night changing the inverter in
    the middle of the day.
    """
    coordinator = _make_coordinator(mock_hass, rescue=False)

    await _rescue(coordinator, _short(), now=now)

    assert coordinator._rescue_stage == 0
    assert coordinator.is_active is False
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_a_rescue_window_from_before_the_update_ends_when_it_is_off(mock_hass):
    """The window of the morning of 21 October was persisted: it must not hold on."""
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    assert coordinator.is_active is True
    coordinator.evening_rescue_charge = False  # what an updated install starts with
    mock_hass.services.async_call.reset_mock()

    await _rescue(coordinator, _short())

    assert coordinator.is_active is False
    assert coordinator._adhoc_until is None
    assert coordinator.original_min_soc is None  # the inverter's own floor is back


@pytest.mark.asyncio
async def test_with_the_switch_and_close_enough_it_buys(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator.evening_rescue_charge = True

    await _rescue(coordinator, _short(), now=LATE)

    assert coordinator._rescue_stage == 2
    assert coordinator._adhoc_allow_grid_charge is True
    assert coordinator.initial_calculated_soc == 62.0


@pytest.mark.asyncio
async def test_too_early_it_holds_and_buys_later(mock_hass):
    """Bought at noon is bought for a sun that might still have come."""
    coordinator = _make_coordinator(mock_hass)
    coordinator.evening_rescue_charge = True

    await _rescue(coordinator, _short(), now=EARLY)
    assert coordinator._rescue_stage == 1
    assert coordinator._adhoc_allow_grid_charge is False

    await _rescue(coordinator, _short(), now=LATE)
    assert coordinator._rescue_stage == 2
    assert coordinator._adhoc_allow_grid_charge is True
    assert coordinator.initial_calculated_soc == 62.0


@pytest.mark.asyncio
async def test_the_stage_never_falls_back_within_a_day(mock_hass):
    """A poll that sees the shortfall close does not hand the battery back."""
    coordinator = _make_coordinator(mock_hass)
    coordinator.evening_rescue_charge = True
    await _rescue(coordinator, _short(), now=LATE)
    assert coordinator._rescue_stage == 2

    await _rescue(coordinator, _short(missing=0.1), now=LATE + timedelta(minutes=15))

    assert coordinator._rescue_stage == 2
    assert coordinator._adhoc_allow_grid_charge is True


# The interlocks -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_mode_rescues_nothing(mock_hass):
    """The house is running on the battery; nothing is ours to write."""
    mock_hass.states.async_set("binary_sensor.backup", "on")
    coordinator = _make_coordinator(mock_hass, {CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup"})

    await _rescue(coordinator, _short())

    assert coordinator._rescue_stage == 0
    assert coordinator.is_active is False


@pytest.mark.asyncio
async def test_a_switched_off_integration_rescues_nothing(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_enabled = False

    await _rescue(coordinator, _short())

    assert coordinator._rescue_stage == 0


@pytest.mark.asyncio
async def test_an_unreadable_battery_rescues_nothing(mock_hass):
    """Holding at a level nobody can read is not holding, it is guessing."""
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set("sensor.soc", "unavailable")

    await _rescue(coordinator, _short())

    assert coordinator._rescue_stage == 0


@pytest.mark.asyncio
async def test_the_rescue_ends_when_the_period_starts(mock_hass):
    """And the inverter goes back to what it was before."""
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    coordinator.original_min_soc = 8.0
    coordinator._reset_absolute_charge_power = AsyncMock()
    mock_hass.services.async_call.reset_mock()

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=ZONE_START
    ):
        await coordinator._check_current_window()

    assert coordinator.is_active is False
    assert coordinator._rescue_stage == 0
    assert any(
        call.args[:2] == ("number", "set_value")
        for call in mock_hass.services.async_call.await_args_list
    )


# The switch ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_switch_survives_a_restart(mock_hass):
    from custom_components.inverter_charge_night.switch import EveningRescueChargeSwitch

    coordinator = _make_coordinator(mock_hass, rescue=False)
    switch = EveningRescueChargeSwitch(coordinator, coordinator.entry)
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    assert coordinator.evening_rescue_charge is True

    with patch(CALL_LATER, return_value=MagicMock()):
        restarted = InverterChargeNightCoordinator(mock_hass, coordinator.entry)
    assert restarted.evening_rescue_charge is True


@pytest.mark.asyncio
async def test_switching_it_off_ends_a_running_rescue(mock_hass):
    """Off means off: the user takes the inverter back at once."""
    from custom_components.inverter_charge_night.switch import EveningRescueChargeSwitch

    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short(), now=LATE)
    assert coordinator._adhoc_allow_grid_charge is True

    switch = EveningRescueChargeSwitch(coordinator, coordinator.entry)
    switch.async_write_ha_state = MagicMock()
    with patch(f"{COORDINATOR}.dt_util.now", return_value=LATE):
        await switch.async_turn_off()

    assert coordinator.evening_rescue_charge is False
    assert coordinator.is_active is False
    assert coordinator._adhoc_until is None


@pytest.mark.asyncio
async def test_holding_does_not_lock_out_the_buying_that_follows_it(mock_hass):
    """Through the real update path, not by calling the rescue directly.

    Stage one opens a window, which makes the coordinator active - and an
    active window used to switch the outlook off entirely. The outlook would
    then be None on every later poll and stage two could never fire. The tests
    above call _maybe_rescue_the_evening() with an outlook in hand and would
    never have noticed.
    """
    mock_hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "next_rising": "2026-06-02T05:00:00+00:00",
            "next_setting": "2026-06-01T21:00:00+00:00",
        },
    )
    coordinator = _make_coordinator(mock_hass, {CONF_PV_FORECAST_TODAY_ENTITY: "sensor.pv_today"})
    # After the helper, which sets its own defaults: a nearly empty battery and
    # a day that will not fill it, so the shortfall is still there at 16:30.
    mock_hass.states.async_set("sensor.soc", "12", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("sensor.pv_today", "0.5", {"unit_of_measurement": "kWh"})
    coordinator.evening_rescue_charge = True

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=EARLY
    ):
        await coordinator._async_update_data()

    assert coordinator._rescue_stage == 1, "too early to buy, so it holds"
    assert coordinator.last_evening_outlook is not None, "the outlook has to survive its own window"

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=LATE
    ):
        await coordinator._async_update_data()

    assert coordinator._rescue_stage == 2
    assert coordinator._adhoc_allow_grid_charge is True
