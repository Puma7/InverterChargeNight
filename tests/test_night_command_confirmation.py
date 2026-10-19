"""Regressions for acknowledged night commands and actionable diagnostics."""
from datetime import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.inverter_charge_night.coordinator import InverterChargeNightCoordinator
from custom_components.inverter_charge_night.diagnostics import async_get_config_entry_diagnostics


def make(hass, **overrides):
    entry = MagicMock()
    entry.entry_id = "synthetic"
    entry.title = "Synthetic night"
    entry.data = {
        "min_soc_entity": "number.synthetic_floor",
        "battery_soc_entity": "sensor.synthetic_soc",
        "grid_charge_switch": "switch.synthetic_grid",
        "charge_power_entity": "number.synthetic_limit",
        "charge_power_sent_entity": "sensor.synthetic_ac",
        "charge_power_received_entity": "sensor.synthetic_dc",
        "start_time": "00:00", "end_time": "05:00",
        "user_min_soc": 10.0, "user_max_soc": 100.0,
        "command_delay": 0, "auto_efficient_charge": True,
        **overrides,
    }
    entry.options = {}
    hass.states.async_set("number.synthetic_floor", "10", {"min": 0, "max": 100, "step": 1})
    hass.states.async_set("sensor.synthetic_soc", "20")
    hass.states.async_set("switch.synthetic_grid", "off")
    hass.states.async_set("number.synthetic_limit", "5000", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.synthetic_ac", "3", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.synthetic_dc", "2800", {"unit_of_measurement": "W"})
    hass.states.async_set("sun.sun", "below_horizon")
    c = InverterChargeNightCoordinator(hass, entry)
    c.is_active = True
    entry.runtime_data = c
    return c


@pytest.mark.parametrize(("value", "attrs", "maximum", "expected"), [
    (32.5, {"step": 1, "min": 0, "max": 100}, 100, 33),
    (32.5, {"step": 0.5, "min": 0, "max": 100}, 100, 32.5),
    (32.5, {"step": 1, "min": 0, "max": 100}, 32.5, None),
    (32.5, {"step": 1, "min": 0, "max": 32}, 100, None),
    (32.5, {}, 100, 32.5),
    (30.00000000001, {"step": 1, "min": 0, "max": 100}, 100, 30),
    (20, {"step": 1, "min": 30, "max": 100}, 100, 30),
    (32.5, {"step": 2, "min": 5, "max": 100}, 100, 33),
])
def test_advertised_floor_step_preserves_bounds(mock_hass, value, attrs, maximum, expected):
    c = make(mock_hass, user_max_soc=maximum)
    mock_hass.states.async_set("number.synthetic_floor", "10", attrs)
    assert c.inverter_floor_soc(value) == expected


@pytest.mark.asyncio
async def test_rejected_floor_does_not_enable_charging(mock_hass):
    c = make(mock_hass)

    async def reject(domain, service, data, **kwargs):
        if domain == "number":
            # HA's non-blocking call would return before this failure.
            if kwargs.get("blocking"):
                raise HomeAssistantError("Synthetic device refusal")

    mock_hass.services.async_call = AsyncMock(side_effect=reject)
    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now",
               return_value=datetime(2026, 1, 15, 2)):
        await c._control_charge(32.5)
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": "number.synthetic_floor", "value": 33}, blocking=True
    )
    assert c._last_soc_set is None
    assert c.original_min_soc == 10


@pytest.mark.asyncio
async def test_integer_floor_reaches_device_before_grid_activation(mock_hass):
    c = make(mock_hass)
    order = []

    async def accept(domain, service, data, **kwargs):
        assert kwargs.get("blocking") is True
        order.append((domain, service, data))
        if domain == "number":
            assert float(data["value"]).is_integer()
            mock_hass.states.async_set(data["entity_id"], str(data["value"]), {"step": 1})
        elif service == "turn_on":
            assert float(mock_hass.states.get("number.synthetic_floor").state) == 33
            mock_hass.states.async_set(data["entity_id"], "on")

    mock_hass.services.async_call = AsyncMock(side_effect=accept)
    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now",
               return_value=datetime(2026, 1, 15, 2)):
        await c._control_charge(32.5)
    assert [(x[0], x[1]) for x in order] == [("number", "set_value"), ("switch", "turn_on")]
    assert c._last_soc_set == 33
    assert mock_hass.states.get("switch.synthetic_grid").state == "on"


@pytest.mark.asyncio
async def test_wait_reason_and_redacted_live_control_snapshot(mock_hass):
    c = make(mock_hass)
    c.planned_charge_power_w = 2000
    c.planned_power_is_applied = False
    await c._handle_auto_charge()
    assert c.auto_test_state == "waiting"
    assert c.auto_test_attributes()["waiting_reason"] == "grid_charge_not_confirmed"
    report = await async_get_config_entry_diagnostics(mock_hass, c.entry)
    assert report["state"]["charge_control"]["charge_limit_feedback_w"] == 5000
    assert report["state"]["charge_control"]["ac_charge_power_w"] == 3000
    assert report["state"]["charge_control"]["dc_charge_power_w"] == 2800
    assert report["state"]["charge_control"]["planned_power_is_applied"] is False
    assert "number.synthetic_floor" not in json.dumps(report)
    assert "number.synthetic_limit" not in json.dumps(report)


@pytest.mark.asyncio
async def test_unconfirmed_limit_never_starts_trial(mock_hass):
    c = make(mock_hass)
    mock_hass.services.async_call = AsyncMock(side_effect=HomeAssistantError("Unconfirmed readback"))
    await c._start_auto_test(3000)
    assert c._auto_test_active is False
    assert c.auto_test_attributes()["waiting_reason"] == "charge_limit_write_failed"
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": "number.synthetic_limit", "value": 3000.0}, blocking=True
    )
