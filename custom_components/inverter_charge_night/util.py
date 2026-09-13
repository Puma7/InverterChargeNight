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
    # The seconds are dropped, but an impossible value still makes the whole
    # string invalid: "23:59:99" is not a time, and silently reading it as
    # 23:59 would hide a corrupt stored value instead of reporting it.
    if len(parts) == 3 and not 0 <= parts[2] <= 59:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None
