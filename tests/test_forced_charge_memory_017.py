"""The next night after a forced charge (plan 017).

3.10.x keeps the grid switch off for the rest of a window once switching it on
lifted the Kostal's charge limit. The next window tried again: on 24/25 October
2026 KORE read 21.4 kW back a second after ManualCharge went on, and for the
seconds until the switch was off again the inverter could draw at that.

A window that then reached its target under the raised min SOC proves the
floor does the grid charging on this inverter. Later windows leave the switch
off from the start - for a while, and only for the same switch and limit.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from unittest.mock import AsyncMock

from tests.test_forced_charge_night import (  # noqa: F401
    CONFIG, WINDOW_START, _calls, _fresh, night,
)
from tests.test_grid_limit import BATTERY, GRID, SUN, _make_coordinator

NEXT_NIGHT = WINDOW_START + timedelta(days=1)


async def _run_night(hass: Any, coordinator: Any, clock: Any, start: Any, *, charge: bool,
                     switch_stuck_on: bool = False, on_while: Any = None,
                     switch_stale: bool = False, sun_up_while: Any = None,
                     sun_up_at_end: bool = False) -> None:
    clock.now = start
    hass.states.get(BATTERY).state = "33"
    await coordinator._on_window_start(start)
    coordinator.initial_calculated_soc = 43.1
    soc = 33.0
    while clock.now < start + timedelta(hours=5):
        _fresh(hass, clock)
        if switch_stale:
            _hold_stale_off(hass, start)
        await coordinator._async_update_data()
        if switch_stale:
            _hold_stale_off(hass, start)
        if switch_stuck_on and coordinator._grid_switch_suspended:
            hass.states.get(GRID).state = "on"  # the turn_off never took
        if on_while is not None and coordinator._grid_switch_suspended:
            hass.states.get(GRID).state = "on" if on_while(soc) else "off"
        if charge:
            soc = min(44.0, soc + .75)  # the floor charges, about 1 kW
            hass.states.get(BATTERY).state = str(soc)
        if sun_up_while is not None:
            hass.states.get(SUN).state = "above_horizon" if sun_up_while(soc) else "below_horizon"
        clock.now += timedelta(minutes=15)
    if sun_up_at_end:
        hass.states.get(SUN).state = "above_horizon"
    await coordinator._on_window_end(clock.now)
    hass.states.get(SUN).state = "below_horizon"


def _hold_stale_off(hass: Any, start: Any) -> None:
    """KORE has not published the switch since before the window."""
    switch = hass.states.get(GRID)
    switch.state = "off"
    switch.last_reported = switch.last_updated = start - timedelta(minutes=1)


def _turn_ons_since(hass: Any, count: int) -> int:
    return sum(1 for call in _calls(hass)[count:] if call == ("switch", "turn_on", GRID))


@pytest.mark.asyncio
async def test_a_night_charged_by_the_floor_keeps_the_switch_off_the_next_night(night):  # noqa: F811
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True)
    assert _turn_ons_since(hass, 0) == 1
    assert coordinator._grid_switch_forced_charge is not None

    before = len(_calls(hass))
    await _run_night(hass, coordinator, clock, NEXT_NIGHT, charge=True)

    assert _turn_ons_since(hass, before) == 0
    assert coordinator.target_reached or float(hass.states.get(BATTERY).state) >= 43.1


@pytest.mark.asyncio
async def test_a_night_that_did_not_charge_proves_nothing(night):  # noqa: F811
    """Without the floor charging, the switch is the only way - it is tried again."""
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=False)
    assert coordinator._grid_switch_forced_charge is None

    before = len(_calls(hass))
    await _run_night(hass, coordinator, clock, NEXT_NIGHT, charge=False)

    assert _turn_ons_since(hass, before) == 1


@pytest.mark.asyncio
async def test_a_switch_that_never_read_off_proves_nothing(night):  # noqa: F811
    """Codex on #45: then the forced charge itself may have reached the target."""
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True, switch_stuck_on=True)

    assert coordinator._grid_switch_forced_charge is None


@pytest.mark.asyncio
async def test_a_switch_that_reads_on_again_at_the_end_proves_nothing(night):  # noqa: F811
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True,
                     on_while=lambda soc: soc >= 43.5)

    assert coordinator._grid_switch_forced_charge is None


@pytest.mark.asyncio
async def test_a_switch_that_read_off_only_at_the_target_proves_nothing(night):  # noqa: F811
    """The charge up to the target happened with the switch on."""
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True,
                     on_while=lambda soc: soc < 43.1)

    assert coordinator._grid_switch_forced_charge is None


@pytest.mark.asyncio
async def test_a_switch_on_again_after_the_baseline_takes_it_along(night):  # noqa: F811
    """CodeRabbit on #45: the charge up to the target happened with it on."""
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True,
                     on_while=lambda soc: 40 <= soc < 43.1)

    assert float(hass.states.get(BATTERY).state) >= 43.1
    assert coordinator._grid_switch_forced_charge is None


@pytest.mark.asyncio
async def test_a_charge_the_sun_may_have_done_proves_nothing(night):  # noqa: F811
    """Codex on #45: the panels raise the SOC with the switch off too.

    An evening window: the sun is up for part of the charge and down by its end.
    """
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True,
                     sun_up_while=lambda soc: soc < 38)

    assert float(hass.states.get(BATTERY).state) >= 43.1
    assert coordinator._grid_switch_forced_charge is None


@pytest.mark.asyncio
async def test_a_sun_up_at_the_window_end_proves_nothing(night):  # noqa: F811
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True, sun_up_at_end=True)

    assert coordinator._grid_switch_forced_charge is None


@pytest.mark.asyncio
async def test_an_off_from_before_the_pause_proves_nothing(night):  # noqa: F811
    """CodeRabbit on #45: KORE may still hold the "off" from before the turn_on."""
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True, switch_stale=True)

    assert float(hass.states.get(BATTERY).state) >= 43.1
    assert coordinator._grid_switch_forced_charge is None


@pytest.mark.asyncio
async def test_the_switch_is_tried_again_after_a_month(night):  # noqa: F811
    """A firmware update may have changed it."""
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True)

    before = len(_calls(hass))
    await _run_night(hass, coordinator, clock, WINDOW_START + timedelta(days=31), charge=True)

    assert _turn_ons_since(hass, before) == 1


@pytest.mark.asyncio
async def test_another_switch_is_tried(night):  # noqa: F811
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True)
    coordinator.config = {**coordinator.config, "charge_power_entity": "number.other_limit"}

    assert coordinator._grid_switch_known_forced() is False


@pytest.mark.asyncio
async def test_the_finding_survives_a_restart(night):  # noqa: F811
    hass, coordinator, clock = night
    await _run_night(hass, coordinator, clock, WINDOW_START, charge=True)
    persisted = hass.config_entries.async_update_entry.call_args.kwargs["options"]

    assert persisted["runtime_state"]["grid_switch_forced_charge"][0] == (
        coordinator._grid_switch_signature()
    )

    # Home Assistant restarts during the day; the next night leaves the switch off
    again = _make_coordinator(hass, CONFIG, options={"runtime_state": dict(persisted["runtime_state"])})
    again._start_periodic_verification = AsyncMock()
    again._stop_periodic_verification = AsyncMock()
    again._restore_state()
    before = len(_calls(hass))
    await _run_night(hass, again, clock, NEXT_NIGHT, charge=True)

    assert _turn_ons_since(hass, before) == 0


# --- The floor's rounding once the target is met ----------------------------


async def _verify_with(hass: Any, coordinator: Any, clock: Any, *, battery: str, inverter: str,
                       reached: bool) -> list[Any]:
    from tests.test_grid_limit import MIN_SOC

    clock.now = WINDOW_START
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 36.1
    coordinator.target_reached = reached
    hass.states.async_set(MIN_SOC, inverter, {"min": 0, "max": 100, "step": 1,
                                              "unit_of_measurement": "%"})
    hass.states.get(BATTERY).state = battery
    _fresh(hass, clock)
    before = len(hass.services.async_call.await_args_list)
    await coordinator._verify_and_restore_min_soc_locked()
    return [c.args for c in hass.services.async_call.await_args_list[before:]
            if c.args[:2] == ("number", "set_value") and c.args[2]["entity_id"] == MIN_SOC]


@pytest.mark.asyncio
async def test_the_rounded_up_floor_is_not_written_back_once_reached(night):  # noqa: F811
    """04:16 on 24/25 October: 37 % written back to 36 % after the target was met."""
    hass, coordinator, clock = night

    writes = await _verify_with(hass, coordinator, clock, battery="37", inverter="37", reached=True)

    assert writes == []


@pytest.mark.asyncio
async def test_a_floor_above_the_rounded_step_is_still_corrected(night):  # noqa: F811
    hass, coordinator, clock = night

    writes = await _verify_with(hass, coordinator, clock, battery="40", inverter="39", reached=True)

    assert [w[2]["value"] for w in writes] == [36.0]


@pytest.mark.asyncio
async def test_while_charging_the_floor_is_still_rounded_up(night):  # noqa: F811
    hass, coordinator, clock = night

    writes = await _verify_with(hass, coordinator, clock, battery="35", inverter="36", reached=False)

    assert [w[2]["value"] for w in writes] == [37.0]
