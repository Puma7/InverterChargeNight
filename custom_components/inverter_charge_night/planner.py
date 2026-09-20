"""Planner v2: target SOC from two bounds and the charge power for the window.

Pure functions only. Every Home Assistant state read lives in the coordinator,
which fills a :class:`PlanInput`; this module must stay free of Home Assistant
imports so it can be tested as plain arithmetic (see plans/README.md section 3).

The two bounds, both in kWh and then converted to SOC:

* ``bridge``  - house load from the window end until the PV output exceeds the
  load (sunrise + delay), plus a reserve. The battery must hold at least this
  much, otherwise the house buys at the day tariff after the window.
* ``surplus`` - tomorrow's forecast (with margin) minus the daytime load until
  sunset. Only the surplus needs room in the battery; the bridge energy is
  consumed again before the PV crossover, so it frees that room by itself.

A high-price period (plan 011) adds to the lower bound, but only by what the
sun will not have delivered by then: the evening's load minus the day's
surplus. Buying the rest of it tonight at the cheap tariff is the same
arbitrage as the bridge, one tariff step further along the day.

``lower <= upper`` means both can be satisfied and the lower bound wins (never
buy more than needed). A conflict is decided by prices when they are known,
otherwise the bridge wins because grid energy by day costs more than the
feed-in tariff that is lost when PV has no room.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .const import (
    DEFAULT_GRID_CONTINUOUS_PCT,
    DEFAULT_GRID_VOLTAGE_V,
    DEFAULT_SAFE_FALLBACK_SOC,
)

REASON_BRIDGE = "bridge"
REASON_HEADROOM = "headroom"
REASON_CONFLICT_BRIDGE_WINS = "conflict_bridge_wins"
REASON_CONFLICT_HEADROOM_WINS = "conflict_headroom_wins"
REASON_FALLBACK = "fallback"


@dataclass(frozen=True)
class PlanInput:
    """Everything the planner needs; all energies in kWh, all SOC values in %."""

    capacity_kwh: float
    current_soc: float
    user_min_soc: float
    user_max_soc: float
    forecast_kwh_next_day: float
    forecast_available: bool
    error_margin_pct: float
    window_end: datetime
    pv_crossover: datetime  # sunrise + delay, on the day after window_end
    sunset: datetime
    house_load_kw_profile: Sequence[float]  # 24 values, one per hour of the day
    reserve_kwh: float
    charge_efficiency: float
    prices_ct: tuple[float, float, float] | None = None  # (night, day, feed-in)
    # The next high-price period after the window, e.g. 18:00-21:00 tomorrow.
    # The house must come through it without buying at that tariff.
    high_price_window: tuple[datetime, datetime] | None = None
    # The load profile is an average of the last days; an evening with the oven
    # on is above it. This is the user's allowance for that, in percent.
    reserve_margin_pct: float = 0.0


@dataclass(frozen=True)
class PlanResult:
    """The planner's answer plus the numbers it was derived from."""

    target_soc: float
    bridge_kwh: float
    surplus_kwh: float
    lower_bound_soc: float
    upper_bound_soc: float
    reason: str
    # What the high-price period is expected to draw, and how much of that the
    # night has to buy because the day's surplus will not cover it.
    evening_reserve_kwh: float = 0.0
    evening_shortfall_kwh: float = 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def integrate_load(profile: Sequence[float], start: datetime, end: datetime) -> float:
    """Integrate an hourly kW profile between two instants and return kWh.

    ``profile`` holds one average power per hour of the day (index 0 = 00:00
    to 01:00). Partial hours count proportionally, and the range may span
    midnight or several days. An empty range (``end <= start``) is 0.
    """
    if len(profile) != 24:
        raise ValueError(f"Load profile needs 24 hourly values, got {len(profile)}")
    if end <= start:
        return 0.0
    energy_kwh = 0.0
    cursor = start
    while cursor < end:
        next_hour = (cursor + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        segment_end = min(next_hour, end)
        hours = (segment_end - cursor).total_seconds() / 3600.0
        energy_kwh += float(profile[cursor.hour]) * hours
        cursor = segment_end
    return energy_kwh


def evening_reserve_kwh(p: PlanInput) -> float:
    """The energy the high-price period is expected to draw, in kWh.

    Zero without a configured period. The margin is the user's allowance for an
    evening above the average the profile was learned from.
    """
    if p.high_price_window is None:
        return 0.0
    start, end = p.high_price_window
    load_kwh = integrate_load(p.house_load_kw_profile, start, end)
    return load_kwh * (1 + max(0.0, p.reserve_margin_pct) / 100.0)


def surplus_kwh(p: PlanInput) -> float:
    """Tomorrow's forecast (with margin) minus the load it has to cover first.

    What is left over is what the day will actually put into the battery, and
    therefore what the evening does not have to be bought for.
    """
    daytime_load_kwh = integrate_load(p.house_load_kw_profile, p.pv_crossover, p.sunset)
    forecast_with_margin = max(0.0, p.forecast_kwh_next_day) * (1 + p.error_margin_pct / 100.0)
    return max(0.0, forecast_with_margin - daytime_load_kwh)


def evening_shortfall_kwh(p: PlanInput) -> float:
    """What the high-price period needs and the sun will not deliver.

    Without a usable forecast the surplus counts for nothing: a surplus nobody
    can see is one nobody may plan on.
    """
    covered_by_pv = surplus_kwh(p) if p.forecast_available else 0.0
    return max(0.0, evening_reserve_kwh(p) - covered_by_pv)


def evening_reserve_soc(p: PlanInput) -> float:
    """The SOC the battery must not fall below if the evening is to be covered.

    The counterpart of the lower bound in :func:`plan_target_soc`, for the
    discharge direction: morning discharge empties the battery into the morning
    peak, and what the evening needs must not be sold there. Never below the
    user minimum, never above the user maximum.
    """
    if p.capacity_kwh <= 0:
        raise ValueError("Battery capacity must be positive")
    shortfall = evening_shortfall_kwh(p)
    return _clamp(
        p.user_min_soc + shortfall / p.capacity_kwh * 100.0,
        p.user_min_soc,
        p.user_max_soc,
    )


def plan_target_soc(p: PlanInput) -> PlanResult:
    """Derive the target SOC from the bridge and surplus bounds.

    Raises ValueError for a non-positive capacity or an invalid SOC range; the
    caller treats that like any other failed calculation.
    """
    if p.capacity_kwh <= 0:
        raise ValueError("Battery capacity must be positive")
    if not 0 <= p.user_min_soc < p.user_max_soc <= 100:
        raise ValueError(
            f"Invalid SOC range: min {p.user_min_soc} must be below max {p.user_max_soc}"
        )

    capacity = float(p.capacity_kwh)
    bridge_kwh = integrate_load(p.house_load_kw_profile, p.window_end, p.pv_crossover) + max(
        0.0, float(p.reserve_kwh)
    )
    surplus = surplus_kwh(p)

    evening_kwh = evening_reserve_kwh(p)
    # The sun charges the battery before the evening does, so only the part it
    # will not cover has to be bought tonight.
    evening_shortfall = evening_shortfall_kwh(p)

    lower = _clamp(
        (bridge_kwh + evening_shortfall + capacity * p.user_min_soc / 100.0)
        / capacity
        * 100.0,
        p.user_min_soc,
        p.user_max_soc,
    )
    upper = _clamp(
        (1.0 - max(0.0, surplus - bridge_kwh) / capacity) * 100.0,
        p.user_min_soc,
        p.user_max_soc,
    )

    if not p.forecast_available:
        # Same protection as the headroom formula: never charge to the maximum
        # on a missing forecast, use the safe fallback instead.
        target = _clamp(
            max(DEFAULT_SAFE_FALLBACK_SOC, lower), p.user_min_soc, p.user_max_soc
        )
        return PlanResult(
            round(target, 1),
            bridge_kwh,
            surplus,
            round(lower, 1),
            round(upper, 1),
            REASON_FALLBACK,
            evening_kwh,
            evening_shortfall,
        )

    if lower <= upper:
        target, reason = lower, REASON_BRIDGE
    elif p.prices_ct is None:
        target, reason = lower, REASON_CONFLICT_BRIDGE_WINS
    else:
        night_ct, day_ct, feed_in_ct = p.prices_ct
        conflict_kwh = (lower - upper) / 100.0 * capacity
        # Charging up to the bridge displaces PV that is then fed in instead of
        # stored: each kWh costs the night price and earns the feed-in tariff.
        cost_bridge = conflict_kwh * (night_ct - feed_in_ct)
        # Stopping at the headroom bound leaves that much of the bridge uncovered:
        # the house buys it by day instead of by night.
        cost_headroom = conflict_kwh * (day_ct - night_ct)
        if cost_bridge <= cost_headroom:
            target, reason = lower, REASON_CONFLICT_BRIDGE_WINS
        else:
            target, reason = upper, REASON_CONFLICT_HEADROOM_WINS

    target = _clamp(target, p.user_min_soc, p.user_max_soc)
    return PlanResult(
        round(target, 1),
        bridge_kwh,
        surplus,
        round(lower, 1),
        round(upper, 1),
        reason,
        evening_kwh,
        evening_shortfall,
    )


def required_charge_power_w(
    target_soc: float,
    current_soc: float,
    capacity_kwh: float,
    hours_remaining: float,
    efficiency: float,
) -> float:
    """Return the constant AC power (W) that reaches ``target_soc`` in time.

    Returns 0 when nothing is missing and ``inf`` when the time is already up,
    so the caller can clamp the value to the inverter's limits.
    """
    if capacity_kwh <= 0:
        raise ValueError("Battery capacity must be positive")
    if not 0 < efficiency <= 1:
        raise ValueError("Charge efficiency must be in (0, 1]")
    missing_kwh = max(0.0, (target_soc - current_soc) / 100.0 * capacity_kwh)
    if missing_kwh == 0:
        return 0.0
    if hours_remaining <= 0:
        return math.inf
    return missing_kwh * 1000.0 / hours_remaining / efficiency


# House connection limit (plan 008) -------------------------------------------
#
# Pure arithmetic, deliberately free of Home Assistant: the coordinator reads the
# states and decides what to do with the result. Both functions round towards
# charging *less* wherever an input is missing or implausible, because the
# consequence of being wrong in the other direction is a warm meter terminal
# over a six-hour window.


def grid_budget_w(
    fuse_a: float | None,
    phases: int,
    voltage_v: float,
    continuous_pct: float,
    explicit_max_w: float | None,
) -> float | None:
    """Return the continuously permissible grid import in W.

    ``None`` means nothing is configured and no limit applies. An explicit
    budget wins over the fuse calculation. Otherwise the rating is
    ``voltage_v * fuse_a`` on one phase and ``3 * voltage_v * fuse_a`` on three
    (which is the same as ``sqrt(3) * 400 V * I`` at a 230 V phase voltage), and
    the budget is ``continuous_pct`` of it.

    Implausible inputs never widen the budget: an unknown phase count counts as
    a single phase, and a voltage or percentage outside its sensible range falls
    back to the documented default instead of being taken at face value. A
    three-phase voltage above 300 V is read as the line-to-line voltage written
    on a German meter cabinet (400 V) and divided by sqrt(3), because taking it
    as a phase voltage would inflate the budget by that same factor.
    """
    if explicit_max_w is not None and math.isfinite(explicit_max_w) and explicit_max_w > 0:
        return float(explicit_max_w)
    if fuse_a is None or not math.isfinite(fuse_a) or fuse_a <= 0:
        return None
    phase_count = 3 if phases == 3 else 1
    volts = float(voltage_v) if math.isfinite(voltage_v) and voltage_v > 0 else float(DEFAULT_GRID_VOLTAGE_V)
    if phase_count == 3 and volts > 300:
        # The number on a German meter cabinet is 400 V, the voltage between two
        # phases. Taken as a phase voltage it would inflate the budget by sqrt(3)
        # and the limit would never engage before the real one is passed.
        volts /= math.sqrt(3.0)
    if not 100.0 <= volts <= 300.0:
        # Whatever that is, it is not a phase voltage of a house connection.
        # The documented default is the safe answer: it can only make the
        # budget smaller, never larger.
        volts = float(DEFAULT_GRID_VOLTAGE_V)
    pct = (
        float(continuous_pct)
        if math.isfinite(continuous_pct) and 0 < continuous_pct <= 100
        else float(DEFAULT_GRID_CONTINUOUS_PCT)
    )
    return phase_count * volts * float(fuse_a) * pct / 100.0


def allowed_charge_power_w(
    budget_w: float,
    grid_import_w: float,
    own_charge_w: float,
    headroom_w: float,
) -> float:
    """Return what the battery may additionally draw from the grid, in W.

    ``grid_import_w`` already contains the battery's own charge power, so it is
    subtracted out: what remains is the load this integration does not control
    (wallboxes, heat pump, the rest of the house). The battery gets whatever the
    budget still has left after that load and the safety margin.

    The result is never negative and never larger than the budget. A non-finite
    input yields 0: an unusable number must stop the charge, not uncap it.
    """
    values = (budget_w, grid_import_w, own_charge_w, headroom_w)
    if not all(math.isfinite(value) for value in values):
        return 0.0
    # Feeding in (a negative import) does not earn the battery extra budget.
    other_load_w = max(0.0, max(0.0, grid_import_w) - max(0.0, own_charge_w))
    return max(0.0, budget_w - max(0.0, headroom_w) - other_load_w)
