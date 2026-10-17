"""Island status independent of REST telemetry availability."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from .entity import DOMAIN, LabEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([BackupSensor(hass.data[DOMAIN], key, label) for key, label in [
        ("backup", "Island Mode"), ("charge_inhibit", "Charge Inhibit"),
        ("overcurrent", "Overcurrent"), ("voltage_ok", "Voltage OK"),
    ]])


class BackupSensor(LabEntity, BinarySensorEntity):
    @property
    def available(self):
        return self.lab.plant.backup_sensor_online if self.key in ("backup", "charge_inhibit") else True

    @property
    def is_on(self):
        return self.lab.plant.islanded if self.key == "backup" else getattr(self.lab.plant, self.key)
