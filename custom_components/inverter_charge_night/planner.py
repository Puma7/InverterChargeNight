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
from datetime import UTC, date, datetime, timedelta

from .const import (
    BRIDGE_MODEL_FIXED_DELAY,
    BRIDGE_MODEL_MODELLED,
    CURTAILMENT_INTEGRATION_STEP_MIN,
    DEFAULT_GRID_CONTINUOUS_PCT,
    DEFAULT_GRID_VOLTAGE_V,
    DEFAULT_SAFE_FALLBACK_SOC,
    RESERVE_DROP_MARGIN_CT,
)
from .prices import evening_reserve_pays

REASON_BRIDGE = "bridge"
REASON_HEADROOM = "headroom"
REASON_CONFLICT_BRIDGE_WINS = "conflict_bridge_wins"
REASON_CONFLICT_HEADROOM_WINS = "conflict_headroom_wins"
REASON_FALLBACK = "fallback"

BALANCE_STEP_S = 300  # plan 015 stage B: the balance's time step


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
    # Energy on its way out of the battery passes through the inverter as well,
    # so the battery has to hold more than the house will draw. 1.0 means "do
    # not account for it" and is what a caller that predates this field gets.
    discharge_efficiency: float = 1.0
    prices_ct: tuple[float, float, float] | None = None  # (night, day, feed-in)
    # The next high-price period after the window, e.g. 18:00-21:00 tomorrow.
    # The house must come through it without buying at that tariff.
    high_price_window: tuple[datetime, datetime] | None = None
    # The load profile is an average of the last days; an evening with the oven
    # on is above it. This is the user's allowance for that, in percent.
    reserve_margin_pct: float = 0.0
    # The mean price over this entry's own window and over the high-price
    # period, when a price entity supplies them. Separate from prices_ct, which
    # is the conflict triple and needs all three of its values to mean
    # anything. None on either side keeps the reserve, which is what the period
    # was configured for.
    window_price_ct: float | None = None
    evening_price_ct: float | None = None
    # Plan 015 stage A: carried on top of the lower bound, but only into room
    # the upper bound leaves - a buffer must never make the sun spill. Bought
    # once and carried: each night plans from the battery as it finds it.
    safety_buffer_kwh: float = 0.0
    # Plan 015 stage C: ``house_load_kw_profile`` is what the night target is
    # bought for, and may be set above the mean. The room kept free for the sun
    # must not be: a high load estimate there means less room, and the battery
    # spills. This is the mean the room is reckoned with; None means the same
    # profile as the need, which is what a caller that predates it gets.
    room_load_kw_profile: Sequence[float] | None = None
    # Plan 015 stage B. With ``modelled``, a sunrise and a forecast, the bridge
    # is no longer "load until sunrise + delay" but a balance, every five
    # minutes from the window end, of the load against tomorrow's PV curve -
    # the clear-sky bell, or ``pv_hourly_shape`` (24 fractions of the day's
    # yield, learned from the PV meter) where there is one.
    bridge_model: str = BRIDGE_MODEL_FIXED_DELAY
    sunrise: datetime | None = None
    pv_hourly_shape: Sequence[float] | None = None


@dataclass(frozen=True)
class PlanResult:
    """The planner's answer plus the numbers it was derived from.

    Energies here are what the **battery** has to hold, not what the house will
    draw: the two differ by the discharge loss, and the planner's job is to buy
    the first so the house gets the second.
    """

    target_soc: float
    bridge_kwh: float
    surplus_kwh: float
    lower_bound_soc: float
    upper_bound_soc: float
    reason: str
    # What the high-price period is expected to draw (house side), and how much
    # the battery therefore has to gain tonight because the day's surplus will
    # not cover it - that second figure is battery side, so it carries the
    # discharge loss, the same way ``bridge_kwh`` does.
    evening_reserve_kwh: float = 0.0
    evening_shortfall_kwh: float = 0.0
    # True when prices said the evening is cheap enough that holding energy
    # back for it costs more than buying it then.
    evening_reserve_dropped: bool = False
    # The safety buffer the target carries above the lower bound, in SOC
    # points, and whether the upper bound cut it short.
    buffer_soc: float = 0.0
    buffer_capped: bool = False
    # Plan 015 stage B: when the forecast PV first carries the planned load,
    # or None where the fixed delay decided it
    pv_crossover: datetime | None = None


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
    zone = start.tzinfo
    # Advance along real time: a local hour can be absent or occur twice.
    if zone is not None:
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
    if end <= start:
        return 0.0
    energy_kwh = 0.0
    cursor = start
    while cursor < end:
        local = cursor.astimezone(zone) if zone is not None else cursor
        seconds_left = 3600 - local.minute * 60 - local.second - local.microsecond / 1_000_000
        segment_end = min(cursor + timedelta(seconds=seconds_left), end)
        hours = (segment_end - cursor).total_seconds() / 3600.0
        energy_kwh += float(profile[local.hour]) * hours
        cursor = segment_end
    return energy_kwh


def evening_reserve_kwh(p: PlanInput) -> float:
    """The energy the high-price period is expected to draw, in kWh.

    Zero without a configured period, and zero when prices say the period is
    not worth carrying: a kilowatt-hour put aside in the window has to pass
    through the inverter twice, and an evening that is cheaper than that round
    trip is an evening to buy rather than to save for. Unknown prices hold the
    reserve, which is what the period was configured for.

    The margin is the user's allowance for an evening above the average the
    profile was learned from.
    """
    if p.high_price_window is None:
        return 0.0
    if not evening_reserve_pays(
        p.window_price_ct, p.evening_price_ct, p.charge_efficiency, RESERVE_DROP_MARGIN_CT
    ):
        return 0.0
    start, end = p.high_price_window
    load_kwh = integrate_load(p.house_load_kw_profile, start, end)
    return load_kwh * (1 + max(0.0, p.reserve_margin_pct) / 100.0)


def _room_profile(p: PlanInput) -> Sequence[float]:
    """The load the room for tomorrow's sun is reckoned with (plan 015 stage C)."""
    return p.room_load_kw_profile if p.room_load_kw_profile is not None else p.house_load_kw_profile


def quantile(values: Sequence[float], q: float) -> float:
    """The ``q`` quantile of ``values``, interpolated between neighbours.

    The same rule as numpy's default: position ``q * (n - 1)`` in the sorted
    values. Raises ValueError for an empty sequence.
    """
    if not values:
        raise ValueError("No values for a quantile")
    ordered = sorted(values)
    position = _clamp(q, 0.0, 1.0) * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def need_profile(
    samples: Sequence[Sequence[float]],
    mean_profile: Sequence[float],
    q: float | None,
    min_samples: int,
) -> list[float]:
    """The hourly load the night target is bought for (plan 015 stage C).

    ``q`` None is the mean. Otherwise each hour of the day takes the ``q``
    quantile of its days, but never less than the mean - an hour whose days
    are skewed low must not buy less than before - and an hour with fewer
    than ``min_samples`` days keeps the mean.

    Summed over the hours, per-hour quantiles lie above the quantile of the
    daily total. For a safety level that is the point, not a flaw.
    """
    result = [float(value) for value in mean_profile]
    if q is None:
        return result
    for hour, values in enumerate(samples[: len(result)]):
        if len(values) >= min_samples:
            result[hour] = max(result[hour], quantile(values, q))
    return result


def surplus_kwh(p: PlanInput, *, cautious: bool = False) -> float:
    """Tomorrow's forecast (with margin) minus the load it has to cover first.

    What is left over is what the day will actually put into the battery, and
    therefore what the evening does not have to be bought for.

    The margin says how far the forecast may be off, and each use takes the
    side that is safe for it: the room kept free for the sun assumes more of
    it, the evening that has to be carried assumes less (``cautious``). Until
    21 October 2026 the evening was credited the larger forecast too - 12.0
    instead of 10.9 kWh - and the night bought too little for it.
    """
    # Both directions are the safe one: the room kept free for the sun takes
    # the mean load (less self-use, more room), the evening the higher need
    # (less surplus credited to it).
    profile = p.house_load_kw_profile if cautious else _room_profile(p)
    daytime_load_kwh = integrate_load(profile, p.pv_crossover, p.sunset)
    margin = max(0.0, p.error_margin_pct) / 100.0
    factor = max(0.0, 1 - margin) if cautious else 1 + margin
    forecast_with_margin = max(0.0, p.forecast_kwh_next_day) * factor
    return max(0.0, forecast_with_margin - daytime_load_kwh)


def evening_shortfall_kwh(p: PlanInput) -> float:
    """What the high-price period needs and the sun will not deliver.

    Without a usable forecast the surplus counts for nothing: a surplus nobody
    can see is one nobody may plan on.

    Reserve and surplus are both house side and are netted there; the caller
    grosses the difference up for the way out of the battery. That credits the
    surplus at ``1 / discharge_efficiency`` rather than at the grid charge
    efficiency, which is right for a DC-coupled hybrid inverter: the forecast
    is AC output, and the PV reaches the battery on the DC side without the AC
    conversion the grid charge efficiency describes. ``evening_outlook`` uses
    the charge efficiency instead - the conservative direction, for a check
    that acts on its own.
    """
    covered_by_pv = surplus_kwh(p, cautious=True) if p.forecast_available else 0.0
    return max(0.0, evening_reserve_kwh(p) - covered_by_pv)


def _from_battery_kwh(house_kwh: float, discharge_efficiency: float) -> float:
    """What the battery must hold to deliver ``house_kwh`` to the house.

    The house load profile and the forecast are both measured on the house
    side of the inverter. Energy coming out of the battery is not: it loses a
    few percent on the way, and a plan that ignores that buys a few percent too
    little - every time, in the same direction, which is the kind of error that
    only shows up as "the battery did not quite make it through the evening".
    """
    efficiency = min(1.0, max(0.01, discharge_efficiency))
    return house_kwh / efficiency


def evening_reserve_soc(p: PlanInput) -> float:
    """The SOC the battery must not fall below if the evening is to be covered.

    The counterpart of the lower bound in :func:`plan_target_soc`, for the
    discharge direction: morning discharge empties the battery into the morning
    peak, and what the evening needs must not be sold there. Never below the
    user minimum, never above the user maximum.
    """
    if p.capacity_kwh <= 0:
        raise ValueError("Battery capacity must be positive")
    shortfall = _from_battery_kwh(evening_shortfall_kwh(p), p.discharge_efficiency)
    return _clamp(
        p.user_min_soc + shortfall / p.capacity_kwh * 100.0,
        p.user_min_soc,
        p.user_max_soc,
    )


@dataclass(frozen=True)
class DayBalance:
    """The modelled day behind a plan (plan 015 stage B); battery-side kWh."""

    required_kwh: float     # the lowest the balance falls: what the battery must hold
    bridge_kwh: float       # of that, what is drawn before the PV first carries the load
    crossover: datetime     # when it first does, or the evening start if it never does
    rise_kwh: float         # the highest the room balance climbs: what the sun puts in
    surplus_kwh: float      # house side: what the PV gives beyond the room load


def _pv_kw(
    p: PlanInput, sunrise: datetime, when: datetime, local: datetime, solar_day: date,
    daily_kwh: float,
) -> float:
    shape = p.pv_hourly_shape
    if shape is not None:
        # A fraction of the day's yield in that clock hour is its mean power in
        # kW - on the forecast's own day only: an evening period that runs past
        # midnight must not be credited the next morning's sun (Codex on #42)
        if local.date() != solar_day:
            return 0.0
        return max(0.0, daily_kwh) * float(shape[local.hour])
    return pv_power_kw_at(sunrise, p.sunset, when, daily_kwh)


def _evening(p: PlanInput) -> tuple[datetime, datetime] | None:
    """The high-price period the night buys for, or None when it does not."""
    if p.high_price_window is None:
        return None
    if not evening_reserve_pays(
        p.window_price_ct, p.evening_price_ct, p.charge_efficiency, RESERVE_DROP_MARGIN_CT
    ):
        return None
    return p.high_price_window


def day_balance(p: PlanInput) -> DayBalance | None:
    """Run the planned day in five-minute steps (plan 015 stage B).

    The need side - the higher load, the cautious forecast (less sun) - says
    how low the battery falls between the window end and the end of the
    high-price period: that is what it has to hold. The room side - the mean
    load, the generous forecast (more sun) - says how high the sun lifts it
    before sunset: that is what has to stay free. Both are the safe direction
    for what they decide.

    Energy out of the battery is grossed up by the discharge efficiency;
    PV into it is credited one to one, as the surplus always has been (a
    DC-coupled battery sees the panels before the AC conversion).

    None where the model cannot run: no sunrise, no forecast, a profile of the
    wrong length, or a day that ends before the window does.
    """
    if (p.bridge_model != BRIDGE_MODEL_MODELLED or p.sunrise is None
            or not p.forecast_available):
        return None
    if len(p.house_load_kw_profile) != 24 or len(_room_profile(p)) != 24:
        return None
    if p.pv_hourly_shape is not None and len(p.pv_hourly_shape) != 24:
        return None
    evening = _evening(p)
    end = evening[1] if evening is not None else p.sunset
    cutoff = evening[0] if evening is not None and evening[0] < p.sunset else p.sunset
    if end <= p.window_end:
        return None
    margin = max(0.0, p.error_margin_pct) / 100.0
    forecast = max(0.0, p.forecast_kwh_next_day)
    low_kwh, high_kwh = forecast * max(0.0, 1 - margin), forecast * (1 + margin)
    evening_factor = 1 + max(0.0, p.reserve_margin_pct) / 100.0
    efficiency = min(1.0, max(0.01, p.discharge_efficiency))
    room = _room_profile(p)
    zone = p.window_end.tzinfo
    step = timedelta(seconds=BALANCE_STEP_S)
    need_level = room_level = 0.0
    lowest = highest = 0.0
    surplus = 0.0
    crossover: datetime | None = None
    bridge = 0.0
    cursor = p.window_end.astimezone(UTC) if zone is not None else p.window_end
    finish = end.astimezone(UTC) if zone is not None else end
    sunrise = p.sunrise.astimezone(UTC) if zone is not None else p.sunrise
    solar_day = (p.sunrise.astimezone(zone) if zone is not None else p.sunrise).date()
    # The room side runs to sunset whatever the period: PV after a period that
    # ends early still has to fit (Codex on #42)
    sunset = p.sunset.astimezone(UTC) if zone is not None else p.sunset
    last = max(finish, sunset)
    while cursor < last:
        # Steps end on the clock's five-minute marks, so every step lies in one
        # hour of the profile and the evening starts on a step boundary
        into_step = (cursor.minute * 60 + cursor.second + cursor.microsecond / 1e6) % BALANCE_STEP_S
        bound = finish if cursor < finish else last
        span = min(step - timedelta(seconds=into_step), bound - cursor)
        hours = span.total_seconds() / 3600.0
        middle = cursor + span / 2
        local = middle.astimezone(zone) if zone is not None else middle
        if cursor < finish:
            in_evening = evening is not None and evening[0] <= local < evening[1]
            need = float(p.house_load_kw_profile[local.hour]) * (
                evening_factor if in_evening else 1.0
            )
            pv_low = _pv_kw(p, sunrise, middle, local, solar_day, low_kwh)
            net = (pv_low - need) * hours
            need_level += net if net >= 0 else net / efficiency
            lowest = min(lowest, need_level)
            if crossover is None and middle >= sunrise and pv_low >= need > 0:
                start = cursor.astimezone(zone) if zone is not None else cursor
                crossover = start
                bridge = -lowest
        pv_high = _pv_kw(p, sunrise, middle, local, solar_day, high_kwh)
        excess = (pv_high - float(room[local.hour])) * hours
        surplus += max(0.0, excess)
        room_level += excess if excess >= 0 else excess / efficiency
        highest = max(highest, room_level)
        cursor += span
    required = -lowest
    if crossover is None:
        crossover = cutoff
        bridge = required
    return DayBalance(required, bridge, crossover, highest, surplus)


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
    # Both figures are what the battery has to hold, not what the house draws:
    # the difference is the inverter's discharge loss, and ignoring it under-buys
    # a little every single night.
    bridge_kwh = _from_battery_kwh(
        integrate_load(p.house_load_kw_profile, p.window_end, p.pv_crossover),
        p.discharge_efficiency,
    ) + max(0.0, float(p.reserve_kwh))
    surplus = surplus_kwh(p)

    evening_kwh = evening_reserve_kwh(p)
    # Asking the gate rather than inferring it from a zero. A period whose load
    # profile happens to be zero also gives 0.0 kWh, and reporting that as
    # "prices dropped the reserve" names a decision no price ever made.
    evening_dropped = p.high_price_window is not None and not evening_reserve_pays(
        p.window_price_ct, p.evening_price_ct, p.charge_efficiency, RESERVE_DROP_MARGIN_CT
    )
    # The sun charges the battery before the evening does, so only the part it
    # will not cover has to be bought tonight.
    evening_shortfall = _from_battery_kwh(evening_shortfall_kwh(p), p.discharge_efficiency)

    lower = _clamp(
        (bridge_kwh + evening_shortfall + capacity * p.user_min_soc / 100.0)
        / capacity
        * 100.0,
        p.user_min_soc,
        p.user_max_soc,
    )
    # The bridge as the room sees it: the mean morning, so that a high need
    # estimate cannot pretend the battery will be emptier when the sun comes
    room_bridge_kwh = _from_battery_kwh(
        integrate_load(_room_profile(p), p.window_end, p.pv_crossover),
        p.discharge_efficiency,
    ) + max(0.0, float(p.reserve_kwh))
    upper = _clamp(
        (1.0 - max(0.0, surplus - room_bridge_kwh) / capacity) * 100.0,
        p.user_min_soc,
        p.user_max_soc,
    )

    modelled_crossover: datetime | None = None
    balance = day_balance(p)
    if balance is not None:
        # Plan 015 stage B: the balance replaces both bounds. The reserve is
        # held on top of what the day draws, and is no room for the sun.
        reserve = max(0.0, float(p.reserve_kwh))
        bridge_kwh = balance.bridge_kwh + reserve
        evening_shortfall = max(0.0, balance.required_kwh - balance.bridge_kwh)
        surplus = balance.surplus_kwh
        lower = _clamp(
            (balance.required_kwh + reserve + capacity * p.user_min_soc / 100.0)
            / capacity * 100.0,
            p.user_min_soc,
            p.user_max_soc,
        )
        upper = _clamp(
            (1.0 - balance.rise_kwh / capacity) * 100.0, p.user_min_soc, p.user_max_soc
        )
        modelled_crossover = balance.crossover

    buffer_soc = max(0.0, float(p.safety_buffer_kwh)) / capacity * 100.0

    if not p.forecast_available:
        # Same protection as the headroom formula: never charge to the maximum
        # on a missing forecast, use the safe fallback instead. Without a
        # forecast there is no upper bound to cap the buffer at, so it simply
        # rides on the lower bound; the fallback already assumes a dull day.
        buffered = lower + buffer_soc
        target = _clamp(
            max(DEFAULT_SAFE_FALLBACK_SOC, buffered), p.user_min_soc, p.user_max_soc
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
            evening_dropped,
            # The lift to the safe fallback is no buffer anybody configured
            # (CodeRabbit on #42)
            round(min(buffer_soc, max(0.0, target - lower)), 1),
            target < buffered,
            modelled_crossover,
        )

    applied_buffer = 0.0
    buffer_capped = False
    if lower <= upper:
        # The buffer fills room the sun will not need, and no more: capped at
        # the upper bound, it can never make the battery spill PV tomorrow.
        target, reason = min(lower + buffer_soc, upper), REASON_BRIDGE
        applied_buffer = target - lower
        buffer_capped = buffer_soc > 0 and lower + buffer_soc > upper
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

    clamped = _clamp(target, p.user_min_soc, p.user_max_soc)
    if clamped < target:
        buffer_capped = buffer_capped or applied_buffer > 0
        applied_buffer = max(0.0, applied_buffer - (target - clamped))
    return PlanResult(
        round(clamped, 1),
        bridge_kwh,
        surplus,
        round(lower, 1),
        round(upper, 1),
        reason,
        evening_kwh,
        evening_shortfall,
        evening_dropped,
        round(applied_buffer, 1),
        buffer_capped,
        modelled_crossover,
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


# The evening outlook (plan 013) ------------------------------------------------
#
# The night plan asks "how much must the battery hold by morning". This asks the
# question the day after a forecast that was too optimistic: "will there still be
# enough in it when the expensive hours start this evening, and if not, by how
# much is it short". Pure arithmetic again - the coordinator reads the states.


@dataclass(frozen=True)
class EveningOutlook:
    """What the battery is heading for at the start of the high-price period."""

    zone_start: datetime
    zone_end: datetime
    required_soc: float        # what it has to be at zone_start
    projected_soc: float       # what it will be if nothing is done
    missing_kwh: float         # battery side, 0.0 when it will make it
    pv_to_come_kwh: float      # what the sun is still expected to deliver
    load_to_come_kwh: float    # what the house will draw before then
    forecast_available: bool


def pv_fraction_between(
    sunrise: datetime, sunset: datetime, start: datetime, end: datetime
) -> float:
    """The share of a day's PV energy produced between two instants.

    A clear-sky bell: output follows ``sin(pi * x)`` across the solar day, so
    the energy between two points is ``(cos(pi*x1) - cos(pi*x2)) / 2``.

    It is an approximation and is used as one. Clouds, orientation, shading and
    snow all move it, and it is deliberately not linear: at four in the
    afternoon a linear model still promises half the day's yield, which is the
    one error that matters here - it would let the battery run into the evening
    short while the plan says it is fine.
    """
    day_s = (sunset - sunrise).total_seconds()
    if day_s <= 0:
        return 0.0
    first = _clamp((start - sunrise).total_seconds() / day_s, 0.0, 1.0)
    last = _clamp((end - sunrise).total_seconds() / day_s, 0.0, 1.0)
    if last <= first:
        return 0.0
    return (math.cos(math.pi * first) - math.cos(math.pi * last)) / 2.0


def evening_outlook(
    *,
    now: datetime,
    capacity_kwh: float,
    current_soc: float,
    user_min_soc: float,
    user_max_soc: float,
    house_load_kw_profile: Sequence[float],
    zone_start: datetime,
    zone_end: datetime,
    margin_pct: float,
    charge_efficiency: float,
    discharge_efficiency: float,
    sunrise: datetime,
    sunset: datetime,
    forecast_kwh_today: float,
    forecast_available: bool,
    forecast_error_margin_pct: float = 0.0,
) -> EveningOutlook:
    """Project the battery forward to the start of the high-price period.

    What it needs there is the period's own load, grossed up for the way out of
    the battery, on top of the user minimum - the day's surplus is *not*
    subtracted here the way it is in the night plan, because the sun between now
    and then is already in the projection. That sun is the forecast less its
    error margin, as for the evening in the night plan (:func:`surplus_kwh`).

    Raises ValueError for a non-positive capacity, like the other entry points.
    """
    if capacity_kwh <= 0:
        raise ValueError("Battery capacity must be positive")

    zone_load_kwh = integrate_load(house_load_kw_profile, zone_start, zone_end) * (
        1 + max(0.0, margin_pct) / 100.0
    )
    required_soc = _clamp(
        user_min_soc + _from_battery_kwh(zone_load_kwh, discharge_efficiency) / capacity_kwh * 100.0,
        user_min_soc,
        user_max_soc,
    )

    load_to_come = integrate_load(house_load_kw_profile, now, zone_start)
    cautious = max(0.0, 1 - max(0.0, forecast_error_margin_pct) / 100.0)
    pv_to_come = (
        max(0.0, forecast_kwh_today) * cautious
        * pv_fraction_between(sunrise, sunset, now, zone_start)
        if forecast_available
        else 0.0
    )
    # The sun serves the house first; only what is left over reaches the
    # battery, and only a shortfall has to come out of it.
    net_house_kwh = pv_to_come - load_to_come
    if net_house_kwh >= 0:
        delta_battery_kwh = net_house_kwh * min(1.0, max(0.01, charge_efficiency))
    else:
        delta_battery_kwh = _from_battery_kwh(net_house_kwh, discharge_efficiency)

    projected_soc = _clamp(
        current_soc + delta_battery_kwh / capacity_kwh * 100.0, 0.0, user_max_soc
    )
    missing_kwh = max(0.0, (required_soc - projected_soc) / 100.0 * capacity_kwh)

    return EveningOutlook(
        zone_start=zone_start,
        zone_end=zone_end,
        required_soc=round(required_soc, 1),
        projected_soc=round(projected_soc, 1),
        missing_kwh=missing_kwh,
        pv_to_come_kwh=pv_to_come,
        load_to_come_kwh=load_to_come,
        forecast_available=forecast_available,
    )


def pv_power_kw_at(
    sunrise: datetime, sunset: datetime, when: datetime, daily_kwh: float
) -> float:
    """Clear-sky output at one instant, in kW.

    This is the derivative of :func:`pv_fraction_between` and has to stay that
    way: the energy between two points is ``(cos(pi*x1) - cos(pi*x2)) / 2``, so
    the density is ``(pi/2) * sin(pi*x)`` per unit of ``x``, and ``x`` runs
    across the solar day. Integrating this curve over any stretch reproduces
    the fraction exactly, which is what keeps a display built on one from
    contradicting a decision built on the other.

    Zero outside the solar day, and zero for a day that has no length.
    """
    day_s = (sunset - sunrise).total_seconds()
    if day_s <= 0 or daily_kwh <= 0:
        return 0.0
    x = (when - sunrise).total_seconds() / day_s
    if x <= 0.0 or x >= 1.0:
        return 0.0
    day_h = day_s / 3600.0
    return daily_kwh * (math.pi / 2.0) * math.sin(math.pi * x) / day_h


@dataclass(frozen=True)
class CurtailmentOutlook:
    """What a permanent feed-in cap will throw away today, and what would hold it."""

    limit_w: float
    # The envelope of the hours the cap actually bites in. None on a day it
    # never bites - which is the honest answer on a dull day, not zero.
    binding_start: datetime | None
    binding_end: datetime | None
    overflow_kwh: float          # thrown away today if the battery has no room
    room_needed_kwh: float       # what the battery has to have free by then
    morning_target_soc: float    # the cap's target, already clamped
    clamped_by: str | None       # "evening_reserve" | "user_min" | None
    forecast_available: bool


CLAMPED_BY_EVENING_RESERVE = "evening_reserve"
CLAMPED_BY_USER_MIN = "user_min"


def curtailment_outlook(
    *,
    capacity_kwh: float,
    user_min_soc: float,
    user_max_soc: float,
    house_load_kw_profile: Sequence[float],
    sunrise: datetime,
    sunset: datetime,
    forecast_kwh_today: float,
    forecast_available: bool,
    error_margin_pct: float,
    limit_w: float,
    charge_efficiency: float,
    evening_reserve_soc_pct: float,
    step_minutes: int = CURTAILMENT_INTEGRATION_STEP_MIN,
) -> CurtailmentOutlook | None:
    """How much PV a permanent feed-in cap will throw away today.

    A cap at the grid connection point is not an event somebody switches on:
    it bites in exactly the hours where ``PV - house load`` exceeds it, and
    those follow from the forecast, the sun times and the load profile. On a
    dull day the answer is correctly "it never bites".

    Returns ``None`` when the question cannot be asked - no cap configured, no
    forecast, a day with no length. None means "no opinion" and every caller
    has to treat it as such; it is never 0.0, because 0.0 is a real answer.

    The forecast is *damped* by the error margin here, the opposite direction
    from :func:`surplus_kwh`, which inflates it. Both lean the same way in the
    end: away from a battery that is empty in the evening. Overestimating the
    overflow throttles the morning on a day that did not need it, and that day
    costs twice - short in the evening, and then bought back from the grid.
    """
    if capacity_kwh <= 0:
        raise ValueError("Battery capacity must be positive")
    if limit_w <= 0 or not forecast_available or sunset <= sunrise:
        return None
    if step_minutes <= 0:
        raise ValueError("Integration step must be positive")
    if len(house_load_kw_profile) != 24:
        raise ValueError(
            f"Load profile needs 24 hourly values, got {len(house_load_kw_profile)}"
        )

    damped_kwh = max(0.0, forecast_kwh_today) * max(
        0.0, 1.0 - max(0.0, error_margin_pct) / 100.0
    )
    limit_kw = limit_w / 1000.0
    step = timedelta(minutes=step_minutes)

    overflow_kwh = 0.0
    first: datetime | None = None
    last: datetime | None = None
    cursor = sunrise
    while cursor < sunset:
        segment_end = min(cursor + step, sunset)
        hours = (segment_end - cursor).total_seconds() / 3600.0
        # Sample at the midpoint: the bell is curved, and its endpoints are the
        # two places a rectangle rule is worst.
        midpoint = cursor + (segment_end - cursor) / 2
        pv_kw = pv_power_kw_at(sunrise, sunset, midpoint, damped_kwh)
        load_kw = float(house_load_kw_profile[midpoint.hour])
        excess_kw = pv_kw - load_kw - limit_kw
        if excess_kw > 0:
            overflow_kwh += excess_kw * hours
            if first is None:
                first = cursor
            last = segment_end
        cursor = segment_end

    # What the overflow will occupy in the battery is the AC amount times the
    # charge efficiency, not divided by it - the loss happens on the way in, so
    # less arrives than was diverted. This is the opposite direction from the
    # night plan, where the question is how much to buy to store a given amount.
    room_needed_kwh = overflow_kwh * min(1.0, max(0.01, charge_efficiency))
    unclamped_soc = user_max_soc - room_needed_kwh / capacity_kwh * 100.0

    clamped_by: str | None = None
    floor = user_min_soc
    if evening_reserve_soc_pct > floor:
        floor = evening_reserve_soc_pct
    if unclamped_soc < floor:
        clamped_by = (
            CLAMPED_BY_EVENING_RESERVE
            if floor == evening_reserve_soc_pct and evening_reserve_soc_pct > user_min_soc
            else CLAMPED_BY_USER_MIN
        )

    return CurtailmentOutlook(
        limit_w=limit_w,
        binding_start=first,
        binding_end=last,
        overflow_kwh=overflow_kwh,
        room_needed_kwh=room_needed_kwh,
        morning_target_soc=round(_clamp(unclamped_soc, floor, user_max_soc), 1),
        clamped_by=clamped_by,
        forecast_available=forecast_available,
    )
