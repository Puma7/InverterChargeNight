"""Tests for auto efficient charge logic."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.const import (
    AUTO_EFFICIENCY_KEYS,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_BATTERY_SOC_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
)


def _make_coordinator(hass: HomeAssistant, data: dict, options: dict | None = None):
    entry = MagicMock()
    entry.title = "Test"
    entry.entry_id = "test_entry"
    entry.data = data
    entry.options = options or {}
    return InverterChargeNightCoordinator(hass, entry)


def test_get_power_w_converts_kw(mock_hass: HomeAssistant):
    mock_hass.states.async_set("sensor.power", "2.5", {"unit_of_measurement": "kW"})

    coordinator = _make_coordinator(mock_hass, {CONF_MIN_CHARGE_POWER_W: 1000, CONF_MAX_CHARGE_POWER_W: 2000})
    assert coordinator._get_power_w("sensor.power") == 2500.0


def test_select_next_auto_test_power_within_range(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    candidate = coordinator._select_next_auto_test_power_w()
    assert candidate is not None
    assert 5000 <= candidate <= 15000
    assert candidate % 100 == 0


@pytest.mark.asyncio
async def test_apply_absolute_charge_power_limit_converts_units(mock_hass: HomeAssistant):
    mock_hass.services.async_call = AsyncMock()
    mock_hass.states.async_set("number.abs_max", "12.0", {"unit_of_measurement": "kW"})

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.abs_max",
            CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 10000,
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    await coordinator._apply_absolute_charge_power_limit()

    mock_hass.services.async_call.assert_called()
    args, kwargs = mock_hass.services.async_call.call_args
    data = kwargs.get("service_data") if kwargs else None
    if data is None and len(args) >= 3:
        data = args[2]
    assert data["value"] == 10.0


@pytest.mark.asyncio
async def test_reset_absolute_charge_power_restores_original(mock_hass: HomeAssistant):
    mock_hass.services.async_call = AsyncMock()
    mock_hass.states.async_set("number.abs_max", "12.0", {"unit_of_measurement": "kW"})

    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.abs_max",
            CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 10000,
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._original_absolute_charge_power = 12.0
    await coordinator._reset_absolute_charge_power()

    mock_hass.services.async_call.assert_called()
    args, kwargs = mock_hass.services.async_call.call_args
    data = kwargs.get("service_data") if kwargs else None
    if data is None and len(args) >= 3:
        data = args[2]
    assert data["value"] == 12.0


@pytest.mark.asyncio
async def test_start_auto_test_sets_fields(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=True)
    await coordinator._start_auto_test(5000)

    assert coordinator._auto_test_active is True
    assert coordinator._auto_test_power_w == 5000
    coordinator._set_ac_charge_limit_w.assert_awaited_once_with(5000)


@pytest.mark.asyncio
async def test_a_test_whose_setpoint_did_not_arrive_is_not_started(mock_hass: HomeAssistant):
    """Whole-repo review, finding 8.

    The write result was ignored. With the setpoint not on the inverter, the
    battery charges at whatever stands there - usually the previous test's
    higher value, which the follow check (it only catches drawing *less*)
    lets through - and the sample is filed under a power it was never taken
    at. That entry then steers every later night.
    """
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._set_ac_charge_limit_w = AsyncMock(return_value=False)
    await coordinator._start_auto_test(5000)

    coordinator._set_ac_charge_limit_w.assert_awaited_once_with(5000)
    assert coordinator._auto_test_active is False
    assert coordinator._auto_test_power_w is None
    assert coordinator._auto_test_start is None


def test_reset_auto_test_state_clears_values(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    coordinator._auto_test_active = True
    coordinator._auto_test_power_w = 5000
    coordinator._auto_test_start = MagicMock()
    coordinator._auto_last_sample_time = MagicMock()
    coordinator._auto_energy_sent_wh = 1.0
    coordinator._auto_energy_received_wh = 1.0

    coordinator._reset_auto_test_state()

    assert coordinator._auto_test_active is False
    assert coordinator._auto_test_power_w is None


def test_save_auto_efficiency_data_updates_options(mock_hass: HomeAssistant):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_MIN_CHARGE_POWER_W: 5000,
            CONF_MAX_CHARGE_POWER_W: 15000,
        },
    )
    mock_hass.config_entries.async_update_entry = MagicMock()
    data = {"best_power_w": 5000}

    coordinator._save_auto_efficiency_data(data)

    mock_hass.config_entries.async_update_entry.assert_called_once()


# Efficiency per state-of-charge band (plan 015) ---------------------------------


@pytest.mark.parametrize(
    "soc,expected",
    [(0.0, "0"), (19.9, "0"), (20.0, "20"), (55.0, "40"), (99.9, "80"), (100.0, "80")],
)
def test_a_state_of_charge_falls_into_its_band(soc, expected):
    """100 % joins the top band: a battery is only briefly full, and a band of
    its own would never gather enough samples to be used."""
    assert InverterChargeNightCoordinator._efficiency_band(soc) == expected


def test_a_soc_outside_zero_to_hundred_is_clamped_into_a_band():
    assert InverterChargeNightCoordinator._efficiency_band(-5.0) == "0"
    assert InverterChargeNightCoordinator._efficiency_band(140.0) == "80"


def test_a_measurement_is_filed_under_the_band_it_was_measured_in(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._band_for_measurement(42.0, 58.0) == "40"


def test_a_measurement_across_too_many_bands_stays_battery_wide(mock_hass):
    """A charge from a third full to nearly full is a blend of four bands and
    is evidence about none of them."""
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._band_for_measurement(20.0, 95.0) is None


def test_a_measurement_without_a_readable_soc_stays_battery_wide(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    assert coordinator._band_for_measurement(None, 55.0) is None
    assert coordinator._band_for_measurement(55.0, None) is None


def test_a_band_only_overrules_once_it_has_been_searched(mock_hass):
    """Two measurements are samples; three are a search. Until then the
    battery-wide optimum is the better guess."""
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.get_auto_efficiency_data = MagicMock(
        return_value={
            "best_power_w": 6000,
            "bands": {"40": {"best_power_w": 3000, "history": {"3000": 0.05, "4000": 0.07}}},
        }
    )
    assert coordinator.best_charge_power_w(50.0) == 6000

    coordinator.get_auto_efficiency_data.return_value["bands"]["40"]["history"]["5000"] = 0.09
    assert coordinator.best_charge_power_w(50.0) == 3000


def test_an_unmeasured_band_falls_back_to_the_battery_wide_optimum(mock_hass):
    coordinator = _make_coordinator(mock_hass, {})
    coordinator.get_auto_efficiency_data = MagicMock(
        return_value={
            "best_power_w": 6000,
            "bands": {"40": {"best_power_w": 3000, "history": {"1": 0.1, "2": 0.1, "3": 0.1}}},
        }
    )
    assert coordinator.best_charge_power_w(90.0) == 6000
    # And an installation that measured before bands existed keeps its result
    coordinator.get_auto_efficiency_data.return_value.pop("bands")
    assert coordinator.best_charge_power_w(50.0) == 6000
    assert coordinator.best_charge_power_w(None) == 6000


def test_recording_a_sample_fills_the_band_and_its_best(mock_hass):
    mock_hass.states.async_set("sensor.soc", "52", {"unit_of_measurement": "%"})
    coordinator = _make_coordinator(mock_hass, {CONF_BATTERY_SOC_ENTITY: "sensor.soc"})
    coordinator._auto_start_soc = 48.0
    data: dict = {}

    coordinator._record_band_sample(data, 5000, 0.08)
    assert data["bands"]["40"] == {
        "history": {"5000": 0.08},
        "best_loss": 0.08,
        "best_power_w": 5000,
    }

    # A worse measurement is kept as history but does not become the best
    coordinator._record_band_sample(data, 7000, 0.12)
    assert data["bands"]["40"]["best_power_w"] == 5000
    assert data["bands"]["40"]["history"] == {"5000": 0.08, "7000": 0.12}

    # A better one takes over
    coordinator._record_band_sample(data, 3000, 0.04)
    assert data["bands"]["40"]["best_power_w"] == 3000


def test_a_finished_measurement_lands_in_its_band_and_battery_wide(mock_hass):
    """The whole path: settle, measure, finalize - and both stores filled."""
    mock_hass.states.async_set("sensor.soc", "56", {"unit_of_measurement": "%"})
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_BATTERY_SOC_ENTITY: "sensor.soc",
            CONF_MIN_CHARGE_POWER_W: 1000,
            CONF_MAX_CHARGE_POWER_W: 10000,
        },
    )
    saved: dict = {}
    coordinator.get_auto_efficiency_data = MagicMock(return_value=saved)
    coordinator._save_auto_efficiency_data = MagicMock()

    now = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)
    with patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now",
        return_value=now - timedelta(hours=1),
    ):
        coordinator._begin_auto_measurement(now - timedelta(hours=1))
    assert coordinator._auto_start_soc == 56.0

    coordinator._auto_test_active = True
    coordinator._auto_test_power_w = 5000
    coordinator._auto_test_start = now - timedelta(hours=2)
    coordinator._auto_energy_sent_wh = 5000.0
    coordinator._auto_energy_received_wh = 4600.0
    mock_hass.states.async_set("sensor.soc", "64", {"unit_of_measurement": "%"})

    with patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now", return_value=now
    ):
        coordinator._finalize_auto_test()

    stored = coordinator._save_auto_efficiency_data.call_args.args[0]
    assert stored["history"]["5000"] == pytest.approx(0.08)
    # 56 % to 64 %, midpoint 60 %
    assert stored["bands"]["60"]["best_power_w"] == 5000
    assert stored["bands"]["60"]["history"]["5000"] == pytest.approx(0.08)


def test_the_band_store_survives_the_startup_migration():
    """_migrate_entry_data drops every efficiency key it does not know, so a
    missing entry in AUTO_EFFICIENCY_KEYS would silently wipe the bands on
    every restart."""
    assert "bands" in AUTO_EFFICIENCY_KEYS
