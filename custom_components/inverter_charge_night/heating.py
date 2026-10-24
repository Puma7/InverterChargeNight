"""Plan 015 stage D: how much more the house draws on a cold day.

A heat pump turns frost into kilowatt-hours. The load profile is learned from
the last fortnight, and a night before a day ten degrees colder than that
fortnight buys too little. This module holds the arithmetic and nothing else:
the coordinator reads Home Assistant and fills in the numbers.

The model is the heating engineer's one, heating degrees below a limit:

    heating_degrees(T) = max(0, limit - T)
    daily_kwh          = a + b * heating_degrees(T)

fitted by least squares over the days the profile was learned from. A house
without electric heating has ``b`` near zero, or a fit that explains nothing,
and the factor stays 1: the feature switches itself off where it has nothing
to say.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# The heating limit of a typical German house: below a daily mean of 15 °C it heats
DEFAULT_HEATING_LIMIT_C = 15.0
# Fewer days, or days too alike, and a slope is noise
MIN_FIT_DAYS = 10
MIN_HEATING_SPREAD_K = 3.0
MIN_FIT_R2 = 0.3
# A forecast is a forecast: never let it halve the need, nor more than half again
FACTOR_MIN = 0.85
FACTOR_MAX = 1.5
# The self-collected daily means kept for learning
TEMPERATURE_LOG_DAYS = 60
# A day with fewer samples than this (the coordinator polls every 15 minutes)
# was not watched long enough for its mean to stand for the day
MIN_SAMPLES_PER_DAY = 24

STATUS_OFF = "off"
STATUS_LEARNING = "learning"
STATUS_ACTIVE = "active"
STATUS_FORECAST_UNAVAILABLE = "forecast_unavailable"


def heating_degrees(temperature_c: float, limit_c: float) -> float:
    """How far a daily mean lies below the heating limit, in kelvin."""
    return max(0.0, limit_c - temperature_c)


def to_celsius(value: float, unit: str | None) -> float:
    """A temperature in °C, from °C or °F; anything else is taken as °C."""
    if unit is not None and unit.strip().upper() in ("°F", "F", "℉"):
        return (value - 32.0) * 5.0 / 9.0
    return value


@dataclass(frozen=True)
class HeatingModel:
    """``daily_kwh = intercept + slope * heating_degrees``, and how well it fits."""

    intercept_kwh: float
    slope_kwh_per_k: float
    r2: float
    days: int
    spread_k: float

    @property
    def usable(self) -> bool:
        """Enough days, enough spread, a real slope and a fit worth the name."""
        return (
            self.days >= MIN_FIT_DAYS
            and self.spread_k >= MIN_HEATING_SPREAD_K
            and self.slope_kwh_per_k > 0
            and self.r2 >= MIN_FIT_R2
        )

    def daily_kwh(self, degrees: float) -> float:
        return self.intercept_kwh + self.slope_kwh_per_k * degrees


def fit_heating_model(
    days: Iterable[tuple[float, float]], limit_c: float
) -> HeatingModel | None:
    """Least squares over ``(daily mean °C, daily kWh)`` pairs; None without two days.

    A negative slope - more use on warm days - is no heating at all and is
    reported as a zero slope, which makes the model unusable.
    """
    pairs = [(heating_degrees(t, limit_c), kwh) for t, kwh in days
             if math.isfinite(t) and math.isfinite(kwh) and kwh >= 0]
    n = len(pairs)
    if n < 2:
        return None
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in pairs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    syy = sum((y - mean_y) ** 2 for _, y in pairs)
    spread = max(x for x, _ in pairs) - min(x for x, _ in pairs)
    if sxx <= 0:
        return HeatingModel(mean_y, 0.0, 0.0, n, spread)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    if slope <= 0:
        return HeatingModel(mean_y, 0.0, r2, n, spread)
    return HeatingModel(intercept, slope, r2, n, spread)


def temperature_factor(
    model: HeatingModel | None,
    forecast_c: float,
    profile_degrees: float,
    limit_c: float,
) -> float:
    """How much more (or less) tomorrow draws than the days the profile is from.

    ``profile_degrees`` is the mean heating degrees of those days. 1.0 for an
    unusable model; clamped to ``FACTOR_MIN`` .. ``FACTOR_MAX`` otherwise.
    """
    if model is None or not model.usable:
        return 1.0
    baseline = model.daily_kwh(profile_degrees)
    if baseline <= 0:
        return 1.0
    expected = model.daily_kwh(heating_degrees(forecast_c, limit_c))
    return min(FACTOR_MAX, max(FACTOR_MIN, expected / baseline))


def _local_day(raw: Any, to_local: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    local = to_local(parsed) if parsed.tzinfo is not None else parsed
    return local.date() if isinstance(local, datetime) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def daily_mean_from_forecast(
    forecast: Sequence[Mapping[str, Any]], day: date, to_local: Any
) -> float | None:
    """The day's mean from a daily forecast: (high + low) / 2, or the high alone.

    ``to_local`` converts an aware datetime to the local zone (dt_util.as_local),
    so a daily entry stamped 12:00 UTC still lands on its local day.
    """
    for entry in forecast:
        if _local_day(entry.get("datetime"), to_local) != day:
            continue
        high = _number(entry.get("temperature"))
        if high is None:
            continue
        low = _number(entry.get("templow"))
        return (high + low) / 2.0 if low is not None else high
    return None


def hourly_mean_from_forecast(
    forecast: Sequence[Mapping[str, Any]], day: date, to_local: Any
) -> float | None:
    """The day's mean from an hourly forecast; None without six hours of it."""
    values = [
        value
        for entry in forecast
        if _local_day(entry.get("datetime"), to_local) == day
        and (value := _number(entry.get("temperature"))) is not None
    ]
    return sum(values) / len(values) if len(values) >= 6 else None


class TemperatureLog:
    """Daily mean outdoor temperatures, collected from the polls themselves.

    For a weather entity without a temperature sensor beside it: the weather
    entity has no long-term statistics to learn from, so the integration keeps
    its own diary. A day counts once it is over and was watched often enough.
    """

    def __init__(self, raw: Any = None) -> None:
        self.days: dict[date, float] = {}
        self._day: date | None = None
        self._sum = 0.0
        self._count = 0
        if not isinstance(raw, Mapping):
            return
        days = raw.get("days")
        if isinstance(days, Mapping):
            for key, value in days.items():
                number = _number(value)
                try:
                    parsed = date.fromisoformat(str(key))
                except ValueError:
                    continue
                if number is not None:
                    self.days[parsed] = number

    def observe(self, today: date, temperature_c: float | None) -> bool:
        """Add a sample; True when a finished day was added to the diary."""
        finished = False
        if self._day is not None and today != self._day:
            if self._count >= MIN_SAMPLES_PER_DAY:
                self.days[self._day] = self._sum / self._count
                finished = True
            self._day, self._sum, self._count = None, 0.0, 0
        if temperature_c is not None and math.isfinite(temperature_c):
            if self._day is None:
                self._day = today
            self._sum += temperature_c
            self._count += 1
        if finished:
            for old in sorted(self.days)[:-TEMPERATURE_LOG_DAYS]:
                del self.days[old]
        return finished

    def snapshot(self) -> dict[str, Any]:
        return {"days": {day.isoformat(): round(mean, 2) for day, mean in sorted(self.days.items())}}
