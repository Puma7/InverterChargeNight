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

from .const import DEFAULT_SAFE_FALLBACK_SOC

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


@dataclass(frozen=True)
class PlanResult:
    """The planner's answer plus the numbers it was derived from."""

    target_soc: float
    bridge_kwh: float
    surplus_kwh: float
    lower_bound_soc: float
    upper_bound_soc: float
    reason: str


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
    daytime_load_kwh = integrate_load(p.house_load_kw_profile, p.pv_crossover, p.sunset)
    forecast_with_margin = max(0.0, p.forecast_kwh_next_day) * (1 + p.error_margin_pct / 100.0)
    surplus_kwh = max(0.0, forecast_with_margin - daytime_load_kwh)

    lower = _clamp(
        (bridge_kwh + capacity * p.user_min_soc / 100.0) / capacity * 100.0,
        p.user_min_soc,
        p.user_max_soc,
    )
    upper = _clamp(
        (1.0 - max(0.0, surplus_kwh - bridge_kwh) / capacity) * 100.0,
        p.user_min_soc,
        p.user_max_soc,
    )

    if not p.forecast_available:
        # Same protection as the headroom formula: never charge to the maximum
        # on a missing forecast, use the safe fallback instead.
        target = _clamp(DEFAULT_SAFE_FALLBACK_SOC, p.user_min_soc, p.user_max_soc)
        return PlanResult(
            round(target, 1), bridge_kwh, surplus_kwh, round(lower, 1), round(upper, 1), REASON_FALLBACK
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
    return PlanResult(round(target, 1), bridge_kwh, surplus_kwh, round(lower, 1), round(upper, 1), reason)


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
