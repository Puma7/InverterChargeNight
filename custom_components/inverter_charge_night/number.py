"""Number platform for Inverter Charge Night."""
from __future__ import annotations

import logging

from homeassistant.const import EntityCategory

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import InverterChargeNightConfigEntry, InverterChargeNightCoordinator
from .entity import InverterChargeNightEntity
from .const import (
    CONF_USER_MIN_SOC,
    CONF_USER_MAX_SOC,
)

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InverterChargeNightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    coordinator = entry.runtime_data
    async_add_entities([MinSOCOverrideNumber(coordinator, entry), SnowNightsNumber(coordinator, entry)])


class MinSOCOverrideNumber(InverterChargeNightEntity, NumberEntity):
    """Number entity for manual SOC override."""

    _attr_translation_key = "min_soc_override"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, entry, "min_soc_override")

    @property
    def native_value(self) -> float | None:
        """Return the active override, else the SOC the planner calculated.

        The coordinator owns the override (it survives a restart through the
        persisted runtime state), so the entity keeps no copy of its own: after
        a restart inside a window the UI would otherwise show the planner's
        number while the inverter is driven to the restored override.
        """
        if self.coordinator.override_soc is not None:
            return float(self.coordinator.override_soc)
        value = self.coordinator.data.get("calculated_soc")
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the override value."""
        user_min_soc = float(self.coordinator.config.get(CONF_USER_MIN_SOC, 8.0))
        user_max_soc = float(self.coordinator.config.get(CONF_USER_MAX_SOC, 100.0))

        rounded_value = float(int(round(value)))
        clamped_value = max(user_min_soc, min(rounded_value, user_max_soc))

        if clamped_value != value:
            _LOGGER.warning(
                "Override value %.1f%% clamped to %.1f%% to respect user bounds [%.1f%%, %.1f%%]",
                value,
                clamped_value,
                user_min_soc,
                user_max_soc,
            )

        value = clamped_value
        self.coordinator.override_soc = value
        self.coordinator.target_reached = False
        # Lowering the target by hand has to free the discharge-block floor too,
        # or the battery stays blocked at the old level until the window ends.
        self.coordinator.release_window_floor_to(value)

        if self.coordinator.minimum_calculated_soc is None or value < self.coordinator.minimum_calculated_soc:
            self.coordinator.minimum_calculated_soc = value

        self.coordinator._persist_state()
        self.async_write_ha_state()

        if self.coordinator.is_active and self.coordinator.is_enabled:
            # The refresh applies the override through the mode-correct control
            # path (charge or discharge); calling the charge path here directly
            # would switch grid charging on even in discharge mode.
            await self.coordinator.async_request_refresh()


class SnowNightsNumber(InverterChargeNightEntity, NumberEntity):
    """Number of upcoming nights that charge to the user maximum (snow on the modules).

    Snow mode overrides the forecast and the manual override; the coordinator
    counts the value down at every window end.
    """

    _attr_translation_key = "snow_nights"
    _attr_native_min_value = 0
    _attr_native_max_value = 14
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: InverterChargeNightCoordinator, entry: ConfigEntry) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, entry, "snow_nights")

    @property
    def native_value(self) -> int:
        """Return the number of snow nights left."""
        return self.coordinator.snow_nights

    async def async_set_native_value(self, value: float) -> None:
        """Set the number of snow nights and apply it to a running window at once."""
        nights = max(0, int(value))
        _LOGGER.info("Snow nights set to %d", nights)
        self.coordinator.snow_nights = nights
        if nights > 0:
            # The target moved up: let the update loop charge again
            self.coordinator.target_reached = False
        else:
            # Snow mode charged to the maximum and the floor followed. Calling
            # it off has to free that floor, or the battery stays blocked near
            # full until the window ends.
            self.coordinator.release_window_floor_to(self.coordinator.current_target_soc())
        self.coordinator._persist_state()
        self.async_write_ha_state()

        if self.coordinator.is_active and self.coordinator.is_enabled:
            # The refresh applies the new target through the mode-correct control path
            await self.coordinator.async_request_refresh()
