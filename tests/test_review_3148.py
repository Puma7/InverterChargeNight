"""3.14.8: a review of what 3.14.6 and 3.14.7 added, before a night finds it.

Stall watch: the hold, the nudge's bookkeeping and the efficiency search.
PV yield: a fog day stays a fog day, the armed day's figures, the day's
temperature samples stored every hour.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import coordinator as coordinator_module
from custom_components.inverter_charge_night.heating import TemperatureLog
from custom_components.inverter_charge_night.pv_yield import PVYieldMonitor
from tests.test_efficiency_finder import CONFIG as FINDER_CONFIG, NOW as FINDER_NOW, _measure, finder  # noqa: F401
from tests.test_night_3147 import AC_LIMIT, ISSUE, MIN_SOC, _poll, _set_values, _start, stuck  # noqa: F401
from tests.test_pv_yield import DAY, NOW, RISE, SET, make_co


# --- the stall watch --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_wallbox_squeeze_does_not_become_the_nights_hold(stuck):
    """The connection limit cut the limit to 300 W for a wallbox just before
    the poll that found the stall: the hold is what the plan asked for."""
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    for _ in range(2):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    planned = coordinator._grid_plan_request_w
    assert planned is not None and planned > 300
    coordinator._planned_setpoint_written_w = 300.0  # _react_to_grid_import between polls
    hass.states.async_set(AC_LIMIT, "300", {"unit_of_measurement": "W"})
    clock.now += timedelta(minutes=15)
    await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True
    assert coordinator._stall_hold_w == pytest.approx(planned)


@pytest.mark.asyncio
async def test_a_failed_raise_after_the_nudge_keeps_the_stall_and_puts_the_floor_back(stuck):
    """The nudge's second write times out: the floor is the floor, not 17 %."""
    hass, coordinator, clock = stuck
    original = hass.services.async_call.side_effect
    writes = {"n": 0}

    async def flaky(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value" and data["entity_id"] == MIN_SOC:
            writes["n"] += 1
            if writes["n"] == 2:
                raise TimeoutError("Modbus timeout")
        return await original(domain, service, data, **kwargs)

    hass.services.async_call.side_effect = flaky
    coordinator.is_enabled = True
    await _start(hass, coordinator, clock)
    for _ in range(3):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True
    hold = coordinator._stall_hold_w
    assert coordinator._last_soc_set == 100.0
    # The verification put the floor back at once, a third write
    assert _set_values(hass, MIN_SOC) == [17.0, 100.0, 100.0]
    assert hass.states.get(MIN_SOC).state == "100.0"
    clock.now += timedelta(minutes=15)
    await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True
    assert coordinator.planned_charge_power_w == hold


@pytest.mark.asyncio
async def test_with_the_floor_not_back_the_watch_still_reads_the_floor(stuck):
    """The raise and the restore both fail: the min SOC stays at 17 % until the
    next poll, but the watch must not take that for "no longer has to charge"."""
    hass, coordinator, clock = stuck
    original = hass.services.async_call.side_effect
    writes = {"n": 0}

    async def failing(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value" and data["entity_id"] == MIN_SOC:
            writes["n"] += 1
            if writes["n"] >= 2:
                raise TimeoutError("Modbus timeout")
        return await original(domain, service, data, **kwargs)

    hass.services.async_call.side_effect = failing
    coordinator.is_enabled = True
    await _start(hass, coordinator, clock)
    for _ in range(3):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True
    assert coordinator._last_soc_set == 100.0
    hold = coordinator._stall_hold_w
    clock.now += timedelta(minutes=15)
    await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True and coordinator.planned_charge_power_w == hold


@pytest.mark.asyncio
async def test_no_efficiency_trial_starts_while_the_battery_does_not_charge(mock_hass, finder):  # noqa: F811
    """A trial at up to the maximum would undo the stall's hold."""
    mock_hass.states.async_set("switch.grid", "on")
    finder.entry.data = {**FINDER_CONFIG, "grid_charge_switch": "switch.grid"}
    finder.config = finder.entry.data
    finder.auto_efficient_charge = True
    finder.target_reached = False
    end = _measure(finder, mock_hass, power_w=10000, samples=[(10000, 9000)] * 2, minutes=60)
    finder._start_auto_test = AsyncMock()
    finder._set_ac_charge_limit_w = AsyncMock()
    finder._enough_charge_left_for = MagicMock(return_value=True)
    finder._charge_stalled, finder._stall_hold_w = True, 3000.0
    with patch(FINDER_NOW, return_value=end):
        await finder._handle_auto_charge()
    # The finished trial is still recorded; nothing new is written
    assert finder.get_auto_efficiency_data()["history"]["10000"] == pytest.approx(0.1)
    finder._start_auto_test.assert_not_awaited()
    finder._set_ac_charge_limit_w.assert_not_awaited()
    assert finder._auto_wait_reason == "battery_not_charging"


# --- the PV yield -----------------------------------------------------------------

def test_a_fog_day_judged_at_sunset_is_not_armed_by_a_cold_evening():
    """7.4 °C at sunset is fog; the clear evening pulling the mean to 6.9 °C is not snow."""
    monitor = PVYieldMonitor()
    monitor.observe(RISE, RISE, SET, 20, 0)
    assert monitor.observe(SET, RISE, SET, 20, 1, 7.4) is False
    assert monitor.status == "low_yield_unconfirmed" and monitor.day_mean_c == 7.4
    assert monitor.observe(SET + timedelta(hours=5, minutes=45), RISE, SET, 20, 1, 6.9) is False
    assert monitor.charge_day is None


@pytest.mark.asyncio
async def test_a_window_after_midnight_logs_the_armed_days_figures(mock_hass, caplog):
    co = make_co(mock_hass)
    co.config["start_time"], co.config["end_time"] = "00:30", "05:00"
    co._pv_yield.low_day = DAY - timedelta(days=1)
    co._pv_yield.observe(RISE, RISE, SET, 20.637, 0)
    assert co._pv_yield.observe(SET, RISE, SET, 20.637, 1.0049)
    after = (RISE + timedelta(days=1)).replace(hour=0, minute=30)
    co._pv_yield.observe(after, RISE + timedelta(days=1), SET + timedelta(days=1), None, None)
    restored = PVYieldMonitor(co._pv_yield.snapshot())  # a restart after midnight
    assert restored.armed_kwh == pytest.approx((1.0049, 20.637))
    with patch(NOW, return_value=after), caplog.at_level(logging.INFO):
        assert co._automatic_pv_shortfall_active()
        await co._calculate_initial_soc()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Very low")]
    assert lines == ["Very low PV yield on 2026-01-15 (1.0 of 20.6 kWh, the second dark day in a row): "
                     "charging to 95%"]


def test_the_day_so_far_is_stored_every_hour(mock_hass):
    """Only a finished day was stored: a restart at 15:00 kept the night's samples."""
    co = make_co(mock_hass)
    co.config["outdoor_temperature_entity"] = "sensor.outside"
    saved: list[dict[str, Any]] = []
    co._persist_state.side_effect = lambda: saved.append(co._temperature_log.snapshot())
    start = RISE.replace(hour=0)
    for i in range(61):  # 00:00 .. 15:00, every 15 minutes
        at = start + timedelta(minutes=15 * i)
        mock_hass.states.async_set("sensor.outside", "2" if at.hour < 9 else "12", {"unit_of_measurement": "°C"})
        with patch(NOW, return_value=at):
            co._observe_outdoor_temperature()
    live = co._temperature_log
    restored = TemperatureLog(saved[-1])
    assert live.today_mean(DAY) is not None
    assert restored._count >= live._count - 3  # at most the last hour is lost
    assert len(saved) == 61 // 4  # once an hour, not every poll


@pytest.mark.asyncio
async def test_a_restart_after_the_window_drops_the_hold_and_keeps_the_notice(stuck):
    """Home Assistant was down at the window end: the startup reset ends the stall too."""
    hass, coordinator, clock = stuck
    await _start(hass, coordinator, clock)
    for _ in range(3):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True
    coordinator.is_active = False  # restarted at noon, the window long over
    clock.now = clock.now.replace(hour=12)
    coordinator._inverter_still_holds_our_settings = MagicMock(return_value=True)
    coordinator._reset_settings = AsyncMock(return_value=True)
    hass.deleted_issue.reset_mock()
    await coordinator._check_current_window()
    coordinator._reset_settings.assert_awaited_once()
    assert coordinator._charge_stalled is False and coordinator._stall_hold_w is None
    assert ISSUE not in [c.args[2] for c in hass.deleted_issue.call_args_list]


# --- Sourcery on #54 -------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_low_write_that_timed_out_but_went_through_is_put_back(stuck):
    """The low write reached the inverter and then timed out: the floor goes back at once."""
    hass, coordinator, clock = stuck
    original = hass.services.async_call.side_effect
    writes = {"n": 0}

    async def late(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        await original(domain, service, data, **kwargs)
        if service == "set_value" and data["entity_id"] == MIN_SOC:
            writes["n"] += 1
            if writes["n"] == 1:
                raise TimeoutError("Modbus timeout after the write")

    hass.services.async_call.side_effect = late
    coordinator.is_enabled = True
    await _start(hass, coordinator, clock)
    for _ in range(3):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True
    assert _set_values(hass, MIN_SOC) == [17.0, 100.0]  # the restore, not a second nudge
    assert hass.states.get(MIN_SOC).state == "100.0"


@pytest.mark.asyncio
async def test_a_low_write_that_failed_before_the_inverter_writes_nothing_more(stuck):
    """Nothing went through: the verification reads 100 % and writes nothing."""
    hass, coordinator, clock = stuck
    original = hass.services.async_call.side_effect

    async def refused(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if service == "set_value" and data["entity_id"] == MIN_SOC and data["value"] == 17.0:
            raise TimeoutError("Modbus timeout")
        return await original(domain, service, data, **kwargs)

    hass.services.async_call.side_effect = refused
    coordinator.is_enabled = True
    await _start(hass, coordinator, clock)
    for _ in range(3):
        clock.now += timedelta(minutes=15)
        await _poll(hass, coordinator, clock, 17.0)
    assert coordinator._charge_stalled is True
    assert _set_values(hass, MIN_SOC) == [17.0]  # the failed attempt, no restore write
    assert hass.states.get(MIN_SOC).state == "100.0"


@pytest.mark.asyncio
async def test_a_nudge_cancelled_while_settling_hands_the_floor_to_a_task(stuck):
    """Cancelled between the two writes: the floor is put back by a task of its own."""
    hass, coordinator, clock = stuck
    coordinator.is_enabled = True
    await _start(hass, coordinator, clock)
    created: list[Any] = []
    hass.async_create_task = MagicMock(side_effect=lambda coro, **kwargs: created.append(coro))
    with (patch.object(coordinator_module, "STALL_NUDGE_SETTLE_S", 12),
          patch.object(coordinator_module.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))):
        with pytest.raises(asyncio.CancelledError):
            await coordinator._nudge_min_soc(17.0, 100.0)
    assert coordinator._nudging_min_soc is False
    assert hass.states.get(MIN_SOC).state == "17.0"
    assert len(created) == 1
    await created[0]
    assert _set_values(hass, MIN_SOC) == [17.0, 100.0]
    assert hass.states.get(MIN_SOC).state == "100.0"


def test_the_day_so_far_is_stored_by_the_clock_not_by_the_polls():
    """An hourly update interval stores every hour, and an outage stores nothing."""
    log = TemperatureLog()
    start = datetime(2026, 1, 14, 0, 0)
    stored = []
    for hour in range(6):
        at = start + timedelta(hours=hour)
        log.observe(at.date(), 3.0)
        stored.append(log.take_storage_checkpoint(at))
    assert stored == [False, True, True, True, True, True]  # the first sets the clock
    # A sensor outage: no samples, no writes, however long it lasts
    for minutes in range(15, 300, 15):
        at = start + timedelta(hours=5, minutes=minutes)
        log.observe(at.date(), None)
        assert log.take_storage_checkpoint(at) is False
    # Fifteen-minute polls: once an hour
    log2 = TemperatureLog()
    fired = 0
    for i in range(16):
        at = start + timedelta(minutes=15 * i)
        log2.observe(at.date(), 3.0)
        fired += log2.take_storage_checkpoint(at)
    assert fired == 3  # 01:00, 02:00, 03:00


def test_a_restored_day_stores_only_new_samples():
    """The restored count is what storage already holds."""
    log = TemperatureLog({"days": {}, "today": {"day": "2026-01-14", "sum": 12.0, "count": 4}})
    at = datetime(2026, 1, 14, 9, 0)
    assert log.take_storage_checkpoint(at) is False
    assert log.take_storage_checkpoint(at + timedelta(hours=2)) is False  # nothing new
    log.observe(at.date(), 3.0)
    assert log.take_storage_checkpoint(at + timedelta(hours=2)) is False  # sets the clock
    log.observe(at.date(), 3.0)
    assert log.take_storage_checkpoint(at + timedelta(hours=3)) is True


@pytest.mark.parametrize("raw", [
    {"armed_kwh": [10 ** 400, 20.0]},
    {"armed_kwh": [1.0, 10 ** 400]},
    {"expected_kwh": 10 ** 400, "actual_kwh": 10 ** 400},
    {"day_mean_c": 10 ** 400},
])
def test_an_unrepresentable_stored_number_is_malformed(raw):
    """A JSON integer too large for a float must not stop the coordinator."""
    monitor = PVYieldMonitor(raw)
    assert monitor.armed_kwh is None and monitor.expected_kwh is None
    assert monitor.actual_kwh is None and monitor.day_mean_c is None


def test_a_decision_of_3_14_7_keeps_its_figures_after_midnight():
    """3.14.7 stored no armed figures: the day's own are taken before midnight resets them."""
    monitor = PVYieldMonitor({
        "day": DAY.isoformat(), "expected_kwh": 20.6, "actual_kwh": 1.0,
        "charge_day": (DAY + timedelta(days=1)).isoformat(), "status": "low_yield",
        "low_day": DAY.isoformat(), "trigger": "second_day",
    })
    assert monitor.armed_kwh == (1.0, 20.6)
    assert PVYieldMonitor({"charge_day": DAY.isoformat(), "trigger": "cold"}).armed_kwh is None
