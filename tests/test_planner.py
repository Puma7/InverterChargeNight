"""Tests for the pure planner module (plan 006, step 2).

Every case uses a 10 kWh battery with a window ending at 05:59, sunrise at
07:30 with a 90 minute crossover delay (PV > load at 09:00) and sunset at
17:00, so the bridge covers 3 h 01 min of house load plus the reserve.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from custom_components.inverter_charge_night.const import DEFAULT_SAFE_FALLBACK_SOC
from custom_components.inverter_charge_night.planner import (
    REASON_BRIDGE,
    REASON_CONFLICT_BRIDGE_WINS,
    REASON_CONFLICT_HEADROOM_WINS,
    REASON_FALLBACK,
    PlanInput,
    curtailment_outlook,
    evening_outlook,
    evening_reserve_soc,
    integrate_load,
    pv_fraction_between,
    pv_power_kw_at,
    plan_target_soc,
    required_charge_power_w,
)

WINDOW_END = datetime(2026, 1, 15, 5, 59)
PV_CROSSOVER = datetime(2026, 1, 15, 9, 0)
SUNSET = datetime(2026, 1, 15, 17, 0)
FLAT_500W = [0.5] * 24
CAPACITY = 10.0
USER_MIN = 8.0
USER_MAX = 100.0

# 0.5 kW from 05:59 to 09:00 (3 h 1 min) + 0.5 kWh reserve
BRIDGE_KWH = 0.5 * (3 + 1 / 60) + 0.5
# 0.5 kW from 09:00 to 17:00
DAYTIME_LOAD_KWH = 0.5 * 8


def _plan_input(**overrides) -> PlanInput:
    values = dict(
        capacity_kwh=CAPACITY,
        current_soc=40.0,
        user_min_soc=USER_MIN,
        user_max_soc=USER_MAX,
        forecast_kwh_next_day=5.0,
        forecast_available=True,
        error_margin_pct=10.0,
        window_end=WINDOW_END,
        pv_crossover=PV_CROSSOVER,
        sunset=SUNSET,
        house_load_kw_profile=FLAT_500W,
        reserve_kwh=0.5,
        charge_efficiency=0.9,
        prices_ct=None,
    )
    values.update(overrides)
    return PlanInput(**values)


def _lower_bound(bridge_kwh: float) -> float:
    return round((bridge_kwh + CAPACITY * USER_MIN / 100) / CAPACITY * 100, 1)


# --- integrate_load --------------------------------------------------------


def test_integrate_load_partial_hours_and_midnight():
    profile = [1.0] * 24
    profile[23] = 2.0  # 23:00-24:00
    profile[0] = 4.0  # 00:00-01:00
    start = datetime(2026, 1, 14, 23, 30)
    end = datetime(2026, 1, 15, 1, 15)
    # 0.5 h at 2 kW + 1 h at 4 kW + 0.25 h at 1 kW
    assert integrate_load(profile, start, end) == pytest.approx(1.0 + 4.0 + 0.25)


def test_integrate_load_empty_or_reversed_range_is_zero():
    assert integrate_load(FLAT_500W, WINDOW_END, WINDOW_END) == 0.0
    assert integrate_load(FLAT_500W, PV_CROSSOVER, WINDOW_END) == 0.0


def test_integrate_load_exact_hour_boundaries():
    profile = list(range(24))  # hour h has h kW
    start = datetime(2026, 1, 15, 2, 0)
    end = datetime(2026, 1, 15, 5, 0)
    assert integrate_load(profile, start, end) == pytest.approx(2 + 3 + 4)


def test_integrate_load_rejects_wrong_profile_length():
    with pytest.raises(ValueError):
        integrate_load([0.5] * 23, WINDOW_END, PV_CROSSOVER)


# --- plan_target_soc --------------------------------------------------------


def test_winter_day_long_bridge_targets_near_max():
    """Forecast 2 kWh, bridge until 11:30 at 1.2 kW: the target is close to the maximum."""
    profile = [1.2] * 24
    plan = plan_target_soc(
        _plan_input(
            forecast_kwh_next_day=2.0,
            pv_crossover=datetime(2026, 1, 15, 11, 30),
            sunset=datetime(2026, 1, 15, 16, 0),
            house_load_kw_profile=profile,
            reserve_kwh=1.0,
        )
    )
    bridge = 1.2 * (5 + 31 / 60) + 1.0  # 05:59 -> 11:30
    assert plan.bridge_kwh == pytest.approx(bridge)
    assert plan.surplus_kwh == 0.0  # 2.2 kWh forecast < 5.4 kWh daytime load
    assert plan.reason == REASON_BRIDGE
    assert plan.target_soc == _lower_bound(bridge)
    assert 80.0 <= plan.target_soc <= USER_MAX
    assert plan.upper_bound_soc == USER_MAX


def test_summer_day_targets_bridge_not_minimum():
    """Forecast 40 kWh into a 10 kWh battery: the old formula would go to the minimum."""
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=40.0, prices_ct=None))
    assert plan.bridge_kwh == pytest.approx(BRIDGE_KWH)
    assert plan.surplus_kwh == pytest.approx(44.0 - DAYTIME_LOAD_KWH)
    # The surplus exceeds the battery: the upper bound is the user minimum
    assert plan.upper_bound_soc == USER_MIN
    assert plan.lower_bound_soc == _lower_bound(BRIDGE_KWH)
    # Conflict without prices: the bridge wins, the house does not buy by day
    assert plan.reason == REASON_CONFLICT_BRIDGE_WINS
    assert plan.target_soc == _lower_bound(BRIDGE_KWH)
    assert plan.target_soc > USER_MIN


def test_no_conflict_returns_lower_bound_with_reason_bridge():
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=5.0))
    # 5.5 kWh with margin - 4 kWh daytime load = 1.5 kWh surplus < bridge -> upper = max
    assert plan.surplus_kwh == pytest.approx(1.5)
    assert plan.upper_bound_soc == USER_MAX
    assert plan.reason == REASON_BRIDGE
    assert plan.target_soc == _lower_bound(BRIDGE_KWH)


def test_conflict_with_prices_bridge_wins_when_night_energy_is_cheap():
    """Night 14 ct, day 30 ct, feed-in 8 ct: buying at night beats buying by day."""
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=13.0, prices_ct=(14.0, 30.0, 8.0)))
    assert plan.lower_bound_soc > plan.upper_bound_soc
    assert plan.reason == REASON_CONFLICT_BRIDGE_WINS
    assert plan.target_soc == plan.lower_bound_soc


def test_conflict_with_prices_headroom_wins_when_feed_in_is_worth_more():
    """Night 28 ct, day 30 ct, feed-in 20 ct: losing feed-in costs more than buying by day."""
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=13.0, prices_ct=(28.0, 30.0, 20.0)))
    assert plan.lower_bound_soc > plan.upper_bound_soc
    assert plan.reason == REASON_CONFLICT_HEADROOM_WINS
    assert plan.target_soc == plan.upper_bound_soc
    assert plan.target_soc >= USER_MIN


def test_conflict_upper_bound_keeps_room_only_for_the_uncovered_surplus():
    # 12 kWh * 1.1 - 4 kWh = 9.2 kWh surplus; minus the bridge -> room needed
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=12.0))
    expected_upper = round((1 - (9.2 - BRIDGE_KWH) / CAPACITY) * 100, 1)
    assert plan.upper_bound_soc == expected_upper


def test_missing_forecast_uses_safe_fallback():
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=0.0, forecast_available=False))
    assert plan.reason == REASON_FALLBACK
    assert plan.target_soc == DEFAULT_SAFE_FALLBACK_SOC
    # The bounds are still reported for diagnostics
    assert plan.bridge_kwh == pytest.approx(BRIDGE_KWH)
    assert plan.lower_bound_soc == _lower_bound(BRIDGE_KWH)


def test_missing_forecast_fallback_still_covers_the_bridge():
    """The bridge is house load, not forecast: a missing forecast does not shrink it.

    The fallback decides how much to buy for a PV day nobody can see yet. What
    the house will draw after the window is known either way, so the lower
    bound still holds - buying less than that means buying the rest by day.
    """
    plan = plan_target_soc(
        _plan_input(forecast_available=False, user_min_soc=60.0, user_max_soc=90.0)
    )
    assert plan.lower_bound_soc == pytest.approx(60.0 + BRIDGE_KWH / CAPACITY * 100, abs=0.05)
    assert plan.target_soc == plan.lower_bound_soc


def test_the_fallback_is_clamped_to_the_user_maximum():
    plan = plan_target_soc(
        _plan_input(forecast_available=False, user_min_soc=60.0, user_max_soc=62.0)
    )
    assert plan.target_soc == 62.0


# The evening reserve (plan 011) -------------------------------------------------

EVENING = (datetime(2026, 1, 15, 18, 0), datetime(2026, 1, 15, 21, 0))
EVENING_KWH = 0.5 * 3  # the flat 500 W profile over three hours


def test_without_a_high_price_window_nothing_changes():
    plan = plan_target_soc(_plan_input())
    assert plan.evening_reserve_kwh == 0.0
    assert plan.evening_shortfall_kwh == 0.0
    assert plan.target_soc == _lower_bound(BRIDGE_KWH)


def test_a_sunny_day_covers_the_evening_by_itself():
    """5 kWh forecast against 4 kWh of daytime load leaves more than the evening needs."""
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=10.0, high_price_window=EVENING))
    assert plan.evening_reserve_kwh == pytest.approx(EVENING_KWH)
    assert plan.evening_shortfall_kwh == 0.0
    assert plan.target_soc == _lower_bound(BRIDGE_KWH)


def test_a_dull_day_buys_the_evening_at_the_cheap_tariff():
    """No surplus at all, so the whole evening has to come from the night."""
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=0.0, high_price_window=EVENING))
    assert plan.evening_shortfall_kwh == pytest.approx(EVENING_KWH)
    assert plan.target_soc == _lower_bound(BRIDGE_KWH + EVENING_KWH)


def test_a_partly_covered_evening_buys_only_the_rest():
    # 5.5 kWh with margin against 4 kWh of daytime load: 1.5 kWh surplus
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=5.0, high_price_window=EVENING))
    surplus = 5.0 * 1.1 - DAYTIME_LOAD_KWH
    assert plan.evening_shortfall_kwh == pytest.approx(max(0.0, EVENING_KWH - surplus))


def test_the_margin_allows_for_an_evening_above_the_average():
    plan = plan_target_soc(
        _plan_input(
            forecast_kwh_next_day=0.0, high_price_window=EVENING, reserve_margin_pct=20.0
        )
    )
    assert plan.evening_reserve_kwh == pytest.approx(EVENING_KWH * 1.2)


def test_a_missing_forecast_buys_the_whole_evening():
    """A surplus nobody can see is a surplus nobody may plan on."""
    plan = plan_target_soc(
        _plan_input(
            forecast_available=False, forecast_kwh_next_day=20.0, high_price_window=EVENING
        )
    )
    assert plan.evening_shortfall_kwh == pytest.approx(EVENING_KWH)


def test_an_evening_window_crossing_midnight_is_integrated_whole():
    plan = plan_target_soc(
        _plan_input(
            forecast_kwh_next_day=0.0,
            high_price_window=(datetime(2026, 1, 15, 22, 0), datetime(2026, 1, 16, 1, 0)),
        )
    )
    assert plan.evening_reserve_kwh == pytest.approx(1.5)


def test_the_evening_reserve_cannot_push_past_the_user_maximum():
    plan = plan_target_soc(
        _plan_input(
            forecast_kwh_next_day=0.0,
            capacity_kwh=2.0,
            user_max_soc=80.0,
            high_price_window=EVENING,
        )
    )
    assert plan.target_soc == 80.0


def test_window_end_after_pv_crossover_bridge_is_only_the_reserve():
    """A window that ends after the crossover needs no bridge energy beyond the reserve."""
    plan = plan_target_soc(
        _plan_input(window_end=datetime(2026, 1, 15, 9, 30), pv_crossover=PV_CROSSOVER)
    )
    assert plan.bridge_kwh == pytest.approx(0.5)
    assert plan.target_soc == _lower_bound(0.5)


def test_target_always_within_user_bounds():
    """Realistic inputs never produce a target outside [user_min, user_max] (STOP condition)."""
    for forecast in (0.0, 2.0, 5.0, 12.0, 40.0):
        for load in (0.2, 0.5, 1.5, 3.0):
            for prices in (None, (14.0, 30.0, 8.0), (28.0, 30.0, 20.0)):
                plan = plan_target_soc(
                    _plan_input(
                        forecast_kwh_next_day=forecast,
                        house_load_kw_profile=[load] * 24,
                        prices_ct=prices,
                    )
                )
                assert USER_MIN <= plan.target_soc <= USER_MAX, (forecast, load, prices)
                assert USER_MIN <= plan.lower_bound_soc <= USER_MAX
                assert USER_MIN <= plan.upper_bound_soc <= USER_MAX


def test_zero_capacity_raises():
    with pytest.raises(ValueError):
        plan_target_soc(_plan_input(capacity_kwh=0.0))


def test_invalid_soc_range_raises():
    with pytest.raises(ValueError):
        plan_target_soc(_plan_input(user_min_soc=50.0, user_max_soc=50.0))


def test_negative_reserve_is_ignored():
    plan = plan_target_soc(_plan_input(reserve_kwh=-5.0))
    assert plan.bridge_kwh == pytest.approx(BRIDGE_KWH - 0.5)


# --- required_charge_power_w ------------------------------------------------


def test_required_charge_power_two_kwh_in_four_hours():
    # 2 kWh missing (20 % of 10 kWh) in 4 h at 90 % efficiency -> 556 W
    power = required_charge_power_w(60.0, 40.0, CAPACITY, 4.0, 0.9)
    assert round(power) == 556


def test_required_charge_power_nothing_missing_is_zero():
    assert required_charge_power_w(40.0, 40.0, CAPACITY, 4.0, 0.9) == 0.0
    assert required_charge_power_w(30.0, 40.0, CAPACITY, 4.0, 0.9) == 0.0


def test_required_charge_power_time_up_is_infinite():
    assert math.isinf(required_charge_power_w(60.0, 40.0, CAPACITY, 0.0, 0.9))


def test_required_charge_power_invalid_inputs_raise():
    with pytest.raises(ValueError):
        required_charge_power_w(60.0, 40.0, 0.0, 4.0, 0.9)
    with pytest.raises(ValueError):
        required_charge_power_w(60.0, 40.0, CAPACITY, 4.0, 0.0)


def test_required_charge_power_scales_with_remaining_time():
    slow = required_charge_power_w(60.0, 40.0, CAPACITY, 4.0, 1.0)
    fast = required_charge_power_w(60.0, 40.0, CAPACITY, 0.5, 1.0)
    assert fast == pytest.approx(slow * 8)
    assert (WINDOW_END + timedelta(hours=4)).hour == 9  # sanity on the fixtures used above


# The same reserve, seen from the discharge direction ----------------------------


def test_the_discharge_floor_is_the_user_minimum_without_a_period():
    plan_input = _plan_input()
    assert evening_reserve_soc(plan_input) == USER_MIN


def test_the_discharge_floor_holds_back_what_the_evening_needs():
    """1.5 kWh on a 10 kWh battery is 15 points above the user minimum."""
    plan_input = _plan_input(forecast_kwh_next_day=0.0, high_price_window=EVENING)
    assert evening_reserve_soc(plan_input) == pytest.approx(USER_MIN + 15.0)


def test_a_sunny_day_leaves_the_discharge_alone():
    plan_input = _plan_input(forecast_kwh_next_day=10.0, high_price_window=EVENING)
    assert evening_reserve_soc(plan_input) == USER_MIN


def test_the_discharge_floor_never_passes_the_user_maximum():
    plan_input = _plan_input(
        forecast_kwh_next_day=0.0,
        capacity_kwh=2.0,
        user_max_soc=40.0,
        high_price_window=EVENING,
    )
    assert evening_reserve_soc(plan_input) == 40.0


def test_the_discharge_floor_rejects_an_impossible_capacity():
    with pytest.raises(ValueError):
        evening_reserve_soc(_plan_input(capacity_kwh=0.0, high_price_window=EVENING))


# The discharge loss (Pascal, 20.09.) --------------------------------------------


def test_without_a_discharge_efficiency_nothing_changes():
    """The field defaults to 1.0, so every caller that predates it is unaffected."""
    assert plan_target_soc(_plan_input()).target_soc == _lower_bound(BRIDGE_KWH)


def test_the_bridge_must_hold_more_than_the_house_will_draw():
    """4 kWh in the house needs about 4.2 in the battery at 95 %.

    The load profile is measured on the house side; what leaves the battery
    loses a few percent through the inverter on the way there.
    """
    plan = plan_target_soc(_plan_input(discharge_efficiency=0.95))
    load_kwh = BRIDGE_KWH - 0.5  # the reserve is battery-side already
    assert plan.bridge_kwh == pytest.approx(load_kwh / 0.95 + 0.5)
    assert plan.target_soc > _lower_bound(BRIDGE_KWH)


def test_the_evening_reserve_is_grossed_up_too():
    plan = plan_target_soc(
        _plan_input(
            forecast_kwh_next_day=0.0, high_price_window=EVENING, discharge_efficiency=0.90
        )
    )
    # The reserve itself stays the house draw; what has to be bought is more
    assert plan.evening_reserve_kwh == pytest.approx(EVENING_KWH)
    assert plan.target_soc == pytest.approx(
        _lower_bound((BRIDGE_KWH - 0.5) / 0.9 + 0.5 + EVENING_KWH / 0.9), abs=0.05
    )


def test_the_discharge_floor_is_grossed_up_too():
    plan_input = _plan_input(
        forecast_kwh_next_day=0.0, high_price_window=EVENING, discharge_efficiency=0.90
    )
    assert evening_reserve_soc(plan_input) == pytest.approx(
        USER_MIN + (EVENING_KWH / 0.9) / CAPACITY * 100, abs=0.01
    )


@pytest.mark.parametrize("efficiency", [0.0, -1.0, 2.0])
def test_an_impossible_discharge_efficiency_cannot_divide_by_zero(efficiency):
    """Clamped into (0, 1], so a bad setting is survivable rather than fatal."""
    plan = plan_target_soc(_plan_input(discharge_efficiency=efficiency))
    assert math.isfinite(plan.target_soc)


# The evening outlook (plan 013) -------------------------------------------------

SUNRISE = datetime(2026, 6, 1, 5, 0)
SUNSET_SUMMER = datetime(2026, 6, 1, 21, 0)
ZONE = (datetime(2026, 6, 1, 18, 0), datetime(2026, 6, 1, 21, 0))


def _outlook(**overrides):
    values = dict(
        now=datetime(2026, 6, 1, 14, 0),
        capacity_kwh=10.0,
        current_soc=50.0,
        user_min_soc=8.0,
        user_max_soc=100.0,
        house_load_kw_profile=FLAT_500W,
        zone_start=ZONE[0],
        zone_end=ZONE[1],
        margin_pct=0.0,
        charge_efficiency=1.0,
        discharge_efficiency=1.0,
        sunrise=SUNRISE,
        sunset=SUNSET_SUMMER,
        forecast_kwh_today=20.0,
        forecast_available=True,
    )
    values.update(overrides)
    return evening_outlook(**values)


def test_the_sun_share_is_a_bell_not_a_line():
    """At four in the afternoon a linear model still promises half the day.

    That is the error that matters here: it would let the battery walk into
    the evening short while the projection says it is fine.
    """
    remaining = pv_fraction_between(
        SUNRISE, SUNSET_SUMMER, datetime(2026, 6, 1, 16, 0), SUNSET_SUMMER
    )
    linear = (SUNSET_SUMMER - datetime(2026, 6, 1, 16, 0)) / (SUNSET_SUMMER - SUNRISE)
    assert remaining < linear
    assert remaining == pytest.approx(0.222, abs=0.005)


def test_the_whole_solar_day_is_all_of_it():
    assert pv_fraction_between(SUNRISE, SUNSET_SUMMER, SUNRISE, SUNSET_SUMMER) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "start,end",
    [
        (SUNSET_SUMMER, SUNSET_SUMMER + timedelta(hours=2)),  # after dark
        (SUNRISE - timedelta(hours=3), SUNRISE),  # before dawn
        (datetime(2026, 6, 1, 14, 0), datetime(2026, 6, 1, 14, 0)),  # no span
    ],
)
def test_no_sun_outside_the_solar_day(start, end):
    assert pv_fraction_between(SUNRISE, SUNSET_SUMMER, start, end) == 0.0


def test_a_polar_night_does_not_divide_by_zero():
    assert pv_fraction_between(SUNRISE, SUNRISE, SUNRISE, SUNSET_SUMMER) == 0.0


def test_a_sunny_afternoon_makes_it_to_the_evening():
    outlook = _outlook()
    # 1.5 kWh needed for the zone on top of 8 % -> 23 %; the battery is at 50
    assert outlook.required_soc == pytest.approx(23.0)
    assert outlook.missing_kwh == 0.0
    assert outlook.projected_soc > 50.0


def test_a_flat_battery_on_a_dull_day_is_short():
    """Snow on the panels, nobody set the snow nights: the case Pascal named."""
    outlook = _outlook(current_soc=12.0, forecast_kwh_today=0.5)
    assert outlook.missing_kwh > 0.0
    assert outlook.projected_soc < outlook.required_soc


def test_without_a_forecast_the_sun_counts_for_nothing():
    """A projection nobody can check has to be the pessimistic one."""
    with_sun = _outlook(current_soc=20.0)
    without = _outlook(current_soc=20.0, forecast_available=False)
    assert without.pv_to_come_kwh == 0.0
    assert without.missing_kwh > with_sun.missing_kwh


def test_the_house_eats_into_the_battery_when_the_sun_does_not_cover_it():
    outlook = _outlook(forecast_kwh_today=0.0, forecast_available=True, current_soc=50.0)
    # 0.5 kW from 14:00 to 18:00 is 2 kWh out of a 10 kWh battery
    assert outlook.load_to_come_kwh == pytest.approx(2.0)
    assert outlook.projected_soc == pytest.approx(30.0)


def test_the_discharge_loss_raises_what_the_evening_needs():
    lossless = _outlook()
    lossy = _outlook(discharge_efficiency=0.9)
    assert lossy.required_soc > lossless.required_soc


def test_the_margin_raises_what_the_evening_needs():
    assert _outlook(margin_pct=25.0).required_soc > _outlook().required_soc


def test_the_projection_cannot_exceed_the_user_maximum():
    outlook = _outlook(current_soc=95.0, user_max_soc=96.0, forecast_kwh_today=40.0)
    assert outlook.projected_soc == 96.0


def test_an_impossible_capacity_is_rejected():
    with pytest.raises(ValueError):
        _outlook(capacity_kwh=0.0)


# The price gate on the reserve (plan 012, stage 3) -------------------------------


def test_without_prices_the_reserve_is_held_as_before():
    """The shipped behaviour, and what an installation with no price entity gets."""
    plan = plan_target_soc(_plan_input(forecast_kwh_next_day=0.0, high_price_window=EVENING))
    assert plan.evening_reserve_kwh == pytest.approx(EVENING_KWH)
    assert plan.evening_reserve_dropped is False


def test_a_dear_evening_holds_the_reserve():
    plan = plan_target_soc(
        _plan_input(
            forecast_kwh_next_day=0.0,
            high_price_window=EVENING,
            window_price_ct=22.0,
            evening_price_ct=38.0,
        )
    )
    assert plan.evening_reserve_kwh == pytest.approx(EVENING_KWH)
    assert plan.evening_reserve_dropped is False


def test_a_cheap_evening_is_bought_rather_than_saved_for():
    """Holding costs the window price plus the round trip; buying costs the evening."""
    plan = plan_target_soc(
        _plan_input(
            forecast_kwh_next_day=0.0,
            high_price_window=EVENING,
            window_price_ct=38.0,
            evening_price_ct=22.0,
        )
    )
    assert plan.evening_reserve_kwh == 0.0
    assert plan.evening_shortfall_kwh == 0.0
    assert plan.evening_reserve_dropped is True
    assert plan.target_soc == _lower_bound(BRIDGE_KWH)


def test_dropping_the_reserve_frees_the_morning_discharge_too():
    """One gate, both directions - the floor follows the same reserve."""
    plan_input = _plan_input(
        forecast_kwh_next_day=0.0,
        high_price_window=EVENING,
        window_price_ct=38.0,
        evening_price_ct=22.0,
    )
    assert evening_reserve_soc(plan_input) == USER_MIN


def test_only_one_known_price_keeps_the_reserve():
    """Half an answer is not an answer, and holding is what was configured."""
    for prices in (({"window_price_ct": 38.0}), ({"evening_price_ct": 22.0})):
        plan = plan_target_soc(
            _plan_input(forecast_kwh_next_day=0.0, high_price_window=EVENING, **prices)
        )
        assert plan.evening_reserve_kwh == pytest.approx(EVENING_KWH), prices


def test_prices_without_a_period_change_nothing():
    plan = plan_target_soc(_plan_input(window_price_ct=38.0, evening_price_ct=22.0))
    assert plan.evening_reserve_kwh == 0.0
    assert plan.evening_reserve_dropped is False, "there was no reserve to drop"


# --- Curtailment: a permanent feed-in cap (plan 014) ------------------------
#
# A 16 h solar day so the numbers are a summer day rather than the January one
# the rest of this file uses.

CURT_SUNRISE = datetime(2026, 6, 21, 5, 30)
CURT_SUNSET = datetime(2026, 6, 21, 21, 30)
CURT_FORECAST = 100.0


def _integrate_pv_power(start, end, steps=20000, daily_kwh=CURT_FORECAST):
    """Integrate the power curve the slow, obvious way, for the property test."""
    total = 0.0
    step = (end - start) / steps
    for i in range(steps):
        midpoint = start + step * (i + 0.5)
        total += pv_power_kw_at(CURT_SUNRISE, CURT_SUNSET, midpoint, daily_kwh) * (
            step.total_seconds() / 3600.0
        )
    return total


@pytest.mark.parametrize(
    "start_hour,end_hour",
    [(5, 21), (6, 12), (11, 14), (9, 10), (5, 6), (20, 21), (4, 23)],
)
def test_the_power_curve_integrates_to_the_energy_fraction(start_hour, end_hour):
    """The one test that keeps the two PV models from drifting apart.

    ``pv_power_kw_at`` is the analytic derivative of ``pv_fraction_between``.
    If that ever stops being true, a sensor built on one contradicts a decision
    built on the other, and nothing else in the suite would notice. The last
    case runs past both ends of the solar day on purpose.
    """
    start = datetime(2026, 6, 21, start_hour, 0)
    end = datetime(2026, 6, 21, end_hour, 0)
    expected = pv_fraction_between(CURT_SUNRISE, CURT_SUNSET, start, end) * CURT_FORECAST
    assert _integrate_pv_power(start, end) == pytest.approx(expected, abs=1e-4)


def test_the_power_curve_is_zero_outside_the_solar_day():
    before = datetime(2026, 6, 21, 4, 0)
    after = datetime(2026, 6, 21, 22, 0)
    assert pv_power_kw_at(CURT_SUNRISE, CURT_SUNSET, before, CURT_FORECAST) == 0.0
    assert pv_power_kw_at(CURT_SUNRISE, CURT_SUNSET, after, CURT_FORECAST) == 0.0
    assert pv_power_kw_at(CURT_SUNSET, CURT_SUNRISE, before, CURT_FORECAST) == 0.0


def _curtailment(**overrides):
    kwargs = dict(
        capacity_kwh=35.0,
        user_min_soc=10.0,
        user_max_soc=95.0,
        house_load_kw_profile=[0.7] * 24,
        sunrise=CURT_SUNRISE,
        sunset=CURT_SUNSET,
        forecast_kwh_today=CURT_FORECAST,
        forecast_available=True,
        error_margin_pct=0.0,
        limit_w=7200.0,
        charge_efficiency=0.95,
        evening_reserve_soc_pct=20.0,
    )
    kwargs.update(overrides)
    return curtailment_outlook(**kwargs)


@pytest.mark.parametrize(
    "kwp,expect_binding",
    [(10, True), (12, True), (14, True), (16, False), (20, False)],
)
def test_a_bigger_array_means_the_cap_bites_less(kwp, expect_binding):
    """The cap is on the feed-in, so a larger array clears it earlier.

    At 100 kWh across a 16 h day the modelled peak is about 9.8 kW; a 60 % cap
    above that is never reached, and "it never bites" has to come back as
    exactly that rather than as a shortfall of zero.
    """
    outlook = _curtailment(limit_w=0.6 * kwp * 1000)
    assert (outlook.binding_start is not None) is expect_binding
    if expect_binding:
        assert outlook.overflow_kwh > 0
        assert outlook.binding_end > outlook.binding_start
    else:
        assert outlook.overflow_kwh == 0.0
        assert outlook.binding_end is None
        assert outlook.morning_target_soc == 95.0, "nothing to make room for"


def test_the_overflow_takes_its_charge_losses_into_account():
    """Room is the AC amount *times* efficiency - less arrives than was diverted.

    The opposite direction from the night plan, where the question is how much
    to buy in order to store a given amount.
    """
    outlook = _curtailment(charge_efficiency=0.9)
    assert outlook.room_needed_kwh == pytest.approx(outlook.overflow_kwh * 0.9)
    assert outlook.room_needed_kwh < outlook.overflow_kwh


def test_the_error_margin_damps_the_forecast_here():
    """Inflating it would throttle on days that did not need it, and that costs twice."""
    plain = _curtailment(error_margin_pct=0.0)
    damped = _curtailment(error_margin_pct=10.0)
    assert damped.overflow_kwh < plain.overflow_kwh


def test_the_morning_target_never_falls_below_the_evening_reserve():
    """A day throttled so hard the evening is lost has bought the expensive hours."""
    outlook = _curtailment(limit_w=2000.0, evening_reserve_soc_pct=80.0)
    assert outlook.morning_target_soc == 80.0
    assert outlook.clamped_by == "evening_reserve"


def test_without_an_evening_reserve_the_user_minimum_is_the_floor():
    outlook = _curtailment(limit_w=2000.0, evening_reserve_soc_pct=10.0)
    assert outlook.morning_target_soc == 10.0
    assert outlook.clamped_by == "user_min"


def test_an_unthrottled_day_reports_no_clamp():
    assert _curtailment(limit_w=8400.0).clamped_by is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"limit_w": 0.0},
        {"limit_w": -1.0},
        {"forecast_available": False},
        {"sunset": CURT_SUNRISE},
        {"sunset": CURT_SUNRISE - timedelta(hours=1)},
    ],
    ids=["no_limit", "negative_limit", "no_forecast", "no_day", "reversed_day"],
)
def test_an_unanswerable_question_returns_none_not_zero(overrides):
    """None means "no opinion"; 0.0 would mean "nothing overflows today"."""
    assert _curtailment(**overrides) is None


def test_a_zero_forecast_still_answers():
    """The question is answerable, and the answer is that nothing overflows."""
    outlook = _curtailment(forecast_kwh_today=0.0)
    assert outlook is not None
    assert outlook.overflow_kwh == 0.0
    assert outlook.binding_start is None


def test_bad_inputs_raise_rather_than_guess():
    with pytest.raises(ValueError):
        _curtailment(capacity_kwh=0.0)
    with pytest.raises(ValueError):
        _curtailment(house_load_kw_profile=[0.7] * 23)
    with pytest.raises(ValueError):
        _curtailment(step_minutes=0)


def test_a_higher_house_load_leaves_less_to_spill():
    """The cap is on what reaches the grid, so the house eats into it first."""
    quiet = _curtailment(house_load_kw_profile=[0.2] * 24)
    busy = _curtailment(house_load_kw_profile=[3.0] * 24)
    assert busy.overflow_kwh < quiet.overflow_kwh
