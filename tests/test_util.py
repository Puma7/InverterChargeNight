"""Tests for shared helpers."""
from custom_components.inverter_charge_night.util import parse_time_str


def test_parse_time_str():
    assert parse_time_str("00:00") == (0, 0)
    assert parse_time_str("2:00") == (2, 0)
    assert parse_time_str("23:59") == (23, 59)
    assert parse_time_str("24:00") is None
    assert parse_time_str("12:60") is None
    assert parse_time_str("ab:cd") is None
    assert parse_time_str(None) is None


def test_parse_time_str_accepts_seconds():
    """A TimeSelector submits HH:MM:SS; seconds are accepted and ignored."""
    assert parse_time_str("12:00:00") == (12, 0)
    assert parse_time_str("22:30:15") == (22, 30)
    assert parse_time_str("12") is None
    assert parse_time_str("1:2:3:4") is None


def test_an_impossible_seconds_value_makes_the_time_invalid():
    """Dropping the seconds must not turn a corrupt value into a valid time."""
    from custom_components.inverter_charge_night.util import parse_time_str

    assert parse_time_str("23:59:59") == (23, 59)
    assert parse_time_str("23:59:99") is None
    assert parse_time_str("23:59:-1") is None
