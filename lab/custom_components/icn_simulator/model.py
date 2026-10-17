"""Deterministic energy balance; independent of Home Assistant and wall time.

All power is in W, energy in kWh, time in seconds. Positive battery power
means charging on the DC side; positive grid power means importing.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import math


@dataclass
class Plant:
    capacity: float = 35.8
    soc: float = 25.0
    min_soc: float = 8.0
    charge_limit: float = 5000.0
    discharge_limit: float = 5000.0
    charge_efficiency: float = 0.90
    discharge_efficiency: float = 0.95
    house_load: float = 600.0
    ev_load: float = 0.0
    pv_power: float = 0.0
    heat_pump: float = 0.0
    cooker: float = 0.0
    oven: float = 0.0
    microwave: float = 0.0
    washer: float = 0.0
    dryer: float = 0.0
    dishwasher: float = 0.0
    extra_l1: float = 0.0
    pv_string1: float = 0.0
    pv_string2: float = 0.0
    string1_available: bool = True
    string2_available: bool = True
    snow_cover: float = 0.0
    voltage_l1: float = 230.0
    voltage_l2: float = 230.0
    voltage_l3: float = 230.0
    main_fuse: float = 63.0
    power_factor: float = 1.0
    grid_available: bool = True
    transfer_island: bool = False
    protection_trip: bool = False
    meter_online: bool = True
    soc_sensor_online: bool = True
    backup_sensor_online: bool = True
    wallbox1_connected: bool = False
    wallbox2_connected: bool = False
    wallbox1_enabled: bool = False
    wallbox2_enabled: bool = False
    wallbox1_current: float = 6.0
    wallbox2_current: float = 6.0
    wallbox1_demand: float = 22000.0
    wallbox2_demand: float = 22000.0
    wallbox1_stalled: bool = False
    wallbox2_stalled: bool = False
    forecast_today: float = 4.0
    forecast_tomorrow: float = 4.0
    speed: float = 1.0
    grid_charge: bool = False
    block_discharge: bool = False
    backup: bool = False
    online: bool = True
    forecast_available: bool = True
    reject_writes: bool = False
    reject_charge_limit: bool = False
    battery_power: float = 0.0
    charge_ac_power: float = 0.0
    grid_power: float = 600.0
    unserved_power: float = 0.0
    grid_charge_energy: float = 0.0
    stored_energy: float = 0.0
    discharged_energy: float = 0.0
    house_energy: float = 0.0
    house_pv_energy: float = 0.0
    house_battery_energy: float = 0.0
    house_grid_energy: float = 0.0
    pv_battery_energy: float = 0.0
    grid_import_energy: float = 0.0
    grid_export_energy: float = 0.0
    pv_energy: float = 0.0
    elapsed_hours: float = 0.0

    @property
    def islanded(self):
        return self.backup or self.transfer_island

    @property
    def voltage_ok(self):
        # Scenario thresholds, not a representation of certified relay curves.
        return all(207 <= v <= 253 for v in self.voltages)

    @property
    def voltages(self):
        return (self.voltage_l1, self.voltage_l2, self.voltage_l3)

    @property
    def grid_connected(self):
        return self.grid_available and not self.islanded and not self.protection_trip and self.voltage_ok

    @property
    def house_phases(self):
        base = self.house_load / 3 + self.heat_pump / 3
        return (base + self.cooker / 2 + self.oven + self.extra_l1,
                base + self.cooker / 2 + self.microwave + self.washer,
                base + self.dryer + self.dishwasher)

    @property
    def total_house_load(self):
        return sum(self.house_phases)

    @property
    def effective_pv(self):
        return self.pv_string_power(1) + self.pv_string_power(2)

    def pv_string_power(self, string):
        if not self.grid_connected and not self.islanded:
            return 0.0
        return ((self.pv_power / 2 + getattr(self, f"pv_string{string}")) *
                getattr(self, f"string{string}_available") * (1 - self.snow_cover / 100))

    def wallbox_power(self, box):
        if (not self.grid_connected or not getattr(self, f"wallbox{box}_connected")
            or not getattr(self, f"wallbox{box}_enabled") or getattr(self, f"wallbox{box}_stalled")):
            return 0.0
        current = getattr(self, f"wallbox{box}_current")
        return min(getattr(self, f"wallbox{box}_demand"), current * sum(self.voltages)) if current >= 6 else 0.0

    @property
    def ev_power(self):
        return (self.ev_load if self.grid_connected else 0) + self.wallbox_power(1) + self.wallbox_power(2)

    @property
    def phase_powers(self):
        if not self.grid_connected:
            return (0.0, 0.0, 0.0)
        # Three-phase inverter and EVs; appliances retain their individual phase.
        balanced = (self.ev_power - self.effective_pv + max(0, self.battery_power) / self.charge_efficiency +
                    min(0, self.battery_power) * self.discharge_efficiency) / 3
        return tuple(p + balanced for p in self.house_phases)

    @property
    def phase_currents(self):
        return tuple(p / max(v * self.power_factor, 1) for p, v in zip(self.phase_powers, self.voltages))

    @property
    def overcurrent(self):
        return any(abs(i) > self.main_fuse for i in self.phase_currents)

    @property
    def charge_inhibit(self):
        return not self.grid_connected

    @property
    def grid_guard_power(self):
        return max(self.grid_power, max(self.phase_currents) * 3 * 230)

    def advance(self, seconds: float) -> None:
        """Integrate piecewise to preserve bounds when a target is reached.

        Manual grid charging stops at the requested minimum SOC, as on the
        inverter. PV may fill the remaining capacity up to 100 percent.
        Communication failure does not stop the physical plant.
        """
        if not math.isfinite(seconds) or not 0 < seconds <= 86400:
            raise ValueError("seconds must be finite and in (0, 86400]")
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        remaining = seconds
        while remaining > 1e-9:
            step = min(remaining, 5.0)
            self._advance_step(step / 3600)
            remaining -= step

    def _advance_step(self, hours: float) -> None:
        house = self.total_house_load
        pv = self.effective_pv
        load = house + self.ev_power
        net = load - pv
        energy = self.capacity * self.soc / 100
        floor = self.capacity * (0 if self.islanded else self.min_soc) / 100
        self.battery_power = self.charge_ac_power = 0.0

        if net < 0:
            dc = min(-net * self.charge_efficiency, self.charge_limit,
                     max(0, self.capacity - energy) * 1000 / hours)
            self.battery_power = dc
            net += dc / self.charge_efficiency
        elif self.grid_charge and self.grid_connected and energy < floor:
            ac = min(self.charge_limit,
                     (floor - energy) * 1000 / hours / self.charge_efficiency)
            self.charge_ac_power = ac
            self.battery_power = ac * self.charge_efficiency
            net += ac
        elif (self.islanded or (self.grid_connected and not self.block_discharge)) and energy > floor:
            ac = min(net, self.discharge_limit,
                     (energy - floor) * 1000 / hours * self.discharge_efficiency)
            self.battery_power = -ac / self.discharge_efficiency
            net -= ac

        self.soc = min(100.0, max(0.0,
            (energy + self.battery_power * hours / 1000) / self.capacity * 100))
        self.grid_power = net if self.grid_connected else 0.0
        self.unserved_power = max(0.0, net) if not self.grid_connected else 0.0
        self.grid_charge_energy += self.charge_ac_power * hours / 1000
        self.stored_energy += max(0.0, self.battery_power) * hours / 1000
        self.discharged_energy += max(0.0, -self.battery_power) * hours / 1000
        self.house_energy += house * hours / 1000
        pv_home = min(house, pv)
        battery_home = min(max(0, house - pv_home),
                           max(0, -self.battery_power) * self.discharge_efficiency)
        self.house_pv_energy += pv_home * hours / 1000
        self.house_battery_energy += battery_home * hours / 1000
        if self.grid_connected:
            self.house_grid_energy += max(0, house - pv_home - battery_home) * hours / 1000
        if self.charge_ac_power == 0:
            self.pv_battery_energy += max(0, self.battery_power) * hours / 1000
        self.grid_import_energy += max(0.0, self.grid_power) * hours / 1000
        self.grid_export_energy += max(0.0, -self.grid_power) * hours / 1000
        self.pv_energy += pv * hours / 1000
        self.elapsed_hours += hours

    def load_scenario(self, name: str) -> None:
        """Reset every control, counter and fault before applying a preset."""
        preset = SCENARIOS[name]
        fresh = Plant()
        for field in fields(self):
            setattr(self, field.name, getattr(fresh, field.name))
        for key, value in preset.items():
            setattr(self, key, value)


SCENARIOS = {
    "winter_cloudy": {"soc": 20.0, "forecast_today": 2.0, "forecast_tomorrow": 2.0},
    "winter_sunny": {"soc": 35.0, "forecast_today": 18.0, "forecast_tomorrow": 18.0},
    "summer_surplus": {"soc": 55.0, "forecast_today": 55.0, "forecast_tomorrow": 55.0},
    "ev_charging": {"soc": 20.0, "ev_load": 22000.0, "forecast_tomorrow": 3.0},
    "almost_full": {"soc": 94.0, "forecast_today": 4.0, "forecast_tomorrow": 4.0},
    "forecast_failure": {"soc": 20.0, "forecast_available": False},
    "inverter_offline": {"soc": 20.0, "online": False},
    "rejected_commands": {"soc": 20.0, "reject_writes": True},
    "island": {"soc": 60.0, "backup": True},
    "two_ev_22kw": {"soc": 20.0, "wallbox1_connected": True, "wallbox2_connected": True,
        "wallbox1_enabled": True, "wallbox2_enabled": True,
        "wallbox1_current": 32.0, "wallbox2_current": 32.0, "block_discharge": True},
    "cooking_and_heat_pump": {"soc": 20.0, "heat_pump": 6000.0, "cooker": 7000.0,
        "oven": 3000.0, "microwave": 1400.0, "washer": 2200.0, "dryer": 2500.0,
        "dishwasher": 2000.0, "wallbox1_connected": True, "wallbox2_connected": True,
        "wallbox1_enabled": True, "wallbox2_enabled": True,
        "wallbox1_current": 32.0, "wallbox2_current": 32.0, "block_discharge": True},
    "phase_imbalance": {"soc": 20.0, "extra_l1": 15000.0, "block_discharge": True},
    "manual_transfer": {"soc": 60.0, "transfer_island": True, "backup": False},
    "grid_outage": {"soc": 60.0, "grid_available": False},
    "overvoltage": {"soc": 20.0, "voltage_l1": 265.0},
    "undervoltage": {"soc": 20.0, "voltage_l2": 190.0},
    "phase_loss": {"soc": 20.0, "voltage_l3": 0.0},
    "ev_undercurrent": {"wallbox1_connected": True, "wallbox1_enabled": True,
        "wallbox1_current": 32.0, "wallbox1_stalled": True},
    "pv_snow": {"soc": 20.0, "pv_string1": 6000.0, "pv_string2": 6000.0,
        "snow_cover": 100.0, "forecast_tomorrow": 20.0},
    "pv_string_failure": {"soc": 20.0, "pv_string1": 6000.0, "pv_string2": 6000.0,
        "string2_available": False, "forecast_tomorrow": 20.0},
    "meter_failure": {"soc": 20.0, "meter_online": False},
}
