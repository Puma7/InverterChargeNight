"""Physical invariants; these run without Home Assistant or device mocks."""
import importlib.util
from pathlib import Path
import sys
import pytest

SPEC = importlib.util.spec_from_file_location("icn_plant", Path(__file__).parents[1] /
    "custom_components/icn_simulator/model.py")
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)
Plant = MODEL.Plant


def test_grid_charge_accounts_for_loss_and_house_and_ev():
    plant = Plant(capacity=10, soc=20, min_soc=100, grid_charge=True,
                  charge_limit=2000, house_load=500, ev_load=11000)
    plant.advance(3600)
    assert plant.soc == pytest.approx(38)
    assert plant.stored_energy == pytest.approx(1.8)
    assert plant.grid_charge_energy == pytest.approx(2)
    assert plant.grid_import_energy == pytest.approx(13.5)


def test_manual_charging_stops_at_target_and_leaves_pv_headroom():
    plant = Plant(capacity=10, soc=20, min_soc=70, grid_charge=True,
                  charge_limit=5000, house_load=0)
    plant.advance(6 * 3600)
    assert plant.soc == pytest.approx(70)
    assert plant.stored_energy == pytest.approx(5)
    assert plant.grid_charge_energy == pytest.approx(5 / 0.9)
    plant.grid_charge = False
    plant.pv_power = 5000
    plant.advance(3600)
    assert plant.soc == pytest.approx(100)
    assert plant.grid_export_energy > 0


def test_discharge_loss_and_minimum_soc_are_respected():
    plant = Plant(capacity=10, soc=60, min_soc=10, house_load=1000)
    plant.advance(8 * 3600)
    assert plant.soc == pytest.approx(10)
    assert plant.discharged_energy == pytest.approx(5)
    assert plant.house_energy == pytest.approx(8)
    assert plant.grid_import_energy == pytest.approx(8 - 5 * 0.95)


def test_island_has_no_grid_and_reports_unserved_load():
    plant = Plant(capacity=10, soc=10, min_soc=70, grid_charge=True,
                  block_discharge=True, backup=True, house_load=1000)
    plant.advance(3600)
    assert plant.grid_import_energy == 0
    assert plant.grid_charge_energy == 0
    assert plant.soc == pytest.approx(0)
    assert plant.unserved_power == pytest.approx(1000)


def test_communication_loss_does_not_stop_physical_charging():
    plant = Plant(soc=20, min_soc=90, grid_charge=True, online=False)
    plant.advance(300)
    assert plant.soc > 20


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan"), 86401])
def test_reject_invalid_time(seconds):
    with pytest.raises(ValueError):
        Plant().advance(seconds)


def test_scenario_resets_faults_commands_and_energy():
    plant = Plant(online=False, backup=True, grid_charge=True, stored_energy=5)
    plant.load_scenario("winter_cloudy")
    assert plant.online and not plant.backup and not plant.grid_charge
    assert plant.stored_energy == 0 and plant.soc == 20
