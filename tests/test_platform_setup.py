"""Tests for platform setup functions.

Every platform must produce exactly the entities it is meant to produce, in
type and in unique id, so that deleting or renaming one fails here instead of
silently shrinking the integration.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.inverter_charge_night import (
    binary_sensor,
    number,
    select,
    sensor,
    switch,
)
from custom_components.inverter_charge_night.binary_sensor import ActiveWindowBinarySensor
from custom_components.inverter_charge_night.number import (
    MinSOCOverrideNumber,
    SnowNightsNumber,
)
from custom_components.inverter_charge_night.select import OperationModeSelect
from custom_components.inverter_charge_night.sensor import (
    BestChargePowerSensor,
    CalculatedSOCSensor,
    PlannedChargePowerSensor,
    GridChargeHeadroomSensor,
)
from custom_components.inverter_charge_night.switch import (
    AutoEfficientChargeSwitch,
    InverterChargeNightSwitch,
    SkipNextSwitch,
)

COMPONENT_DIR = Path(binary_sensor.__file__).parent

# platform module -> the entity classes it sets up, with their unique id suffix
PLATFORM_ENTITIES = {
    "binary_sensor": (binary_sensor, [(ActiveWindowBinarySensor, "active")]),
    "sensor": (
        sensor,
        [
            (CalculatedSOCSensor, "calculated_soc"),
            (BestChargePowerSensor, "best_charge_power"),
            (PlannedChargePowerSensor, "planned_charge_power"),
            (GridChargeHeadroomSensor, "grid_charge_headroom"),
        ],
    ),
    "number": (
        number,
        [(MinSOCOverrideNumber, "min_soc_override"), (SnowNightsNumber, "snow_nights")],
    ),
    "switch": (
        switch,
        [
            (InverterChargeNightSwitch, "enabled"),
            (AutoEfficientChargeSwitch, "auto_efficient_charge"),
            (SkipNextSwitch, "skip_next"),
        ],
    ),
    "select": (select, [(OperationModeSelect, "operation_mode")]),
}


async def _setup(platform, mock_hass, mock_config_entry) -> list:
    """Run one platform's ``async_setup_entry`` and return the added entities."""
    mock_config_entry.runtime_data = MagicMock()
    mock_config_entry.title = "Test"
    add_entities = MagicMock()
    await platform.async_setup_entry(mock_hass, mock_config_entry, add_entities)
    add_entities.assert_called_once()
    return list(add_entities.call_args.args[0])


@pytest.mark.parametrize("platform_name", sorted(PLATFORM_ENTITIES))
@pytest.mark.asyncio
async def test_platform_sets_up_its_entities(platform_name, mock_hass, mock_config_entry):
    platform, expected = PLATFORM_ENTITIES[platform_name]

    entities = await _setup(platform, mock_hass, mock_config_entry)

    assert [type(entity) for entity in entities] == [cls for cls, _ in expected]
    assert [entity.unique_id for entity in entities] == [
        f"{mock_config_entry.entry_id}_{suffix}" for _, suffix in expected
    ]


@pytest.mark.asyncio
async def test_icons_json_covers_every_entity(mock_hass, mock_config_entry):
    """``icons.json`` is the single source for entity icons.

    Every entity's ``translation_key`` needs an entry, and no entry may name an
    entity that does not exist (both were true before: two dead sensor entries
    and three missing ones).
    """
    icons = json.loads((COMPONENT_DIR / "icons.json").read_text(encoding="utf-8"))["entity"]

    produced: dict[str, set[str]] = {}
    for platform_name, (platform, _) in PLATFORM_ENTITIES.items():
        entities = await _setup(platform, mock_hass, mock_config_entry)
        produced[platform_name] = {entity.translation_key for entity in entities}
        assert None not in produced[platform_name], platform_name

    assert {name: set(keys) for name, keys in icons.items()} == produced
    for platform_name, keys in icons.items():
        for key, icon in keys.items():
            assert icon["default"].startswith("mdi:"), (platform_name, key)
