"""The night writes of 20 October 2026, reproduced from the field log.

At 03:10 the inverter answered Modbus with "server device failure" for a few
seconds. The grid meter, the min SOC and the battery SOC were briefly
untrusted. The target had been reached at 23:01 (68 % against a 21 % floor) and
grid charging was off, so nothing could draw from the connection. Still:

* the communication guard wrote a 0 W charge limit on every refresh while a
  fault lasted (about a dozen writes in three minutes);
* the house connection limit wrote 0 W "after the target was reached";
* the Kostal reported its own 22 kW again after every write, and the
  verification wrote 0 W anew every 15 minutes until the window ended.

The value captured before the first write (22063 W) was what the window-end
reset then tried to restore in a loop.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from homeassistant.helpers import issue_registry as ir

from custom_components.inverter_charge_night.const import DOMAIN
from custom_components.inverter_charge_night.coordinator import (
    AC_LIMIT_MAX_REWRITES,
    IDLE_SOC_MAX_AGE_S,
)
from tests.test_grid_limit import (
    AC_LIMIT, BATTERY, GRID, GRID_IMPORT, LIMITED_CONFIG, MIN_SOC, NOW,
    _make_coordinator, _register,
)

BERLIN = ZoneInfo("Europe/Berlin")
THE_HICCUP = datetime(2026, 10, 20, 3, 10, 9, tzinfo=BERLIN)
CONFIG = {**LIMITED_CONFIG, "start_time": "23:00", "end_time": "04:58"}


def _ac_writes(hass: Any) -> list[Any]:
    return [
        c.args[2]["value"]
        for c in hass.services.async_call.await_args_list
        if c.args[1] == "set_value" and c.args[2].get("entity_id") == AC_LIMIT
    ]


def _a_kostal_that_keeps_its_own_limit(hass: Any) -> None:
    """Accepts every write, and the AC limit keeps reporting 22063 W anyway."""

    async def call(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        state = hass.states.get(data.get("entity_id"))
        if state is None:
            return
        if domain in ("switch", "input_boolean"):
            state.state = "on" if service == "turn_on" else "off"
        elif data.get("entity_id") != AC_LIMIT and "value" in data:
            state.state = str(data["value"])
        state.last_reported = state.last_updated = clock.now

    clock = hass.night_clock
    hass.services.async_call = AsyncMock(side_effect=call)


@pytest.fixture
def night(mock_hass):
    """The field night at 03:10: target reached, grid charging off, 68 % on a 21 % floor."""
    clock = SimpleNamespace(now=THE_HICCUP)
    mock_hass.night_clock = clock
    with patch(NOW, side_effect=lambda: clock.now):
        _register(mock_hass)
        mock_hass.states.async_set(BATTERY, "68")
        mock_hass.states.async_set(MIN_SOC, "21", {"min": 0, "max": 100, "unit_of_measurement": "%"})
        mock_hass.states.async_set(
            AC_LIMIT, "22063.310546875", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"}
        )
        for entity_id in (BATTERY, MIN_SOC, AC_LIMIT, GRID, GRID_IMPORT):
            state = mock_hass.states.get(entity_id)
            state.last_reported = state.last_updated = state.last_changed = clock.now
        coordinator = _make_coordinator(mock_hass, CONFIG)
        coordinator._start_periodic_verification = AsyncMock()
        coordinator._stop_periodic_verification = AsyncMock()
        coordinator.is_active = True
        coordinator.target_reached = True
        coordinator.initial_calculated_soc = 21.3
        coordinator.original_min_soc = 8.0
        _a_kostal_that_keeps_its_own_limit(mock_hass)
        yield mock_hass, coordinator, clock


def _meter_drops_out(hass: Any) -> None:
    hass.states.get(GRID_IMPORT).state = "unavailable"


@pytest.mark.asyncio
async def test_a_meter_outage_with_an_idle_battery_writes_no_charge_limit(night):
    """03:10: nothing could draw from the grid, so nothing is written or captured."""
    hass, coordinator, clock = night
    coordinator.enable_communication_monitoring()
    coordinator._async_calculate_and_control = AsyncMock(return_value={})
    _meter_drops_out(hass)

    with patch("homeassistant.components.persistent_notification.async_create"):
        for _ in range(12):  # three minutes of refreshes, as in the log
            await coordinator._async_update_data()
            await coordinator._enforce_grid_limit_now()
            await coordinator._react_to_grid_import()
            clock.now += timedelta(seconds=15)

    assert _ac_writes(hass) == []
    assert coordinator._original_ac_charge_power is None
    # The outage itself is still reported and grid charging stays requested off.
    assert coordinator.communication_diagnostics()["faults"] == ["grid_meter"]
    assert hass.states.get(GRID).state == "off"


@pytest.mark.asyncio
async def test_a_briefly_blank_soc_is_bridged_by_the_last_trusted_reading(night):
    """The same hiccup blanked the SOC for a few seconds; 68 % a moment ago still counts."""
    hass, coordinator, clock = night
    coordinator.enable_communication_monitoring()
    coordinator._async_calculate_and_control = AsyncMock(return_value={})
    assert coordinator._current_battery_soc() == 68.0  # the last trusted reading
    _meter_drops_out(hass)
    hass.states.get(BATTERY).state = "unavailable"
    clock.now += timedelta(seconds=2)

    with patch("homeassistant.components.persistent_notification.async_create"):
        await coordinator._async_update_data()

    assert _ac_writes(hass) == []


@pytest.mark.asyncio
async def test_a_soc_that_stays_blank_keeps_the_protective_zero(night):
    """Fail closed: an old reading does not prove the battery is idle."""
    hass, coordinator, clock = night
    coordinator.enable_communication_monitoring()
    coordinator._async_calculate_and_control = AsyncMock(return_value={})
    assert coordinator._current_battery_soc() == 68.0
    _meter_drops_out(hass)
    hass.states.get(BATTERY).state = "unavailable"
    clock.now += timedelta(seconds=IDLE_SOC_MAX_AGE_S + 1)

    with patch("homeassistant.components.persistent_notification.async_create"):
        for _ in range(10):
            await coordinator._async_update_data()
            clock.now += timedelta(seconds=15)

    # Once per outage, not once per refresh
    assert _ac_writes(hass) == [0.0]
    assert coordinator._original_ac_charge_power == pytest.approx(22063.310546875)


@pytest.mark.parametrize(
    "change",
    [
        "target_not_reached",
        "grid_charging_on",
        "soc_near_the_floor",
        "no_grid_switch",
    ],
)
@pytest.mark.asyncio
async def test_a_battery_that_can_draw_keeps_the_protective_zero(night, change):
    hass, coordinator, clock = night
    coordinator.enable_communication_monitoring()
    coordinator._async_calculate_and_control = AsyncMock(return_value={})
    if change == "target_not_reached":
        coordinator.target_reached = False
    elif change == "grid_charging_on":
        hass.states.get(GRID).state = "on"
    elif change == "soc_near_the_floor":
        # 4.9 points above the 21 % the inverter holds: inside the margin
        hass.states.get(BATTERY).state = "25.9"
    else:
        coordinator.config = {k: v for k, v in coordinator.config.items() if k != "grid_charge_switch"}
    _meter_drops_out(hass)

    with patch("homeassistant.components.persistent_notification.async_create"):
        for _ in range(10):
            await coordinator._async_update_data()
            clock.now += timedelta(seconds=15)

    assert _ac_writes(hass) == [0.0]


@pytest.mark.asyncio
async def test_the_floor_the_inverter_holds_counts_even_above_the_plan(night):
    """A min SOC of 66 % on the inverter leaves no margin at 68 %, whatever the plan says."""
    hass, coordinator, _ = night
    hass.states.get(MIN_SOC).state = "66"
    assert coordinator._battery_idle_on_grid() is False
    hass.states.get(MIN_SOC).state = "21"
    assert coordinator._battery_idle_on_grid() is True


@pytest.mark.asyncio
async def test_an_ignored_limit_is_rewritten_a_bounded_number_of_times(night):
    """03:16-04:46: the Kostal kept 22 kW; seven rewrites became two and a repair issue."""
    hass, coordinator, clock = night
    hass.states.get(BATTERY).state = "24"  # can draw: the bound has to do the work
    coordinator._planned_setpoint_written_w = 0.0
    coordinator._original_ac_charge_power = 22063.310546875

    with patch(f"{ir.__name__}.async_create_issue") as create:
        for _ in range(7):  # every 15 minutes until the window end
            await coordinator._verify_ac_charge_limit()
            clock.now += timedelta(minutes=15)

    assert _ac_writes(hass) == [0] * AC_LIMIT_MAX_REWRITES
    create.assert_called_once()
    assert create.call_args.args[2] == "charge_limit_not_held_entry_1"
    assert create.call_args.kwargs["translation_key"] == "charge_limit_not_held"
    assert create.call_args.kwargs["translation_placeholders"]["actual"] == "22063"
    # Grid charging stays requested off
    assert hass.states.get(GRID).state == "off"


@pytest.mark.asyncio
async def test_the_next_window_gets_its_own_rewrites_and_clears_the_issue(night):
    hass, coordinator, _ = night
    hass.states.get(BATTERY).state = "24"
    coordinator._planned_setpoint_written_w = 0.0
    coordinator._original_ac_charge_power = 22063.310546875
    with patch(f"{ir.__name__}.async_create_issue"):
        for _ in range(AC_LIMIT_MAX_REWRITES + 1):
            await coordinator._verify_ac_charge_limit()
    assert coordinator._ac_limit_not_held is True

    with patch(f"{ir.__name__}.async_delete_issue") as delete:
        coordinator._clear_ac_limit_not_held()

    delete.assert_called_once_with(hass, DOMAIN, "charge_limit_not_held_entry_1")
    assert coordinator._ac_limit_rewrites == 0
    assert coordinator._ac_limit_not_held is False


@pytest.mark.asyncio
async def test_a_briefly_held_limit_does_not_refill_the_budget(night):
    """Codex on #36: the Kostal holds the limit for a while, then drops it.

    A refill on every match would give each drop two more writes, for ever.
    """
    hass, coordinator, _ = night
    hass.states.get(BATTERY).state = "24"
    coordinator._planned_setpoint_written_w = 0.0
    coordinator._original_ac_charge_power = 22063.310546875
    with patch(f"{ir.__name__}.async_create_issue") as create:
        for held in (False, True, False, True, False, False):
            hass.states.get(AC_LIMIT).state = "0" if held else "22063.31"
            await coordinator._verify_ac_charge_limit()

    assert _ac_writes(hass) == [0] * AC_LIMIT_MAX_REWRITES
    create.assert_called_once()


def _restarted(hass: Any, coordinator: Any) -> Any:
    """A new coordinator built from what the old one persisted."""
    from tests.test_grid_limit import _make_coordinator as make

    return make(hass, CONFIG, dict(coordinator.entry.options))


@pytest.mark.asyncio
async def test_a_restart_inside_the_window_keeps_the_spent_budget(night):
    """Codex on #36: a restart must not hand out a fresh pair of rewrites."""
    hass, coordinator, clock = night

    def _update(entry_: Any, options: Any = None, **kwargs: Any) -> bool:
        if options is not None:
            entry_.options = options
        return True

    hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    hass.states.get(BATTERY).state = "24"
    coordinator.is_active = True
    coordinator._window_started_at = THE_HICCUP - timedelta(hours=4)
    coordinator._ac_limit_window = coordinator._window_identity(THE_HICCUP)  # as its start named it
    coordinator._planned_setpoint_written_w = 0.0
    coordinator._original_ac_charge_power = 22063.310546875
    with patch(f"{ir.__name__}.async_create_issue"):
        for _ in range(AC_LIMIT_MAX_REWRITES + 1):
            await coordinator._verify_ac_charge_limit()
    assert coordinator._ac_limit_not_held is True

    clock.now += timedelta(minutes=5)
    fresh = _restarted(hass, coordinator)
    assert fresh._ac_limit_rewrites == AC_LIMIT_MAX_REWRITES
    assert fresh._ac_limit_not_held is True
    fresh._start_periodic_verification = AsyncMock()
    fresh._async_calculate_and_control = AsyncMock(return_value={})
    with patch(f"{ir.__name__}.async_delete_issue") as delete, patch.object(
        fresh, "_setup_battery_soc_listener"
    ), patch.object(fresh, "_setup_inverter_min_soc_listener"), patch.object(
        fresh, "_setup_grid_import_listener"
    ):
        await fresh._on_window_start(clock.now)  # the window is picked up again

    assert not call_for(delete, "charge_limit_not_held_entry_1")
    assert fresh._ac_limit_rewrites == AC_LIMIT_MAX_REWRITES
    assert fresh._ac_limit_not_held is True


@pytest.mark.asyncio
async def test_the_next_nights_window_starts_with_a_fresh_budget(night):
    hass, coordinator, clock = night
    coordinator.is_active = False
    coordinator._window_started_at = THE_HICCUP - timedelta(hours=4)
    coordinator._ac_limit_rewrites = AC_LIMIT_MAX_REWRITES
    coordinator._ac_limit_not_held = True
    coordinator._async_calculate_and_control = AsyncMock(return_value={})
    clock.now = THE_HICCUP + timedelta(hours=20)
    with patch(f"{ir.__name__}.async_delete_issue") as delete, patch.object(
        coordinator, "_setup_battery_soc_listener"
    ), patch.object(coordinator, "_setup_inverter_min_soc_listener"), patch.object(
        coordinator, "_setup_grid_import_listener"
    ):
        await coordinator._on_window_start(clock.now)

    assert call_for(delete, "charge_limit_not_held_entry_1")
    assert coordinator._ac_limit_rewrites == 0


@pytest.mark.asyncio
async def test_an_idle_battery_is_not_rewritten_at_all(night):
    hass, coordinator, clock = night
    coordinator._planned_setpoint_written_w = 0.0
    coordinator._original_ac_charge_power = 22063.310546875

    with patch(f"{ir.__name__}.async_create_issue") as create:
        for _ in range(7):
            # KORE reports the SOC and the min SOC every few seconds
            hass.states.get(BATTERY).last_reported = clock.now
            hass.states.get(MIN_SOC).last_reported = clock.now
            await coordinator._verify_ac_charge_limit()
            clock.now += timedelta(minutes=15)

    assert _ac_writes(hass) == []
    create.assert_not_called()


@pytest.mark.asyncio
async def test_a_soc_that_stopped_reporting_does_not_prove_an_idle_battery(night):
    """Codex on #36: an old number is as unknown as a blank entity."""
    hass, coordinator, clock = night
    coordinator.enable_communication_monitoring()
    coordinator._async_calculate_and_control = AsyncMock(return_value={})
    _meter_drops_out(hass)
    clock.now += timedelta(seconds=IDLE_SOC_MAX_AGE_S + 1)  # 68 % still shown, not reported since

    assert coordinator._battery_idle_on_grid() is False
    with patch("homeassistant.components.persistent_notification.async_create"):
        for _ in range(5):
            await coordinator._async_update_data()
            clock.now += timedelta(seconds=15)

    assert _ac_writes(hass) == [0.0]


@pytest.mark.asyncio
async def test_a_window_start_clears_the_charge_limit_issue(night):
    hass, coordinator, _ = night
    coordinator.is_active = False
    coordinator._window_started_at = None  # the previous window is unknown: a new one
    coordinator._ac_limit_rewrites = AC_LIMIT_MAX_REWRITES
    coordinator._ac_limit_not_held = True
    coordinator._async_calculate_and_control = AsyncMock(return_value={})
    coordinator.async_refresh = AsyncMock()

    with patch(f"{ir.__name__}.async_delete_issue") as delete, patch.object(
        coordinator, "_setup_battery_soc_listener"
    ), patch.object(coordinator, "_setup_inverter_min_soc_listener"), patch.object(
        coordinator, "_setup_grid_import_listener"
    ):
        await coordinator._on_window_start(THE_HICCUP)

    assert call_for(delete, "charge_limit_not_held_entry_1")
    assert coordinator._ac_limit_rewrites == 0


def call_for(mock: MagicMock, issue_id: str) -> bool:
    return any(c.args[2] == issue_id for c in mock.call_args_list)


@pytest.mark.asyncio
async def test_removing_the_entry_deletes_the_charge_limit_issue(mock_hass):
    from custom_components.inverter_charge_night import async_remove_entry

    entry = MagicMock()
    entry.entry_id = "entry_1"
    with patch(f"{ir.__name__}.async_delete_issue") as delete:
        await async_remove_entry(mock_hass, entry)

    assert call_for(delete, "charge_limit_not_held_entry_1")
    assert call_for(delete, "reset_incomplete_entry_1")


@pytest.mark.asyncio
async def test_the_sensor_and_diagnostics_show_a_limit_that_is_not_held(night):
    from custom_components.inverter_charge_night.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.inverter_charge_night.sensor import CalculatedSOCSensor

    hass, coordinator, _ = night
    hass.states.get(BATTERY).state = "24"
    coordinator._planned_setpoint_written_w = 0.0
    coordinator._original_ac_charge_power = 22063.310546875
    with patch(f"{ir.__name__}.async_create_issue"):
        for _ in range(AC_LIMIT_MAX_REWRITES + 1):
            await coordinator._verify_ac_charge_limit()

    coordinator.data = {}
    sensor = CalculatedSOCSensor(coordinator, coordinator.entry)
    assert sensor.extra_state_attributes["charge_limit_not_held"] is True
    coordinator.entry.runtime_data = coordinator
    coordinator.entry.version = 1
    coordinator.entry.minor_version = 1
    diagnostics = await async_get_config_entry_diagnostics(hass, coordinator.entry)
    charge_control = diagnostics["state"]["charge_control"]
    assert charge_control["charge_limit_not_held"] is True
    assert charge_control["charge_limit_rewrites"] == AC_LIMIT_MAX_REWRITES


@pytest.mark.asyncio
async def test_a_restart_inside_a_long_ad_hoc_window_keeps_the_spent_budget(night):
    """Codex on #36: an eight-hour charge_to window outlives a one-hour schedule.

    Three hours in, the guess "longer than the configured window" called the
    restart a new window and handed out a fresh budget. The ad-hoc deadline
    names the window instead.
    """
    hass, coordinator, clock = night
    coordinator.config = {**coordinator.config, "start_time": "23:00", "end_time": "00:00"}
    coordinator._adhoc_until = THE_HICCUP + timedelta(hours=5)
    coordinator._ac_limit_window = coordinator._window_identity(THE_HICCUP - timedelta(hours=3))
    coordinator._ac_limit_rewrites = AC_LIMIT_MAX_REWRITES
    coordinator._ac_limit_not_held = True
    coordinator._window_started_at = THE_HICCUP - timedelta(hours=3)
    coordinator.is_active = False
    coordinator._async_calculate_and_control = AsyncMock(return_value={})
    with patch(f"{ir.__name__}.async_delete_issue") as delete, patch.object(
        coordinator, "_setup_battery_soc_listener"
    ), patch.object(coordinator, "_setup_inverter_min_soc_listener"), patch.object(
        coordinator, "_setup_grid_import_listener"
    ):
        await coordinator._on_window_start(clock.now)

    assert not call_for(delete, "charge_limit_not_held_entry_1")
    assert coordinator._ac_limit_not_held is True


def test_two_scheduled_nights_are_two_windows_even_when_nearly_a_day_long(night):
    """Codex on #36: a 23.5 h schedule made tomorrow's window look like a resume."""
    _, coordinator, _ = night
    coordinator.config = {**coordinator.config, "start_time": "05:00", "end_time": "04:30"}
    tonight = coordinator._window_identity(THE_HICCUP)
    tomorrow = coordinator._window_identity(THE_HICCUP + timedelta(hours=23, minutes=55))
    assert tonight is not None and tomorrow is not None
    assert tonight != tomorrow


@pytest.mark.asyncio
async def test_no_writer_touches_a_limit_declared_not_held(night):
    """Codex on #36: the repair issue says writing stopped - every writer has to honour it."""
    hass, coordinator, _ = night
    coordinator._ac_limit_not_held = True
    hass.states.get(GRID).state = "on"

    assert await coordinator._set_ac_charge_limit_w(3000) is False

    assert _ac_writes(hass) == []
    assert coordinator._original_ac_charge_power is None
    assert hass.states.get(GRID).state == "off"  # paused instead


@pytest.mark.asyncio
async def test_an_unknown_inverter_floor_does_not_prove_an_idle_battery(night):
    """Codex on #36: the plan says 21 %, but nobody knows what the inverter holds now."""
    hass, coordinator, clock = night
    coordinator.enable_communication_monitoring()
    coordinator._async_calculate_and_control = AsyncMock(return_value={})
    _meter_drops_out(hass)
    hass.states.get(MIN_SOC).state = "unavailable"

    assert coordinator._battery_idle_on_grid() is False
    with patch("homeassistant.components.persistent_notification.async_create"):
        for _ in range(5):
            await coordinator._async_update_data()
            clock.now += timedelta(seconds=15)

    assert _ac_writes(hass) == [0.0]


@pytest.mark.asyncio
async def test_the_floor_seen_just_before_the_entity_went_blank_still_counts(night):
    """The listener keeps the last numeric floor when the same hiccup blanks it."""
    from homeassistant.core import State

    hass, coordinator, clock = night
    with patch(
        "custom_components.inverter_charge_night.coordinator.async_track_state_change_event"
    ) as track:
        coordinator._setup_inverter_min_soc_listener()
    on_change = track.call_args.args[2]
    before = State(MIN_SOC, "21", last_reported=clock.now)
    hass.states.get(MIN_SOC).state = "unavailable"
    hass.states.get(MIN_SOC).last_reported = clock.now
    coordinator._last_trusted_min_soc = None
    await on_change(SimpleNamespace(data={"old_state": before, "new_state": hass.states.get(MIN_SOC)}))
    clock.now += timedelta(seconds=10)

    assert coordinator._battery_idle_on_grid() is True
    clock.now += timedelta(seconds=IDLE_SOC_MAX_AGE_S)
    assert coordinator._battery_idle_on_grid() is False  # too old to vouch for the floor


@pytest.mark.asyncio
async def test_a_rewrite_that_another_writer_preempted_spends_nothing(night):
    """Codex on #36: the pause awaits, and another writer may replace the setpoint meanwhile."""
    hass, coordinator, _ = night
    hass.states.get(BATTERY).state = "24"
    coordinator._planned_setpoint_written_w = 0.0
    coordinator._original_ac_charge_power = 22063.310546875

    async def another_writer_gets_in(reason: str) -> None:
        coordinator._planned_setpoint_written_w = 1500.0

    coordinator._pause_grid_charge = AsyncMock(side_effect=another_writer_gets_in)
    for _ in range(AC_LIMIT_MAX_REWRITES + 1):
        coordinator._planned_setpoint_written_w = 0.0
        await coordinator._verify_ac_charge_limit()

    assert _ac_writes(hass) == []
    assert coordinator._ac_limit_rewrites == 0
    assert coordinator._ac_limit_not_held is False


def _inverter_writes(hass: Any) -> list[tuple[str, str, Any]]:
    return [
        (c.args[1], c.args[2].get("entity_id"), c.args[2].get("value"))
        for c in hass.services.async_call.await_args_list
        if c.args[1] in ("set_value", "turn_on", "turn_off")
    ]


@pytest.mark.asyncio
async def test_after_the_window_end_nothing_reaches_the_inverter_until_noon(night):
    """The field configuration, house connection limit included, from 04:58 to 13:00.

    The morning of 20 October wrote the min SOC every 15 minutes. Here the
    window ends once, then eight hours pass with everything that ran that
    morning: refreshes, the 15-second staleness check, verifications, window
    checks, grid meter readings that come and go, a min SOC entity that blinks,
    and the Kostal reporting its own 22 kW. At 04:58 the min SOC goes back once
    and the charge ceiling at most once, and nothing reaches the inverter after.
    """
    hass, coordinator, clock = night
    coordinator.enable_communication_monitoring()
    # What 03:10 left behind: the Kostal's own 22 kW captured as the original
    coordinator._original_ac_charge_power = 22063.310546875
    device = hass.services.async_call.side_effect

    async def kore_validates_the_range(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        clock.now += timedelta(seconds=1)  # a command takes time; its feedback is newer
        if data.get("entity_id") == AC_LIMIT and service == "set_value" and data["value"] > 12540.0:
            from homeassistant.exceptions import ServiceValidationError
            raise ServiceValidationError("outside valid range 0.0 - 12540.0")
        await device(domain, service, data, **kwargs)

    hass.services.async_call = AsyncMock(side_effect=kore_validates_the_range)
    clock.now = THE_HICCUP.replace(hour=4, minute=58, second=0)
    for entity_id in (BATTERY, MIN_SOC, AC_LIMIT, GRID, GRID_IMPORT):
        hass.states.get(entity_id).last_reported = clock.now

    with patch("homeassistant.components.persistent_notification.async_create"), patch(
        "custom_components.inverter_charge_night.coordinator.async_call_later"
    ) as later:
        await coordinator._on_window_end(clock.now, scheduled=True)
        at_the_end = _inverter_writes(hass)
        # The min SOC once, the charge ceiling at most once - as the entity's maximum
        assert at_the_end.count(("set_value", MIN_SOC, 8.0)) == 1
        assert at_the_end.count(("set_value", AC_LIMIT, 12540.0)) <= 1
        assert len(at_the_end) <= 2
        assert coordinator._pending_reset is False
        assert coordinator._original_ac_charge_power is None
        later.assert_not_called()  # no retry armed

        for step in range(8 * 12):  # every 5 minutes until 13:00
            clock.now += timedelta(minutes=5)
            meter = hass.states.get(GRID_IMPORT)
            meter.state = "unavailable" if step % 7 == 3 else "800"
            meter.last_reported = clock.now
            minimum = hass.states.get(MIN_SOC)
            minimum.state = "unavailable" if step % 11 == 5 else "8.0"
            minimum.last_reported = clock.now
            hass.states.get(BATTERY).last_reported = clock.now
            hass.states.get(AC_LIMIT).state = "22063.310546875"  # the Kostal's own value
            await coordinator._async_update_data()
            await coordinator._verify_and_restore_min_soc()
            await coordinator._check_current_window()
            await coordinator._react_to_grid_import()
            await coordinator._verify_ac_charge_limit()

    assert _inverter_writes(hass) == at_the_end


@pytest.mark.asyncio
async def test_the_regular_poll_retries_an_unconfirmed_stop(night):
    """The poll's target-reached branch is where the bounded retry runs (Codex on #37)."""
    hass, coordinator, _ = night
    coordinator._grid_stop_pending = True  # an earlier stop had no off feedback
    retry = AsyncMock()
    coordinator._retry_grid_stop = retry

    await coordinator._async_calculate_and_control()

    retry.assert_awaited_once()



@pytest.mark.asyncio
async def test_a_raised_target_drops_the_stop_of_the_old_one(night):
    """Codex on #37: the next poll sent turn_off for the old target before charging
    towards the raised one - a relay flip on every raise."""
    hass, coordinator, clock = night
    hass.states.get(GRID).state = "on"

    async def device(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        clock.now += timedelta(seconds=1)
        if service == "set_value":  # number writes hold; only the switch lags
            state = hass.states.get(data["entity_id"])
            state.state = str(data["value"])
            state.last_reported = state.last_updated = clock.now

    hass.services.async_call = AsyncMock(side_effect=device)
    assert await coordinator._stop_grid_charging() is False
    stops = len(hass.services.async_call.await_args_list)
    # The bridge planner raises the target above the SOC
    plan = SimpleNamespace(target_soc=80.0, reason="bridge")
    coordinator._parse_forecast_energy = MagicMock(return_value=(10.0, True))
    coordinator._plan_target = AsyncMock(return_value=plan)
    assert await coordinator._replan_in_window(68.0) == 80.0
    retry = AsyncMock()
    coordinator._retry_grid_stop = retry

    await coordinator._async_calculate_and_control()

    retry.assert_not_awaited()
    assert coordinator._grid_stop_pending is False
    after = [c.args[:2] for c in hass.services.async_call.await_args_list[stops:]]
    assert ("switch", "turn_off") not in after
    # ...and the control path charges towards the raised target instead: the
    # planned limit first, then the floor
    assert [c.args for c in hass.services.async_call.await_args_list[stops:stops + 2]] == [
        ("number", "set_value", {"entity_id": AC_LIMIT, "value": 742.0}),
        ("number", "set_value", {"entity_id": MIN_SOC, "value": 80.0}),
    ]


@pytest.mark.parametrize("soc", ["unavailable", "15"])
@pytest.mark.asyncio
async def test_a_pending_stop_stays_bounded_whatever_the_soc_reads(night, soc):
    """CodeRabbit on #37: an unknown SOC sent turn_off on every poll past the bound,
    and a SOC back below the target skipped the retry altogether."""
    from custom_components.inverter_charge_night.coordinator import GRID_STOP_MAX_RETRIES

    hass, coordinator, clock = night
    hass.states.get(GRID).state = "on"

    async def switch_ignores_turn_off(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        clock.now += timedelta(seconds=1)

    hass.services.async_call = AsyncMock(side_effect=switch_ignores_turn_off)
    assert await coordinator._stop_grid_charging() is False
    hass.states.get(BATTERY).state = soc

    for _ in range(10):  # ten polls
        await coordinator._async_calculate_and_control()

    turn_offs = [c for c in hass.services.async_call.await_args_list
                 if c.args[:2] == ("switch", "turn_off")]
    assert len(turn_offs) == 1 + GRID_STOP_MAX_RETRIES

    # The switch reports off after all: the next poll confirms it without a command
    hass.states.get(GRID).state = "off"
    await coordinator._async_calculate_and_control()
    assert coordinator._grid_stop_pending is False
    assert len([c for c in hass.services.async_call.await_args_list
                if c.args[:2] == ("switch", "turn_off")]) == 1 + GRID_STOP_MAX_RETRIES



@pytest.mark.asyncio
async def test_a_communication_outage_keeps_a_pending_stop_bounded(night):
    """CodeRabbit on #37: the outage guard returned before the poll's retry and sent
    turn_off on every refresh - every 15 seconds while the fault lasted."""
    from custom_components.inverter_charge_night.coordinator import GRID_STOP_MAX_RETRIES

    hass, coordinator, clock = night
    coordinator.enable_communication_monitoring()
    hass.states.get(GRID).state = "on"

    async def switch_ignores_turn_off(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        clock.now += timedelta(seconds=1)

    hass.services.async_call = AsyncMock(side_effect=switch_ignores_turn_off)
    assert await coordinator._stop_grid_charging() is False
    hass.states.get(BATTERY).state = "unavailable"  # a communication fault

    with patch("homeassistant.components.persistent_notification.async_create"):
        for _ in range(20):  # five minutes of 15-second refreshes
            await coordinator._async_update_data()
            clock.now += timedelta(seconds=15)

    turn_offs = [c for c in hass.services.async_call.await_args_list
                 if c.args[:2] == ("switch", "turn_off")]
    assert len(turn_offs) == 1 + GRID_STOP_MAX_RETRIES
