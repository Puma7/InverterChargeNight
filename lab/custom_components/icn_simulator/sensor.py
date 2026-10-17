"""Telemetry with recorder-compatible units and state classes."""
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from .entity import DOMAIN, LabEntity

SENSORS = [
    ("soc", "Battery SoC", "%", "battery"),
    ("forecast_today", "Forecast Today", "kWh", "energy"),
    ("forecast_tomorrow", "Forecast Tomorrow", "kWh", "energy"),
    ("battery_power", "Battery Power", "W", "power"),
    ("charge_ac_power", "Charge AC Power", "W", "power"),
    ("grid_power", "Grid Power", "W", "power"),
    ("unserved_power", "Unserved Power", "W", "power"),
    ("house_energy", "House Energy", "kWh", "energy"),
    ("grid_charge_energy", "Grid Charge Energy", "kWh", "energy"),
    ("stored_energy", "Stored Energy", "kWh", "energy"),
    ("discharged_energy", "Discharged Energy", "kWh", "energy"),
    ("grid_import_energy", "Grid Import Energy", "kWh", "energy"),
    ("grid_export_energy", "Grid Export Energy", "kWh", "energy"),
    ("pv_energy", "PV Energy", "kWh", "energy"),
    ("elapsed_hours", "Simulated Hours", "h", "duration"),
    ("grid_guard_power", "Grid Guard Power", "W", "power"),
    ("total_house_load", "Total House Load", "W", "power"),
    ("ev_power", "EV Power", "W", "power"),
    ("effective_pv", "Effective PV", "W", "power"),
]


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([LabSensor(hass.data[DOMAIN], *row) for row in SENSORS])


class LabSensor(LabEntity, SensorEntity):
    def __init__(self, lab, key, label, unit, device_class):
        super().__init__(lab, key, label)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = (None if key.startswith("forecast_") else
            SensorStateClass.TOTAL_INCREASING if key.endswith("energy") else SensorStateClass.MEASUREMENT)

    @property
    def available(self):
        if self.key == "soc" and not self.lab.plant.soc_sensor_online:
            return False
        if self.key in ("grid_power", "grid_guard_power"):
            return self.lab.plant.meter_online
        if self.key.startswith("forecast_"):
            return self.lab.plant.forecast_available
        return super().available

    @property
    def native_value(self):
        return round(getattr(self.lab.plant, self.key), 5)

    @property
    def extra_state_attributes(self):
        if self.key in ("grid_power", "grid_guard_power"):
            return {"phase_currents_a": list(self.lab.plant.phase_currents),
                    "phase_voltages_v": list(self.lab.plant.voltages),
                    "phase_powers_w": list(self.lab.plant.phase_powers),
                    "guard": "max(actual W, worst phase A * 690)" if self.key == "grid_guard_power" else None}
        if self.key == "elapsed_hours":
            return {"scope": "physics_only", "scheduler_clock": "Europe/Berlin wall time",
                    "scenario": self.lab.scenario, "capacity_kwh": self.lab.plant.capacity,
                    "recent_commands": list(self.lab.commands)}
        return None
