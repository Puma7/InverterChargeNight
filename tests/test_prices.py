"""Reading a price entity (plan 012, stage 3).

Pure arithmetic, no Home Assistant. The shapes below are modelled on what
Tibber, aWATTar/EPEX Spot, Nordpool and ENTSO-e publish; the parser does not
know their names, it recognises the shape, so these double as a check that the
recognition is wide enough to be useful and narrow enough to be safe.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.inverter_charge_night.const import (
    PRICE_UNIT_CT_KWH,
    PRICE_UNIT_EUR_KWH,
    PRICE_UNIT_EUR_MWH,
    RESERVE_DROP_MARGIN_CT,
)
from custom_components.inverter_charge_night.prices import (
    evening_reserve_pays,
    mean_price_ct,
    parse_price_series,
)

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 20, 14, 0, tzinfo=TZ)
MIDNIGHT = NOW.replace(hour=0, minute=0)


def _hours(count=24, start=MIDNIGHT):
    return [start + timedelta(hours=h) for h in range(count)]


def _parse(attributes, **kwargs):
    kwargs.setdefault("tz", TZ)
    kwargs.setdefault("now", NOW)
    return parse_price_series(attributes, **kwargs)


# The shapes ---------------------------------------------------------------------


def test_a_full_price_in_euros_is_read():
    """Tibber-shaped: camelCase keys, the total the customer pays, EUR/kWh."""
    series, reason = _parse(
        {"raw_today": [{"startsAt": t.isoformat(), "total": 0.28} for t in _hours()]},
        entity_unit="EUR/kWh",
    )
    assert reason == ""
    assert series is not None
    assert series.source_attribute == "raw_today"
    assert series.value_key == "total"
    assert series.unit == PRICE_UNIT_EUR_KWH
    assert series.intervals[0].ct_per_kwh == pytest.approx(28.0)


def test_a_key_that_names_its_own_unit_is_believed():
    """aWATTar-shaped: price_ct_per_kwh says what it is, entity unit or not."""
    series, _ = _parse(
        {
            "data": [
                {"start_timestamp": t.isoformat(), "price_ct_per_kwh": 6.0} for t in _hours()
            ]
        },
        surcharge_ct=24.0,
    )
    assert series is not None
    assert series.unit == PRICE_UNIT_CT_KWH
    assert series.intervals[0].ct_per_kwh == pytest.approx(30.0)


def test_explicit_ends_are_used_as_given():
    """Nordpool-shaped: start, end and value, in cents."""
    series, _ = _parse(
        {
            "raw_today": [
                {"start": t.isoformat(), "end": (t + timedelta(hours=1)).isoformat(), "value": 30.0}
                for t in _hours()
            ]
        },
        entity_unit="ct/kWh",
    )
    assert series is not None
    assert series.intervals[0].end - series.intervals[0].start == timedelta(hours=1)


def test_a_missing_end_comes_from_the_next_start():
    """ENTSO-e-shaped: only a time and a price."""
    series, _ = _parse(
        {"prices_today": [{"time": t.isoformat(), "price": 0.30} for t in _hours()]},
        entity_unit="EUR/kWh",
    )
    assert series is not None
    assert series.intervals[0].end == series.intervals[1].start
    # The last one borrows the median length rather than assuming an hour
    assert series.intervals[-1].end - series.intervals[-1].start == timedelta(hours=1)


def test_quarter_hours_are_not_read_as_hours():
    """Day-ahead products are moving to fifteen minutes; nothing may assume 60."""
    series, _ = _parse(
        {
            "raw_today": [
                {"start": (MIDNIGHT + timedelta(minutes=15 * i)).isoformat(), "total": 0.30}
                for i in range(96)
            ]
        },
        entity_unit="EUR/kWh",
    )
    assert series is not None
    assert series.intervals[0].end - series.intervals[0].start == timedelta(minutes=15)


def test_euros_per_megawatt_hour_is_a_factor_of_a_thousand():
    series, _ = _parse(
        {"data": [{"start": t.isoformat(), "marketprice": 60.0} for t in _hours()]},
        entity_unit="EUR/MWh",
        surcharge_ct=24.0,
    )
    assert series is not None
    assert series.unit == PRICE_UNIT_EUR_MWH
    assert series.intervals[0].ct_per_kwh == pytest.approx(30.0)


# The refusals -------------------------------------------------------------------


def test_an_exchange_price_without_the_bill_on_top_is_refused():
    """The dangerous one: it looks like a price and inverts the decision.

    6 ct and 32 ct are both plausible numbers. Only one of them is what a
    kilowatt-hour costs, and the reserve decision comes out the other way
    round on the wrong one.
    """
    series, reason = _parse(
        {"data": [{"start": t.isoformat(), "price_ct_per_kwh": 6.0 + h * 0.1}
                  for h, t in enumerate(_hours())]}
    )
    assert series is None
    assert reason == "looks_like_exchange_price"


def test_no_unit_anywhere_is_refused_rather_than_guessed():
    """Guessing here is a factor of a hundred."""
    series, reason = _parse(
        {"raw_today": [{"start": t.isoformat(), "value": 30.0} for t in _hours()]}
    )
    assert series is None
    assert reason == "no_unit"


def test_cents_that_are_the_size_of_euros_are_refused():
    series, reason = _parse(
        {"raw_today": [{"start": t.isoformat(), "value": 0.30} for t in _hours()]},
        entity_unit="ct/kWh",
    )
    assert series is None
    assert reason == "unit_looks_like_eur"


def test_the_user_can_say_what_the_unit_is():
    series, _ = _parse(
        {"raw_today": [{"start": t.isoformat(), "value": 0.30} for t in _hours()]},
        unit_override=PRICE_UNIT_EUR_KWH,
    )
    assert series is not None
    assert series.intervals[0].ct_per_kwh == pytest.approx(30.0)


def test_nothing_that_looks_like_a_series_is_refused():
    series, reason = _parse({"state_class": "measurement", "friendly_name": "Price"})
    assert series is None
    assert reason == "no_series_found"


def test_a_series_that_ended_before_now_is_stale():
    old = MIDNIGHT - timedelta(days=1)
    series, reason = _parse(
        {"raw_today": [{"start": t.isoformat(), "total": 0.30} for t in _hours(start=old)]},
        entity_unit="EUR/kWh",
    )
    assert series is None
    assert reason == "stale"


def test_a_naive_timestamp_takes_the_supplied_zone():
    series, _ = _parse(
        {"raw_today": [{"start": t.replace(tzinfo=None).isoformat(), "total": 0.30}
                       for t in _hours()]},
        entity_unit="EUR/kWh",
    )
    assert series is not None
    assert series.intervals[0].start.tzinfo is not None


def test_broken_items_are_skipped_not_fatal():
    items = [{"start": t.isoformat(), "total": 0.30} for t in _hours()]
    items[3] = {"start": "not a time", "total": 0.30}
    items[7] = {"start": items[7]["start"], "total": None}
    items[11] = {"start": items[11]["start"], "total": float("nan")}
    series, _ = _parse({"raw_today": items}, entity_unit="EUR/kWh")
    assert series is not None
    assert len(series.intervals) == 21


def test_an_absurd_value_is_dropped_but_the_rest_survives():
    items = [{"start": t.isoformat(), "total": 0.30} for t in _hours()]
    items[5]["total"] = 1e9
    series, _ = _parse({"raw_today": items}, entity_unit="EUR/kWh")
    assert series is not None
    assert all(i.ct_per_kwh == pytest.approx(30.0) for i in series.intervals)


def test_a_month_of_history_does_not_drown_the_parse():
    """An entity that publishes weeks of data is trimmed to what is relevant.

    Two days either side of now, so that today and tomorrow are complete and
    the plausibility checks are about prices that still mean something.
    """
    far = [{"start": (MIDNIGHT - timedelta(days=20) + timedelta(hours=h)).isoformat(),
            "total": 0.30} for h in range(480)]
    near = [{"start": t.isoformat(), "total": 0.30} for t in _hours()]
    series, _ = _parse({"raw_today": far + near}, entity_unit="EUR/kWh")
    assert series is not None
    assert len(series.intervals) < 100, "the twenty days of history are gone"
    assert series.intervals[0].start >= NOW - timedelta(hours=48)
    assert series.intervals[-1].end > NOW


# The window surcharge -----------------------------------------------------------


def test_the_window_hours_get_their_own_surcharge():
    """Where the reduced grid fee of a cheap window lives."""
    window = (MIDNIGHT.replace(hour=23), MIDNIGHT + timedelta(days=1, hours=5))
    series, _ = _parse(
        {"data": [{"start": t.isoformat(), "price_ct_per_kwh": 6.0}
                  for t in _hours(30)]},
        surcharge_ct=24.0,
        window_surcharge_ct=16.0,
        window=window,
    )
    assert series is not None
    by_hour = {i.start.hour: i.ct_per_kwh for i in series.intervals}
    assert by_hour[12] == pytest.approx(30.0)   # day
    assert by_hour[23] == pytest.approx(22.0)   # inside the window


# The mean -----------------------------------------------------------------------


def _flat_series(value=0.30, count=24):
    series, _ = _parse(
        {"raw_today": [{"start": t.isoformat(), "total": value} for t in _hours(count)]},
        entity_unit="EUR/kWh",
    )
    return series


def test_the_mean_over_a_covered_stretch():
    assert mean_price_ct(_flat_series(), MIDNIGHT.replace(hour=18),
                         MIDNIGHT.replace(hour=21)) == pytest.approx(30.0)


def test_the_mean_is_weighted_by_time_not_by_item():
    items = [
        {"start": MIDNIGHT.isoformat(), "end": (MIDNIGHT + timedelta(hours=3)).isoformat(), "total": 0.10},
        {"start": (MIDNIGHT + timedelta(hours=3)).isoformat(),
         "end": (MIDNIGHT + timedelta(hours=4)).isoformat(), "total": 0.50},
        {"start": (MIDNIGHT + timedelta(hours=4)).isoformat(),
         "end": (MIDNIGHT + timedelta(hours=5)).isoformat(), "total": 0.50},
        {"start": (MIDNIGHT + timedelta(hours=5)).isoformat(),
         "end": (MIDNIGHT + timedelta(hours=6)).isoformat(), "total": 0.50},
    ]
    # ``now`` at midnight, or the six hours would all be in the past and the
    # series would be refused as stale - which is its own test below.
    series, _ = _parse({"raw_today": items}, entity_unit="EUR/kWh", now=MIDNIGHT)
    # Three hours at 10 ct and three at 50 ct is 30, not the item mean of 40
    assert mean_price_ct(series, MIDNIGHT, MIDNIGHT + timedelta(hours=6)) == pytest.approx(30.0)


def test_a_stretch_that_is_not_fully_covered_is_unknown():
    """Averaging 18:00 to 19:00 and calling it 18:00 to 21:00 is the quiet
    wrongness this module exists to avoid."""
    series = _flat_series(count=20)  # ends at 20:00
    assert mean_price_ct(series, MIDNIGHT.replace(hour=18), MIDNIGHT.replace(hour=21)) is None


def test_a_gap_in_the_middle_is_unknown():
    items = [{"start": t.isoformat(), "end": (t + timedelta(hours=1)).isoformat(), "total": 0.30}
             for t in _hours()]
    del items[12]
    series, _ = _parse({"raw_today": items}, entity_unit="EUR/kWh")
    assert mean_price_ct(series, MIDNIGHT.replace(hour=11), MIDNIGHT.replace(hour=14)) is None


def test_an_empty_stretch_is_unknown_not_zero():
    """0 ct is a real price and can never stand for "no answer"."""
    assert mean_price_ct(_flat_series(), MIDNIGHT, MIDNIGHT) is None
    assert mean_price_ct(None, MIDNIGHT, MIDNIGHT + timedelta(hours=1)) is None


# The decision -------------------------------------------------------------------


def test_the_reserve_is_held_when_the_evening_is_dearer():
    assert evening_reserve_pays(20.0, 40.0, 0.95, RESERVE_DROP_MARGIN_CT) is True


def test_the_reserve_is_dropped_when_the_evening_is_clearly_cheaper():
    assert evening_reserve_pays(40.0, 20.0, 0.95, RESERVE_DROP_MARGIN_CT) is False


def test_a_difference_inside_the_margin_is_not_worth_acting_on():
    # 30 / 0.95 = 31.6; an evening at 30.5 is cheaper but only just
    assert evening_reserve_pays(30.0, 30.5, 0.95, RESERVE_DROP_MARGIN_CT) is True


def test_the_round_trip_loss_counts_against_holding():
    """Worse efficiency makes storing dearer, so it argues against holding.

    At 20 ct in the window against 30 in the evening, holding pays outright.
    Lose half of it on the way through the inverter and the same kilowatt-hour
    costs 40 to store - so buying it in the evening is the cheaper of the two.
    """
    assert evening_reserve_pays(20.0, 30.0, 1.0, 0.0) is True
    assert evening_reserve_pays(20.0, 30.0, 0.5, 0.0) is False


@pytest.mark.parametrize("window_ct,evening_ct", [(None, 30.0), (30.0, None), (None, None)])
def test_unknown_prices_keep_todays_answer(window_ct, evening_ct):
    """Which is to hold: it is what the period was configured for."""
    assert evening_reserve_pays(window_ct, evening_ct, 0.95, RESERVE_DROP_MARGIN_CT) is True


@pytest.mark.parametrize("efficiency", [0.0, -1.0, 5.0])
def test_an_impossible_efficiency_cannot_divide_by_zero(efficiency):
    assert isinstance(evening_reserve_pays(30.0, 30.0, efficiency, 2.0), bool)
