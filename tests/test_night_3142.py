"""3.14.2: the night of 28/29 October 2026.

The first night the efficiency search measured anything: 6.69 % loss at
1000 W, 2.97 % at 3000 W. Three things went wrong around it:

* The Kostal's limit register reads what the battery takes right now. At the
  target it read about 50 W, the keepalive took that for somebody's lower limit
  and stopped at 00:26 - the 5000 W written was not held until 03:10.
* The first trial fixed the battery side's sign from one reading, taken before
  the battery charged, and read a loss of 193 %.
* Every morning the restore of the charge limit was logged as an error and
  left "write unconfirmed" standing for 15 minutes after it had succeeded.
"""
from __future__ import annotations

from typing import Any

import pytest

from custom_components.inverter_charge_night.const import CONF_CHARGE_POWER_RECEIVED_ENTITY
from tests.test_calendar_fault_campaign import rig  # noqa: F401 - fixture
from tests.test_forced_charge_night import _calls, _charging, night  # noqa: F401 - fixture
from tests.test_grid_limit import AC_LIMIT, RECEIVED


def _with_battery_power(hass: Any, coordinator: Any, battery_w: str) -> None:
    coordinator.config = {**coordinator.config, CONF_CHARGE_POWER_RECEIVED_ENTITY: RECEIVED}
    hass.states.async_set(RECEIVED, battery_w, {"unit_of_measurement": "W"})


# --- The keepalive and the battery at rest ------------------------------------------


@pytest.mark.parametrize("battery_w", [None, "0", "-12"])
@pytest.mark.asyncio
async def test_a_battery_at_rest_is_no_lower_limit(night, battery_w):
    """00:31: the limit read 54 W, the battery took nothing - the 1000 W is kept."""
    hass, coordinator, clock = night
    _charging(hass, coordinator)
    if battery_w is not None:
        _with_battery_power(hass, coordinator, battery_w)
    hass.states.get(AC_LIMIT).state = "54"

    await coordinator._keep_charge_limit_alive()

    assert _calls(hass) == [("number", "set_value", AC_LIMIT)]
    assert hass.services.async_call.await_args_list[0].args[2]["value"] == 1000.0


@pytest.mark.parametrize("battery_w", ["-500", "500"])
@pytest.mark.asyncio
async def test_a_battery_charging_at_the_lower_limit_is_left_alone(night, battery_w):
    """Someone's 500 W - the battery charges at it, either sign: not written over."""
    hass, coordinator, clock = night
    _charging(hass, coordinator)
    _with_battery_power(hass, coordinator, battery_w)
    hass.states.get(AC_LIMIT).state = "500"

    await coordinator._keep_charge_limit_alive()

    assert _calls(hass) == []


@pytest.mark.parametrize(("reading", "battery_w"), [("800", "0"), ("300", "0"), ("450", "-150")])
@pytest.mark.asyncio
async def test_a_lower_reading_the_battery_does_not_charge_at_is_kept(night, reading, battery_w):
    """800 W read while the battery rests: the Kostal's allowance, not a limit.

    Without KORE's record of the command. 300 W read and nothing drawn sits
    right at the tolerance (CodeRabbit on #48).
    """
    hass, coordinator, clock = night
    _charging(hass, coordinator)
    _with_battery_power(hass, coordinator, battery_w)
    hass.states.get(AC_LIMIT).state = reading

    await coordinator._keep_charge_limit_alive()

    assert _calls(hass) == [("number", "set_value", AC_LIMIT)]


# --- The sign of the battery side, judged when the battery charges -------------------


def _rest_then_charge(coordinator: Any, hass: Any, *, rest_samples: int, ramp_w: float = 0.0,
                      minutes: int = 20) -> Any:
    """23:03: the trial settled while the battery still rested, then it took 1000 W.

    The Kostal reports that as -1000 W; the AC side read about 1072 W.
    """
    from datetime import timedelta
    from unittest.mock import patch

    from custom_components.inverter_charge_night.const import AUTO_TEST_SETTLE_S
    from tests.test_efficiency_finder import NOW, RECEIVED as BATTERY_SIDE, START
    from tests.test_efficiency_measurement_017 import GRID, HOUSE

    hass.states.async_set(AC_LIMIT, "1000", {"unit_of_measurement": "W"})
    coordinator._auto_test_active = True
    coordinator._auto_test_power_w = 1000
    coordinator._auto_test_start = START
    coordinator._auto_measure_start = None
    coordinator._auto_last_sample_time = None

    def _set(charging: bool) -> None:
        # At rest (or still ramping up) the grid carries the house and the ramp
        battery_w = 1000.0 if charging else ramp_w
        hass.states.async_set(GRID, str(500 + battery_w * 1.072), {"unit_of_measurement": "W"})
        hass.states.async_set(HOUSE, "500", {"unit_of_measurement": "W"})
        hass.states.async_set(BATTERY_SIDE, str(-battery_w), {"unit_of_measurement": "W"})

    now = START + timedelta(seconds=AUTO_TEST_SETTLE_S + 1)
    for i in range(rest_samples + minutes * 2 + 1):
        _set(i > rest_samples)
        with patch(NOW, return_value=now):
            coordinator._accumulate_auto_energy()
        now += timedelta(seconds=30)
    return now - timedelta(seconds=30)


@pytest.mark.parametrize(("rest_samples", "ramp_w"), [(0, 0.0), (3, 0.0), (4, 150.0)])
def test_a_trial_that_settles_at_rest_still_turns_the_kostal_sign(mock_hass, rest_samples, ramp_w):
    """23:23: a loss of 193 % - the sign was fixed from the battery at rest."""
    from unittest.mock import patch

    from tests.test_efficiency_finder import NOW, RECEIVED as BATTERY_SIDE
    from tests.test_efficiency_measurement_017 import _derived

    coordinator = _derived(mock_hass)
    end = _rest_then_charge(coordinator, mock_hass, rest_samples=rest_samples, ramp_w=ramp_w)
    with patch(NOW, return_value=end):
        coordinator._finalize_auto_test()

    loss = coordinator.get_auto_efficiency_data()["history"]["1000"]
    # The ramp counts with the sign found later: summed as it read, the loss was 9.5 %
    assert loss == pytest.approx(1 - 1000 / 1072, abs=0.005)
    assert coordinator._power_signs == {BATTERY_SIDE: -1.0}


def test_the_sign_seen_once_is_kept_for_the_next_night(mock_hass):
    from tests.test_efficiency_finder import RECEIVED as BATTERY_SIDE
    from tests.test_efficiency_measurement_017 import _derived

    coordinator = _derived(mock_hass)
    coordinator._power_signs = {BATTERY_SIDE: -1.0}
    coordinator._auto_test_power_w = 1000

    # At rest the remembered sign stands in, still to be judged
    assert coordinator._charge_orientation(0.0, "received") == (-1.0, False)
    coordinator._persist_state()
    again = _derived(mock_hass)
    again.entry.options = coordinator.entry.options
    again._restore_state()
    assert again._power_signs == {BATTERY_SIDE: -1.0}


# --- The morning: a slow reading is no interrupted communication ---------------------


@pytest.mark.parametrize(("retries", "switch", "flagged"), [
    (0, "off", False),  # the window end, the reading half a minute away
    (1, "off", True),   # the first retry came back unconfirmed too
    (0, "on", True),    # grid charging is not off: said at once
])
@pytest.mark.asyncio
async def test_a_pending_restore_is_reported_after_its_first_retry(rig, retries, switch, flagged):
    from unittest.mock import patch

    from tests.test_grid_limit import GRID

    hass, co, _ = rig
    co.enable_communication_monitoring()
    hass.states.get(GRID).state = switch
    co._pending_reset = True
    co._reset_retry_count = retries

    with patch("homeassistant.components.persistent_notification.async_create") as notify:
        co._update_communication_health()

    assert ("write_unconfirmed" in co.communication_diagnostics().get("faults", [])) is flagged
    assert notify.called is flagged


@pytest.mark.asyncio
async def test_the_retry_that_succeeds_says_recovered_at_once(rig):
    """04:59:09 the retry succeeded, 05:13:06 the status said so - now at 04:59:09."""
    from unittest.mock import patch

    hass, co, _ = rig
    co.enable_communication_monitoring()
    co._pending_reset = True
    co._reset_retry_count = 1
    with patch("homeassistant.components.persistent_notification.async_create"):
        co._update_communication_health()
        assert co.communication_diagnostics()["faults"] == ["write_unconfirmed"]

        co._record_reset_outcome(True)

    assert co.communication_diagnostics()["state"] == "healthy"


@pytest.mark.parametrize(("retries", "level"), [(0, "INFO"), (1, "WARNING")])
def test_the_first_retry_is_routine(rig, caplog, retries, level):
    import logging
    from unittest.mock import MagicMock, patch

    hass, co, _ = rig
    co._reset_retry_count = retries
    caplog.set_level(logging.INFO, logger="custom_components.inverter_charge_night")
    with patch("custom_components.inverter_charge_night.coordinator.async_call_later",
               MagicMock(return_value=lambda: None)):
        co._schedule_reset_retry()

    [record] = [r for r in caplog.records if "not reset yet" in r.getMessage()]
    assert record.levelname == level


def test_the_ramp_blocks_do_not_count_as_uncertainty(mock_hass):
    """Codex on #48: two ramp blocks under the wrong sign, then eighteen right ones."""
    from statistics import pstdev

    from tests.test_efficiency_measurement_017 import _derived

    coordinator = _derived(mock_hass)
    _rest_then_charge(coordinator, mock_hass, rest_samples=4, ramp_w=150.0)

    losses = coordinator._auto_block_losses
    assert losses
    assert all(loss == pytest.approx(1 - 1000 / 1072, abs=0.005) for loss in losses)
    assert pstdev(losses) < 0.001


# --- KORE's record of the last command (Codex on #48) --------------------------------


def _commanded(hass: Any, reading: str, commanded: float) -> None:
    attributes = {**hass.states.get(AC_LIMIT).attributes, "last_commanded_value": commanded}
    hass.states.async_set(AC_LIMIT, reading, attributes)


@pytest.mark.parametrize(("reading", "commanded", "battery_w"), [
    ("0", 0.0, "0"),          # the user's stop: the battery takes nothing, as told
    ("54", 0.0, "0"),         # the same stop, read as the Kostal's 50 W allowance
    ("800", 800.0, "0"),      # 800 W in a charging pause
    ("800", 800.0, "-310"),   # 800 W, the battery takes less than it may
    ("998.5", 0.0, "-1000"),  # the stop is recorded, 1038 not read again yet (CodeRabbit on #48)
])
@pytest.mark.asyncio
async def test_a_lower_command_is_left_alone_whatever_the_battery_draws(night, reading,
                                                                         commanded, battery_w):
    hass, coordinator, clock = night
    _charging(hass, coordinator)
    _with_battery_power(hass, coordinator, battery_w)
    _commanded(hass, reading, commanded)

    await coordinator._keep_charge_limit_alive()

    assert _calls(hass) == []


@pytest.mark.parametrize(("reading", "battery_w"), [("54", "0"), ("500", "-500")])
@pytest.mark.asyncio
async def test_the_own_command_standing_is_kept_alive(night, reading, battery_w):
    """The 1000 W written is the last command: the lower reading is the battery's allowance."""
    hass, coordinator, clock = night
    _charging(hass, coordinator)
    _with_battery_power(hass, coordinator, battery_w)
    _commanded(hass, reading, 1000.0)

    await coordinator._keep_charge_limit_alive()

    assert _calls(hass) == [("number", "set_value", AC_LIMIT)]
    assert hass.services.async_call.await_args_list[0].args[2]["value"] == 1000.0


@pytest.mark.parametrize("attributes", [
    {"max": 22.0},                       # no unit, a maximum below 1000: kW
    {"unit_of_measurement": "kW"},       # labelled kW (Codex on #48)
])
def test_a_command_in_kilowatts_counts_in_watts(mock_hass, attributes):
    from tests.test_efficiency_measurement_017 import _derived

    coordinator = _derived(mock_hass)
    mock_hass.states.async_set("number.kw_limit", "0.05", {**attributes, "last_commanded_value": 1.0})

    assert coordinator._commanded_ac_charge_limit_w("number.kw_limit") == 1000.0


@pytest.mark.asyncio
async def test_a_lower_command_that_lapsed_gets_the_limit_back(night):
    """Somebody's 0 W stop, not kept alive: the Kostal reads its own 22 kW again.

    Not on the first look: 1038 is read every 30 s, the 22 kW may be the
    reading from before the stop (CodeRabbit on #48).
    """
    from datetime import timedelta
    from unittest.mock import patch

    from homeassistant.util import dt as dt_util

    from custom_components.inverter_charge_night.coordinator import LOWER_COMMAND_SETTLE_S

    hass, coordinator, clock = night
    _charging(hass, coordinator)
    _commanded(hass, "22027.8", 0.0)

    now = dt_util.now()
    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now", return_value=now):
        await coordinator._keep_charge_limit_alive()
    assert _calls(hass) == []

    later = now + timedelta(seconds=LOWER_COMMAND_SETTLE_S)
    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now", return_value=later):
        await coordinator._keep_charge_limit_alive()

    assert _calls(hass) == [("number", "set_value", AC_LIMIT)]
    assert hass.services.async_call.await_args_list[0].args[2]["value"] == 1000.0


@pytest.mark.asyncio
async def test_a_new_stop_after_the_own_write_gets_its_own_settling(night):
    """Codex on #48: a 0 W stop lapsed, the keepalive wrote the limit again -
    and before the next round a new 0 W stop, 1038 still at the old 22 kW.

    The new stop waits its own minute instead of inheriting the old one's.
    """
    from datetime import timedelta
    from unittest.mock import patch

    from homeassistant.util import dt as dt_util

    from custom_components.inverter_charge_night.coordinator import LOWER_COMMAND_SETTLE_S

    hass, coordinator, clock = night
    _charging(hass, coordinator)
    now = dt_util.now()
    coordinator._lower_command_seen = (0.0, now - timedelta(seconds=10 * LOWER_COMMAND_SETTLE_S))
    _commanded(hass, "22027.8", 0.0)
    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now", return_value=now):
        await coordinator._keep_charge_limit_alive()  # the lapse: written again
        assert _calls(hass) == [("number", "set_value", AC_LIMIT)]
        hass.services.async_call.reset_mock()

        await coordinator._keep_charge_limit_alive()  # the new stop, same value

    assert _calls(hass) == []


@pytest.mark.parametrize("accepted", [True, False])
@pytest.mark.asyncio
async def test_any_own_write_supersedes_a_stop_seen_before(night, accepted):
    """KORE records a rejected write as the last command as well."""
    from homeassistant.util import dt as dt_util

    hass, coordinator, clock = night
    _charging(hass, coordinator)
    if accepted:
        hass.services.async_call.side_effect = None
    coordinator._lower_command_seen = (0.0, dt_util.now())

    await coordinator._write_ac_charge_limit(AC_LIMIT, "number", 1000.0, "Set AC charge limit")

    assert coordinator._lower_command_seen is None


@pytest.mark.asyncio
async def test_a_new_window_forgets_the_stop_seen_last_night(mock_hass):
    from unittest.mock import patch

    from homeassistant.util import dt as dt_util

    from tests import test_discharge_block as window
    from tests.test_grid_limit import NOW

    window._register(mock_hass, battery="70")
    made = window._make(mock_hass)
    made._lower_command_seen = (0.0, dt_util.now())
    try:
        with patch(NOW, return_value=window.INSIDE_WINDOW):
            await made._on_window_start(window.WINDOW_START)
        assert made._lower_command_seen is None
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_the_keepalive_stopping_forgets_the_stop_seen(mock_hass):
    """CodeRabbit on #48: the settling belongs to one run of the keepalive."""
    from homeassistant.util import dt as dt_util

    from tests import test_discharge_block as window

    window._register(mock_hass, battery="70")
    made = window._make(mock_hass)
    made._lower_command_seen = (0.0, dt_util.now())

    await made._stop_periodic_verification()

    assert made._lower_command_seen is None
