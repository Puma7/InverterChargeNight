"""Realistic setup mistakes, review edits and conditional requirements."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.inverter_charge_night import config_flow as flow_module
from custom_components.inverter_charge_night.const import *
from custom_components.inverter_charge_night.communication import CommunicationState
from tests.test_config_flow import (
    ENTITIES_INPUT, TIME_SOC_INPUT, POWER_INPUT, ADVANCED_INPUT,
    _config_flow, entity_registry,
)
from tests.test_calendar_fault_campaign import rig
from tests.test_grid_limit import BACKUP, GRID


@pytest.mark.parametrize("unit", ["%", "A", "V", "kWh"])
@pytest.mark.parametrize("key", [CONF_CHARGE_POWER_ENTITY, CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY, CONF_DISCHARGE_LIMIT_ENTITY])
def test_percent_and_other_nonpower_controls_are_rejected(mock_hass, entity_registry, unit, key):
    mock_hass.states.async_set("number.wrong_control", "100", {"unit_of_measurement": unit})
    errors = flow_module._validate_user_input({key: "number.wrong_control"}, mock_hass)
    assert errors[key] == "power_control_requires_watts"


@pytest.mark.parametrize("key", [CONF_PV_FORECAST_ENTITY, CONF_PV_FORECAST_TODAY_ENTITY])
def test_planner_outputs_cannot_be_forecast_inputs(mock_hass, entity_registry, key):
    entity_registry.async_get.return_value = SimpleNamespace(platform=DOMAIN)
    assert flow_module._validate_user_input({key: "sensor.evening_outlook"}, mock_hass)[key] == "forecast_is_planner_output"


def test_kore_total_ac_power_is_rejected_as_charging_input(mock_hass, entity_registry):
    entity_registry.async_get.return_value = SimpleNamespace(platform="kostal_kore", unique_id="entry_devices:local:ac_P")
    errors = flow_module._validate_user_input({CONF_CHARGE_POWER_SENT_ENTITY: "sensor.ac_power"}, mock_hass)
    assert errors[CONF_CHARGE_POWER_SENT_ENTITY] == "requires_charge_input_power"


def test_measurements_and_controls_must_be_independent(mock_hass, entity_registry):
    errors = flow_module._validate_user_input({
        CONF_CHARGE_POWER_ENTITY: "number.charge", CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY: "number.charge",
        CONF_ABSOLUTE_MAX_CHARGE_POWER_W: 15000,
        CONF_CHARGE_POWER_SENT_ENTITY: "sensor.same", CONF_CHARGE_POWER_RECEIVED_ENTITY: "sensor.same",
    }, mock_hass)
    assert errors[CONF_CHARGE_POWER_RECEIVED_ENTITY] == "separate_power_measurements"
    assert errors[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY] == "separate_power_controls"


def test_optional_becomes_required_only_for_the_enabled_feature():
    schema = flow_module._schema_power({CONF_AUTO_EFFICIENT_CHARGE: True})
    markers = {m.schema: m for m in schema.schema}
    for key in (CONF_CHARGE_POWER_ENTITY, CONF_CHARGE_POWER_SENT_ENTITY, CONF_CHARGE_POWER_RECEIVED_ENTITY):
        assert isinstance(markers[key], vol.Required)
    assert isinstance(markers[CONF_ABSOLUTE_MAX_CHARGE_POWER_W], vol.Optional)
    assert isinstance(markers[CONF_ABSOLUTE_MAX_CHARGE_POWER_ENTITY], vol.Optional)


@pytest.mark.asyncio
async def test_review_retains_other_sections_and_revalidates_before_saving(mock_hass, entity_registry):
    flow = _config_flow(mock_hass)
    await flow.async_step_user({**ENTITIES_INPUT, CONF_BATTERY_CAPACITY: 35.8})
    await flow.async_step_time_soc(TIME_SOC_INPUT)
    await flow.async_step_power(POWER_INPUT)
    result = await flow.async_step_advanced(ADVANCED_INPUT)
    assert result["type"] == "menu" and result["step_id"] == "review"
    assert result["description_placeholders"]["usable"] == "32.936"
    assert result["description_placeholders"]["timezone"] == "Europe/Berlin"
    mock_hass.config_entries.async_update_entry.assert_not_called()
    await flow.async_step_power()
    result = await flow.async_step_power({**POWER_INPUT, CONF_MAX_CHARGE_POWER_W: 20000})
    assert result["step_id"] == "review"
    assert flow._data[CONF_BATTERY_CAPACITY] == 35.8
    assert flow._data[CONF_MAX_CHARGE_POWER_W] == 20000
    # Sensor registration changes while the review menu is open.
    entity_registry.async_get.return_value = SimpleNamespace(platform=DOMAIN)
    result = await flow.async_step_finish()
    assert result["step_id"] == "user"
    assert result["errors"][CONF_PV_FORECAST_ENTITY] == "forecast_is_planner_output"


@pytest.mark.parametrize("stamp", [None, "bad", "2026-01-01T00:00:00", 42])
def test_invalid_last_success_timestamp_is_not_restored(stamp):
    assert CommunicationState({"last_success_at": stamp}).last_success_at is None


def test_last_success_survives_restart_during_outage():
    original = CommunicationState()
    original.observe([], datetime(2026, 1, 15, tzinfo=UTC))
    original.observe(["rest"], datetime(2026, 1, 16, tzinfo=UTC))
    assert CommunicationState(original.snapshot()).last_success_at == "2026-01-15T00:00:00+00:00"


def test_custom_backup_states_do_not_report_healthy_grid_as_a_fault(rig):
    hass, co, _ = rig
    co.config[CONF_BACKUP_MODE_ENTITY] = BACKUP
    co.config[CONF_BACKUP_MODE_STATES] = "17, ESB"
    hass.states.async_set(BACKUP, "6")
    assert co._is_backup_active() is False
    assert "island_contact" not in co._communication_faults()
    hass.states.async_set(BACKUP, "unavailable")
    assert "island_contact" in co._communication_faults()


def test_unchanged_outage_samples_are_persisted_without_duplicate_notices(rig):
    hass, co, _ = rig
    co.enable_communication_monitoring()
    hass.states.async_set(GRID, "unavailable")
    with patch("homeassistant.components.persistent_notification.async_create") as notify, patch.object(co, "_persist_state") as persist:
        co._update_communication_health()
        before = notify.call_count
        co._update_communication_health()
        assert notify.call_count == before
        assert persist.call_count == 2
        assert co.communication_diagnostics()["failed_samples"] == 2


def test_replacing_listener_cancels_both_old_subscriptions(rig):
    _, co, _ = rig
    old, timer = MagicMock(), MagicMock()
    with patch("custom_components.inverter_charge_night.coordinator.async_track_state_change_event", return_value=old), patch("custom_components.inverter_charge_night.coordinator.async_track_time_interval", return_value=timer):
        co.setup_communication_listener()
        co.config[CONF_BATTERY_SOC_ENTITY] = "sensor.replacement_battery"
        co.setup_communication_listener()
        old.assert_called_once()
        timer.assert_called_once()
        co._unsubscribe_communication()
        assert old.call_count == 2 and timer.call_count == 2
