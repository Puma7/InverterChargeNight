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

from tests.test_forced_charge_night import WINDOW_START, _calls, _fresh, night  # noqa: F401
from tests.test_grid_limit import BATTERY, GRID

NEXT_NIGHT = WINDOW_START + timedelta(days=1)


async def _run_night(hass: Any, coordinator: Any, clock: Any, start: Any, *, charge: bool) -> None:
    clock.now = start
    hass.states.get(BATTERY).state = "33"
    await coordinator._on_window_start(start)
    coordinator.initial_calculated_soc = 43.1
    soc = 33.0
    while clock.now < start + timedelta(hours=5):
        _fresh(hass, clock)
        await coordinator._async_update_data()
        if charge:
            soc = min(44.0, soc + .75)  # the floor charges, about 1 kW
            hass.states.get(BATTERY).state = str(soc)
        clock.now += timedelta(minutes=15)
    await coordinator._on_window_end(clock.now)


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
