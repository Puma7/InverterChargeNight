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


def observed(expected=20, actual=1):
    monitor = PVYieldMonitor()
    assert monitor.observe(RISE, RISE, SET, expected, 0) is False
    incident = monitor.observe(SET, RISE, SET, expected, actual)
    return monitor, incident


@pytest.mark.parametrize("expected,actual,low", [(20, 1, True), (10, .5, True), (10, 1.5, True), (10, 1.51, False), (20, 19, False), (4, 0, False), (0, 0, False)])
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
    assert monitor.observe(SET, RISE, SET, 0, 1)
    assert monitor.expected_kwh == 20
    assert not monitor.observe(SET + timedelta(minutes=1), RISE, SET, 0, 1)


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
    assert monitor.snapshot() == dict(day=None, expected_kwh=None, actual_kwh=None, charge_day=None, status="learning")


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
    co._pv_yield.observe(RISE, RISE, SET, 20, 0)
    publish(mock_hass, SET, 1)
    with patch(NOW, return_value=SET), patch("custom_components.inverter_charge_night.coordinator.ir.async_create_issue"), patch("homeassistant.components.persistent_notification.async_create") as notice:
        co._observe_pv_yield()
        notice.assert_called_once()
        assert "20" in notice.call_args.args[1]
        assert notice.call_args.kwargs["notification_id"] == "icn_pv_yield_pv-test"
