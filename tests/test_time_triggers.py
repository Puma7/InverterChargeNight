"""Tests for time parsing and triggers."""
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_END_TIME,
    CONF_START_TIME,
    CONF_ACTIVE_START_DATE,
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_RANGE_YEARLY,
)


COORDINATOR = "custom_components.inverter_charge_night.coordinator"


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


def test_parse_time_valid(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    hour, minute = coordinator._parse_time("23:15", "00:00")
    assert (hour, minute) == (23, 15)


def test_parse_time_invalid_uses_default(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    hour, minute = coordinator._parse_time("99:99", "01:30")
    assert (hour, minute) == (1, 30)


def test_parse_time_invalid_default_fallback(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    hour, minute = coordinator._parse_time("bad", "also-bad")
    assert (hour, minute) == (0, 0)


def test_parse_date_optional_invalid_and_range(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._parse_date_optional("2025-13-01") is None
    assert coordinator._parse_date_optional(None) is None
    assert coordinator._parse_date_optional("") is None


def _at(coordinator, when):
    """Ask the date-range check as if today were ``when``."""
    with patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=when,
    ):
        return coordinator._is_within_date_range()


def test_a_reversed_range_within_one_year_is_a_season(mock_hass):
    """31 December to 1 January cannot be meant absolutely, only as a season.

    Read absolutely it contains no day at all. It used to be logged as invalid
    and the restriction then dropped entirely, which is the opposite of what
    the user asked for: the window ran all year.
    """
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_ACTIVE_START_DATE: "2025-12-31", CONF_ACTIVE_END_DATE: "2026-01-01"},
    )
    assert _at(coordinator, datetime(2027, 12, 31)) is True
    assert _at(coordinator, datetime(2027, 1, 1)) is True
    assert _at(coordinator, datetime(2027, 6, 15)) is False


def test_a_winter_season_holds_every_year(mock_hass):
    """The case the old code got wrong: 1 November to 31 March."""
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_ACTIVE_START_DATE: "2026-11-01", CONF_ACTIVE_END_DATE: "2027-03-31"},
    )
    for inside in (datetime(2029, 11, 1), datetime(2029, 12, 24), datetime(2030, 3, 31)):
        assert _at(coordinator, inside) is True, inside
    for outside in (datetime(2029, 10, 31), datetime(2030, 4, 1), datetime(2030, 7, 1)):
        assert _at(coordinator, outside) is False, outside


def test_a_summer_season_repeats_when_asked_to(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ACTIVE_START_DATE: "2026-04-01",
            CONF_ACTIVE_END_DATE: "2026-09-30",
            CONF_ACTIVE_RANGE_YEARLY: True,
        },
    )
    assert _at(coordinator, datetime(2031, 6, 1)) is True
    assert _at(coordinator, datetime(2031, 10, 1)) is False


def test_an_absolute_range_still_expires(mock_hass):
    """Off means off: the years count, and the range is over when it is over.

    And when it is, the user is told: an integration that stops for good one
    morning with nothing anywhere saying why is the worst of both worlds.
    """
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ACTIVE_START_DATE: "2026-04-01",
            CONF_ACTIVE_END_DATE: "2026-09-30",
            CONF_ACTIVE_RANGE_YEARLY: False,
        },
    )
    with patch(f"{COORDINATOR}.ir.async_create_issue") as create, patch(
        f"{COORDINATOR}.ir.async_delete_issue"
    ) as delete:
        assert _at(coordinator, datetime(2026, 6, 1)) is True
        create.assert_not_called()

        assert _at(coordinator, datetime(2027, 6, 1)) is False
        assert create.call_args.kwargs["translation_key"] == "active_range_expired"
        assert create.call_args.kwargs["translation_placeholders"]["end_date"] == "2026-09-30"

        # Checked on every poll, but the registry is only written when it changes
        create.reset_mock()
        assert _at(coordinator, datetime(2027, 6, 2)) is False
        create.assert_not_called()

        # ... and cleared as soon as the range covers today again
        assert _at(coordinator, datetime(2026, 6, 1)) is True
        assert delete.called


def test_a_february_29_boundary_survives_a_common_year(mock_hass):
    """Comparing month and day avoids building 29 February 2027."""
    coordinator = _make_coordinator(
        mock_hass,
        {CONF_ACTIVE_START_DATE: "2024-02-29", CONF_ACTIVE_END_DATE: "2024-03-31"},
    )
    assert _at(coordinator, datetime(2027, 3, 1)) is True
    assert _at(coordinator, datetime(2027, 2, 28)) is False


def test_is_within_date_range_before_start(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_ACTIVE_START_DATE: "2025-12-31", CONF_ACTIVE_RANGE_YEARLY: False}
    )
    assert _at(coordinator, datetime(2025, 1, 1)) is False


def test_a_one_sided_season_runs_to_the_end_of_the_year(mock_hass):
    coordinator = _make_coordinator(mock_hass, {CONF_ACTIVE_START_DATE: "2025-11-01"})
    assert _at(coordinator, datetime(2030, 12, 5)) is True
    assert _at(coordinator, datetime(2030, 1, 5)) is False


def test_is_time_between_normal_range(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._is_time_between(
        check_time=time(12, 0),
        start_time=time(8, 0),
        end_time=time(18, 0),
    ) is True


def test_is_time_between_overnight(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._is_time_between(
        check_time=time(1, 0),
        start_time=time(22, 0),
        end_time=time(5, 0),
    ) is True


def test_setup_and_remove_time_triggers(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    )
    trigger = MagicMock()

    with patch(
        "custom_components.inverter_charge_night.coordinator.async_track_time_change",
        return_value=trigger,
    ):
        coordinator.setup_time_triggers()

    assert len(coordinator._time_triggers) == 2
    coordinator.remove_time_triggers()
    trigger.assert_called()


def test_update_time_triggers_updates_listener(mock_hass):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_START_TIME: "00:00",
            CONF_END_TIME: "05:59",
            CONF_BATTERY_SOC_ENTITY: "sensor.old",
        },
    )
    coordinator.is_active = True
    coordinator._setup_battery_soc_listener = MagicMock()
    coordinator.setup_time_triggers = MagicMock()

    coordinator.entry.data = {
        CONF_START_TIME: "01:00",
        CONF_END_TIME: "06:00",
        CONF_BATTERY_SOC_ENTITY: "sensor.new",
    }

    coordinator.update_time_triggers()

    coordinator.setup_time_triggers.assert_called_once()
    coordinator._setup_battery_soc_listener.assert_called_once()


def test_is_time_between_equal_times_never_active(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._is_time_between(time(22, 0), time(22, 0), time(22, 0)) is False
    assert coordinator._is_time_between(time(3, 0), time(22, 0), time(22, 0)) is False


def test_setup_time_triggers_equal_times_registers_no_triggers(mock_hass, caplog):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "22:00", CONF_END_TIME: "22:00"}
    )

    with patch(
        "custom_components.inverter_charge_night.coordinator.async_track_time_change"
    ) as track_time_change:
        coordinator.setup_time_triggers()

    track_time_change.assert_not_called()
    assert coordinator._time_triggers == []
    assert "will never activate" in caplog.text
    # The window check still runs so an active window gets ended
    mock_hass.async_create_task.assert_called_once()


def test_update_time_triggers_schedules_single_window_check(mock_hass):
    coordinator = _make_coordinator(
        mock_hass, {CONF_START_TIME: "00:00", CONF_END_TIME: "05:59"}
    )

    with patch(
        "custom_components.inverter_charge_night.coordinator.async_track_time_change",
        return_value=MagicMock(),
    ):
        coordinator.update_time_triggers()

    assert len(coordinator._time_triggers) == 2
    mock_hass.async_create_task.assert_called_once()
    assert (
        mock_hass.async_create_task.call_args.kwargs["name"]
        == "inverter_charge_night_check_window"
    )


# The active date range is a calendar-day bound in the middle of the night -----


TRACK_TIME_CHANGE = "custom_components.inverter_charge_night.coordinator.async_track_time_change"


def _date_range_coordinator(mock_hass, start=None, end=None):
    config = {
        CONF_START_TIME: "23:01",
        CONF_END_TIME: "04:58",
    }
    if start:
        config[CONF_ACTIVE_START_DATE] = start
    if end:
        config[CONF_ACTIVE_END_DATE] = end
    return _make_coordinator(mock_hass, config)


def test_a_date_range_registers_a_midnight_check(mock_hass):
    """An overnight window crosses the range boundary in its own middle.

    Without this the first night of the range loses everything after midnight
    (the start trigger fired while still outside it), and the last night runs
    on until the next poll notices.
    """
    coordinator = _date_range_coordinator(mock_hass, start="2026-10-01", end="2027-03-31")
    with patch(TRACK_TIME_CHANGE) as track:
        coordinator.setup_time_triggers()

    hours = [c.kwargs.get("hour") for c in track.call_args_list]
    assert 0 in hours, "no check at the date boundary"
    assert len(track.call_args_list) == 3  # start, end, date boundary
    coordinator.remove_time_triggers()


def test_without_a_date_range_there_is_no_extra_trigger(mock_hass):
    coordinator = _date_range_coordinator(mock_hass)
    with patch(TRACK_TIME_CHANGE) as track:
        coordinator.setup_time_triggers()

    assert len(track.call_args_list) == 2
    coordinator.remove_time_triggers()


@pytest.mark.asyncio
async def test_the_midnight_check_starts_the_first_night_of_the_range(mock_hass):
    """23:01 on 30 September is outside the range; 00:05 on 1 October is not."""
    coordinator = _date_range_coordinator(mock_hass, start="2026-10-01")
    coordinator._check_current_window = AsyncMock()

    await coordinator._on_date_boundary(datetime(2026, 10, 1, 0, 0, 5))

    coordinator._check_current_window.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_midnight_check_does_nothing_while_disabled(mock_hass):
    coordinator = _date_range_coordinator(mock_hass, start="2026-10-01")
    coordinator.is_enabled = False
    coordinator._check_current_window = AsyncMock()

    await coordinator._on_date_boundary(datetime(2026, 10, 1, 0, 0, 5))

    coordinator._check_current_window.assert_not_awaited()
