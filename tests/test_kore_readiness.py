"""The coupled campaign must await real entity feedback before charging."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers.entity_component import DATA_INSTANCES

from lab.custom_components.icn_simulator.kore_readiness import wait_for_kore_feedback


@pytest.mark.asyncio
async def test_readiness_rechecks_current_entity_after_reload(mock_hass):
    entity_id = "number.kore_limit"
    current = SimpleNamespace(coordinator=SimpleNamespace(async_refresh=AsyncMock()))

    async def old_refresh():
        mock_hass.states.async_set(entity_id, "unavailable")
        current.coordinator = SimpleNamespace(async_refresh=AsyncMock(side_effect=new_refresh))

    async def new_refresh():
        mock_hass.states.async_set(entity_id, "5000")

    current.coordinator.async_refresh.side_effect = old_refresh
    component = SimpleNamespace(get_entity=lambda _: current)
    mock_hass.data[DATA_INSTANCES] = {"number": component}
    result = await wait_for_kore_feedback(mock_hass, {"limit": entity_id}, timeout=1)
    assert result == {"limit": "5000"}
    current.coordinator.async_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_feedback_times_out_instead_of_allowing_charge(mock_hass):
    mock_hass.data[DATA_INSTANCES] = {}
    with pytest.raises(RuntimeError, match="KORE feedback did not become ready"):
        await wait_for_kore_feedback(mock_hass, {"limit": "number.missing"}, timeout=.01)


@pytest.mark.asyncio
async def test_ready_feedback_does_not_wait_for_unrelated_ha_background_work(mock_hass):
    mock_hass.data[DATA_INSTANCES] = {}
    mock_hass.states.async_set("number.limit", "5000")
    unrelated_save_finished = asyncio.Event()
    mock_hass.async_block_till_done.side_effect = unrelated_save_finished.wait
    result = await wait_for_kore_feedback(mock_hass, {"limit": "number.limit"}, timeout=.05)
    assert result == {"limit": "5000"}


@pytest.mark.asyncio
async def test_shared_setup_waits_for_late_charge_limit_publication(mock_hass):
    from lab.custom_components.icn_simulator.fixtures import configure_kore
    from lab.custom_components.icn_simulator.endpoints import inverter_host

    entry = SimpleNamespace(entry_id="kore", state=SimpleNamespace(value="loaded"),
                            data={"host": inverter_host()})
    mock_hass.config_entries.async_entries.return_value = [entry]
    specs = [
        ("number.floor", "_devices:local_Battery:MinSocRel", "8"),
        ("switch.grid", "_devices:local_Battery:ManualCharge", "off"),
        ("sensor.soc", "_devices:local:battery_SoC", "25"),
        ("number.charge", "_modbus_bat_max_charge_limit", "unknown"),
        ("number.discharge", "_modbus_bat_max_discharge_limit", "5000"),
    ]
    entities = {}
    for entity_id, suffix, state in specs:
        entities[entity_id] = SimpleNamespace(entity_id=entity_id, unique_id="kore" + suffix,
                                             config_entry_id="kore", disabled_by=None)
        mock_hass.states.async_set(entity_id, state)
    rest = SimpleNamespace(async_refresh=AsyncMock())

    async def publish_limit():
        mock_hass.states.async_set("number.charge", "5000")

    late = SimpleNamespace(async_refresh=AsyncMock(side_effect=publish_limit))
    controls = {entity_id: SimpleNamespace(coordinator=late if entity_id == "number.charge" else rest)
                for entity_id, _, _ in specs}
    component = SimpleNamespace(get_entity=controls.get)
    mock_hass.data[DATA_INSTANCES] = {"number": component, "switch": component, "sensor": component}
    mock_hass.data["kostal_kore"] = {"kore": {
        "process_coordinator": rest, "modbus_coordinator": SimpleNamespace(async_refresh=AsyncMock())}}
    with patch("lab.custom_components.icn_simulator.fixtures.er.async_get",
               return_value=SimpleNamespace(entities=entities)):
        actual_entry, mapping = await configure_kore(mock_hass)
    assert actual_entry is entry
    assert mock_hass.states.get(mapping["charge_power_entity"]).state == "5000"
    late.async_refresh.assert_awaited()
