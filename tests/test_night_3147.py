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
from tests.test_grid_limit import AC_LIMIT, BASE_CONFIG, BATTERY, MIN_SOC, NOW, _make_coordinator, _register

START = datetime(2026, 1, 15, 0, 0)  # the window of BASE_CONFIG: 00:00-05:59
ISSUE = "battery_not_charging_entry_1"


def _set_values(hass: Any, entity_id: str) -> list[float]:
    return [float(c.args[2]["value"]) for c in hass.services.async_call.await_args_list
            if c.args[1] == "set_value" and c.args[2].get("entity_id") == entity_id]


async def _poll(hass: Any, coordinator: Any, clock: Any, soc: float) -> None:
    hass.states.async_set(BATTERY, str(soc))
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
