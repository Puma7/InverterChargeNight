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
    # Paused, not suspended: a lost write is no forced charge, and the next
    # poll may switch grid charging on again (CodeRabbit on #39)
    assert coordinator._grid_switch_suspended is False


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
    coordinator._original_ac_charge_power = 22027.836
    coordinator._planned_setpoint_written_w = 1000.0

    # The rewrite right after switching on: unconfirmed there means lifted
    await coordinator._confirm_limit_after_switch_on()
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


@pytest.mark.asyncio
async def test_a_switch_whose_feedback_lags_is_still_switched_off(night):
    """Lab, 21 October: KORE publishes the switch with its next settings poll. Right
    after turn_on it still read "off" - and neither the suspension nor the pause,
    both reading that state, took effect: the forced charge ran on."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._planned_setpoint_written_w = 1000.0
    coordinator._original_ac_charge_power = 22027.836
    hass.states.get(AC_LIMIT).state = str(FORCED_W)  # the forced charge is on
    hass.states.get(GRID).state = "off"  # ...but HA does not show the switch yet
    device = _kostal(hass, clock)

    async def lagging(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if domain == "switch":
            return  # the inverter obeys; HA learns it later
        if service == "set_value" and data.get("entity_id") == AC_LIMIT and not lagging.off:
            clock.now += timedelta(seconds=1)
            hass.states.get(AC_LIMIT).last_reported = clock.now
            raise HomeAssistantError(f"Readback is unconfirmed for {AC_LIMIT}")
        await device(domain, service, data, **kwargs)

    lagging.off = False
    hass.services.async_call = AsyncMock(side_effect=lagging)

    await coordinator._confirm_limit_after_switch_on()

    assert coordinator._grid_switch_suspended is True
    assert ("switch", "turn_off", GRID) in _calls(hass)


@pytest.mark.parametrize("path", ["recheck", "verification", "write"])
@pytest.mark.asyncio
async def test_a_forced_charge_goes_off_without_a_house_connection_limit(night, path):
    """Codex and CodeRabbit on #38: the stop sat behind the house connection
    limit, an optional feature. Without it the switch was suspended but left
    on, and the forced charge ran at the Kostal's own 22 kW to the target."""
    from custom_components.inverter_charge_night.const import CONF_GRID_IMPORT_ENTITY

    hass, coordinator, clock = night
    coordinator.config = {k: v for k, v in coordinator.config.items() if k != CONF_GRID_IMPORT_ENTITY}
    assert not coordinator._grid_limit_configured()
    coordinator.is_active = True
    coordinator._planned_setpoint_written_w = 1000.0
    coordinator._original_ac_charge_power = 22027.836
    coordinator._battery_idle_on_grid = MagicMock(return_value=False)
    hass.states.get(GRID).state = "on"
    hass.states.get(AC_LIMIT).state = str(FORCED_W)

    if path == "recheck":
        assert await coordinator._limit_lifted_by_switch_on() is True
    elif path == "verification":
        await coordinator._verify_ac_charge_limit()
    else:
        assert await coordinator._set_ac_charge_limit_w(1000) is False

    assert coordinator._grid_switch_suspended is True
    assert ("switch", "turn_off", GRID) in _calls(hass)
    assert hass.states.get(GRID).state == "off"


@pytest.mark.asyncio
async def test_the_keepalive_comes_well_inside_the_shortest_timeout(mock_hass):
    """Codex on #38: a 30-s sleep against a 30-s timeout let the limit lapse to
    the Kostal's own value before every keepalive, for the time the write took."""
    import asyncio

    from custom_components.inverter_charge_night.coordinator import KOSTAL_MODBUS_TIMEOUT_MIN_S

    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.is_active = coordinator.is_enabled = True
    coordinator._keep_charge_limit_alive = AsyncMock()
    coordinator._charge_limit_expires = MagicMock(return_value=True)
    loops: dict[str, Any] = {}

    def capture(coro: Any, name: str | None = None) -> MagicMock:
        loops[name or ""] = coro
        return MagicMock()

    mock_hass.async_create_background_task = MagicMock(side_effect=capture)
    await coordinator._start_periodic_verification()
    loops.pop("inverter_charge_night_periodic_verification").close()
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) > 2:
            raise asyncio.CancelledError

    with patch("asyncio.sleep", new=sleep):
        await loops["inverter_charge_night_charge_limit_keepalive"]

    assert coordinator._keep_charge_limit_alive.await_count == 2
    assert all(delay <= KOSTAL_MODBUS_TIMEOUT_MIN_S * 2 / 3 for delay in sleeps)


@pytest.mark.asyncio
async def test_the_keepalive_leaves_a_lower_limit_alone(night):
    """CodeRabbit on #38: someone lowered the limit - the user, evcc. The
    verification leaves a lower limit alone; the keepalive raised it again
    within 20 s."""
    hass, coordinator, clock = night
    _charging(hass, coordinator)
    hass.states.get(AC_LIMIT).state = "500"

    await coordinator._keep_charge_limit_alive()

    assert _calls(hass) == []


@pytest.mark.parametrize("held", [True, False])
@pytest.mark.asyncio
async def test_a_keepalive_that_is_not_delivered_is_verified_at_once(night, held):
    """Codex on #39: a refused keepalive was treated like KORE's float readback.
    The Kostal drops the limit within a minute, the verification ran up to 15
    minutes later. A reading that still matches is the float case; anything
    else is checked now."""
    hass, coordinator, clock = night
    _charging(hass, coordinator)
    coordinator._verify_ac_charge_limit = AsyncMock()
    if not held:
        hass.states.get(AC_LIMIT).state = "22027.836"  # lapsed to the Kostal's own
    hass.services.async_call = AsyncMock(side_effect=ServiceValidationError("unavailable"))

    await coordinator._keep_charge_limit_alive()

    assert hass.services.async_call.await_count == 1
    assert coordinator._verify_ac_charge_limit.await_count == (0 if held else 1)


@pytest.mark.asyncio
async def test_a_transient_write_failure_does_not_suspend_the_switch(night):
    """CodeRabbit on #39: a Modbus timeout while grid charging was on suspended
    the switch for the whole window - on an inverter that needs the switch to
    charge, the night was lost. Only a reading above the limit is a forced charge."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._original_ac_charge_power = 22027.836
    hass.states.get(GRID).state = "on"
    hass.states.get(AC_LIMIT).state = "998.5"
    hass.services.async_call = AsyncMock(side_effect=HomeAssistantError("Modbus timeout"))

    assert await coordinator._set_ac_charge_limit_w(1000) is False

    assert coordinator._grid_switch_suspended is False


@pytest.mark.asyncio
async def test_no_raised_floor_while_the_limit_cannot_be_written(night):
    """Codex on #38: a floor above the SOC makes the Kostal charge at once, at
    whatever limit stands - its own 22 kW when the planned one could not be
    written. The floor waits for a limit that was written; the next poll tries
    again. A plan that writes nothing on purpose is not a failure."""
    hass, coordinator, clock = night
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 43.1
    device = hass.services.async_call.side_effect
    down = [True]

    async def modbus(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if down[0] and service == "set_value" and data.get("entity_id") == AC_LIMIT:
            raise HomeAssistantError("Modbus timeout")
        await device(domain, service, data, **kwargs)

    hass.services.async_call = AsyncMock(side_effect=modbus)
    _fresh(hass, clock)
    await coordinator._async_update_data()

    floors = [c.args[2]["value"] for c in hass.services.async_call.await_args_list
              if c.args[1] == "set_value" and c.args[2]["entity_id"] == MIN_SOC]
    assert all(value < 33 for value in floors), floors
    assert ("switch", "turn_on", GRID) not in _calls(hass)

    down[0] = False  # Modbus back: the next poll raises the floor under the limit
    clock.now += timedelta(minutes=15)
    _fresh(hass, clock)
    await coordinator._async_update_data()
    args = [c.args for c in hass.services.async_call.await_args_list]
    raised = args.index(("number", "set_value", {"entity_id": MIN_SOC, "value": 43.1}))
    assert args[raised - 1][2]["entity_id"] == AC_LIMIT


@pytest.mark.asyncio
async def test_a_limit_the_inverter_does_not_hold_does_not_stop_the_night(night):
    """Once the repair issue says the inverter does not hold the limit, writes
    stop for the window on purpose. That refusal is no failed write: the floor
    still charges the battery."""
    hass, coordinator, clock = night
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 43.1
    coordinator._ac_limit_not_held = True
    _fresh(hass, clock)

    await coordinator._async_update_data()

    assert ("number", "set_value", {"entity_id": MIN_SOC, "value": 43.1}) in [
        c.args for c in hass.services.async_call.await_args_list
    ]


@pytest.mark.asyncio
async def test_a_failed_lowering_write_is_no_lift(night):
    """Codex on #39: the plan goes from 5000 W down to 1000 W and the write
    times out. The reading still shows the 5000 W written before - above the
    new limit, but nothing lifted it. Only a reading above anything written is."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._original_ac_charge_power = 22027.836
    coordinator._planned_setpoint_written_w = 5000.0
    hass.states.get(GRID).state = "on"
    hass.states.get(AC_LIMIT).state = "4998.5"
    device = hass.services.async_call.side_effect

    async def modbus(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value" and data.get("entity_id") == AC_LIMIT:
            raise HomeAssistantError("Modbus timeout")
        await device(domain, service, data, **kwargs)

    hass.services.async_call = AsyncMock(side_effect=modbus)

    assert await coordinator._set_ac_charge_limit_w(1000) is False

    assert coordinator._grid_switch_suspended is False
    # The house connection limit still pauses for the failed write - once,
    # not for the rest of the window (CodeRabbit on #39)
    assert ("switch", "turn_off", GRID) in _calls(hass)
    assert hass.states.get(GRID).state == "off"


@pytest.mark.asyncio
async def test_the_verifier_does_not_raise_a_floor_the_poll_held_back(night):
    """Codex on #38: the poll held the floor back for a limit it could not write;
    the min SOC verification found the old floor and wrote the new one anyway."""
    hass, coordinator, clock = night
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 43.1
    device = hass.services.async_call.side_effect

    async def modbus(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value" and data.get("entity_id") == AC_LIMIT:
            raise HomeAssistantError("Modbus timeout")
        await device(domain, service, data, **kwargs)

    hass.services.async_call = AsyncMock(side_effect=modbus)
    _fresh(hass, clock)
    await coordinator._async_update_data()
    await coordinator._verify_and_restore_min_soc()

    assert ("number", "set_value", {"entity_id": MIN_SOC, "value": 43.1}) not in [
        c.args for c in hass.services.async_call.await_args_list
    ]


@pytest.mark.asyncio
async def test_a_lapse_that_acknowledged_keepalives_do_not_cure_is_verified(night):
    """Codex on #38: an adapter can acknowledge a write the inverter drops. A
    lapse still read 75 s after the first keepalive saw it is verified then,
    not up to 15 minutes later; one stale reading is not enough."""
    from custom_components.inverter_charge_night.coordinator import KEEPALIVE_LAPSE_GRACE_S

    hass, coordinator, clock = night
    _charging(hass, coordinator)
    hass.states.get(AC_LIMIT).state = "22027.836"  # lapsed
    hass.services.async_call = AsyncMock()  # acknowledged, dropped
    coordinator._verify_ac_charge_limit = AsyncMock()

    await coordinator._keep_charge_limit_alive()
    clock.now += timedelta(seconds=40)
    await coordinator._keep_charge_limit_alive()
    assert coordinator._verify_ac_charge_limit.await_count == 0

    clock.now += timedelta(seconds=KEEPALIVE_LAPSE_GRACE_S - 30)
    await coordinator._keep_charge_limit_alive()
    assert coordinator._verify_ac_charge_limit.await_count == 1


@pytest.mark.parametrize(("platform", "kept"), [("kostal_kore", True), ("modbus", False), (None, False)])
@pytest.mark.asyncio
async def test_only_an_expiring_control_is_kept_alive(mock_hass, platform, kept):
    """Codex on #38: a setting that persists does not need a write every 20 s -
    1080 writes a night of traffic and wear. KostalKore's 1038 lapses; others do not."""
    from homeassistant.helpers import entity_registry as er

    coordinator = _make_coordinator(mock_hass, CONFIG)
    coordinator.is_active = coordinator.is_enabled = True
    registry = MagicMock()
    registry.async_get.return_value = SimpleNamespace(platform=platform) if platform else None
    mock_hass.data[er.DATA_REGISTRY] = registry
    names: list[str] = []

    def capture(coro: Any, name: str | None = None) -> MagicMock:
        names.append(name or "")
        coro.close()
        return MagicMock()

    mock_hass.async_create_background_task = MagicMock(side_effect=capture)
    await coordinator._start_periodic_verification()

    assert ("inverter_charge_night_charge_limit_keepalive" in names) is kept
    registry.async_get.assert_called_with(AC_LIMIT)


@pytest.mark.parametrize("delivered", [True, False])
@pytest.mark.asyncio
async def test_a_lifted_limit_is_written_again_before_the_floor_rises(night, delivered):
    """Codex on #38: later in the window the plan wrote nothing - its setpoint
    was within 100 W of the one written - but the Kostal had dropped that limit
    since. The raised floor would charge at its own 22 kW. The limit in force
    is read first and written again; if that fails, the floor waits."""
    hass, coordinator, clock = night
    await coordinator._on_window_start(WINDOW_START)
    coordinator.initial_calculated_soc = 43.1
    coordinator._original_ac_charge_power = 22027.836
    coordinator._planned_setpoint_written_w = 1000.0
    coordinator._plan_charge_power = AsyncMock()  # nothing new to write
    hass.states.get(AC_LIMIT).state = "22027.836"  # dropped back to the Kostal's own
    if not delivered:
        device = hass.services.async_call.side_effect

        async def modbus(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
            if service == "set_value" and data.get("entity_id") == AC_LIMIT:
                raise HomeAssistantError("Modbus timeout")
            await device(domain, service, data, **kwargs)

        hass.services.async_call = AsyncMock(side_effect=modbus)
    _fresh(hass, clock)

    await coordinator._async_update_data()

    args = [c.args for c in hass.services.async_call.await_args_list]
    floor = ("number", "set_value", {"entity_id": MIN_SOC, "value": 43.1})
    if delivered:
        raised = args.index(floor)
        assert ("number", "set_value", {"entity_id": AC_LIMIT, "value": 1000.0}) in args[:raised]
    else:
        assert floor not in args


@pytest.mark.asyncio
async def test_the_recheck_does_not_trust_a_switch_that_still_reads_off(night):
    """Codex on #38: switched on, the rewrite held, and 45 s later KORE still
    showed the switch "off" while the limit read the forced value. The recheck
    knows the switch was commanded on: it suspends and stops it all the same."""
    from custom_components.inverter_charge_night.coordinator import SWITCH_ON_RECHECK_S

    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._original_ac_charge_power = 22027.836
    coordinator._planned_setpoint_written_w = 1000.0
    hass.states.get(AC_LIMIT).state = "998.5"
    hass.states.get(GRID).state = "off"  # KORE has not published the switch yet
    hass.services.async_call = AsyncMock()

    with patch("custom_components.inverter_charge_night.coordinator.async_call_later") as later:
        await coordinator._confirm_limit_after_switch_on()
        delay, recheck = later.call_args.args[1:]
        assert delay == SWITCH_ON_RECHECK_S
        hass.states.get(AC_LIMIT).state = str(FORCED_W)
        hass.services.async_call = _kostal(hass, clock)
        await recheck(clock.now)

    assert coordinator._grid_switch_suspended is True
    assert ("switch", "turn_off", GRID) in _calls(hass)


def _limit_writes_fail(hass: Any) -> None:
    device = hass.services.async_call.side_effect

    async def modbus(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value" and data.get("entity_id") == AC_LIMIT:
            raise HomeAssistantError("Modbus timeout")
        await device(domain, service, data, **kwargs)

    hass.services.async_call = AsyncMock(side_effect=modbus)


@pytest.mark.parametrize("path", ["verification", "switch_on_recheck"])
@pytest.mark.asyncio
async def test_a_failed_protective_rewrite_holds_the_floor_back(night, path):
    """Codex on #38: the verification and the switch-on recheck each write a
    lifted limit back, and ignored a failure - the min SOC verification could
    then raise the floor under the inverter's own limit."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._original_ac_charge_power = 22027.836
    coordinator._planned_setpoint_written_w = 1000.0
    coordinator._battery_idle_on_grid = MagicMock(return_value=False)
    hass.states.get(AC_LIMIT).state = str(FORCED_W)
    hass.states.get(GRID).state = "on" if path == "switch_on_recheck" else "off"
    _limit_writes_fail(hass)

    if path == "verification":
        await coordinator._verify_ac_charge_limit()
    else:
        assert await coordinator._limit_lifted_by_switch_on() is True

    assert coordinator._plan_limit_write_failed is True


@pytest.mark.asyncio
async def test_no_recovery_rewrite_once_the_window_has_ended(night):
    """Codex on #38: the first write after switching on failed while the window
    ended; the window end restored the user's limit, and the recovery rewrite
    put the planned one back over it."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._planned_setpoint_written_w = 1000.0
    hass.states.get(GRID).state = "on"
    calls = []

    async def write(power_w: int) -> bool:
        calls.append(power_w)
        coordinator.is_active = False  # the window ended meanwhile
        return False

    coordinator._set_ac_charge_limit_w = write
    await coordinator._confirm_limit_after_switch_on()

    assert calls == [1000]


@pytest.mark.asyncio
async def test_no_efficiency_trial_on_a_suspended_switch_that_still_reads_on(mock_hass):
    """Lab, after dark: the forced charge was found and the switch suspended and
    paused, but KORE still showed it "on" - and the efficiency search started a
    trial on it. A suspended switch is off for the search."""
    from tests.test_efficiency_finder import CONFIG as FINDER, NOW as FINDER_NOW, START, _make, _power

    mock_hass.states.async_set(GRID, "on")
    mock_hass.states.async_set("number.ac_limit", "1000", {"unit_of_measurement": "W"})
    _power(mock_hass, 0, 0)
    coordinator = _make(mock_hass, {**FINDER, "grid_charge_switch": GRID})
    coordinator.is_active = True
    coordinator.auto_efficient_charge = True
    coordinator._grid_switch_suspended = True
    coordinator._start_auto_test = AsyncMock()

    with patch(FINDER_NOW, return_value=START):
        await coordinator._handle_auto_charge()

    coordinator._start_auto_test.assert_not_awaited()
    assert coordinator._auto_wait_reason == "grid_charge_not_confirmed"
    coordinator._remove_auto_sample_listener()


@pytest.mark.asyncio
async def test_the_switch_is_turned_off_once_when_its_state_arrives_during_the_write(night):
    """Lab, after dark: KORE showed the switch "off" when the rewrite began and
    "on" by the time it failed. The setter paused; the check, reading the state
    from before the write, sent a second turn_off."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._original_ac_charge_power = 22027.836
    coordinator._planned_setpoint_written_w = 1000.0
    hass.states.get(GRID).state = "off"  # not published yet
    hass.states.get(AC_LIMIT).state = str(FORCED_W)
    device = _kostal(hass, clock)

    async def lagging(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value" and data.get("entity_id") == AC_LIMIT and lagging.first:
            lagging.first = False
            hass.states.get(GRID).state = "on"  # KORE publishes it now
            raise HomeAssistantError(f"Readback is unconfirmed for {AC_LIMIT}")
        await device(domain, service, data, **kwargs)

    lagging.first = True
    hass.services.async_call = AsyncMock(side_effect=lagging)

    await coordinator._confirm_limit_after_switch_on()

    assert _calls(hass).count(("switch", "turn_off", GRID)) == 1
    assert coordinator._grid_switch_suspended is True


@pytest.mark.parametrize("shown", ["on", "off"])
@pytest.mark.asyncio
async def test_a_suspended_switch_found_on_is_stopped_again(night, shown):
    """Codex on #38: restarted inside the window with the suspension restored,
    the switch may still be on - its turn_off unconfirmed, or the process gone
    before it ran. The refused activation stops it again; one that reads off
    gets nothing."""
    hass, coordinator, clock = night
    coordinator.is_active = True
    coordinator._grid_switch_suspended = True
    hass.states.get(GRID).state = shown

    assert await coordinator._grid_charge_activation_safe(44.0) is False

    assert (("switch", "turn_off", GRID) in _calls(hass)) is (shown == "on")


@pytest.mark.asyncio
async def test_the_lapse_timer_does_not_outlive_its_lapse(night):
    """Codex on #38: a lapse seen, then the limit lowered by someone else - the
    timer stayed set and the next high reading, much later, counted as a lapse
    of minutes and was verified at once."""
    from custom_components.inverter_charge_night.coordinator import KEEPALIVE_LAPSE_GRACE_S

    hass, coordinator, clock = night
    _charging(hass, coordinator)
    hass.services.async_call = AsyncMock()
    coordinator._verify_ac_charge_limit = AsyncMock()
    hass.states.get(AC_LIMIT).state = "22027.836"
    await coordinator._keep_charge_limit_alive()  # the lapse begins
    hass.states.get(AC_LIMIT).state = "500"  # lowered by evcc
    await coordinator._keep_charge_limit_alive()
    clock.now += timedelta(seconds=KEEPALIVE_LAPSE_GRACE_S * 4)
    hass.states.get(AC_LIMIT).state = "22027.836"  # high again, freshly

    await coordinator._keep_charge_limit_alive()

    coordinator._verify_ac_charge_limit.assert_not_awaited()


@pytest.mark.parametrize("key", ["grid_charge_switch", "charge_power_entity", "min_soc_entity"])
@pytest.mark.asyncio
async def test_new_controls_do_not_inherit_the_suspension(night, key):
    """Codex on #38: the options replaced the grid-charge switch mid-window; the
    window restarted with the same identity and kept the old switch's suspension."""
    hass, coordinator, clock = night
    await coordinator._on_window_start(WINDOW_START)
    coordinator._grid_switch_suspended = True
    coordinator._on_window_end = AsyncMock()

    await coordinator.async_release_changed_entities({**coordinator.config, key: "switch.other"})

    assert coordinator._grid_switch_suspended is (key == "min_soc_entity")
