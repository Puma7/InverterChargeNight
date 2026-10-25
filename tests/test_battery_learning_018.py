"""Plan 018 part 2: capacity and efficiencies from the battery's own energy balance."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night import battery_learning as bl
from custom_components.inverter_charge_night.const import (
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_CHARGE_POWER_HOUSE_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_DISCHARGE_EFFICIENCY,
    CONF_GRID_IMPORT_ENTITY,
    CONF_PV_ENERGY_TODAY_ENTITY,
)
from tests.test_grid_limit import (
    BASE_CONFIG, BATTERY, GRID_IMPORT, NOW, RECEIVED, _make_coordinator, _register,
)

NAMEPLATE = 35.8
T0 = 1_760_000_000.0
HOUSE = "sensor.home_power"
PV_METER = "sensor.pv_today"


def _series(*stretches: tuple[int, float], soc: float = 30.0, capacity: float = 32.0,
            house: float | None = 500.0, grid: Any = None, pv: float | None = 0.0,
            sign: float = -1.0, start: float = T0) -> list[bl.Bucket]:
    """Buckets for stretches of (count, battery W as the battery sees it, + = charging).

    ``sign`` is the sensor's convention: -1 is a Kostal, which reports
    charging as negative. The SOC follows the true power and ``capacity``.
    """
    buckets: list[bl.Bucket] = []
    ts = start
    for count, power in stretches:
        for _ in range(count):
            grid_w = grid(power) if callable(grid) else grid
            buckets.append(bl.Bucket(ts, soc, sign * power, house, grid_w, pv))
            soc += power * bl.BUCKET_S / 3600.0 / 1000.0 / capacity * 100.0
            ts += bl.BUCKET_S
    return buckets


def _night_charge(**kwargs: Any) -> list[bl.Bucket]:
    """Idle, two and a half hours at 3 kW, idle - at 95 % from the grid."""
    return _series((3, 0.0), (30, 3000.0), (3, 0.0),
                   grid=lambda p: 500.0 + p / 0.95, **kwargs)


def test_a_kostal_counts_charging_as_negative():
    assert bl.charging_sign(_night_charge()) == -1.0
    assert bl.charging_sign(_night_charge(sign=1.0)) == 1.0


def test_a_night_charge_measures_the_capacity_and_the_charger():
    samples = bl.segment_samples(_night_charge(), NAMEPLATE)

    kinds = {s.kind: s.value for s in samples}
    assert kinds[bl.KIND_CAPACITY] == pytest.approx(32.0)
    assert kinds[bl.KIND_CHARGE] == pytest.approx(0.95)
    assert bl.KIND_DISCHARGE not in kinds


def test_a_night_discharge_measures_the_inverter():
    """2 kW out of the battery, 1.9 kW of it reaching the house: 95 %."""
    buckets = _series((3, 0.0), (40, -2000.0), (3, 0.0), soc=80.0, house=2500.0,
                      grid=lambda p: 600.0 if p else 2500.0)

    samples = {s.kind: s.value for s in bl.segment_samples(buckets, NAMEPLATE)}

    assert samples[bl.KIND_DISCHARGE] == pytest.approx(0.95)
    assert samples[bl.KIND_CAPACITY] == pytest.approx(32.0)


def test_with_the_sun_on_the_panels_only_the_capacity_counts():
    samples = bl.segment_samples(_night_charge(pv=0.05), NAMEPLATE)

    assert [s.kind for s in samples] == [bl.KIND_CAPACITY]


def test_without_house_or_grid_only_the_capacity_counts():
    samples = bl.segment_samples(_series((3, 0.0), (30, 3000.0), (3, 0.0), house=None),
                                 NAMEPLATE)

    assert [s.kind for s in samples] == [bl.KIND_CAPACITY]


def test_a_short_or_small_stretch_is_the_soc_resolution_not_a_measurement():
    short = _series((3, 0.0), (bl.MIN_SEGMENT_BUCKETS - 1, 6000.0), (3, 0.0))
    small = _series((3, 0.0), (20, 600.0), (3, 0.0))  # 20 x 50 Wh: 3 % of the battery

    assert bl.segment_samples(short, NAMEPLATE) == []
    assert bl.segment_samples(small, NAMEPLATE) == []


def test_a_stretch_cut_by_the_data_is_left_for_tomorrow():
    """Still charging at the last bucket: tomorrow it is measured whole."""
    running = _series((3, 0.0), (30, 3000.0))
    begun = _series((30, 3000.0), (3, 0.0))

    assert bl.segment_samples(running, NAMEPLATE) == []
    assert bl.segment_samples(begun, NAMEPLATE) == []


def test_a_gap_in_the_statistics_splits_the_stretch():
    buckets = _night_charge()
    gapped = [b for i, b in enumerate(buckets) if i != 18]

    starts = [s.start for s in bl.segment_samples(gapped, NAMEPLATE) if s.kind == bl.KIND_CAPACITY]

    assert len(starts) == 2


def test_an_impossible_capacity_says_the_sensors_disagree():
    """A SOC that barely moves for 7.5 kWh would be a 90 kWh battery."""
    buckets = _series((3, 0.0), (30, 3000.0), (3, 0.0), capacity=90.0)
    buckets = [bl.Bucket(b.start, 30.0 + (b.soc - 30.0) * 90.0 / 7.0, b.battery_w, b.house_w,
                         b.grid_w, b.pv_kwh) for b in buckets]  # 10 % more SOC than energy

    capacities = [s for s in bl.segment_samples(buckets, NAMEPLATE) if s.kind == bl.KIND_CAPACITY]

    assert capacities == []


def test_an_impossible_efficiency_is_dropped():
    samples = bl.segment_samples(_series((3, 0.0), (30, 3000.0), (3, 0.0),
                                         grid=lambda p: 500.0 + p * 0.9), NAMEPLATE)

    assert bl.KIND_CHARGE not in {s.kind for s in samples}


def test_the_log_keeps_each_stretch_once_and_a_year_long():
    log = bl.BatteryLog()
    samples = bl.segment_samples(_night_charge(), NAMEPLATE)

    assert log.add(samples) is True
    assert log.add(samples) is False
    assert log.count(bl.KIND_CAPACITY) == 1
    assert log.prune(T0 + (bl.SAMPLES_KEPT_DAYS + 1) * 86400.0) is True
    assert log.samples == []


def test_three_samples_make_a_value_and_recent_ones_weigh_more():
    log = bl.BatteryLog()
    log.add([bl.Sample(T0, bl.KIND_CAPACITY, 34.0), bl.Sample(T0 + 1, bl.KIND_CAPACITY, 34.0)])
    assert log.estimate(bl.KIND_CAPACITY, T0) is None

    year = 365 * 86400.0
    log.add([bl.Sample(T0 + year, bl.KIND_CAPACITY, 31.0), bl.Sample(T0 + year + 1, bl.KIND_CAPACITY, 31.0),
             bl.Sample(T0 + year + 2, bl.KIND_CAPACITY, 31.0)])
    assert log.estimate(bl.KIND_CAPACITY, T0 + year) == 31.0


def test_the_log_survives_a_round_trip_and_ignores_junk():
    log = bl.BatteryLog()
    log.add([bl.Sample(T0, bl.KIND_CHARGE, 0.951234)])
    raw = log.snapshot()
    raw["samples"].extend([{"ts": "x", "kind": "capacity", "v": 1}, {"ts": 1, "kind": "nope", "v": 1}, 7])

    again = bl.BatteryLog(raw)

    assert again.samples == [bl.Sample(T0, bl.KIND_CHARGE, 0.9512)]


def test_learned_values_are_bounded():
    assert bl.bounded_capacity(50.0, NAMEPLATE) == pytest.approx(1.05 * NAMEPLATE)
    assert bl.bounded_capacity(5.0, NAMEPLATE) == pytest.approx(0.6 * NAMEPLATE)
    assert bl.bounded_capacity(None, NAMEPLATE) is None
    assert bl.bounded_efficiency(1.2) == 0.99
    assert bl.bounded_efficiency(0.5) == 0.75


# --- In the coordinator ---------------------------------------------------------

CONFIG = {**BASE_CONFIG, CONF_CHARGE_POWER_RECEIVED_ENTITY: RECEIVED, "battery_capacity": NAMEPLATE}


@pytest.fixture
def coordinator(mock_hass):
    _register(mock_hass)
    return _make_coordinator(mock_hass, CONFIG)


def _learned(coordinator, kind: str, value: float, count: int = 3) -> None:
    now = datetime.now().timestamp()
    coordinator._battery_log.add([bl.Sample(now - i, kind, value) for i in range(count)])


def test_the_plan_uses_the_learned_capacity(coordinator):
    assert coordinator._capacity_kwh() == NAMEPLATE
    _learned(coordinator, bl.KIND_CAPACITY, 31.0)

    assert coordinator._capacity_kwh() == pytest.approx(31.0)
    coordinator.use_learned_values = False
    assert coordinator._capacity_kwh() == NAMEPLATE


def test_the_measured_loss_of_the_search_is_the_charge_efficiency(coordinator):
    _learned(coordinator, bl.KIND_CHARGE, 0.91)
    assert coordinator.learned_charge_efficiency() == (pytest.approx(0.91), "energy_balance")

    coordinator.entry.options = {CONF_AUTO_EFFICIENCY_DATA: {
        "best_loss": 0.04, "measurement_signature": coordinator._efficiency_signature(),
        "bounds_w": [500, 10000]}}

    assert coordinator.learned_charge_efficiency() == (pytest.approx(0.96), "efficiency_search")
    assert coordinator._charge_efficiency() == pytest.approx(0.96)


def test_a_search_of_another_setup_does_not_count(coordinator):
    coordinator.entry.options = {CONF_AUTO_EFFICIENCY_DATA: {
        "best_loss": 0.04, "measurement_signature": "another", "bounds_w": [500, 10000]}}

    assert coordinator.learned_charge_efficiency() == (None, None)
    assert coordinator._charge_efficiency() == 0.9  # the setting


def test_a_search_over_another_range_does_not_count(coordinator):
    coordinator.entry.options = {CONF_AUTO_EFFICIENCY_DATA: {
        "best_loss": 0.04, "measurement_signature": coordinator._efficiency_signature(),
        "bounds_w": [1000, 8000]}}

    assert coordinator._measured_best_loss() is None


def test_the_discharge_efficiency(coordinator):
    coordinator.config = {**coordinator.config, CONF_DISCHARGE_EFFICIENCY: 0.95}
    assert coordinator._discharge_efficiency() == 0.95
    _learned(coordinator, bl.KIND_DISCHARGE, 0.9)

    assert coordinator._discharge_efficiency() == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_the_plan_input_carries_what_was_learned(coordinator):
    _learned(coordinator, bl.KIND_CAPACITY, 31.0)
    _learned(coordinator, bl.KIND_CHARGE, 0.93)
    _learned(coordinator, bl.KIND_DISCHARGE, 0.92)

    with patch(NOW, return_value=datetime(2026, 1, 14, 23, 0)):
        plan_input, _, _ = await coordinator._build_plan_input(5.0, True)

    assert plan_input.capacity_kwh == pytest.approx(31.0)
    assert plan_input.charge_efficiency == pytest.approx(0.93)
    assert plan_input.discharge_efficiency == pytest.approx(0.92)


def test_the_sensor_attributes(coordinator):
    _learned(coordinator, bl.KIND_CAPACITY, 31.0)

    attrs = coordinator.battery_learning_attributes()

    assert attrs["learned_capacity_kwh"] == 31.0
    assert attrs["capacity_kwh_used"] == 31.0
    assert attrs["nameplate_kwh"] == NAMEPLATE
    assert attrs["charge_efficiency_source"] is None
    assert attrs["charge_efficiency_used"] == 0.9


def _recorder() -> Any:
    instance = MagicMock()
    instance.async_add_executor_job = AsyncMock(side_effect=lambda job: job())
    return patch("custom_components.inverter_charge_night.coordinator.get_instance",
                 return_value=instance)


def _rows(buckets: list[bl.Bucket]) -> dict[str, list[dict[str, float]]]:
    return {
        BATTERY: [{"start": b.start, "mean": b.soc} for b in buckets],
        RECEIVED: [{"start": b.start, "mean": b.battery_w / 1000.0} for b in buckets],  # kW
        HOUSE: [{"start": b.start, "mean": b.house_w} for b in buckets],
        GRID_IMPORT: [{"start": b.start, "mean": b.grid_w} for b in buckets],
        PV_METER: [{"start": b.start, "change": b.pv_kwh} for b in buckets],
    }


@pytest.mark.asyncio
async def test_the_recorder_statistics_become_samples(mock_hass):
    _register(mock_hass)
    co = _make_coordinator(mock_hass, {**CONFIG, CONF_CHARGE_POWER_HOUSE_ENTITY: HOUSE,
                                       CONF_GRID_IMPORT_ENTITY: GRID_IMPORT,
                                       CONF_PV_ENERGY_TODAY_ENTITY: PV_METER})
    mock_hass.states.async_set(RECEIVED, "0", {"unit_of_measurement": "kW"})
    for entity in (HOUSE, GRID_IMPORT):
        mock_hass.states.async_set(entity, "0", {"unit_of_measurement": "W"})
    mock_hass.states.async_set(PV_METER, "0", {"unit_of_measurement": "kWh"})
    rows = _rows(_night_charge(start=datetime.now().timestamp() - 86400))

    with _recorder(), patch("homeassistant.components.recorder.statistics.statistics_during_period",
                            return_value=rows):
        assert await co._learn_battery() is True

    assert co._battery_log.count(bl.KIND_CAPACITY) == 1
    assert co._battery_log.count(bl.KIND_CHARGE) == 1
    assert co._battery_learning_status == bl.STATUS_LEARNING  # one stretch is not three


@pytest.mark.asyncio
async def test_without_the_battery_power_there_is_no_balance(mock_hass):
    _register(mock_hass)
    co = _make_coordinator(mock_hass, BASE_CONFIG)

    assert await co._learn_battery() is False
    assert co._battery_learning_status == bl.STATUS_NO_SENSORS


@pytest.mark.asyncio
async def test_without_statistics_the_status_says_so(coordinator):
    with _recorder(), patch("homeassistant.components.recorder.statistics.statistics_during_period",
                            return_value={}):
        assert await coordinator._learn_battery() is False

    assert coordinator._battery_learning_status == bl.STATUS_NO_STATISTICS


@pytest.mark.asyncio
async def test_the_switch_turns_the_learned_values_off_and_on(coordinator):
    from custom_components.inverter_charge_night.switch import UseLearnedValuesSwitch

    coordinator.async_request_refresh = AsyncMock()
    coordinator._persist_state = MagicMock()
    switch = UseLearnedValuesSwitch(coordinator, coordinator.entry)
    switch.async_write_ha_state = MagicMock()
    assert switch.is_on is True

    await switch.async_turn_off()
    assert coordinator.use_learned_values is False
    coordinator._persist_state.assert_called_once()
    await switch.async_turn_off()
    coordinator._persist_state.assert_called_once()

    await switch.async_turn_on()
    assert switch.is_on is True


def test_the_choice_survives_a_restart(mock_hass):
    _register(mock_hass)
    co = _make_coordinator(mock_hass, CONFIG, options={"runtime_state": {"use_learned_values": False}})

    assert co.use_learned_values is False


def test_the_sensors_show_the_learned_values(coordinator):
    from custom_components.inverter_charge_night.sensor import (
        PVForecastCorrectionSensor, UsableBatteryCapacitySensor,
    )

    capacity = UsableBatteryCapacitySensor(coordinator, coordinator.entry)
    correction = PVForecastCorrectionSensor(coordinator, coordinator.entry)
    assert capacity.native_value is None
    assert correction.native_value is None

    _learned(coordinator, bl.KIND_CAPACITY, 31.237)
    assert capacity.native_value == 31.24
    assert capacity.extra_state_attributes["capacity_samples"] == 3
    assert correction.extra_state_attributes["status"] == "no_pv_meter"


def test_a_day_old_sample_is_still_fresh(coordinator):
    """Estimates are weighted by age against now; a day is nothing."""
    _learned(coordinator, bl.KIND_CAPACITY, 31.0)
    later = datetime.now() + timedelta(days=1)
    assert coordinator._battery_log.estimate(bl.KIND_CAPACITY, later.timestamp()) == 31.0
