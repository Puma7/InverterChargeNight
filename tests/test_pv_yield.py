"""Daily PV underproduction: evidence, expiry and integration boundaries."""
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.inverter_charge_night.pv_yield import PVYieldMonitor
from custom_components.inverter_charge_night.planner import integrate_load
from custom_components.inverter_charge_night.prices import parse_price_series, mean_price_ct
from custom_components.inverter_charge_night.coordinator import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.config_flow import _validate_user_input

DAY = date(2026, 1, 15)
RISE = datetime(2026, 1, 15, 8, tzinfo=UTC)
SET = RISE.replace(hour=17)
NOW = "custom_components.inverter_charge_night.coordinator.dt_util.now"


def observed(expected=20, actual=1, mean=0.0):
    """A cold day by default: one dark day arms the full charge (3.14.7)."""
    monitor = PVYieldMonitor()
    assert monitor.observe(RISE, RISE, SET, expected, 0) is False
    incident = monitor.observe(SET, RISE, SET, expected, actual, mean)
    return monitor, incident


@pytest.mark.parametrize("expected,actual,low", [(20, 1, True), (20, 2, True), (10, .5, True), (10, 1.0, True), (10, 1.01, False), (10, 1.5, False), (20, 19, False), (4, 0, False), (0, 0, False)])
def test_complete_day_threshold(expected, actual, low):
    monitor, incident = observed(expected, actual)
    assert incident is low
    assert monitor.active_for(DAY + timedelta(days=1)) is low
    assert monitor.active_for(DAY) is False


@pytest.mark.parametrize("value", [None, float('nan'), float('inf'), -1])
def test_missing_or_invalid_forecast_never_arms(value):
    monitor, incident = observed(value, 0)
    assert incident is False
    assert monitor.status == "unavailable"


@pytest.mark.parametrize("value", [None, float('nan'), float('inf'), -1])
def test_missing_or_invalid_actual_never_arms(value):
    monitor, incident = observed(20, value)
    assert incident is False
    assert monitor.status == "unavailable"


def test_enabling_at_sunset_does_not_invent_a_full_day():
    monitor = PVYieldMonitor()
    assert not monitor.observe(SET, RISE, SET, 20, .5)
    assert monitor.expected_kwh is None


def test_freezes_morning_forecast_not_remaining_energy_in_evening():
    monitor = PVYieldMonitor()
    monitor.observe(RISE, RISE, SET, 10, 0)
    monitor.observe(RISE + timedelta(hours=1), RISE, SET, 20, .1)
    assert monitor.observe(SET, RISE, SET, 0, 1, 0.0)
    assert monitor.expected_kwh == 20
    assert not monitor.observe(SET + timedelta(minutes=1), RISE, SET, 0, 1, 0.0)


def test_next_night_only_and_persists_through_midnight():
    monitor, _ = observed()
    restored = PVYieldMonitor(monitor.snapshot())
    next_rise = RISE + timedelta(days=1)
    restored.observe(next_rise.replace(hour=1), next_rise, SET + timedelta(days=1), None, 0)
    assert restored.active_for(DAY + timedelta(days=1))
    restored.observe(RISE + timedelta(days=2), RISE + timedelta(days=2), SET + timedelta(days=2), 20, 0)
    assert not restored.active_for(DAY + timedelta(days=2))


@pytest.mark.parametrize("raw", [None, [], {"status": []}, {"status": {}}, {"day": "oops", "charge_day": "wrong", "status": "unsafe", "expected_kwh": float('nan'), "actual_kwh": True}])
def test_restore_rejects_malformed_data(raw):
    monitor = PVYieldMonitor(raw)
    assert monitor.snapshot() == dict(day=None, expected_kwh=None, actual_kwh=None, charge_day=None, status="learning",
                                      low_day=None, day_mean_c=None, trigger=None)


def make_co(hass):
    entry = MagicMock()
    entry.entry_id = "pv-test"
    entry.title = "PV Test"
    entry.options = {}
    entry.data = {"auto_pv_shortfall": True, "pv_forecast_today_entity": "sensor.today",
                  "pv_energy_today_entity": "sensor.actual", "start_time": "23:01", "end_time": "04:58",
                  "user_max_soc": 95}
    co = InverterChargeNightCoordinator(hass, entry)
    co._persist_state = MagicMock()
    return co


def publish(hass, now, actual, age=0):
    hass.states.async_set("sun.sun", "below_horizon", {"next_rising": (RISE + timedelta(days=1)).isoformat(), "next_setting": (SET + timedelta(days=1)).isoformat()})
    hass.states.async_set("sensor.today", "20", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.actual", str(actual), {"unit_of_measurement": "kWh"})
    hass.states.get("sensor.actual").last_reported = now - timedelta(seconds=age)


def test_coordinator_observation_notice_target_and_recovery(mock_hass):
    co = make_co(mock_hass)
    co._pv_yield.low_day = DAY - timedelta(days=1)  # the second dark day in a row
    publish(mock_hass, RISE, 0)
    with patch(NOW, return_value=RISE):
        co._observe_pv_yield()
    publish(mock_hass, SET, 1)
    with patch(NOW, return_value=SET), patch("custom_components.inverter_charge_night.coordinator.ir.async_create_issue") as create:
        co._observe_pv_yield()
        create.assert_called_once()
        assert create.call_args.kwargs["translation_key"] == "pv_yield_shortfall"
    with patch(NOW, return_value=SET.replace(hour=23, minute=10)):
        assert co.current_target_soc() == 95
        assert co.pv_yield_attributes()["automatic_full_charge"]
        co._adhoc_until = SET + timedelta(hours=8)
        assert not co._automatic_pv_shortfall_active()
    co._pv_yield.status = "normal"
    with patch(NOW, return_value=SET), patch("custom_components.inverter_charge_night.coordinator.ir.async_delete_issue") as delete:
        co._observe_pv_yield()
        delete.assert_called_once()
    co._persist_state.assert_called()


@pytest.mark.parametrize("age", [1801, -10])
def test_stale_or_future_actual_is_not_a_snow_diagnosis(mock_hass, age):
    co = make_co(mock_hass)
    co._pv_yield.observe(RISE, RISE, SET, 20, 0)
    publish(mock_hass, SET, .5, age)
    with patch(NOW, return_value=SET):
        co._observe_pv_yield()
    assert co._pv_yield.status == "unavailable"
    assert co._pv_yield.charge_day is None


def test_missing_sun_and_disabled_mode_do_not_assess(mock_hass):
    co = make_co(mock_hass)
    co._observe_pv_yield()
    assert co._pv_yield.day is None
    co.config["auto_pv_shortfall"] = False
    co._observe_pv_yield()
    assert not co.pv_yield_attributes()["enabled"]


def test_configuration_requires_both_daily_energy_and_today_forecast(mock_hass):
    errors = _validate_user_input({"auto_pv_shortfall": True}, mock_hass)
    assert errors["pv_forecast_today_entity"] == "required_entity"
    assert errors["pv_energy_today_entity"] == "required_entity"


@pytest.mark.parametrize("month,day,hours", [(3, 29, 4), (10, 25, 6)])
def test_load_profile_counts_repeated_and_missing_local_hours(month, day, hours):
    zone = ZoneInfo("Europe/Berlin")
    begin = datetime(2026, month, day, tzinfo=zone)
    assert integrate_load([1] * 24, begin, begin.replace(hour=5)) == hours


def test_load_profile_in_fractional_offset_zone():
    zone = ZoneInfo("Asia/Kathmandu")
    begin = datetime(2026, 1, 15, 11, 30, tzinfo=zone)
    profile = [0.] * 24
    profile[11], profile[12] = 2, 4
    assert integrate_load(profile, begin, begin.replace(hour=12, minute=30)) == 3


def test_price_average_preserves_both_folds_of_native_zoneinfo_datetimes():
    zone = ZoneInfo("Europe/Berlin")
    begin = datetime(2026, 10, 25, tzinfo=zone)
    values = [30, 30, 90, 90, 30, 30]
    points = [(begin.astimezone(UTC) + timedelta(hours=i)).astimezone(zone) for i in range(6)]
    series, error = parse_price_series({"prices": [{"start": point, "price": price} for point, price in zip(points, values)]}, entity_unit="ct/kWh", tz=zone, now=begin)
    assert not error and series is not None
    assert len(series.intervals) == 6
    assert mean_price_ct(series, begin, begin.replace(hour=5)) == 50
    assert mean_price_ct(series, begin.replace(tzinfo=None), begin.replace(hour=5, tzinfo=None)) == 50


@pytest.mark.asyncio
async def test_shortfall_sends_localized_persistent_notification(mock_hass):
    co = make_co(mock_hass)
    await co.async_load_communication_translations()
    co._pv_yield.low_day = DAY - timedelta(days=1)
    co._pv_yield.observe(RISE, RISE, SET, 20, 0)
    publish(mock_hass, SET, 1)
    with patch(NOW, return_value=SET), patch("custom_components.inverter_charge_night.coordinator.ir.async_create_issue"), patch("homeassistant.components.persistent_notification.async_create") as notice:
        co._observe_pv_yield()
        notice.assert_called_once()
        assert "20" in notice.call_args.args[1]
        assert notice.call_args.kwargs["notification_id"] == "icn_pv_yield_pv-test"


# --- 3.14.7: a dark day of fog is no snow (7/8 November 2026) ------------------

def test_one_foggy_day_does_not_arm_the_full_charge():
    """7 November: fog, no snow. A dark day at 7 °C and more waits for a second one."""
    monitor, incident = observed(20, 1, mean=8.0)
    assert incident is False
    assert monitor.status == "low_yield_unconfirmed"
    assert monitor.low_day == DAY and monitor.charge_day is None
    assert not monitor.active_for(DAY + timedelta(days=1))


def test_without_a_temperature_one_dark_day_is_not_enough():
    monitor, incident = observed(20, 1, mean=None)
    assert incident is False and monitor.status == "low_yield_unconfirmed"


@pytest.mark.parametrize("mean", [6.9, 0.0, -5.0])
def test_a_cold_dark_day_arms_at_once(mean):
    monitor, incident = observed(20, 1, mean=mean)
    assert incident is True
    assert monitor.trigger == "cold"
    assert monitor.active_for(DAY + timedelta(days=1))


def test_seven_degrees_is_not_cold():
    assert observed(20, 1, mean=7.0)[1] is False


def _next_day(monitor, offset, actual, mean=None, expected=20):
    rise, set_ = RISE + timedelta(days=offset), SET + timedelta(days=offset)
    monitor.observe(rise, rise, set_, expected, 0, mean)
    return monitor.observe(set_, rise, set_, expected, actual, mean)


def test_the_second_dark_day_in_a_row_arms_at_any_temperature():
    monitor, _ = observed(20, 1, mean=None)
    restored = PVYieldMonitor(monitor.snapshot())  # a restart in between
    assert _next_day(restored, 1, 1.5, mean=12.0) is True
    assert restored.trigger == "second_day"
    assert restored.active_for(DAY + timedelta(days=2))


def test_a_bright_day_in_between_breaks_the_row():
    monitor, _ = observed(20, 1, mean=None)
    assert _next_day(monitor, 1, 15) is False and monitor.status == "normal"
    assert _next_day(monitor, 2, 1) is False and monitor.status == "low_yield_unconfirmed"


def test_ten_percent_is_the_new_limit():
    """2 of 20 kWh is dark, 3 of 20 (15 %, dark before 3.14.7) is not."""
    assert observed(20, 2)[1] is True
    monitor, incident = observed(20, 3)
    assert incident is False and monitor.status == "normal"


def test_a_foggy_day_raises_no_notice_and_charges_as_planned(mock_hass):
    co = make_co(mock_hass)
    co.config["weather_entity"] = "weather.home"
    mock_hass.states.async_set("weather.home", "fog", {"temperature": 8.0, "temperature_unit": "°C"})
    with patch(NOW, return_value=RISE):
        for _ in range(6):
            co._observe_outdoor_temperature()
    publish(mock_hass, RISE, 0)
    with patch(NOW, return_value=RISE):
        co._observe_pv_yield()
    publish(mock_hass, SET, 1)
    with patch(NOW, return_value=SET), patch("custom_components.inverter_charge_night.coordinator.ir.async_create_issue") as create:
        co._observe_pv_yield()
    create.assert_not_called()
    assert co._pv_yield.status == "low_yield_unconfirmed"
    assert co._pv_yield.day_mean_c == 8.0
    with patch(NOW, return_value=SET.replace(hour=23, minute=10)):
        assert not co._automatic_pv_shortfall_active()


def test_a_frosty_dark_day_arms_from_the_weather_entity(mock_hass):
    co = make_co(mock_hass)
    co.config["weather_entity"] = "weather.home"
    mock_hass.states.async_set("weather.home", "snowy", {"temperature": -1.0, "temperature_unit": "°C"})
    with patch(NOW, return_value=RISE):
        for _ in range(6):
            co._observe_outdoor_temperature()
    publish(mock_hass, RISE, 0)
    with patch(NOW, return_value=RISE):
        co._observe_pv_yield()
    publish(mock_hass, SET, 1)
    with patch(NOW, return_value=SET), patch("custom_components.inverter_charge_night.coordinator.ir.async_create_issue") as create:
        co._observe_pv_yield()
    create.assert_called_once()
    assert co._pv_yield.trigger == "cold"
    with patch(NOW, return_value=SET.replace(hour=23, minute=10)):
        assert co._automatic_pv_shortfall_active()


@pytest.mark.asyncio
async def test_the_log_names_the_dark_day_not_snow(mock_hass, caplog):
    """23:01 on 7/8 November said "Snow mode" with 0 snow nights."""
    import logging
    co = make_co(mock_hass)
    co._pv_yield.low_day = DAY - timedelta(days=1)
    co._pv_yield.observe(RISE, RISE, SET, 20, 0)
    co._pv_yield.observe(SET, RISE, SET, 20, 1)
    with patch(NOW, return_value=SET.replace(hour=23, minute=1)), caplog.at_level(logging.INFO):
        await co._calculate_initial_soc()
    lines = [r.getMessage() for r in caplog.records]
    assert "Very low PV yield on 2026-01-15 (1 of 20 kWh, the second dark day in a row): charging to 95%" in lines
    assert not any(line.startswith("Snow mode") for line in lines)
    assert co.calculated_soc == 95
