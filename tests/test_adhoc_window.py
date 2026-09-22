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


@pytest.mark.asyncio
async def test_extending_a_window_actually_moves_the_target(mock_hass):
    """Stage 2 of the evening rescue raises the target - and it used to be dropped.

    Stage 1 holds the battery: an ad-hoc window at the current level with no
    grid charging. One poll later ``_control_charge`` has captured the
    inverter's own min SOC into ``original_min_soc``. Stage 2 then reopens the
    window with the level the evening actually needs.

    ``_calculate_initial_soc`` decides between three cases in order, and the
    first one is "restarted during an active window, keep the persisted
    target". After stage 1 that branch matches - min SOC captured, target
    set - so it returned before ever reaching the ad-hoc branch, and stage 2's
    target was discarded. The grid buying that the whole feature exists for
    never happened.

    The same defeats a second ``charge_to``, and a ``charge_to`` after a
    ``block_discharge``.
    """
    coordinator = _make_coordinator(mock_hass)

    # Stage 1: hold what is there.
    assert await _open(coordinator, target=40.0, grid=False) is True
    assert coordinator.calculated_soc == 40.0

    # A poll runs: the inverter's own floor is captured, as _control_charge does.
    coordinator.original_min_soc = 8.0
    coordinator.initial_calculated_soc = 40.0

    # Stage 2: the evening needs 62 %, and buying is now allowed.
    assert await _open(coordinator, target=62.0, grid=True) is True

    assert coordinator.calculated_soc == 62.0, (
        "stage 2 raised the target and the window must follow it"
    )
    assert coordinator.minimum_calculated_soc == 62.0
    assert coordinator._adhoc_allow_grid_charge is True


@pytest.mark.asyncio
async def test_a_restart_during_an_adhoc_window_does_not_leave_a_dead_deadline(mock_hass):
    """``is_active`` is not persisted, so a restart forgets the window was running.

    The deadline is restored, the window is not. ``_on_window_end`` then
    early-returns on ``not is_active`` and never clears ``_adhoc_until``, so a
    timestamp in the past is left behind for good. From then on
    ``_window_end_datetime`` answers every planner question with it - and the
    next real window is ended one poll after it starts, because the expiry
    check fires on ``in_window``.

    A rescue that a restart interrupts must not cost the night charge.
    """
    coordinator = _make_coordinator(mock_hass)
    assert await _open(coordinator, target=62.0, until=ZONE_START) is True

    # Restart: a fresh coordinator restores the deadline but not is_active.
    restarted = _make_coordinator(mock_hass, options=coordinator.entry.options)
    restarted._adhoc_until = ZONE_START
    restarted._adhoc_reason = "evening_rescue"
    restarted.is_active = False

    # The deadline passes while nothing resumed the window.
    after = ZONE_START + timedelta(minutes=30)
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=after
    ):
        await restarted._on_window_end(after)

    assert restarted._adhoc_until is None, (
        "an expired ad-hoc deadline must be cleared even when the window was "
        "not resumed, or it poisons every later window"
    )
    assert restarted._window_end_datetime(after) > after, (
        "the window end must never be a timestamp in the past"
    )


@pytest.mark.asyncio
async def test_a_restart_during_a_rescue_picks_the_window_back_up(mock_hass):
    """The other half of the restart fix, and the half that keeps the evening.

    ``is_active`` is not persisted, so after a restart the deadline comes back
    and the window does not. Clearing a deadline that has passed stops it from
    poisoning later windows - but a rescue interrupted *before* its deadline has
    to carry on, or the battery it was holding is released an hour before the
    expensive period it was holding it for.

    Worse than merely not resuming: with the window not active, the "settings
    from an earlier window are still on the inverter" branch fires on the next
    poll and resets the inverter - undoing the hold the persisted deadline
    existed to preserve.

    This goes through a real restore from the persisted options rather than
    setting the fields by hand, because the restore is where it went wrong.
    """
    coordinator = _make_coordinator(mock_hass)
    assert await _open(coordinator, target=62.0, until=ZONE_START, grid=False) is True
    persisted = dict(coordinator.entry.options)

    # Home Assistant restarts at 15:00, three hours before the period begins.
    at_restart = AFTERNOON + timedelta(hours=1)
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=at_restart
    ):
        restarted = _make_coordinator(mock_hass, options=persisted)
        assert restarted._adhoc_until == ZONE_START, "the deadline must survive the restart"
        assert restarted.is_active is False, "and the window, by design, does not"
        restarted._reset_settings = AsyncMock(return_value=True)

        await restarted._check_current_window()

    assert restarted.is_active is True, "the interrupted rescue must carry on"
    assert restarted.calculated_soc == 62.0, "with the target it was opened with"
    assert restarted._adhoc_until == ZONE_START
    restarted._reset_settings.assert_not_awaited()


async def _poll(coordinator, now):
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=now
    ):
        return await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_stage_two_actually_buys_after_stage_one_held(mock_hass):
    """Whole-repo review, finding 1: moving the target is not the same as charging.

    Stage 1 holds at the level it finds, so its first poll sees the target as
    reached and sets target_reached. Stage 2 then raises the target. The 3.7.1
    fix made the target move - and its test checked exactly that, and nothing
    after it. But target_reached stayed True, the poll only ever sets it and
    never clears it, and control is skipped while it is True: the grid switch
    was never turned on. _replan_in_window clears it for the same reason; the
    extend path forgot to.
    """
    coordinator = _make_coordinator(mock_hass)
    # Stage 1: hold at the current 40 %.
    assert await _open(coordinator, target=40.0, grid=False) is True
    await _poll(coordinator, AFTERNOON + timedelta(minutes=5))
    assert coordinator.target_reached is True, "holding at the level it finds is reached"

    # Stage 2: the evening needs 62 %, buying allowed.
    late = AFTERNOON + timedelta(hours=2)
    assert await _open(coordinator, target=62.0, grid=True, now=late) is True
    mock_hass.services.async_call.reset_mock()
    await _poll(coordinator, late + timedelta(minutes=1))

    assert coordinator.target_reached is False, "40 % is not 62 %"
    assert any(
        c.args[:2] == ("switch", "turn_on") and c.args[2].get("entity_id") == GRID
        for c in mock_hass.services.async_call.await_args_list
    ), "stage 2 exists to buy - the grid switch has to go on"


@pytest.mark.parametrize(
    "block_mode",
    ["auto", "off"],
    ids=["block_over_min_soc", "block_switched_off"],
)
@pytest.mark.asyncio
async def test_holding_writes_the_floor_straight_away(mock_hass, block_mode):
    """Whole-repo review, finding 10: a hold has to hold from the first poll.

    Holding means raising the inverter's min SOC to the level the battery is
    at, so the house stops drawing it down. The claim was that this floor only
    reached the inverter after a full update interval - up to an hour of the
    battery the rescue exists to protect being emptied into the house.
    """
    from custom_components.inverter_charge_night.const import CONF_DISCHARGE_BLOCK_MODE

    # With the block switched off a configured window writes nothing at its
    # start, deliberately - but an ad-hoc hold has no other job than holding,
    # and the min SOC is then the only lever it has.
    coordinator = _make_coordinator(mock_hass, {CONF_DISCHARGE_BLOCK_MODE: block_mode})
    mock_hass.states.async_set(BATTERY, "55", {"unit_of_measurement": "%"})
    mock_hass.services.async_call.reset_mock()

    assert await _open(coordinator, target=55.0, grid=False) is True
    await _poll(coordinator, AFTERNOON + timedelta(minutes=1))

    floor_writes = [
        c.args[2]["value"]
        for c in mock_hass.services.async_call.await_args_list
        if c.args[:2] == ("number", "set_value") and c.args[2].get("entity_id") == MIN_SOC
    ]
    assert floor_writes, "nothing was written to the inverter's min SOC at all"
    assert floor_writes[-1] == pytest.approx(55.0), "the floor has to sit at the held level"
