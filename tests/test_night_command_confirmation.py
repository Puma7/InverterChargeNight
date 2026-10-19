"""Regressions for acknowledged night commands and actionable diagnostics."""
from datetime import datetime
import asyncio
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
    assert c._representable_min_soc(value) == expected


def test_charge_and_hold_rounding_leave_semantic_target_and_discharge_unchanged(mock_hass):
    c = make(mock_hass, discharge_block_mode="off")
    assert c.inverter_floor_soc(32.5) == 32.5
    assert c._charging_floor_soc(32.5) == 33
    mock_hass.states.async_set("sensor.synthetic_soc", "32.8")
    assert c._charging_floor_soc(32.5) == 32
    c.entry.data = {**c.entry.data, "operation_mode": "morning_discharge"}
    c.operation_mode = "morning_discharge"
    assert c.inverter_floor_soc(32.5) == 32.5
    assert c._charging_floor_soc(32.5) == 32.5


@pytest.mark.asyncio
@pytest.mark.parametrize("soc", [20, 40])
async def test_rounding_does_not_bypass_disabled_discharge_block(mock_hass, soc):
    c = make(mock_hass, discharge_block_mode="off")
    c.initial_calculated_soc = 32.5
    mock_hass.states.async_set("sensor.synthetic_soc", str(soc))
    mock_hass.services.async_call = AsyncMock()
    await c._write_raised_min_soc_floor()
    mock_hass.services.async_call.assert_not_awaited()
    assert c.original_min_soc is None


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
    assert report["state"]["charge_control"]["min_soc_feedback_pct"] == 10
    assert report["state"]["charge_control"]["grid_charge_feedback"] == "off"
    assert "number.synthetic_floor" not in json.dumps(report)
    assert "number.synthetic_limit" not in json.dumps(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("floor,grid", [
    ("unknown", "unknown"), ("unavailable", "unavailable"),
    ("invalid", "invalid"), ("nan", "unknown"),
])
async def test_unusable_control_feedback_is_not_reported_as_zero_or_off(mock_hass, floor, grid):
    c = make(mock_hass)
    c.original_min_soc = 10
    c._pending_reset = True
    mock_hass.states.async_set("number.synthetic_floor", floor)
    mock_hass.states.async_set("switch.synthetic_grid", grid)
    before = json.dumps({"data": c.entry.data, "options": c.entry.options}, sort_keys=True)
    report = await async_get_config_entry_diagnostics(mock_hass, c.entry)
    control = report["state"]["charge_control"]
    assert control["min_soc_feedback_pct"] is None
    assert control["grid_charge_feedback"] is None
    assert control["original_min_soc_pct"] == 10
    assert control["reset_pending"] is True
    assert json.dumps({"data": c.entry.data, "options": c.entry.options}, sort_keys=True) == before
    assert "number.synthetic_floor" not in json.dumps(report)


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


@pytest.mark.asyncio
async def test_verifier_does_not_restore_an_old_plan_during_a_new_write(mock_hass):
    c = make(mock_hass)
    c._planned_setpoint_written_w = 1000

    async def accept(domain, service, data, **kwargs):
        mock_hass.states.async_set("number.synthetic_limit", str(data["value"]),
                                   {"unit_of_measurement": "W"})
        await c._verify_ac_charge_limit()

    mock_hass.services.async_call = AsyncMock(side_effect=accept)
    assert await c._set_ac_charge_limit_w(3000) is True
    assert c._planned_setpoint_written_w == 3000
    assert c._get_ac_charge_limit_w("number.synthetic_limit") == 3000
    mock_hass.services.async_call.assert_awaited_once_with(
        "number", "set_value", {"entity_id": "number.synthetic_limit", "value": 3000.0}, blocking=True
    )


@pytest.mark.asyncio
async def test_verification_rechecks_ownership_after_awaiting_pause(mock_hass):
    c = make(mock_hass)
    c._planned_setpoint_written_w = 1000
    c._grid_limit_configured = MagicMock(return_value=True)

    async def new_owner(_reason):
        c._planned_setpoint_written_w = 3000

    c._pause_grid_charge = AsyncMock(side_effect=new_owner)
    c._set_ac_charge_limit_w = AsyncMock()
    await c._verify_ac_charge_limit()
    c._pause_grid_charge.assert_awaited_once()
    c._set_ac_charge_limit_w.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_finishes_after_an_inflight_charging_floor(mock_hass):
    c = make(mock_hass)
    started, release = asyncio.Event(), asyncio.Event()
    writes = []

    async def service(domain, action, data, **kwargs):
        if domain == "number":
            writes.append(data["value"])
            if data["value"] == 33:
                started.set()
                await release.wait()
            mock_hass.states.async_set(data["entity_id"], str(data["value"]), {"step": 1})

    mock_hass.services.async_call = AsyncMock(side_effect=service)
    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now",
               return_value=datetime(2026, 1, 15, 2)):
        charge = asyncio.create_task(c._control_charge(32.5))
        await asyncio.wait_for(started.wait(), 2)
        c.is_active = False
        c._ending = True
        reset = asyncio.create_task(c._reset_settings())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(charge, reset), 2)
    assert float(mock_hass.states.get("number.synthetic_floor").state) == 10
    assert writes == [33, 10]
    assert not c._pending_reset
    assert all(call.args[:2] != ("switch", "turn_on") for call in mock_hass.services.async_call.await_args_list)


@pytest.mark.asyncio
async def test_queued_power_command_cannot_outlive_its_window(mock_hass):
    c = make(mock_hass)
    await c._ac_charge_limit_write_lock.acquire()
    mock_hass.services.async_call = AsyncMock()
    task = asyncio.create_task(c._set_ac_charge_limit_w(3000))
    await asyncio.sleep(0)
    c.is_active = False
    c._ac_charge_limit_write_lock.release()
    assert await task is False
    mock_hass.services.async_call.assert_not_awaited()
    assert c._planned_setpoint_written_w is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["night_charge", "morning_discharge"])
async def test_queued_floor_cannot_write_into_a_new_window(mock_hass, mode):
    c = make(mock_hass, operation_mode=mode)
    c._window_started_at = datetime(2026, 1, 15, 2)
    await c._min_soc_write_lock.acquire()
    mock_hass.services.async_call = AsyncMock()
    method = c._control_charge if mode == "night_charge" else c._control_discharge
    mock_hass.states.async_set("sensor.synthetic_soc", "40" if mode == "morning_discharge" else "20")
    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now",
               return_value=datetime(2026, 1, 15, 2)):
        task = asyncio.create_task(method(32.5))
        await asyncio.sleep(0)
        c._window_started_at = datetime(2026, 1, 15, 3)
        c._min_soc_write_lock.release()
        await task
    mock_hass.services.async_call.assert_not_awaited()
