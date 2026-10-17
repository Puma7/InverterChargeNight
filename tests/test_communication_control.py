"""Positive stop-path checks and user-visible outage/recovery reporting."""
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from tests.test_calendar_fault_campaign import rig
from tests.test_grid_limit import AC_LIMIT, BATTERY, GRID, GRID_IMPORT, BACKUP, MIN_SOC
from custom_components.inverter_charge_night.const import CONF_BACKUP_MODE_ENTITY, CONF_FORCE_DISCHARGE_SWITCH, CONF_DISCHARGE_LIMIT_ENTITY


@pytest.mark.asyncio
@pytest.mark.parametrize("entity", [BATTERY, GRID, AC_LIMIT, MIN_SOC, GRID_IMPORT, BACKUP])
async def test_missing_channel_pauses_through_surviving_paths_and_reports_recovery(rig, entity):
    hass, co, _ = rig
    co.enable_communication_monitoring()
    co.is_active = True
    co.data = {"is_active": True, "kept_target": 70}
    co._async_calculate_and_control = AsyncMock(return_value={"is_active": True})
    hass.states.async_set(GRID, "on")
    if entity == BACKUP:
        co.config[CONF_BACKUP_MODE_ENTITY] = BACKUP
        hass.states.async_set(BACKUP, "off")
    state = hass.states.get(entity)
    original = state.state
    state.state = "unavailable"
    seen = []

    async def device(domain, service, data, **kwargs):
        seen.append((domain, service, data))
        if data["entity_id"] == entity:
            raise RuntimeError("simulated link failure")
        target = hass.states.get(data["entity_id"])
        target.state = str(data["value"]) if domain == "number" else "off"

    hass.services.async_call.side_effect = device
    with patch("homeassistant.components.persistent_notification.async_create") as notify:
        result = await co._async_update_data()
        assert result["kept_target"] == 70
        co._async_calculate_and_control.assert_not_awaited()
        assert co.communication_diagnostics()["incidents"] == 1
        assert co.communication_diagnostics()["state"] == "interrupted"
        # Each surviving path actually gets a stop command; none activates charging.
        if entity != AC_LIMIT:
            assert any(d == "number" and data.get("entity_id") == AC_LIMIT and data["value"] == 0
                       for d, _, data in seen)
        assert any(d == "switch" and action == "turn_off" for d, action, _ in seen)
        assert not any(action == "turn_on" for _, action, _ in seen)
        if entity == GRID:
            assert "nicht bestätigt" in notify.call_args.args[1]
        else:
            assert "meldet Netzladen aus" in notify.call_args.args[1]
        before = notify.call_count
        await co._async_update_data()
        assert notify.call_count == before
        state.state = original
        await co._async_update_data()
        assert co.communication_diagnostics()["state"] == "healthy"
        assert "wiederhergestellt" in notify.call_args.args[1]
        co._async_calculate_and_control.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_successful_limit_write_is_invented_when_both_paths_are_lost(rig):
    hass, co, _ = rig
    co.enable_communication_monitoring()
    co.is_active = True
    co.data = {}
    co._planned_setpoint_written_w = 5000
    hass.states.get(AC_LIMIT).state = "unavailable"
    hass.states.get(GRID).state = "unavailable"
    hass.services.async_call.side_effect = RuntimeError("device unreachable")
    with patch("homeassistant.components.persistent_notification.async_create") as notify:
        await co._async_update_data()
    assert co._planned_setpoint_written_w == 5000
    assert co.communication_diagnostics()["stop_confirmed"] is False
    assert "nicht bestätigt" in notify.call_args.args[1]


@pytest.mark.asyncio
async def test_pending_reset_remains_visible_until_it_succeeds(rig):
    _, co, _ = rig
    co.enable_communication_monitoring()
    co._async_calculate_and_control = AsyncMock(return_value={})
    co._pending_reset = True
    with patch("homeassistant.components.persistent_notification.async_create") as notify:
        await co._async_update_data()
        assert co.communication_diagnostics()["faults"] == ["write_unconfirmed"]
        co._pending_reset = False
        await co._async_update_data()
        assert co.communication_diagnostics()["state"] == "healthy"
        assert "wiederhergestellt" in notify.call_args.args[1]


@pytest.mark.asyncio
async def test_ignored_stop_does_not_restore_a_higher_power_limit(rig):
    hass, co, _ = rig
    co.original_min_soc = 8
    co._original_ac_charge_power = 6000
    co._planned_setpoint_written_w = 0
    hass.states.get(AC_LIMIT).state = "0"
    hass.states.get(GRID).state = "on"
    # Acknowledged service, unchanged actual state: no successful stop feedback.
    hass.services.async_call = AsyncMock()
    assert await co._reset_settings() is False
    assert co._original_ac_charge_power == 6000
    assert co._pending_reset is True
    calls = hass.services.async_call.await_args_list
    assert any(c.args[:2] == ("switch", "turn_off") for c in calls)
    assert not any(c.args[0] == "number" and c.args[2].get("entity_id") == AC_LIMIT
                   and c.args[2].get("value", 0) > 0 for c in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "unknown", "unavailable", "nan", "inf", "bad-value"])
async def test_loss_and_recovery_wake_the_coordinator_without_waiting_for_poll(rig, bad):
    hass, co, _ = rig
    with patch("custom_components.inverter_charge_night.coordinator.async_track_state_change_event") as track:
        co.setup_communication_listener()
    callback = track.call_args.args[2]
    good = SimpleNamespace(entity_id=BATTERY, state="20")
    lost = SimpleNamespace(entity_id=BATTERY, state=bad) if bad is not None else None
    await callback(SimpleNamespace(data={"old_state": good, "new_state": lost}))
    co.async_request_refresh.assert_awaited_once()
    await callback(SimpleNamespace(data={"old_state": lost, "new_state": good}))
    assert co.async_request_refresh.await_count == 2
    await callback(SimpleNamespace(data={"old_state": good, "new_state": good}))
    assert co.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_communication_diagnosis_action_reads_local_state_without_commands(rig):
    from custom_components.inverter_charge_night import services
    hass, co, _ = rig
    co.enable_communication_monitoring()
    with patch.object(services, "_coordinator_for", return_value=co):
        services.async_setup_services(hass)
        handler = next(c.args[2] for c in hass.services.async_register.call_args_list
                       if c.args[1] == "communication_diagnostics")
        result = await handler(SimpleNamespace(hass=hass))
    assert result["incidents"] == 0
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_silent_meter_staleness_is_checked_independently_of_the_planner_poll(rig):
    from datetime import timedelta
    hass, co, clock = rig
    co.enable_communication_monitoring()
    with patch("custom_components.inverter_charge_night.coordinator.async_track_state_change_event"), patch(
        "custom_components.inverter_charge_night.coordinator.async_track_time_interval",
    ) as timer:
        co.setup_communication_listener()
    callback = timer.call_args.args[1]
    await callback(clock.now)
    co.async_request_refresh.assert_not_awaited()
    hass.states.get(GRID_IMPORT).last_reported = clock.now - timedelta(seconds=301)
    await callback(clock.now)
    co.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("force_unreachable", [False, True])
async def test_telemetry_loss_also_stops_forced_export_without_disabling_house_supply(rig, force_unreachable):
    hass, co, _ = rig
    force = "switch.force_export"
    house_limit = "number.house_discharge_limit"
    co.config[CONF_FORCE_DISCHARGE_SWITCH] = force
    co.config[CONF_DISCHARGE_LIMIT_ENTITY] = house_limit
    hass.states.async_set(house_limit, "6000", {"unit_of_measurement": "W"})
    co.operation_mode = "morning_discharge"
    co.enable_communication_monitoring()
    co.is_active, co.data = True, {}
    hass.states.async_set(force, "unavailable" if force_unreachable else "on")
    hass.states.get(BATTERY).state = "unavailable"
    co._async_calculate_and_control = AsyncMock()

    async def device(domain, service, data, **kwargs):
        if data["entity_id"] == force and force_unreachable:
            raise TimeoutError("export control unreachable")
        state = hass.states.get(data["entity_id"])
        state.state = "off" if domain == "switch" else str(data["value"])

    hass.services.async_call.side_effect = device
    with patch("homeassistant.components.persistent_notification.async_create"):
        await co._async_update_data()
    calls = hass.services.async_call.await_args_list
    assert any(c.args[:2] == ("switch", "turn_off") and c.args[2]["entity_id"] == force for c in calls)
    assert not any(c.args[1] == "turn_on" for c in calls)
    assert not any(c.args[2]["entity_id"] == house_limit for c in calls)
    assert hass.states.get(house_limit).state == "6000"
    assert co.communication_diagnostics()["stop_confirmed"] is (not force_unreachable)
    co._async_calculate_and_control.assert_not_awaited()
