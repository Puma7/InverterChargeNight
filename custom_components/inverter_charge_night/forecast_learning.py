"""Plan 018 part 1: how far the PV forecast is off, by season.

A forecast service knows the sun and the weather, not this roof: its pitch,
the gap behind the panels, how they behave in the cold, the tree in front of
them, or how far they have aged. Over a season that shows as a steady bias -
too high every winter, too low every summer - and as a spread around it.
Both are read from the house's own data here: what the forecast said, and
what the PV meter then counted.

This module holds the diary and the arithmetic and nothing else. The
coordinator reads Home Assistant and fills in the numbers.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# The forecast the night plans with (the "tomorrow" entity in the evening) and
# the one the day decides with (the "today" entity in the morning)
KIND_AHEAD = "f"
KIND_TODAY = "t"
KIND_ACTUAL = "a"
FORECAST_KINDS = (KIND_AHEAD, KIND_TODAY)

# Three years: enough for every season twice, and the ageing panels with it
LOG_DAYS = 3 * 366
# Days whose calendar date lies within this many days of the planned one count,
# in any year; their weight falls linearly to zero just beyond it
SEASON_HALF_WIDTH_DAYS = 45
# An older year counts half as much as the last one: panels age, roofs get shade
RECENCY_HALF_LIFE_DAYS = 365.0
# A forecast below this says nothing about the roof, only about the clouds
MIN_FORECAST_KWH = 1.0
# Fewer days in the season window, and the setting stands
MIN_SEASON_DAYS = 14
# A day the PV meter's statistics cover with fewer hours than this is not
# complete: recording began, or Home Assistant was down, part of the way
# through it (a DST day has 23)
MIN_DAY_HOURS = 20
FACTOR_MIN = 0.5
FACTOR_MAX = 1.5
# Each edge is one-sided: four days in five the sun delivers at least the
# lower edge, four days in five at most the upper one - so both hold together
# on at least three days in five
LOWER_QUANTILE = 0.2
UPPER_QUANTILE = 0.8
# Never tighter than this, whatever a good season says (Pascal, 25 October 2026)
MARGIN_FLOOR_PCT = 5.0
MARGIN_LOW_MAX_PCT = 80.0
MARGIN_HIGH_MAX_PCT = 100.0

STATUS_OFF = "off"
STATUS_NO_METER = "no_pv_meter"
STATUS_LEARNING = "learning"
STATUS_ACTIVE = "active"


@dataclass(frozen=True)
class Calibration:
    """What the season says about one kind of forecast."""

    status: str
    factor: float = 1.0
    margin_low_pct: float | None = None
    margin_high_pct: float | None = None
    days: int = 0
    error_raw_pct: float | None = None
    error_corrected_pct: float | None = None

    @property
    def active(self) -> bool:
        return self.status == STATUS_ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "factor": round(self.factor, 3),
            "margin_low_pct": _round(self.margin_low_pct),
            "margin_high_pct": _round(self.margin_high_pct),
            "days": self.days,
            "error_raw_pct": _round(self.error_raw_pct),
            "error_corrected_pct": _round(self.error_corrected_pct),
        }


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def _valid_kwh(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def season_distance_days(a: date, b: date) -> int:
    """Days between two calendar dates in the year, either way round."""
    diff = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)
    return min(diff, 365 - diff)


def weighted_quantile(pairs: Iterable[tuple[float, float]], q: float) -> float | None:
    """The ``q`` quantile of ``(value, weight)`` pairs; None without weight."""
    data = sorted((value, weight) for value, weight in pairs if weight > 0 and math.isfinite(value))
    total = sum(weight for _, weight in data)
    if total <= 0:
        return None
    threshold = q * total
    running = 0.0
    for value, weight in data:
        running += weight
        if running >= threshold:
            return value
    return data[-1][0]


class ForecastLog:
    """Per day: the evening forecast, the morning forecast and the meter's yield."""

    def __init__(self, raw: Any = None) -> None:
        self.days: dict[date, dict[str, float]] = {}
        entries = raw.get("days") if isinstance(raw, Mapping) else None
        if not isinstance(entries, Mapping):
            return
        for key, values in entries.items():
            try:
                day = date.fromisoformat(str(key))
            except ValueError:
                continue
            if not isinstance(values, Mapping):
                continue
            kept = {kind: kwh for kind in (*FORECAST_KINDS, KIND_ACTUAL)
                    if (kwh := _valid_kwh(values.get(kind))) is not None}
            if kept:
                self.days[day] = kept

    def snapshot(self) -> dict[str, Any]:
        return {"days": {day.isoformat(): dict(values) for day, values in sorted(self.days.items())}}

    def note(self, day: date, kind: str, kwh: float | None) -> bool:
        """Record a value; True when the diary changed."""
        value = _valid_kwh(kwh)
        if value is None:
            return False
        entry = self.days.setdefault(day, {})
        if entry.get(kind) == value:
            return False
        entry[kind] = value
        return True

    def note_missing(self, day: date, kind: str, kwh: float | None) -> bool:
        """Record a value only where none is known yet (a backfill)."""
        if kind in self.days.get(day, {}):
            return False
        return self.note(day, kind, kwh)

    def clear(self, kind: str) -> bool:
        """Forget one kind of value, for a source that has changed."""
        changed = False
        for day in list(self.days):
            if self.days[day].pop(kind, None) is not None:
                changed = True
            if not self.days[day]:
                del self.days[day]
        return changed

    def prune(self, today: date) -> bool:
        cutoff = today - timedelta(days=LOG_DAYS)
        old = [day for day in self.days if day < cutoff]
        for day in old:
            del self.days[day]
        return bool(old)

    def days_without_actual(self, today: date) -> list[date]:
        """Completed days with a forecast whose yield is still to be read."""
        return sorted(day for day, values in self.days.items()
                      if day < today and KIND_ACTUAL not in values
                      and any(kind in values for kind in FORECAST_KINDS))

    def calibrate(self, day: date, kind: str, today: date) -> Calibration:
        """Factor and band for ``kind`` forecasts of ``day``, from completed days."""
        samples: list[tuple[float, float, float]] = []  # (weight, forecast, actual)
        oldest = today - timedelta(days=LOG_DAYS)  # as kept by prune, before it ran
        for logged, values in self.days.items():
            if logged >= today or logged < oldest:
                continue
            forecast = values.get(kind)
            actual = values.get(KIND_ACTUAL)
            if forecast is None or actual is None or forecast < MIN_FORECAST_KWH:
                continue
            distance = season_distance_days(logged, day)
            if distance > SEASON_HALF_WIDTH_DAYS:
                continue
            season = 1.0 - distance / (SEASON_HALF_WIDTH_DAYS + 1)
            age = max(0, (today - logged).days)
            recency = 0.5 ** (age / RECENCY_HALF_LIFE_DAYS)
            samples.append((season * recency, forecast, actual))
        if len(samples) < MIN_SEASON_DAYS:
            return Calibration(STATUS_LEARNING, days=len(samples))
        forecast_sum = sum(w * f for w, f, _ in samples)
        actual_sum = sum(w * a for w, _, a in samples)
        if forecast_sum <= 0:
            return Calibration(STATUS_LEARNING, days=len(samples))
        factor = min(FACTOR_MAX, max(FACTOR_MIN, actual_sum / forecast_sum))
        residuals = [(a / (factor * f) - 1.0, w * f) for w, f, a in samples]
        low = weighted_quantile(residuals, LOWER_QUANTILE)
        high = weighted_quantile(residuals, UPPER_QUANTILE)
        margin_low = min(MARGIN_LOW_MAX_PCT, max(MARGIN_FLOOR_PCT, -100.0 * (low or 0.0)))
        margin_high = min(MARGIN_HIGH_MAX_PCT, max(MARGIN_FLOOR_PCT, 100.0 * (high or 0.0)))
        raw_error = sum(w * abs(a - f) for w, f, a in samples) / forecast_sum
        corrected_error = sum(w * abs(a - factor * f) for w, f, a in samples) / (factor * forecast_sum)
        return Calibration(
            STATUS_ACTIVE,
            factor=factor,
            margin_low_pct=margin_low,
            margin_high_pct=margin_high,
            days=len(samples),
            error_raw_pct=100.0 * raw_error,
            error_corrected_pct=100.0 * corrected_error,
        )
