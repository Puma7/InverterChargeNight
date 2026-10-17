"""Common push updates for actual Home Assistant entities."""
from homeassistant.helpers.entity import Entity

DOMAIN = "icn_simulator"


class LabEntity(Entity):
    _attr_should_poll = False

    def __init__(self, lab, key, label):
        self.lab = lab
        self.key = key
        self._attr_name = f"ICN Lab {label}"
        self._attr_unique_id = f"icn_lab_{key}"

    @property
    def available(self):
        # Inputs and fault switches remain usable while telemetry is offline.
        return self.lab.plant.online

    async def async_added_to_hass(self):
        self.lab.listeners.add(self.async_write_ha_state)
        self.async_on_remove(lambda: self.lab.listeners.discard(self.async_write_ha_state))
