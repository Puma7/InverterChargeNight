"""Small pure helpers shared by the config flow and the coordinator."""
from __future__ import annotations

from typing import Any


def parse_time_str(time_str: Any) -> tuple[int, int] | None:
    """Parse "HH:MM" or "HH:MM:SS" into (hour, minute), or None if invalid."""
    try:
        parts = [int(part) for part in time_str.split(":")]
    except (ValueError, AttributeError):
        return None
    if len(parts) not in (2, 3):
        return None
    hour, minute = parts[0], parts[1]
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None
