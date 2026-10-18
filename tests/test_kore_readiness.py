"""The coupled campaign must await real entity feedback before charging."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.entity_component import DATA_INSTANCES

from lab.kore_readiness import wait_for_kore_feedback


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
