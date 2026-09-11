"""Small pure helpers shared by the config flow and the coordinator."""
from __future__ import annotations

from typing import Any


def parse_time_str(time_str: Any) -> tuple[int, int] | None:
    """Parse an HH:MM string into (hour, minute), or None if it is invalid."""
    try:
        hour, minute = map(int, time_str.split(":"))
    except (ValueError, AttributeError):
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None
