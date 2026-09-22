"""Reading an electricity price entity, without knowing which one it is.

Pure functions only, like :mod:`planner` - the coordinator reads the states and
passes the attributes in. The one Home Assistant flavoured input, the local time
zone, arrives as a plain ``tzinfo``.

**Why this does not hold a table of attribute names.** Tibber, aWATTar, EPEX
Spot, Nordpool and ENTSO-e each publish tomorrow's hourly prices under their own
attribute name and their own item keys. A table of those names is only as good
as the day it was written, and a name that has been guessed rather than checked
produces a parser that quietly matches nothing. So the series is found by its
*shape* instead: a list of items that carry a timestamp and a number, in order,
evenly spaced, with values that could be a price. What was matched is reported,
so a wrong guess is visible rather than silent.

Everything here refuses rather than guesses. A price that is wrong by a factor
of a hundred, or that is the exchange price without the grid fees on top, does
not look wrong - it looks like a price, and it inverts the decision it feeds.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from .const import (
    MAX_PLAUSIBLE_PRICE_CT,
    MIN_PLAUSIBLE_MEDIAN_CT,
    MIN_PLAUSIBLE_PRICE_CT,
    MIN_PLAUSIBLE_TOTAL_CT,
    PRICE_SERIES_MAX_AGE_H,
    PRICE_UNIT_CT_KWH,
    PRICE_UNIT_EUR_KWH,
    PRICE_UNIT_EUR_MWH,
    PRICE_UNITS_CT,
    PRICE_UNITS_EUR_KWH,
    PRICE_UNITS_EUR_MWH,
)

# How few items still count as a series. Three is not a day-ahead curve, it is
# a coincidence; four is the smallest thing worth integrating over.
MIN_SERIES_ITEMS = 4

# Keys are compared with the punctuation and the case taken out, so that
# ``startsAt``, ``starts_at`` and ``START_AT`` are one key and not three. That
# is the difference between matching Tibber and silently matching nothing.
_START_KEYS = ("start", "starttime", "startsat", "starttimestamp", "from", "time", "hour", "datetime", "timestamp")
# ...and its end. Absent on several integrations, and then derived.
_END_KEYS = ("end", "endtime", "endsat", "endtimestamp", "to")
# The value itself. "total" first: where an integration offers both, it is the
# one the customer actually pays.
_VALUE_KEYS = ("total", "pricectperkwh", "price", "value", "marketprice", "amount", "cost")

# An attribute whose name says what it is beats one that merely looks right.
_NAME_HINTS = ("price", "raw", "forecast", "today", "tomorrow", "data", "series", "hours")


@dataclass(frozen=True)
class PriceInterval:
    """One priced stretch of time. ``ct_per_kwh`` is the total, surcharge included."""

    start: datetime
    end: datetime
    ct_per_kwh: float


@dataclass(frozen=True)
class PriceSeries:
    """What was read, and enough about how to judge whether it was read right."""

    intervals: tuple[PriceInterval, ...]
    source_attribute: str
    value_key: str
    unit: str
    surcharge_ct: float

    def horizon_end(self) -> datetime | None:
        """The end of the last interval, or None when the series is empty."""
        return self.intervals[-1].end if self.intervals else None


def _as_price(value: Any) -> float | None:
    """A finite number, or None. Booleans are not numbers here."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_datetime(value: Any, tz: tzinfo) -> datetime | None:
    """Parse a timestamp and make it aware.

    A naive timestamp is read in ``tz``, which is the local zone the rest of
    the integration works in. A value with neither an offset nor a zone to
    supply one is refused rather than assumed to be UTC: being an hour or two
    out here moves prices onto the wrong side of a window boundary.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # fromisoformat before 3.11 does not take a trailing Z
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)


def _aware(value: datetime, tz: tzinfo) -> datetime:
    """A naive datetime read in ``tz``; an aware one unchanged.

    Every timestamp in a series is made aware at parse time, so a naive
    ``now`` or a naive query bound would raise on the first comparison - and
    this module promises not to raise.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=tz)


def _normalized(key: str) -> str:
    """A key without its punctuation or case, for comparing names across integrations."""
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _first_key(item: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    """The item's own spelling of the first of ``keys`` it carries."""
    present = {_normalized(str(name)): str(name) for name in item}
    for key in keys:
        if key in present:
            return present[key]
    return None


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _resolve_unit(entity_unit: str, unit_override: str | None, value_key: str) -> str | None:
    """Decide what the numbers mean, or None when nothing says.

    Three sources, and the user's beats the rest: an explicit override, the
    entity's own unit, and finally a key that names its unit (aWATTar's
    ``price_ct_per_kwh``). Nothing left to go on is a refusal - guessing here
    is a factor of a hundred, or of a thousand for EUR/MWh.
    """
    if unit_override in (PRICE_UNIT_CT_KWH, PRICE_UNIT_EUR_KWH, PRICE_UNIT_EUR_MWH):
        return unit_override
    unit = (entity_unit or "").strip().lower()
    if unit in PRICE_UNITS_CT:
        return PRICE_UNIT_CT_KWH
    if unit in PRICE_UNITS_EUR_KWH:
        return PRICE_UNIT_EUR_KWH
    if unit in PRICE_UNITS_EUR_MWH:
        return PRICE_UNIT_EUR_MWH
    key = value_key.lower()
    if "ct" in re.split(r"[^a-z]+", key) or "cent" in key:
        return PRICE_UNIT_CT_KWH
    if "mwh" in key:
        return PRICE_UNIT_EUR_MWH
    return None


def _to_ct(value: float, unit: str) -> float:
    if unit == PRICE_UNIT_EUR_KWH:
        return value * 100.0
    if unit == PRICE_UNIT_EUR_MWH:
        return value / 10.0  # EUR/MWh -> ct/kWh
    return value


def _candidate_lists(attributes: Mapping[str, Any]) -> list[tuple[str, list[Mapping[str, Any]]]]:
    """Attributes that could be a price series, best-named first."""
    found: list[tuple[str, list[Mapping[str, Any]]]] = []
    for name, value in attributes.items():
        if not isinstance(value, (list, tuple)) or len(value) < MIN_SERIES_ITEMS:
            continue
        items = [item for item in value if isinstance(item, Mapping)]
        if len(items) < MIN_SERIES_ITEMS:
            continue
        found.append((str(name), items))
    lowered = [name.lower() for name, _ in found]
    return [
        pair
        for _, pair in sorted(
            zip(lowered, found),
            key=lambda both: (
                0 if any(hint in both[0] for hint in _NAME_HINTS) else 1,
                both[0],
            ),
        )
    ]


def _read_items(
    items: Sequence[Mapping[str, Any]], tz: tzinfo
) -> tuple[list[tuple[datetime, datetime | None, float]], str] | None:
    """Turn raw items into (start, end, value) triples, or None."""
    first = items[0]
    start_key = _first_key(first, _START_KEYS)
    value_key = _first_key(first, _VALUE_KEYS)
    if start_key is None or value_key is None:
        return None
    end_key = _first_key(first, _END_KEYS)

    read: list[tuple[datetime, datetime | None, float]] = []
    for item in items:
        start = _as_datetime(item.get(start_key), tz)
        value = _as_price(item.get(value_key))
        if start is None or value is None:
            continue
        end = _as_datetime(item.get(end_key), tz) if end_key else None
        read.append((start, end, value))
    if len(read) < MIN_SERIES_ITEMS:
        return None
    read.sort(key=lambda triple: triple[0])
    return read, value_key


def _close_intervals(
    read: Sequence[tuple[datetime, datetime | None, float]]
) -> list[tuple[datetime, datetime, float]] | None:
    """Give every item an end.

    Where the integration does not publish one, the next item's start is it,
    and the last item borrows the median length of the others. No hour is
    assumed anywhere: day-ahead products are moving to quarter hours, and a
    hardcoded hour would be silently wrong the day that lands.
    """
    lengths = [
        (nxt[0] - cur[0]).total_seconds()
        for cur, nxt in zip(read, read[1:])
        if (nxt[0] - cur[0]).total_seconds() > 0
    ]
    if not lengths:
        return None
    median_length = timedelta(seconds=_median(lengths))
    closed: list[tuple[datetime, datetime, float]] = []
    for index, (start, end, value) in enumerate(read):
        if end is None or end <= start:
            end = read[index + 1][0] if index + 1 < len(read) else start + median_length
        if end <= start:
            continue
        closed.append((start, end, value))
    return closed or None


def parse_price_series(
    attributes: Mapping[str, Any],
    *,
    entity_unit: str = "",
    unit_override: str | None = None,
    surcharge_ct: float = 0.0,
    tz: tzinfo,
    now: datetime,
) -> tuple[PriceSeries | None, str]:
    """Read a price entity's attributes into a series. Never raises.

    Returns ``(series, "")`` or ``(None, reason)``. The reason is short enough
    for a sensor attribute and specific enough to act on, because the whole
    point of refusing is that somebody can see why.

    ``surcharge_ct`` is what the bill adds on top of what the entity shows -
    grid fee, levies, taxes - and it is applied to every interval. A window
    with a *different* grid fee, which is what a reduced tariff is, is handled
    by the caller instead: it queries the mean over the window's own hours and
    swaps one surcharge for the other. Doing it that way keeps the recurrence
    of a daily window out of a module that has no business knowing about it.
    """
    now = _aware(now, tz)
    # Today and tomorrow usually arrive as two separate attributes - Nordpool
    # publishes raw_today and raw_tomorrow, and the others are shaped the same
    # way. Stopping at the first one leaves a series that ends at midnight, and
    # then mean_price_ct refuses every window that crosses it, which is every
    # night window there is. So matching attributes are merged, and only ones
    # that agree on the value key and the unit: two lists that disagree about
    # either are not two halves of one series.
    merged: list[PriceInterval] = []
    merged_names: list[str] = []
    ref_value_key: str | None = None
    ref_unit: str | None = None

    for name, items in _candidate_lists(attributes):
        parsed = _read_items(items, tz)
        if parsed is None:
            continue
        read, value_key = parsed
        unit = _resolve_unit(entity_unit, unit_override, value_key)
        if unit is None:
            return None, "no_unit"
        if ref_value_key is not None and (value_key, unit) != (ref_value_key, ref_unit):
            continue
        closed = _close_intervals(read)
        if closed is None:
            continue

        raw_ct = [_to_ct(value, unit) for _, _, value in closed]
        median_raw = _median(raw_ct)
        if unit == PRICE_UNIT_CT_KWH and abs(median_raw) < MIN_PLAUSIBLE_MEDIAN_CT:
            # Said to be cents, but the size of euros.
            return None, "unit_looks_like_eur"

        day_surcharge = float(surcharge_ct)
        intervals: list[PriceInterval] = []
        horizon = timedelta(hours=PRICE_SERIES_MAX_AGE_H)
        for (start, end, _), ct in zip(closed, raw_ct):
            if not (now - horizon) <= start <= (now + horizon):
                # A month of history is not this integration's business, and
                # it would only make the plausibility checks meaningless.
                continue
            total = ct + day_surcharge
            if not MIN_PLAUSIBLE_PRICE_CT <= total <= MAX_PLAUSIBLE_PRICE_CT:
                continue
            intervals.append(PriceInterval(start=start, end=end, ct_per_kwh=total))
        if len(intervals) < MIN_SERIES_ITEMS:
            continue

        if ref_value_key is None:
            ref_value_key, ref_unit = value_key, unit
        merged.extend(intervals)
        merged_names.append(name)

    if ref_value_key is None or ref_unit is None or len(merged) < MIN_SERIES_ITEMS:
        return None, "no_series_found"

    merged.sort(key=lambda interval: interval.start)
    # Two intervals with the same bounds are one interval seen twice - which is
    # now the normal case, because today and tomorrow usually overlap by a day.
    # Two that merely share a wall clock hour are a daylight saving fall-back,
    # and they differ in their offset, so this keeps both.
    seen: set[tuple[datetime, datetime]] = set()
    unique = [
        interval
        for interval in merged
        if (interval.start, interval.end) not in seen
        and not seen.add((interval.start, interval.end))  # type: ignore[func-returns-value]
    ]

    median_total = _median([interval.ct_per_kwh for interval in unique])
    if median_total < MIN_PLAUSIBLE_TOTAL_CT and float(surcharge_ct) == 0:
        # An exchange price with the grid fees and taxes still missing. It
        # looks exactly like a price and is short by the larger part of the
        # bill, which is enough to invert the decision it feeds.
        return None, "looks_like_exchange_price"
    if unique[-1].end < now:
        return None, "stale"

    return (
        PriceSeries(
            intervals=tuple(unique),
            source_attribute="+".join(merged_names),
            value_key=ref_value_key,
            unit=ref_unit,
            surcharge_ct=float(surcharge_ct),
        ),
        "",
    )


def mean_price_ct(series: PriceSeries | None, start: datetime, end: datetime) -> float | None:
    """The duration-weighted mean price over ``[start, end)``, or None.

    None unless the stretch is covered end to end. Averaging 18:00 to 19:00 and
    calling it 18:00 to 21:00 is exactly the quiet wrongness this module exists
    to avoid - and 0.0 is a real price, so it can never stand for "unknown".

    Weighting by real elapsed seconds means a 23:00 to 05:00 stretch that is
    seven hours long on a clock-change night is weighted as seven hours.
    """
    if series is None or not series.intervals:
        return None
    zone = series.intervals[0].start.tzinfo
    if zone is not None:
        start = _aware(start, zone)
        end = _aware(end, zone)
    if end <= start:
        return None
    total_seconds = 0.0
    weighted = 0.0
    cursor = start
    for interval in series.intervals:
        if interval.end <= cursor:
            continue
        if interval.start > cursor:
            return None  # a gap: the stretch is not covered
        segment_end = min(interval.end, end)
        seconds = (segment_end - cursor).total_seconds()
        if seconds > 0:
            weighted += interval.ct_per_kwh * seconds
            total_seconds += seconds
        cursor = segment_end
        if cursor >= end:
            break
    if cursor < end or total_seconds <= 0:
        return None
    return weighted / total_seconds


def evening_reserve_pays(
    window_ct: float | None,
    evening_ct: float | None,
    charge_efficiency: float,
    drop_margin_ct: float,
) -> bool:
    """Whether holding energy back for the evening beats buying it then.

    A kilowatt-hour put aside in the window costs ``window_ct`` and loses some
    of itself on the way in and out, so it really costs ``window_ct /
    efficiency``. Buying the same kilowatt-hour in the evening costs
    ``evening_ct``. The reserve is only dropped when the evening is *clearly*
    cheaper: a difference inside ``drop_margin_ct`` is not worth acting on.

    ``PlanInput`` carries only the charge efficiency, so the round trip is
    understated and the answer leans towards holding. That is the safe lean
    and it is deliberate.

    Unknown prices keep today's answer, which is to hold: it is what the user
    configured the period for.
    """
    if window_ct is None or evening_ct is None:
        return True
    efficiency = min(1.0, max(0.01, charge_efficiency))
    return evening_ct + drop_margin_ct > window_ct / efficiency
