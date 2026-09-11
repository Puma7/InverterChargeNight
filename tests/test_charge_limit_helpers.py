"""Tests for charge limit helper methods."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.const import (
    CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY,
    CONF_ABSOLUTE_MAX_CHARGE_POWER_W,
    CONF_CHARGE_POWER_ENTITY,
)


def _make_coordinator(hass, data):
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {}
    return InverterChargeNightCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_set_ac_charge_limit_unsupported_domain(mock_hass, caplog):
    coordinator = _make_coordinator(
        mock_hass, {CONF_CHARGE_POWER_ENTITY: "sensor.power"}
    )
    mock_hass.services.async_call = AsyncMock()

    await coordinator._set_ac_charge_limit_w(5000)

    assert "sensor.power has unsupported domain sensor" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_ac_charge_limit_kw_unit(mock_hass):
    mock_hass.states.async_set("number.charge_limit", "10", {"unit_of_measurement": "kW"})
    mock_hass.services.async_call = AsyncMock()

    coordinator = _make_coordinator(
        mock_hass, {CONF_CHARGE_POWER_ENTITY: "number.charge_limit"}
    )

    await coordinator._set_ac_charge_limit_w(5000)

    args, kwargs = mock_hass.services.async_call.call_args
    data = kwargs.get("service_data") if kwargs else None
    if data is None and len(args) >= 3:
        data = args[2]
    assert data["value"] == 5.0


@pytest.mark.asyncio
async def test_apply_absolute_charge_power_limit_invalid_domain(mock_hass, caplog):
    coordinator = _make_coordinator(
        mock_hass,
        {
            CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "sensor.abs_max",
            CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 5000,
        },
    )
    mock_hass.services.async_call = AsyncMock()

    await coordinator._apply_absolute_charge_power_limit()

    assert "sensor.abs_max has unsupported domain sensor" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()
