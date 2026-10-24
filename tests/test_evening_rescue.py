"""The high-price rescue (plan 013, part two; reworked in plan 016).

The day after a forecast that was too good: snow on the panels with nobody
having set the snow nights. By mid-afternoon it is decidable that the battery
will not carry the high-price period, and there is still time to do something
about it.

Plan 016 made it tight. It holds what the period needs and no more, it buys
only when the prices say that pays, and it is judged afresh on every poll -
it lets go once the sun turns out to be enough after all.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BACKUP_MODE_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_GRID_CHARGE_SWITCH,
    CONF_HIGH_PRICE_END,
    CONF_HIGH_PRICE_START,
    CONF_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)
from custom_components.inverter_charge_night.planner import EveningOutlook

COORDINATOR = "custom_components.inverter_charge_night.coordinator"
CALL_LATER = f"{COORDINATOR}.async_call_later"

ZONE_START = datetime(2026, 6, 1, 18, 0)
ZONE_END = datetime(2026, 6, 1, 21, 0)
EARLY = datetime(2026, 6, 1, 14, 0)   # more than two hours before the period
LATE = datetime(2026, 6, 1, 16, 30)   # inside the last two hours

CONFIG = {
    CONF_MIN_SOC_ENTITY: "number.min_soc",
    CONF_GRID_CHARGE_SWITCH: "switch.grid",
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_PV_FORECAST_ENTITY: "sensor.pv",
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_START_TIME: "23:00",
    CONF_END_TIME: "05:00",
    CONF_HIGH_PRICE_START: "18:00",
    CONF_HIGH_PRICE_END: "21:00",
}


def _make_coordinator(mock_hass, extra=None, *, rescue=True):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = CONFIG | (extra or {})
    entry.options = {}

    def _update(entry_, options=None, **kwargs):
        if options is not None:
            entry_.options = options
        return True

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    mock_hass.states.async_set("number.min_soc", "8", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("switch.grid", "off")
    mock_hass.states.async_set("sensor.soc", "30", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("sensor.pv", "2", {"unit_of_measurement": "kWh"})
    coordinator = InverterChargeNightCoordinator(mock_hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    coordinator._start_periodic_verification = AsyncMock()
    coordinator._stop_periodic_verification = AsyncMock()
    coordinator._house_load_profile = AsyncMock(return_value=[0.5] * 24)
    # Opt-in since 21 October 2026; the tests here are about what it does when on
    coordinator.evening_rescue_charge = rescue
    return coordinator


def _short(missing=2.0, required=62.0, projected=38.0):
    """An outlook that says the battery will not make it."""
    return EveningOutlook(
        zone_start=ZONE_START,
        zone_end=ZONE_END,
        required_soc=required,
        projected_soc=projected,
        missing_kwh=missing,
        pv_to_come_kwh=0.3,
        load_to_come_kwh=2.0,
        forecast_available=True,
    )


PRICE = "sensor.price"


def _prices(mock_hass, before=0.30, period=0.35):
    """Hourly prices for the day: ``before`` until the period, ``period`` in it."""
    midnight = ZONE_START.replace(hour=0)
    items = [
        {
            "start": (midnight + timedelta(hours=h)).isoformat(),
            "total": period if 18 <= h < 21 else before,
        }
        for h in range(36)
    ]
    mock_hass.states.async_set(
        PRICE, str(before), {"unit_of_measurement": "EUR/kWh", "raw_today": items}
    )
    return {CONF_PRICE_ENTITY: PRICE}


def _fine(spare_soc, required=62.0):
    """An outlook in which the battery makes it with ``spare_soc`` points to spare."""
    return _short(missing=0.0, required=required, projected=required + spare_soc)


async def _rescue(coordinator, outlook, now=EARLY):
    coordinator.last_evening_outlook = outlook
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=now
    ):
        await coordinator._maybe_rescue_the_evening()


# Stage one: hold ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_shortfall_holds_what_is_in_the_battery(mock_hass):
    coordinator = _make_coordinator(mock_hass)

    await _rescue(coordinator, _short())

    assert coordinator._rescue_stage == 1
    assert coordinator.is_active is True
    assert coordinator._adhoc_reason == "evening_rescue"
    assert coordinator._adhoc_until == ZONE_START
    # Holding means the level that is there, not the one that is wanted
    assert coordinator.initial_calculated_soc == 30.0
    assert coordinator._adhoc_allow_grid_charge is False
    # ... and what the sun adds is held up to the need
    assert coordinator._adhoc_floor_cap_soc == 62.0


@pytest.mark.asyncio
async def test_a_fuller_battery_is_held_only_at_what_the_period_needs(mock_hass):
    """Pascal's example: 10 kWh in the battery, 5 kWh needed - hold 5, not 10.

    Above the need the battery serves the house through the afternoon; held
    there, it would run on day-tariff grid power and carry the rest past the
    period into the cheap night.
    """
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set("sensor.soc", "80", {"unit_of_measurement": "%"})

    await _rescue(coordinator, _short(required=40.0, projected=35.0, missing=0.5))

    assert coordinator.is_active is True
    assert coordinator.initial_calculated_soc == 40.0
    with patch(f"{COORDINATOR}.dt_util.now", return_value=EARLY):
        assert coordinator.inverter_floor_soc() == 40.0


@pytest.mark.asyncio
async def test_the_floor_follows_the_sun_only_up_to_the_need(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())  # 30 % held, 62 % needed

    floors = []
    for soc in ("50", "70", "90"):
        mock_hass.states.async_set("sensor.soc", soc, {"unit_of_measurement": "%"})
        coordinator._update_window_floor()
        with patch(f"{COORDINATOR}.dt_util.now", return_value=EARLY):
            floors.append(coordinator.inverter_floor_soc())

    assert floors == [50.0, 62.0, 62.0]


@pytest.mark.asyncio
async def test_holding_does_not_buy_from_the_grid(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    mock_hass.services.async_call.reset_mock()

    with patch(f"{COORDINATOR}.dt_util.now", return_value=EARLY):
        await coordinator._control_charge(30.0)

    assert not any(
        call.args[:2] == ("switch", "turn_on")
        for call in mock_hass.services.async_call.await_args_list
    )


@pytest.mark.asyncio
async def test_an_outlook_that_is_fine_changes_nothing(mock_hass):
    coordinator = _make_coordinator(mock_hass)

    await _rescue(coordinator, _short(missing=0.0))

    assert coordinator._rescue_stage == 0
    assert coordinator.is_active is False


@pytest.mark.asyncio
async def test_no_outlook_changes_nothing(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, None)
    assert coordinator.is_active is False


# Stage two: buy -----------------------------------------------------------------


@pytest.mark.parametrize("now", [EARLY, LATE], ids=["early", "late"])
@pytest.mark.asyncio
async def test_without_the_switch_it_does_nothing_at_all(mock_hass, now):
    """Pascal's decision of 21 October 2026: only on request, the hold included.

    Held at 35 % from the morning on, the min SOC went back up every time it
    was set by hand - an integration for the night changing the inverter in
    the middle of the day.
    """
    coordinator = _make_coordinator(mock_hass, rescue=False)

    await _rescue(coordinator, _short(), now=now)

    assert coordinator._rescue_stage == 0
    assert coordinator.is_active is False
    mock_hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_a_rescue_window_from_before_the_update_ends_when_it_is_off(mock_hass):
    """The window of the morning of 21 October was persisted: it must not hold on."""
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    assert coordinator.is_active is True
    coordinator.evening_rescue_charge = False  # what an updated install starts with
    mock_hass.services.async_call.reset_mock()

    await _rescue(coordinator, _short())

    assert coordinator.is_active is False
    assert coordinator._adhoc_until is None
    assert coordinator.original_min_soc is None  # the inverter's own floor is back


@pytest.mark.parametrize(
    "prices",
    [(0.20, 0.45), (0.30, 0.38)],
    ids=["far-apart", "just-past-the-margin"],
)
@pytest.mark.asyncio
async def test_close_enough_and_worth_it_it_buys(mock_hass, prices):
    coordinator = _make_coordinator(mock_hass, _prices(mock_hass, *prices))

    await _rescue(coordinator, _short(), now=LATE)

    assert coordinator._rescue_stage == 2
    assert coordinator._adhoc_allow_grid_charge is True
    assert coordinator.initial_calculated_soc == 62.0


@pytest.mark.parametrize(
    "prices",
    [
        pytest.param(None, id="no-price-entity"),
        # Pascal's tariffs: 30 ct by day, 35 ct in the period. Through the
        # battery the 30 ct cost 30 / (0.90 x 0.95) = 35.1 ct - no gain at all
        pytest.param((0.30, 0.35), id="five-cents-apart"),
        # Beats the 35.1 ct, but not by the 2 ct margin on top (37.1 ct)
        pytest.param((0.30, 0.37), id="inside-the-margin"),
    ],
)
@pytest.mark.asyncio
async def test_buying_that_does_not_pay_only_holds(mock_hass, prices):
    extra = _prices(mock_hass, *prices) if prices else None
    coordinator = _make_coordinator(mock_hass, extra)

    await _rescue(coordinator, _short(), now=LATE)

    assert coordinator._rescue_stage == 1
    assert coordinator._adhoc_allow_grid_charge is False
    assert coordinator.initial_calculated_soc == 30.0


@pytest.mark.asyncio
async def test_too_early_it_holds_and_buys_later(mock_hass):
    """Bought at noon is bought for a sun that might still have come."""
    coordinator = _make_coordinator(mock_hass, _prices(mock_hass, before=0.20, period=0.45))

    await _rescue(coordinator, _short(), now=EARLY)
    assert coordinator._rescue_stage == 1
    assert coordinator._adhoc_allow_grid_charge is False

    await _rescue(coordinator, _short(), now=LATE)
    assert coordinator._rescue_stage == 2
    assert coordinator._adhoc_allow_grid_charge is True
    assert coordinator.initial_calculated_soc == 62.0


# Judged afresh on every poll (plan 016) -------------------------------------------


@pytest.mark.asyncio
async def test_it_lets_go_once_the_sun_is_enough(mock_hass):
    """The block of 3.11.1 stayed until the period, however sunny it turned."""
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    coordinator.original_min_soc = 8.0
    mock_hass.services.async_call.reset_mock()

    await _rescue(coordinator, _fine(spare_soc=6.0), now=EARLY + timedelta(minutes=15))

    assert coordinator.is_active is False
    assert coordinator._adhoc_until is None
    assert coordinator._rescue_stage == 0
    assert coordinator._adhoc_floor_cap_soc is None
    mock_hass.services.async_call.assert_any_await(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 8.0}, blocking=True
    )


@pytest.mark.asyncio
async def test_a_bought_charge_is_let_go_as_well(mock_hass):
    coordinator = _make_coordinator(mock_hass, _prices(mock_hass, before=0.20, period=0.45))
    await _rescue(coordinator, _short(), now=LATE)
    assert coordinator._rescue_stage == 2

    await _rescue(coordinator, _fine(spare_soc=5.0), now=LATE + timedelta(minutes=15))

    assert coordinator.is_active is False
    assert coordinator._rescue_stage == 0


@pytest.mark.parametrize("spare_soc", [0.0, 4.9], ids=["just-enough", "inside-the-band"])
@pytest.mark.asyncio
async def test_inside_the_band_nothing_changes(mock_hass, spare_soc):
    """Engaged at any shortfall, let go only 0.5 kWh above the need."""
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    mock_hass.services.async_call.reset_mock()

    await _rescue(coordinator, _fine(spare_soc), now=EARLY + timedelta(minutes=15))

    assert coordinator.is_active is True
    assert coordinator._rescue_stage == 1
    assert coordinator._adhoc_floor_cap_soc == 62.0
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_later_shortfall_engages_it_again(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    await _rescue(coordinator, _fine(spare_soc=6.0), now=EARLY + timedelta(minutes=15))
    assert coordinator.is_active is False

    await _rescue(coordinator, _short(), now=EARLY + timedelta(minutes=30))

    assert coordinator.is_active is True
    assert coordinator._adhoc_reason == "evening_rescue"
    assert coordinator._rescue_stage == 1


@pytest.mark.asyncio
async def test_a_smaller_need_takes_the_floor_down_with_it(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    mock_hass.states.async_set("sensor.soc", "60", {"unit_of_measurement": "%"})
    coordinator._update_window_floor()
    assert coordinator._window_floor_soc == 60.0

    await _rescue(
        coordinator,
        _short(required=50.0, projected=40.0, missing=1.0),
        now=EARLY + timedelta(minutes=15),
    )

    assert coordinator._adhoc_floor_cap_soc == 50.0
    assert coordinator._window_floor_soc == 50.0
    with patch(f"{COORDINATOR}.dt_util.now", return_value=EARLY):
        assert coordinator.inverter_floor_soc() == 50.0


@pytest.mark.asyncio
async def test_the_cap_survives_a_restart(mock_hass):
    """Without it a restarted rescue would follow the sun to a full battery."""
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=EARLY
    ):
        restarted = InverterChargeNightCoordinator(mock_hass, coordinator.entry)

    assert restarted._adhoc_reason == "evening_rescue"
    assert restarted._adhoc_floor_cap_soc == 62.0


@pytest.mark.asyncio
async def test_a_need_that_barely_moves_keeps_the_target(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    coordinator.async_open_adhoc_window = AsyncMock(return_value=True)

    await _rescue(
        coordinator,
        _short(required=62.9, projected=40.0),
        now=EARLY + timedelta(minutes=15),
    )

    coordinator.async_open_adhoc_window.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_window_an_action_opened_is_not_taken_over(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=EARLY
    ):
        assert await coordinator.async_block_discharge(timedelta(hours=1)) is True

    await _rescue(coordinator, _short())

    assert coordinator._adhoc_reason == "service"
    assert coordinator._adhoc_floor_cap_soc is None
    assert coordinator._rescue_stage == 0


@pytest.mark.asyncio
async def test_an_empty_battery_still_buys_when_that_pays(mock_hass):
    """Codex on #44: the empty-battery guard is about holding, not buying."""
    coordinator = _make_coordinator(mock_hass, _prices(mock_hass, before=0.20, period=0.45))
    mock_hass.states.async_set("sensor.soc", "8", {"unit_of_measurement": "%"})

    await _rescue(coordinator, _short(), now=LATE)

    assert coordinator.is_active is True
    assert coordinator._rescue_stage == 2
    assert coordinator._adhoc_allow_grid_charge is True


@pytest.mark.asyncio
async def test_a_smaller_need_reaches_the_inverter_at_once(mock_hass):
    """Codex on #44: with the target met the update never gets to write it."""
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    mock_hass.states.async_set("sensor.soc", "60", {"unit_of_measurement": "%"})
    coordinator._update_window_floor()
    mock_hass.states.async_set("number.min_soc", "60", {"unit_of_measurement": "%"})
    mock_hass.services.async_call.reset_mock()

    await _rescue(
        coordinator,
        _short(required=50.0, projected=40.0, missing=1.0),
        now=EARLY + timedelta(minutes=15),
    )

    mock_hass.services.async_call.assert_any_await(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 50.0}, blocking=True
    )


@pytest.mark.asyncio
async def test_a_purchase_called_off_takes_the_floor_back_to_the_battery(mock_hass):
    """Left at the need, a min SOC above the battery keeps buying on a Kostal."""
    coordinator = _make_coordinator(mock_hass, _prices(mock_hass, before=0.20, period=0.45))
    await _rescue(coordinator, _short(), now=LATE)
    assert coordinator._rescue_stage == 2
    mock_hass.states.async_set("number.min_soc", "62", {"unit_of_measurement": "%"})
    _prices(mock_hass, before=0.30, period=0.35)
    mock_hass.services.async_call.reset_mock()

    await _rescue(coordinator, _short(), now=LATE + timedelta(minutes=15))

    assert coordinator._rescue_stage == 1
    assert coordinator._adhoc_allow_grid_charge is False
    mock_hass.services.async_call.assert_any_await(
        "number", "set_value", {"entity_id": "number.min_soc", "value": 30.0}, blocking=True
    )


@pytest.mark.asyncio
async def test_a_reset_by_hand_is_not_undone_by_the_next_poll(mock_hass):
    """CodeRabbit on #44: reset_inverter hands the inverter back until the period."""
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=EARLY
    ):
        await coordinator.async_reset_inverter()
    assert coordinator.is_active is False
    assert coordinator._hands_off_until == ZONE_START

    await _rescue(coordinator, _short(), now=EARLY + timedelta(minutes=15))

    assert coordinator.is_active is False
    assert coordinator._rescue_stage == 0


@pytest.mark.asyncio
async def test_an_empty_battery_is_not_held(mock_hass):
    """Nothing above the minimum to keep, and a floor above it would buy."""
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set("sensor.soc", "8", {"unit_of_measurement": "%"})

    await _rescue(coordinator, _short())

    assert coordinator.is_active is False
    mock_hass.services.async_call.assert_not_awaited()


# The interlocks -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_mode_rescues_nothing(mock_hass):
    """The house is running on the battery; nothing is ours to write."""
    mock_hass.states.async_set("binary_sensor.backup", "on")
    coordinator = _make_coordinator(mock_hass, {CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup"})

    await _rescue(coordinator, _short())

    assert coordinator._rescue_stage == 0
    assert coordinator.is_active is False


@pytest.mark.asyncio
async def test_a_switched_off_integration_rescues_nothing(mock_hass):
    coordinator = _make_coordinator(mock_hass)
    coordinator.is_enabled = False

    await _rescue(coordinator, _short())

    assert coordinator._rescue_stage == 0


@pytest.mark.asyncio
async def test_an_unreadable_battery_rescues_nothing(mock_hass):
    """Holding at a level nobody can read is not holding, it is guessing."""
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set("sensor.soc", "unavailable")

    await _rescue(coordinator, _short())

    assert coordinator._rescue_stage == 0


@pytest.mark.asyncio
async def test_the_rescue_ends_when_the_period_starts(mock_hass):
    """And the inverter goes back to what it was before."""
    coordinator = _make_coordinator(mock_hass)
    await _rescue(coordinator, _short())
    coordinator.original_min_soc = 8.0
    coordinator._reset_absolute_charge_power = AsyncMock()
    mock_hass.services.async_call.reset_mock()

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=ZONE_START
    ):
        await coordinator._check_current_window()

    assert coordinator.is_active is False
    assert coordinator._rescue_stage == 0
    assert any(
        call.args[:2] == ("number", "set_value")
        for call in mock_hass.services.async_call.await_args_list
    )


# The switch ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_switch_survives_a_restart(mock_hass):
    from custom_components.inverter_charge_night.switch import EveningRescueChargeSwitch

    coordinator = _make_coordinator(mock_hass, rescue=False)
    switch = EveningRescueChargeSwitch(coordinator, coordinator.entry)
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    assert coordinator.evening_rescue_charge is True

    with patch(CALL_LATER, return_value=MagicMock()):
        restarted = InverterChargeNightCoordinator(mock_hass, coordinator.entry)
    assert restarted.evening_rescue_charge is True


@pytest.mark.asyncio
async def test_switching_it_off_ends_a_running_rescue(mock_hass):
    """Off means off: the user takes the inverter back at once."""
    from custom_components.inverter_charge_night.switch import EveningRescueChargeSwitch

    coordinator = _make_coordinator(mock_hass, _prices(mock_hass, before=0.20, period=0.45))
    await _rescue(coordinator, _short(), now=LATE)
    assert coordinator._adhoc_allow_grid_charge is True

    switch = EveningRescueChargeSwitch(coordinator, coordinator.entry)
    switch.async_write_ha_state = MagicMock()
    with patch(f"{COORDINATOR}.dt_util.now", return_value=LATE):
        await switch.async_turn_off()

    assert coordinator.evening_rescue_charge is False
    assert coordinator.is_active is False
    assert coordinator._adhoc_until is None


@pytest.mark.asyncio
async def test_holding_does_not_lock_out_the_buying_that_follows_it(mock_hass):
    """Through the real update path, not by calling the rescue directly.

    Stage one opens a window, which makes the coordinator active - and an
    active window used to switch the outlook off entirely. The outlook would
    then be None on every later poll and stage two could never fire. The tests
    above call _maybe_rescue_the_evening() with an outlook in hand and would
    never have noticed.
    """
    mock_hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "next_rising": "2026-06-02T05:00:00+00:00",
            "next_setting": "2026-06-01T21:00:00+00:00",
        },
    )
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_PV_FORECAST_TODAY_ENTITY: "sensor.pv_today"}
        | _prices(mock_hass, before=0.20, period=0.45),
    )
    # After the helper, which sets its own defaults: a nearly empty battery and
    # a day that will not fill it, so the shortfall is still there at 16:30.
    mock_hass.states.async_set("sensor.soc", "12", {"unit_of_measurement": "%"})
    mock_hass.states.async_set("sensor.pv_today", "0.5", {"unit_of_measurement": "kWh"})
    coordinator.evening_rescue_charge = True

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=EARLY
    ):
        await coordinator._async_update_data()

    assert coordinator._rescue_stage == 1, "too early to buy, so it holds"
    assert coordinator.last_evening_outlook is not None, "the outlook has to survive its own window"

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=LATE
    ):
        await coordinator._async_update_data()

    assert coordinator._rescue_stage == 2
    assert coordinator._adhoc_allow_grid_charge is True
