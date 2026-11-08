"""3.14.7: the night of 7/8 November 2026.

A foggy 7 November gave a tenth of its forecast; the automatic full charge
armed and the log said "Snow mode". At 23:01 the min SOC went to 100 % under
a 9000 W limit, but the Kostal did not charge: the battery sat at 17 % until
the nightly internal error at 03:10. Meanwhile the plan raised the limit
step by step to 12540 W, and the Kostal ran at about 12 kW once it started;
its controller reached 85 °C.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.inverter_charge_night import coordinator as coordinator_module
from homeassistant.helpers import issue_registry as ir
from tests.test_coordinator import _persisting_hass
from tests.test_grid_limit import AC_LIMIT, BASE_CONFIG, BATTERY, MIN_SOC, NOW, _make_coordinator, _register

START = datetime(2026, 1, 15, 0, 0)  # the window of BASE_CONFIG: 00:00-05:59
ISSUE = "battery_not_charging_entry_1"


def _set_values(hass: Any, entity_id: str) -> list[float]:
    return [float(c.args[2]["value"]) for c in hass.services.async_call.await_args_list
            if c.args[1] == "set_value" and c.args[2].get("entity_id") == entity_id]


async def _poll(hass: Any, coordinator: Any, clock: Any, soc: float, *, fresh: bool = True) -> None:
    hass.states.async_set(BATTERY, str(soc))
    state = hass.states.get(BATTERY)
    # KostalKore reports the SOC every few seconds, the same value included
    state.last_reported = state.last_updated = clock.now if fresh else START
    await coordinator._plan_charge_power(coordinator.calculated_soc)


@pytest.fixture
def stuck(mock_hass):
    """A 10 kWh battery at 17 % under a raised floor, the inverter holding what is written."""
    clock = SimpleNamespace(now=START)
    _register(mock_hass)

    async def call(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value":
            mock_hass.states.async_set(data["entity_id"], str(data["value"]))

    with (patch(NOW, side_effect=lambda: clock.now),
          patch.object(coordinator_module, "STALL_NUDGE_SETTLE_S", 0),
          patch(f"{ir.__name__}.async_create_issue") as create,
          patch(f"{ir.__name__}.async_delete_issue") as delete):
        coordinator = _make_coordinator(mock_hass, BASE_CONFIG)
        mock_hass.services.async_call = AsyncMock(side_effect=call)
        mock_hass.created_issue, mock_hass.deleted_issue = create, delete
        yield mock_hass, coordinator, clock


async def _start(hass: Any, coordinator: Any, clock: Any, target: float = 100.0) -> None:
    coordinator.is_active = True
    coordinator._window_started_at = START
    coordinator.calculated_soc = target
    hass.states.async_set(MIN_SOC, str(target))
    coordinator._last_soc_set = target  # the floor the window raised
    await _poll(hass, coordinator, clock, 17.0)


@pytest.mark.asyncio
async def test_a_battery_that_does_not_charge_is_not_driven_harder(stuck):
    """17 % for hours under a 100 % floor: the limit stays where it was."""
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    # 00:00 writes the limit, 00:15 starts measuring from it, 00:45 notices
    for _ in range(3):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True
    hold = coordinator._stall_hold_w
    before = len(_set_values(hass, AC_LIMIT))
    for _ in range(13):  # ... 04:00: the deadline would ask for ever more
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)

    assert coordinator._charge_stalled is True
    assert _set_values(hass, AC_LIMIT)[before:] == []
    assert coordinator.planned_charge_power_w == hold
    first = hold
    hass.created_issue.assert_any_call(
        hass, "inverter_charge_night", ISSUE,
        is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key="battery_not_charging",
        translation_placeholders={"name": coordinator.entry.title, "soc": "17", "floor": "100",
                                  "minutes": "30", "power": f"{first:.0f}"},
    )


@pytest.mark.asyncio
async def test_the_min_soc_is_nudged_once_a_window(stuck):
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    for _ in range(16):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)

    writes = _set_values(hass, MIN_SOC)
    # The nudge only: down to the battery, up to the floor again, once
    assert writes == [17.0, 100.0]


@pytest.mark.asyncio
async def test_charging_again_clears_the_notice_and_the_hold(stuck):
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    for _ in range(3):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True

    delete = hass.deleted_issue
    soc = 17.0
    for _ in range(3):  # 03:10: the Kostal starts at about 12 kW
        clock.now += timedelta(minutes=15)
        soc += 8.0
        await _poll(hass, coordinator, clock, soc)

    assert coordinator._charge_stalled is False
    assert coordinator._stall_hold_w is None
    delete.assert_any_call(hass, "inverter_charge_night", ISSUE)


@pytest.mark.asyncio
async def test_a_charging_battery_is_never_taken_for_stuck(stuck):
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock, target=60.0)
    soc = 17.0
    for _ in range(8):
        clock.now += timedelta(minutes=15)
        soc += 4.0  # about 6 kW into 36 kWh
        await _poll(hass, coordinator, clock, soc)

    assert coordinator._charge_stalled is False
    assert _set_values(hass, MIN_SOC) == []
    # The plan really wrote its limit (Sourcery on #53)
    assert _set_values(hass, AC_LIMIT) and all(value > 0 for value in _set_values(hass, AC_LIMIT))
    assert not [c for c in hass.created_issue.call_args_list if c.args[2].startswith("battery_not_charging")]


@pytest.mark.asyncio
async def test_twenty_minutes_are_not_judged_before_they_are_over(stuck):
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    clock.now += timedelta(minutes=15)
    await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is False


@pytest.mark.asyncio
async def test_the_next_window_starts_without_the_notice(stuck):
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    for _ in range(3):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled and coordinator._stall_nudged

    delete = hass.deleted_issue
    delete.reset_mock()
    coordinator._clear_charge_stall(new_window=True)

    assert not coordinator._charge_stalled and not coordinator._stall_nudged
    delete.assert_called_once_with(hass, "inverter_charge_night", ISSUE)


# --- review of #53 ------------------------------------------------------------

async def _stall(hass: Any, coordinator: Any, clock: Any, polls: int = 3) -> None:
    await _start(hass, coordinator, clock)
    for _ in range(polls):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)


@pytest.mark.asyncio
async def test_a_restart_keeps_the_stall_its_hold_and_its_nudge(stuck):
    """Sourcery: the hold vanished and a second nudge followed a restart."""
    hass, coordinator, clock = stuck
    _persisting_hass(hass)
    await _stall(hass, coordinator, clock)
    assert coordinator._charge_stalled and coordinator._stall_nudged
    hold = coordinator._stall_hold_w

    restarted = _make_coordinator(hass, BASE_CONFIG, options=coordinator.entry.options)
    restarted._restore_state()
    restarted._clear_charge_stall(new_window=False)  # the same window resumes
    assert restarted._charge_stalled is True
    assert restarted._stall_hold_w == hold
    assert restarted._stall_nudged is True
    before = len(_set_values(hass, MIN_SOC))
    restarted.is_active, restarted._window_started_at = True, START
    restarted.calculated_soc, restarted._last_soc_set = 100.0, 100.0
    restarted._planned_setpoint_written_w = hold
    for _ in range(4):
        clock.now += timedelta(minutes=15)
        await _poll(hass, restarted, clock, 17.0)
    assert _set_values(hass, MIN_SOC)[before:] == []  # no second nudge
    assert restarted.planned_charge_power_w == hold


@pytest.mark.asyncio
async def test_reaching_the_target_ends_the_stall(stuck):
    hass, coordinator, clock = stuck
    await _stall(hass, coordinator, clock)
    coordinator.target_reached = True
    await _poll(hass, coordinator, clock, 99.5)
    assert coordinator._charge_stalled is False and coordinator._stall_hold_w is None
    hass.deleted_issue.assert_any_call(hass, "inverter_charge_night", ISSUE)


@pytest.mark.asyncio
async def test_a_stale_soc_is_not_taken_for_a_stuck_battery(stuck):
    """Sourcery: a sensor that stopped reporting keeps its last value."""
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    for _ in range(8):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0, fresh=False)
    assert coordinator._charge_stalled is False
    assert _set_values(hass, MIN_SOC) == []


@pytest.mark.asyncio
async def test_a_hold_only_window_is_never_nudged(stuck):
    """Sourcery: an ad-hoc window without grid charging only holds its floor."""
    hass, coordinator, clock = stuck
    coordinator._adhoc_until = START + timedelta(hours=5)
    coordinator._adhoc_allow_grid_charge = False
    await _stall(hass, coordinator, clock, polls=8)
    assert coordinator._charge_stalled is False
    assert _set_values(hass, MIN_SOC) == []


@pytest.mark.asyncio
async def test_a_battery_that_follows_a_rising_limit_is_not_stuck(stuck):
    """Sourcery: each limit counts only for the time it stood.

    The deadline raises the limit poll by poll; the battery takes each one
    at 0.9 into 10 kWh. Charged at the earlier, lower limit is not stuck.
    """
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    soc, limits = 17.0, []
    for _ in range(15):
        written = coordinator._planned_setpoint_written_w
        limits.append(written)
        clock.now += timedelta(minutes=15)
        soc += written / 1000 * 0.25 * 0.9 / 10 * 100 * 0.6  # a battery taking 60 % of it
        await _poll(hass, coordinator, clock, soc)
        assert coordinator._charge_stalled is False
    assert limits[-1] > limits[0]  # the deadline did raise it


@pytest.mark.asyncio
async def test_a_falling_soc_does_not_restart_the_clock(stuck):
    """Codex: the battery still supplies the house, 0.25 % down every 15 minutes."""
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    soc = 17.0
    for _ in range(4):
        clock.now += timedelta(minutes=15)
        soc -= 0.25
        await _poll(hass, coordinator, clock, soc)
    assert coordinator._charge_stalled is True


@pytest.mark.asyncio
async def test_recovery_is_judged_on_the_latest_interval(stuck):
    """Codex: after hours stuck, 15 minutes of real charging end the hold."""
    hass, coordinator, clock = stuck
    await _stall(hass, coordinator, clock, polls=16)
    assert coordinator._charge_stalled is True
    clock.now += timedelta(minutes=15)
    await _poll(hass, coordinator, clock, 25.0)
    assert coordinator._charge_stalled is False


@pytest.mark.asyncio
async def test_no_limit_is_written_once_the_window_ended_during_the_nudge(stuck):
    """Codex: the nudge waits 12 s; the window may end meanwhile."""
    hass, coordinator, clock = stuck
    await _stall(hass, coordinator, clock, polls=2)

    async def window_ends(current_soc: float, floor: float) -> None:
        # The window end restores the inverter while the nudge sleeps
        coordinator.is_active = False
        coordinator._planned_setpoint_written_w = None
        hass.states.async_set(AC_LIMIT, "6000")

    coordinator._nudge_min_soc = window_ends  # type: ignore[method-assign]
    before = len(_set_values(hass, AC_LIMIT))
    clock.now += timedelta(minutes=15)
    await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True
    assert _set_values(hass, AC_LIMIT)[before:] == []
    assert hass.states.get(AC_LIMIT).state == "6000"


@pytest.mark.asyncio
async def test_each_limit_counts_only_for_the_time_it_stood(stuck):
    """Sourcery: 300 W for 15 minutes, then 3000 W, is not 30 minutes at 3000 W.

    The battery takes 40 % of each limit: weak, but charging. Against 3000 W
    for the whole half hour it would look stuck.
    """
    hass, coordinator, clock = stuck
    coordinator.is_active, coordinator._last_soc_set = True, 100.0

    async def observe(soc: float, written: float) -> None:
        hass.states.async_set(BATTERY, str(soc))
        hass.states.get(BATTERY).last_reported = clock.now
        coordinator._planned_setpoint_written_w = written
        await coordinator._watch_charge_progress(soc, 100.0, clock.now)

    await observe(17.0, 300.0)
    clock.now += timedelta(minutes=15)
    await observe(17.27, 300.0)  # 300 W stood since the last look
    clock.now += timedelta(minutes=15)
    await observe(19.97, 3000.0)  # 3000 W, written after the last look
    assert coordinator._charge_stalled is False


@pytest.mark.asyncio
async def test_the_nudge_writes_a_value_the_entity_takes(stuck):
    """Sourcery: a min SOC in steps of 5 % takes 15, not 17."""
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    hass.states.async_set(MIN_SOC, "100", {"min": 5, "max": 100, "step": 5})
    for _ in range(3):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    assert _set_values(hass, MIN_SOC) == [15.0, 100.0]
