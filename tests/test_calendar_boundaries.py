"""Wall calendar boundaries checked against independently specified UTC instants."""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.inverter_charge_night.util import (
    add_elapsed, elapsed_seconds, parse_time_str, scheduled_window_bounds, wall_datetime,
)


@pytest.mark.parametrize("zone,day,wall,expected", [
    ("Europe/Berlin", date(2026, 3, 29), time(2, 30), "2026-03-29T01:00:00+00:00"),
    ("Europe/Berlin", date(2026, 10, 25), time(2, 30), "2026-10-25T00:30:00+00:00"),
    ("Australia/Lord_Howe", date(2026, 10, 4), time(2, 15), "2026-10-03T15:30:00+00:00"),
    ("Australia/Lord_Howe", date(2026, 4, 5), time(1, 45), "2026-04-04T14:45:00+00:00"),
    ("Pacific/Apia", date(2011, 12, 30), time(0), "2011-12-30T10:00:00+00:00"),
])
def test_missing_and_repeated_boundaries(zone, day, wall, expected):
    assert wall_datetime(day, wall, ZoneInfo(zone)).astimezone(UTC).isoformat() == expected


def test_a_wholly_missing_spring_window_waits_until_next_day():
    now = datetime(2026, 3, 29, 1, tzinfo=ZoneInfo("Europe/Berlin"))
    bounds = scheduled_window_bounds(now, time(2, 10), time(2, 40))
    assert bounds is not None
    assert bounds[0].date() == date(2026, 3, 30)
    assert bounds[1].date() == date(2026, 3, 30)


def test_disabled_and_naive_windows_and_duration():
    now = datetime(2026, 1, 15, 2)
    assert scheduled_window_bounds(now, time(2), time(2)) is None
    assert wall_datetime(now.date(), time(3), None) == now.replace(hour=3)
    assert add_elapsed(now, timedelta(hours=2)) == now.replace(hour=4)
    assert elapsed_seconds(now.replace(hour=4), now) == 7200
    with pytest.raises(TypeError):
        elapsed_seconds(now.replace(tzinfo=UTC), now)


@pytest.mark.parametrize("value", ["23:59:60", "23:60", "24:00", "-1:30", "02:30:99"])
def test_impossible_clock_strings_are_rejected(value):
    # POSIX/datetime cannot encode second 60: clock-repeat/skip behavior is
    # exercised separately in real HA, without inventing a leap-second date.
    assert parse_time_str(value) is None
