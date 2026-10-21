"""The night of 20/21 October 2026: grid charging switched on and off every 15 minutes.

From the field log, every poll from 23:31 to 02:47 did the same:

* ``Battery:ManualCharge`` on - the Kostal treats it as a forced charge and
  its charge limit read 22.3 kW a second later instead of the 6.7 kW written;
* KORE raised "Readback is unconfirmed" for that write, and also for every
  1000 W write the Kostal answered with 997.9 to 999.9 W;
* each failure paused grid charging, and the next poll switched it on again.

Each switch-on drew up to 12 kW from the grid for a few seconds. The raised
min SOC alone charged the battery to its target under the 1000 W limit.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir

from tests.test_coordinator import _persisting_hass

from tests.test_grid_limit import (
    AC_LIMIT, BATTERY, GRID, GRID_IMPORT, LIMITED_CONFIG, MIN_SOC, NOW,
    _make_coordinator, _register,
)

BERLIN = ZoneInfo("Europe/Berlin")
WINDOW_START = datetime(2026, 10, 20, 23, 1, tzinfo=BERLIN)
CONFIG = {**LIMITED_CONFIG, "start_time": "23:01", "end_time": "04:58", "battery_capacity": 35.8}
FORCED_W = 22296.759765625  # what the limit read a second after ManualCharge went on


def _kostal(hass: Any, clock: SimpleNamespace) -> AsyncMock:
    """KORE in front of the Kostal, as the log shows it."""

    async def call(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        clock.now += timedelta(seconds=1)
        entity_id = data.get("entity_id")
        state = hass.states.get(entity_id)
        if state is None:
            return
        if domain == "switch":
            state.state = "on" if service == "turn_on" else "off"
            if service == "turn_on":
                # A forced charge: the Kostal lifts its charge limit at once
                hass.states.get(AC_LIMIT).state = str(FORCED_W)
        elif service == "set_value" and entity_id == AC_LIMIT:
            value = float(data["value"])
            if hass.states.get(GRID).state != "on":
                # Held, as the float the Kostal reports for it
                state.state = str(value - 1.5 if value else 0.0)
            state.last_reported = state.last_updated = clock.now
            if value:  # KORE compares exactly; 0 reads back as 0
                raise HomeAssistantError(f"Readback is unconfirmed for {entity_id}")
        elif service == "set_value":
            state.state = str(data["value"])
        state.last_reported = state.last_updated = clock.now

    return AsyncMock(side_effect=call)


@pytest.fixture
def night(mock_hass):
    clock = SimpleNamespace(now=WINDOW_START)
    with patch(NOW, side_effect=lambda: clock.now):
        _register(mock_hass, import_w="700")
        mock_hass.states.async_set(BATTERY, "33")
        mock_hass.states.async_set(
            AC_LIMIT, "22027.836", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"}
        )
        coordinator = _make_coordinator(mock_hass, CONFIG)
        coordinator._start_periodic_verification = AsyncMock()
        coordinator._stop_periodic_verification = AsyncMock()
        mock_hass.services.async_call = _kostal(mock_hass, clock)
        _persisting_hass(mock_hass)
        _fresh(mock_hass, clock)
        with patch(f"{ir.__name__}.async_create_issue") as issue:
            mock_hass.created_issue = issue
            yield mock_hass, coordinator, clock


def _calls(hass: Any) -> list[tuple[str, str, Any]]:
    return [(c.args[0], c.args[1], c.args[2].get("entity_id"))
            for c in hass.services.async_call.await_args_list]


def _fresh(hass: Any, clock: SimpleNamespace) -> None:
    for entity_id in (BATTERY, MIN_SOC, GRID_IMPORT, GRID):
        state = hass.states.get(entity_id)
        state.last_reported = state.last_updated = clock.now
    hass.states.get(GRID_IMPORT).state = "700"


@pytest.mark.asyncio
async def test_grid_charging_is_switched_on_once_a_night_not_every_15_minutes(night):
    hass, coordinator, clock = night
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 43.1  # the night's bridge plan
    soc = 33.0
    first_poll = None
    while clock.now < WINDOW_START + timedelta(hours=3, minutes=50):
        _fresh(hass, clock)
        await coordinator._async_update_data()
        if first_poll is None:
            # Switched on, read back, found lifted and switched off in the same
            # poll - not 15 minutes of forced charge until the next one
            first_poll = _calls(hass)
            assert ("switch", "turn_off", GRID) in first_poll[first_poll.index(("switch", "turn_on", GRID)):]
        await coordinator._verify_ac_charge_limit()
        # The floor charges the battery, about 1 kW
        soc = min(44.0, soc + .75)
        hass.states.get(BATTERY).state = str(soc)
        clock.now += timedelta(minutes=15)

    calls = _calls(hass)
    turn_ons = [i for i, c in enumerate(calls) if c == ("switch", "turn_on", GRID)]
    assert len(turn_ons) == 1, calls
    assert coordinator._grid_switch_suspended is True
    # The limit goes on before the floor above the SOC that makes the Kostal charge
    args = [c.args for c in hass.services.async_call.await_args_list]
    raised = args.index(("number", "set_value", {"entity_id": MIN_SOC, "value": 43.1}))
    assert args[raised - 1][:2] == ("number", "set_value") and args[raised - 1][2]["entity_id"] == AC_LIMIT
    # ...and the switch comes after both
    assert turn_ons[0] > raised


@pytest.mark.asyncio
async def test_a_kostal_float_readback_confirms_the_write(night):
    """1000 W written, 998.5 W read back: KORE raises, the reading is the answer."""
    hass, coordinator, clock = night
    coordinator.is_active = True

    assert await coordinator._set_ac_charge_limit_w(1000) is True

    assert coordinator._planned_setpoint_written_w == 1000.0
    assert ("switch", "turn_off", GRID) not in _calls(hass)
    assert hass.states.get(AC_LIMIT).state == "998.5"


@pytest.mark.asyncio
async def test_an_old_reading_does_not_confirm_a_failed_write(night):
    """No fresh reading after the error: the write stays failed and grid charging pauses."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    hass.states.get(AC_LIMIT).state = "1000"
    hass.states.get(GRID).state = "on"

    async def lost(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if domain == "switch":
            hass.states.get(GRID).state = "off"
            return
        if service == "set_value":
            raise HomeAssistantError("Write failed after 2 retries")

    hass.services.async_call = AsyncMock(side_effect=lost)

    assert await coordinator._set_ac_charge_limit_w(1000) is False

    assert ("homeassistant", "update_entity", AC_LIMIT) in _calls(hass)
    assert ("switch", "turn_off", GRID) in _calls(hass)
    assert coordinator._grid_switch_suspended is True


@pytest.mark.asyncio
async def test_a_refused_write_is_not_confirmed_by_a_reading(night):
    hass, coordinator, clock = night
    coordinator.is_active = True
    hass.states.get(AC_LIMIT).state = "1000"

    async def refused(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value":
            raise ServiceValidationError("outside valid range")

    hass.services.async_call = AsyncMock(side_effect=refused)

    assert await coordinator._set_ac_charge_limit_w(1000) is False
    assert ("homeassistant", "update_entity", AC_LIMIT) not in _calls(hass)


@pytest.mark.asyncio
async def test_the_suspension_lasts_the_window_a_restart_included(night):
    hass, coordinator, clock = night
    await coordinator._on_window_start(WINDOW_START)
    coordinator._grid_switch_suspended = True
    coordinator._persist_state()
    saved = dict(coordinator.entry.options["runtime_state"])
    assert saved["grid_switch_suspended"] is True

    # Home Assistant restarts at 01:00: the same window resumes, suspended
    again = _make_coordinator(hass, CONFIG, options={"runtime_state": saved})
    again._start_periodic_verification = AsyncMock()
    again._restore_state()
    clock.now = WINDOW_START + timedelta(hours=2)
    await again._on_window_start(clock.now)
    assert again._grid_switch_suspended is True
    assert await again._grid_charge_activation_safe(43.1) is False

    # The next night starts with the switch allowed again
    clock.now = WINDOW_START + timedelta(days=1)
    await again._on_window_start(clock.now)
    assert again._grid_switch_suspended is False


@pytest.mark.asyncio
async def test_a_switch_that_is_off_is_not_suspended(night):
    """A failed write with grid charging off says nothing about the switch."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    hass.states.get(GRID).state = "off"
    coordinator._suspend_grid_switch_if_on()
    assert coordinator._grid_switch_suspended is False


@pytest.mark.asyncio
async def test_a_lift_reported_only_later_is_caught_by_the_recheck(night):
    """KORE's debounced refresh may still show the old limit right after the
    switch: the recheck SWITCH_ON_RECHECK_S later sees the Kostal's own value."""
    from custom_components.inverter_charge_night.coordinator import SWITCH_ON_RECHECK_S

    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._planned_setpoint_written_w = 1000.0
    hass.states.get(AC_LIMIT).state = "998.5"
    hass.states.get(GRID).state = "on"
    hass.services.async_call = AsyncMock()  # the refresh brings nothing new

    with patch("custom_components.inverter_charge_night.coordinator.async_call_later") as later:
        await coordinator._confirm_limit_after_switch_on()
        assert coordinator._grid_switch_suspended is False
        delay, recheck = later.call_args.args[1:]
        assert delay == SWITCH_ON_RECHECK_S

        hass.states.get(AC_LIMIT).state = str(FORCED_W)  # the next Modbus poll
        hass.services.async_call = _kostal(hass, clock)
        await recheck(clock.now)

    assert coordinator._grid_switch_suspended is True
    assert _calls(hass)[:2] == [("switch", "turn_off", GRID), ("number", "set_value", AC_LIMIT)]
    assert hass.states.get(GRID).state == "off"
    assert hass.states.get(AC_LIMIT).state == "998.5"  # the plan stands again


@pytest.mark.asyncio
async def test_a_held_limit_needs_no_recheck_action(night):
    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._planned_setpoint_written_w = 1000.0
    hass.states.get(AC_LIMIT).state = "998.5"
    hass.states.get(GRID).state = "on"
    hass.services.async_call = AsyncMock()

    with patch("custom_components.inverter_charge_night.coordinator.async_call_later") as later:
        await coordinator._confirm_limit_after_switch_on()
        await later.call_args.args[2](clock.now)

    assert coordinator._grid_switch_suspended is False
    # The one write after switching on, and nothing else
    assert hass.services.async_call.await_args_list == [
        (("number", "set_value", {"entity_id": AC_LIMIT, "value": 1000.0}), {"blocking": True}),
    ]


@pytest.mark.asyncio
async def test_the_fresh_reading_may_take_a_few_seconds(night):
    """KORE polls the charge limit with its slow registers: the reading that
    confirms a write it called unconfirmed arrives with its next poll."""
    import asyncio

    hass, coordinator, clock = night
    coordinator.is_active = True

    async def kore(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value":
            def next_poll() -> None:
                state = hass.states.get(AC_LIMIT)
                state.state = "998.5"
                state.last_reported = clock.now + timedelta(seconds=5)

            asyncio.get_running_loop().call_later(1.5, next_poll)
            raise HomeAssistantError(f"Readback is unconfirmed for {AC_LIMIT}")

    hass.services.async_call = AsyncMock(side_effect=kore)
    with patch("custom_components.inverter_charge_night.coordinator.LIMIT_FEEDBACK_WAIT_S", 4):
        assert await coordinator._set_ac_charge_limit_w(1000) is True
    assert coordinator._planned_setpoint_written_w == 1000.0


@pytest.mark.asyncio
async def test_a_retained_value_with_a_new_timestamp_confirms_nothing(night):
    """KORE republishes the old slow-register value on every fast poll. After
    switching on, the Kostal read 22.3 kW for a rewrite of 1000 W; the HA state
    still said 998.5 with a new timestamp - that must not count as held."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    hass.states.get(AC_LIMIT).state = "998.5"
    hass.states.get(GRID).state = "on"

    async def kore(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if domain == "switch":
            hass.states.get(GRID).state = "off"
            return
        state = hass.states.get(AC_LIMIT)
        state.last_reported = clock.now + timedelta(seconds=5)  # a fast poll, old value
        if service == "set_value":
            raise HomeAssistantError(f"Readback is unconfirmed for {AC_LIMIT}")

    hass.services.async_call = AsyncMock(side_effect=kore)

    assert await coordinator._set_ac_charge_limit_w(1000) is False
    assert coordinator._grid_switch_suspended is True
    assert hass.states.get(GRID).state == "off"


# --- The Kostal drops a 1038 limit after a few minutes (23:01 -> about 8 kW at 23:12) ---


def _charging(hass: Any, coordinator: Any) -> None:
    coordinator.is_active = True
    coordinator._original_ac_charge_power = 22027.836
    coordinator._planned_setpoint_written_w = 1000.0
    hass.states.get(AC_LIMIT).state = "998.5"
    hass.states.get(GRID).state = "off"
    coordinator._battery_idle_on_grid = MagicMock(return_value=False)


@pytest.mark.asyncio
async def test_the_keepalive_writes_the_limit_in_force_again(night):
    hass, coordinator, clock = night
    _charging(hass, coordinator)

    await coordinator._keep_charge_limit_alive()

    assert _calls(hass) == [("number", "set_value", AC_LIMIT)]
    assert hass.services.async_call.await_args_list[0].args[2]["value"] == 1000.0
    # KORE called it unconfirmed (the float readback): no wait, no pause, no record change
    assert coordinator._grid_switch_suspended is False
    assert coordinator._planned_setpoint_written_w == 1000.0


@pytest.mark.parametrize("why", ["inactive", "not_captured", "not_held", "idle", "discharge"])
@pytest.mark.asyncio
async def test_the_keepalive_writes_nothing_it_does_not_own(night, why):
    hass, coordinator, clock = night
    _charging(hass, coordinator)
    if why == "inactive":
        coordinator.is_active = False
    elif why == "not_captured":
        coordinator._original_ac_charge_power = None
    elif why == "not_held":
        coordinator._ac_limit_not_held = True
    elif why == "idle":
        coordinator._battery_idle_on_grid = MagicMock(return_value=True)
    else:
        coordinator.operation_mode = "morning_discharge"

    await coordinator._keep_charge_limit_alive()

    assert _calls(hass) == []


@pytest.mark.asyncio
async def test_a_kostal_that_drops_the_limit_keeps_it_with_the_keepalive(night):
    """Without the keepalive the limit lapses five minutes after each write."""
    from custom_components.inverter_charge_night.coordinator import CHARGE_LIMIT_KEEPALIVE_S

    hass, coordinator, clock = night
    _charging(hass, coordinator)
    last_write = [clock.now]
    device = hass.services.async_call.side_effect

    async def kostal_with_fallback(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if data.get("entity_id") == AC_LIMIT and service == "set_value":
            last_write[0] = clock.now
        await device(domain, service, data, **kwargs)

    hass.services.async_call = AsyncMock(side_effect=kostal_with_fallback)
    lapsed = []
    for _ in range(30):  # half an hour
        clock.now += timedelta(seconds=CHARGE_LIMIT_KEEPALIVE_S)
        if clock.now - last_write[0] > timedelta(minutes=5):
            hass.states.get(AC_LIMIT).state = "22027.836"  # the Kostal's own again
            lapsed.append(clock.now)
        await coordinator._keep_charge_limit_alive()

    assert not lapsed
    assert hass.states.get(AC_LIMIT).state == "998.5"
