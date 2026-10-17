"""Small pure helpers shared by the config flow and the coordinator."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any


def elapsed_seconds(later: datetime, earlier: datetime) -> float:
    """Elapsed seconds, including folds/gaps that same-zone subtraction ignores.

    Naive test clocks remain supported; mixed naive/aware clocks raise rather
    than silently inventing an offset.
    """
    if later.tzinfo is not None and earlier.tzinfo is not None:
        return (later.astimezone(UTC) - earlier.astimezone(UTC)).total_seconds()
    return (later - earlier).total_seconds()


def add_elapsed(moment: datetime, duration: timedelta) -> datetime:
    """Add a real duration; calendar windows use wall times separately."""
    if moment.tzinfo is None:
        return moment + duration
    return (moment.astimezone(UTC) + duration).astimezone(moment.tzinfo)


def wall_datetime(day: date, wall_time: time, zone: tzinfo | None) -> datetime:
    """Resolve a wall boundary at its first occurrence, or first valid minute.

    Spring gaps can remove a boundary (or an entire date). Clamping forward to
    the first existing minute avoids fabricating an instant inside that gap.
    The bounded search covers IANA's historical 24-hour date skips as well.
    """
    candidate = datetime.combine(day, wall_time, tzinfo=zone).replace(fold=0)
    if zone is None:
        return candidate
    for _ in range(48 * 60 + 1):
        returned = candidate.astimezone(UTC).astimezone(zone)
        if returned.replace(tzinfo=None) == candidate.replace(tzinfo=None):
            return candidate
        candidate = (candidate + timedelta(minutes=1)).replace(fold=0)
    raise ValueError("Cannot resolve this local calendar boundary")


def scheduled_window_bounds(
    now: datetime, start: time, end: time,
) -> tuple[datetime, datetime] | None:
    """Current or next interval, with a single first-occurrence DST deadline.

    The end minute may still be queried for planning; membership is decided
    separately using the exact, exclusive deadline. Equal times disable the
    window. A window entirely swallowed by a gap is skipped.
    """
    if start == end:
        return None
    candidates: list[tuple[datetime, datetime]] = []
    for offset in (-1, 0, 1, 2):
        day = now.date() + timedelta(days=offset)
        begins = wall_datetime(day, start, now.tzinfo)
        ends = wall_datetime(day + timedelta(days=int(start > end)), end, now.tzinfo)
        if elapsed_seconds(ends, begins) > 0:
            candidates.append((begins, ends))
    for begins, ends in candidates:
        if elapsed_seconds(now, begins) >= 0 and elapsed_seconds(now, ends) < 60:
            return begins, ends
    return next(((begins, ends) for begins, ends in candidates
                 if elapsed_seconds(begins, now) > 0), None)


def parse_time_str(time_str: Any) -> tuple[int, int] | None:
    """Parse "HH:MM" or "HH:MM:SS" into (hour, minute), or None if invalid."""
    try:
        parts = [int(part) for part in time_str.split(":")]
    except (ValueError, AttributeError):
        return None
    if len(parts) not in (2, 3):
        return None
    hour, minute = parts[0], parts[1]
    # The seconds are dropped, but an impossible value still makes the whole
    # string invalid: "23:59:99" is not a time, and silently reading it as
    # 23:59 would hide a corrupt stored value instead of reporting it.
    if len(parts) == 3 and not 0 <= parts[2] <= 59:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None
