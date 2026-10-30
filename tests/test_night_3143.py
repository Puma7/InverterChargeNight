"""3.14.3: the night of 29/30 October 2026.

The keepalive held the limit all night. Two efficiency trials were thrown
away for the Kostal's slow answer to a raised min SOC:

* 23:01: min SOC 58 %, 5000 W written and read back - and the battery drew
  nothing for seven minutes. Discarded as an idle trial at 23:08; it charged
  later and stood at the floor at 23:31.
* 00:08: min SOC 61 %, 7000 W written. 1038 read the battery's 51 W at rest
  until 00:10:49, the settling ended at 00:10:27: discarded as a start not
  taken. The 7000 W then stood 13 minutes, until the next poll.

The morning still logged the restore of the charge limit as an error, and the
cap to the entity's maximum as a warning, every morning.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.const import AUTO_TEST_SETTLE_S, AUTO_TEST_START_WAIT_S
from tests.test_efficiency_finder import AC_LIMIT, NOW, START, _make, _power


def _trial(hass: Any, coordinator: Any, power_w: int, *, reading: str, commanded: float | None) -> None:
    attributes: dict[str, Any] = {"unit_of_measurement": "W"}
    if commanded is not None:
        attributes["last_commanded_value"] = commanded
    hass.states.async_set(AC_LIMIT, reading, attributes)
    with patch(NOW, return_value=START):
        coordinator._auto_test_active = True
        coordinator._auto_test_power_w = power_w
        coordinator._auto_test_start = START
        coordinator._auto_measure_start = None
        coordinator._auto_test_limit_confirmed = False


def _sample(coordinator: Any, seconds: float) -> None:
    with patch(NOW, return_value=START + timedelta(seconds=seconds)):
        coordinator._accumulate_auto_energy()


def _reading(hass: Any, value: str, commanded: float | None) -> None:
    attributes: dict[str, Any] = {"unit_of_measurement": "W"}
    if commanded is not None:
        attributes["last_commanded_value"] = commanded
    hass.states.async_set(AC_LIMIT, value, attributes)


# --- The trial waits for the battery ------------------------------------------------


def test_the_battery_at_rest_after_the_settling_is_no_failed_start(mock_hass):
    """00:10:27: 1038 still read 51 W, the 7000 W recorded - the trial waits."""
    coordinator = _make(mock_hass)
    _trial(mock_hass, coordinator, 7000, reading="51.5", commanded=7000.0)
    _power(mock_hass, 0, 0)

    _sample(coordinator, AUTO_TEST_SETTLE_S + 1)
    _sample(coordinator, 4 * 60)

    assert coordinator._auto_test_active
    assert coordinator._auto_measure_start is None
    assert coordinator._auto_start_failures is None

    # 00:11: the battery takes the power up - the measurement starts there
    _reading(mock_hass, "7000", 7000.0)
    _power(mock_hass, 7000, 6700)
    _sample(coordinator, 5 * 60)
    assert coordinator._auto_measure_start == START + timedelta(minutes=5)
    assert coordinator._auto_test_limit_confirmed


def test_a_limit_read_back_without_a_draw_waits_too(mock_hass):
    """23:03-23:08: 5000 W read back, the battery took nothing for seven minutes."""
    coordinator = _make(mock_hass)
    _trial(mock_hass, coordinator, 5000, reading="5000", commanded=5000.0)
    _power(mock_hass, 2, 0)

    for minute in range(3, 12):
        _sample(coordinator, minute * 60)
    assert coordinator._auto_test_active
    assert coordinator._auto_measure_start is None

    _power(mock_hass, 5000, 4800)
    _sample(coordinator, 12 * 60)
    assert coordinator._auto_measure_start == START + timedelta(minutes=12)


def test_a_battery_that_never_starts_is_an_idle_trial_not_a_failed_start(mock_hass):
    coordinator = _make(mock_hass)
    _trial(mock_hass, coordinator, 7000, reading="51.5", commanded=7000.0)
    _power(mock_hass, 0, 0)

    _sample(coordinator, AUTO_TEST_SETTLE_S + 1)
    _sample(coordinator, AUTO_TEST_START_WAIT_S)

    assert not coordinator._auto_test_active
    assert "the battery drew 0 W on average" in coordinator._auto_last_result["discarded"]
    assert coordinator._auto_start_failures is None
    assert coordinator._auto_idle_trials is not None and coordinator._auto_idle_trials[1] == 1


def test_power_telemetry_gone_while_waiting_is_a_lost_trial_not_an_idle_one(mock_hass):
    coordinator = _make(mock_hass)
    _trial(mock_hass, coordinator, 7000, reading="51.5", commanded=7000.0)
    _power(mock_hass, 0, 0)
    from tests.test_efficiency_finder import SENT
    mock_hass.states.async_set(SENT, "unavailable")

    _sample(coordinator, AUTO_TEST_SETTLE_S + 1)

    assert not coordinator._auto_test_active
    assert "unavailable at measurement start" in coordinator._auto_last_result["discarded"]
    assert coordinator._auto_idle_trials is None
    assert coordinator._auto_telemetry_losses is not None


def test_a_limit_neither_read_back_nor_recorded_is_still_a_failed_start(mock_hass):
    """KORE before 3.0.16: nothing tells the battery at rest from a write not taken."""
    coordinator = _make(mock_hass)
    _trial(mock_hass, coordinator, 7000, reading="51.5", commanded=None)
    _power(mock_hass, 0, 0)

    _sample(coordinator, AUTO_TEST_SETTLE_S + 1)
    assert coordinator._auto_test_active  # it waits all the same
    _sample(coordinator, AUTO_TEST_START_WAIT_S)

    assert not coordinator._auto_test_active
    assert "no matching feedback" in coordinator._auto_last_result["discarded"]
    assert coordinator._auto_start_failures is not None and coordinator._auto_start_failures[1] == 1


def test_a_battery_charging_under_less_than_the_trial_is_another_power(mock_hass):
    """Charging, but 1038 allows only 4000 of the 7000 W: not this power's sample."""
    coordinator = _make(mock_hass)
    _trial(mock_hass, coordinator, 7000, reading="4000", commanded=7000.0)
    _power(mock_hass, 4000, 3800)

    _sample(coordinator, AUTO_TEST_SETTLE_S + 1)
    assert coordinator._auto_test_active
    _sample(coordinator, AUTO_TEST_START_WAIT_S)

    assert not coordinator._auto_test_active
    assert "may take only 4000 W of the trial's 7000 W" in coordinator._auto_last_result["discarded"]
    assert coordinator._auto_start_failures is None
    with patch(NOW, return_value=START):
        assert coordinator._auto_search_stopped() is None
        assert coordinator.get_auto_efficiency_data()["failed"] == {"7000": 1}


def test_the_battery_reaching_its_floor_during_a_trial_is_no_lowered_limit(mock_hass):
    """The record stands: 51 W is the battery at rest, not somebody's limit."""
    coordinator = _make(mock_hass)
    _trial(mock_hass, coordinator, 3000, reading="3000", commanded=3000.0)
    _power(mock_hass, 3000, 2900)
    _sample(coordinator, AUTO_TEST_SETTLE_S + 1)
    assert coordinator._auto_measure_start is not None

    _reading(mock_hass, "51", 3000.0)
    _sample(coordinator, AUTO_TEST_SETTLE_S + 31)

    assert not coordinator._auto_test_active
    assert "may take only 51 W" in coordinator._auto_last_result["discarded"]
    with patch(NOW, return_value=START):
        assert coordinator._auto_search_stopped() is None


@pytest.mark.parametrize(("commanded", "waiting"), [(7000.0, True), (None, True)])
@pytest.mark.asyncio
async def test_the_verification_leaves_a_waiting_trial_alone(mock_hass, commanded, waiting):
    from tests.test_electrical_safety import INSIDE, _make as safety_make, _register
    from tests.test_electrical_safety import NOW as SAFETY_NOW, GRID

    with patch(SAFETY_NOW, return_value=INSIDE):
        _register(mock_hass)
        mock_hass.states.async_set(GRID, "on")
        coordinator = safety_make(mock_hass)
        coordinator.is_active = True
        coordinator.auto_efficient_charge = True
        coordinator._planned_setpoint_written_w = 7000
        coordinator._auto_test_active = True
        coordinator._auto_test_power_w = 7000
        coordinator._auto_test_start = INSIDE - timedelta(minutes=3)
        coordinator._auto_test_limit_confirmed = False
        from tests.test_electrical_safety import AC_LIMIT as SAFETY_LIMIT
        attributes: dict[str, Any] = {"unit_of_measurement": "W"}
        if commanded is not None:
            attributes["last_commanded_value"] = commanded
        mock_hass.states.async_set(SAFETY_LIMIT, "51", attributes)

        await coordinator._verify_ac_charge_limit()

    assert coordinator._auto_test_active is waiting
    assert coordinator._auto_start_failures is None


# --- A trial that ends between polls hands the limit back at once -------------------


@pytest.mark.asyncio
async def test_a_trial_discarded_by_the_sampler_asks_for_the_plan_at_once(mock_hass):
    """00:10:27 the trial ended, the 7000 W stood until the poll at 00:23."""
    coordinator = _make(mock_hass)
    coordinator.async_request_refresh = AsyncMock()
    _trial(mock_hass, coordinator, 7000, reading="51.5", commanded=None)
    _power(mock_hass, 0, 0)
    captured: dict[str, Any] = {}

    def _capture(hass: Any, entities: Any, callback: Any) -> MagicMock:
        captured["callback"] = callback
        return MagicMock()

    with patch("custom_components.inverter_charge_night.coordinator.async_track_state_change_event",
               side_effect=_capture):
        coordinator._setup_auto_sample_listener()

    with patch(NOW, return_value=START + timedelta(seconds=AUTO_TEST_SETTLE_S + 1)):
        await captured["callback"](MagicMock())
    coordinator.async_request_refresh.assert_not_awaited()  # still waiting

    with patch(NOW, return_value=START + timedelta(seconds=AUTO_TEST_START_WAIT_S)):
        await captured["callback"](MagicMock())
    assert not coordinator._auto_test_active
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_trial_ended_by_the_15_s_check_asks_for_the_plan_at_once(mock_hass):
    coordinator = _make(mock_hass)
    coordinator.async_request_refresh = AsyncMock()
    _trial(mock_hass, coordinator, 7000, reading="51.5", commanded=None)
    _power(mock_hass, 0, 0)
    module = "custom_components.inverter_charge_night.coordinator"
    with patch(f"{module}.async_track_time_interval") as interval, \
            patch(f"{module}.async_track_state_change_event"):
        coordinator.setup_communication_listener()
    check = interval.call_args.args[1]

    when = START + timedelta(seconds=AUTO_TEST_START_WAIT_S)
    with patch(NOW, return_value=when):
        await check(when)

    assert not coordinator._auto_test_active
    coordinator.async_request_refresh.assert_awaited_once()


# --- The morning ---------------------------------------------------------------------


def test_the_cap_to_the_entity_maximum_is_routine(caplog):
    from custom_components.inverter_charge_night.coordinator import _within_entity_range

    state = MagicMock()
    state.attributes = {"min": 0, "max": 12540}
    caplog.set_level(logging.INFO, logger="custom_components.inverter_charge_night")

    assert _within_entity_range(state, 22047.291, "number.limit", "Reset AC charge limit") == 12540
    assert _within_entity_range(state, -5.0, "number.limit", "Reset AC charge limit") == 0

    levels = [r.levelname for r in caplog.records if "lies outside" in r.getMessage()]
    assert levels == ["INFO", "WARNING"]


@pytest.mark.parametrize(("retries", "level"), [(0, "INFO"), (1, "ERROR")])
@pytest.mark.asyncio
async def test_the_first_restore_without_fresh_feedback_is_routine(mock_hass, caplog, retries, level):
    """04:58:07: an ERROR, though the min SOC was back and the retry at 04:59:10
    found the limit restored - _pending_reset is set once the min SOC is back."""
    from tests.test_reset_loop import CONFIG as RESET_CONFIG, _make_coordinator, _register_field_entities

    _register_field_entities(mock_hass)
    coordinator = _make_coordinator(mock_hass, RESET_CONFIG, {})
    coordinator._pending_reset = True  # the min SOC restore has set it
    coordinator._reset_retry_count = retries
    coordinator._original_ac_charge_power = 12540.0
    coordinator._ac_limit_already_restored = AsyncMock(return_value=False)  # type: ignore[method-assign]
    coordinator._write_ac_charge_limit = AsyncMock(return_value=True)  # type: ignore[method-assign]
    coordinator._confirm_ac_limit_restore = AsyncMock(return_value=False)  # type: ignore[method-assign]
    caplog.set_level(logging.INFO, logger="custom_components.inverter_charge_night")

    await coordinator._reset_ac_charge_limit()

    [record] = [r for r in caplog.records if "no fresh matching feedback" in r.getMessage()]
    assert record.levelname == level
