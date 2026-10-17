"""Shared-bus and phase invariants, including failures independent of HA."""
from test_energy_balance import Plant
import pytest


def test_two_ev_demands_exceed_connection_before_regulation():
    p = Plant()
    p.load_scenario("two_ev_22kw")
    p.advance(1)
    assert p.ev_power == 44000
    assert p.grid_power == 44600
    assert p.overcurrent
    assert sum(p.phase_powers) == pytest.approx(p.grid_power)


def test_single_phase_overload_can_hide_in_total_power():
    p = Plant()
    p.load_scenario("phase_imbalance")
    p.advance(1)
    assert p.grid_power < 34776
    assert p.phase_currents[0] > 63
    assert p.grid_guard_power > 34776


@pytest.mark.parametrize("scenario", ["manual_transfer", "grid_outage", "overvoltage", "undervoltage", "phase_loss"])
def test_unusable_grid_cannot_charge_battery_or_cars(scenario):
    p = Plant()
    p.load_scenario(scenario)
    p.grid_charge, p.min_soc = True, 95
    p.wallbox1_connected = p.wallbox1_enabled = True
    p.wallbox1_current = 32
    p.advance(60)
    assert p.charge_ac_power == p.grid_power == p.ev_power == 0
    assert p.charge_inhibit


def test_manual_transfer_supplies_house_independently_of_reported_backup():
    p = Plant(soc=60, transfer_island=True, backup=False, block_discharge=True)
    p.advance(60)
    assert p.soc < 60 and p.battery_power < 0
    assert p.grid_power == p.unserved_power == 0


def test_outage_without_transfer_leaves_house_unserved():
    p = Plant(grid_available=False, soc=60)
    p.advance(1)
    assert p.unserved_power == p.total_house_load
    assert p.soc == pytest.approx(60)


def test_pv_fault_is_independent_of_forecast_and_other_string():
    p = Plant()
    p.load_scenario("pv_string_failure")
    p.advance(1)
    assert p.effective_pv == 6000 and p.forecast_tomorrow == 20
    assert sum(p.phase_powers) == pytest.approx(p.grid_power)
    p.snow_cover = 100
    p.advance(1)
    assert p.effective_pv == 0 and p.forecast_tomorrow == 20


def test_low_current_is_distinct_from_disconnected_or_disabled():
    p = Plant()
    p.load_scenario("ev_undercurrent")
    assert p.wallbox1_connected and p.wallbox1_enabled and p.wallbox1_current == 32
    assert p.wallbox_power(1) == 0


def test_each_appliance_is_allocated_to_its_phase():
    p = Plant(house_load=600, heat_pump=3000, cooker=4000, oven=2000,
              microwave=1000, washer=2000, dryer=1500, dishwasher=2000, block_discharge=True)
    p.advance(1)
    assert p.house_phases == (5200, 6200, 4700)
    assert p.grid_power == p.total_house_load == 16100


def test_eight_hours_without_ha_still_ends_at_inverter_target():
    p = Plant(soc=20, min_soc=70, grid_charge=True, online=False, block_discharge=True)
    p.advance(8 * 3600)
    assert p.soc == pytest.approx(70)
    assert p.charge_ac_power == 0
