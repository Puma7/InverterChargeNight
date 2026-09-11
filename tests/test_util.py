"""Tests for shared helpers."""
from custom_components.inverter_charge_night.util import parse_time_str


def test_parse_time_str():
    assert parse_time_str("00:00") == (0, 0)
    assert parse_time_str("2:00") == (2, 0)
    assert parse_time_str("23:59") == (23, 59)
    assert parse_time_str("24:00") is None
    assert parse_time_str("12:60") is None
    assert parse_time_str("ab:cd") is None
    assert parse_time_str("12:00:00") is None
    assert parse_time_str(None) is None
