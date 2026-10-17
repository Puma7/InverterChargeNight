"""Actual switch service paths for the virtual inverter."""
from homeassistant.components.switch import SwitchEntity
from .entity import DOMAIN, LabEntity

CONTROLS = {"grid_charge", "block_discharge"}
SWITCHES = [
    ("grid_charge", "Grid Charge"), ("block_discharge", "Block Discharge"),
    ("backup", "Backup"), ("online", "Online"),
    ("forecast_available", "Forecast Available"), ("reject_writes", "Reject Writes"),
] + [(key, label) for key, label in [
    ("grid_available", "Public Grid Available"), ("transfer_island", "Manual Transfer Island"),
    ("protection_trip", "Simulated Protection Trip"), ("meter_online", "Grid Meter Online"),
    ("string1_available", "PV String 1 Available"), ("string2_available", "PV String 2 Available"),
    ("wallbox1_connected", "EV 1 Connected"), ("wallbox2_connected", "EV 2 Connected"),
    ("wallbox1_enabled", "EV 1 Enabled"), ("wallbox2_enabled", "EV 2 Enabled"),
    ("wallbox1_stalled", "EV 1 Stalled"), ("wallbox2_stalled", "EV 2 Stalled"),
]]


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([LabSwitch(hass.data[DOMAIN], *row) for row in SWITCHES])


class LabSwitch(LabEntity, SwitchEntity):
    @property
    def available(self):
        return self.key not in CONTROLS or self.lab.plant.online

    @property
    def is_on(self):
        return bool(getattr(self.lab.plant, self.key))

    async def _set(self, enabled):
        if self.key in CONTROLS:
            if self.lab.plant.reject_writes:
                self.lab.audit(self.key, enabled, accepted=False)
                return
            self.lab.audit(self.key, enabled)
        setattr(self.lab.plant, self.key, enabled)
        self.lab.publish()

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)
