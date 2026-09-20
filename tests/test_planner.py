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
    integrate_load,
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
