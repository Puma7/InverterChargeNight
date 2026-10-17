"""Writable inverter controls and simulation inputs."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.exceptions import HomeAssistantError
from .entity import DOMAIN, LabEntity

# key, label, min, max, step, unit; inverter controls also honour write faults.
CONTROLS = {"min_soc", "charge_limit", "discharge_limit"}
NUMBERS = [
    ("min_soc", "Min SoC", 0, 100, 0.1, "%"),
    ("charge_limit", "Charge Limit", 0, 20000, 100, "W"),
    ("discharge_limit", "Discharge Limit", 0, 20000, 100, "W"),
    ("soc", "Set SoC", 0, 100, 0.1, "%"),
    ("house_load", "House Load", 0, 20000, 100, "W"),
    ("ev_load", "EV Load", 0, 22000, 100, "W"),
    ("pv_power", "PV Power", 0, 25000, 100, "W"),
    ("forecast_today", "Set Forecast Today", 0, 150, 0.1, "kWh"),
    ("forecast_tomorrow", "Set Forecast Tomorrow", 0, 150, 0.1, "kWh"),
    ("speed", "Physics Speed", 0, 3600, 1, "x"),
] + [(key, label, 0, maximum, 100, "W") for key, label, maximum in [
    ("heat_pump", "Heat Pump", 12000), ("cooker", "Cooker", 11000),
    ("oven", "Oven", 4000), ("microwave", "Microwave", 2000),
    ("washer", "Washer", 3000), ("dryer", "Dryer", 4000),
    ("dishwasher", "Dishwasher", 3000), ("extra_l1", "Extra L1", 20000),
    ("pv_string1", "PV String 1", 12500), ("pv_string2", "PV String 2", 12500),
    ("wallbox1_demand", "EV 1 Demand", 22000), ("wallbox2_demand", "EV 2 Demand", 22000),
]] + [(f"voltage_l{i}", f"Voltage L{i}", 0, 300, 1, "V") for i in range(1, 4)] + [
    ("snow_cover", "Snow Cover", 0, 100, 1, "%"),
    ("wallbox1_current", "EV 1 Current Limit", 0, 32, 1, "A"),
    ("wallbox2_current", "EV 2 Current Limit", 0, 32, 1, "A"),
]


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([LabNumber(hass.data[DOMAIN], *row) for row in NUMBERS])


class LabNumber(LabEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, lab, key, label, minimum, maximum, step, unit):
        super().__init__(lab, key, label)
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit

    @property
    def available(self):
        return self.key not in CONTROLS or self.lab.plant.online

    @property
    def native_value(self):
        return round(getattr(self.lab.plant, self.key), 3)

    async def async_set_native_value(self, value):
        if self.key in CONTROLS:
            if self.lab.plant.reject_writes or (self.key == "charge_limit" and self.lab.plant.reject_charge_limit):
                self.lab.audit(self.key, value, accepted=False)
                return  # Firmware can acknowledge a command but retain the old value.
            if not self.lab.plant.online:
                raise HomeAssistantError("Simulated inverter is offline")
            self.lab.audit(self.key, value)
        setattr(self.lab.plant, self.key, float(value))
        self.lab.publish()
