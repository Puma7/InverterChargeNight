"""Setup journeys: navigation, interruption, feature prerequisites and safe apply."""
import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries

from custom_components.inverter_charge_night import config_flow as cf
from custom_components.inverter_charge_night.const import *
from custom_components.inverter_charge_night.setup_drafts import SetupDrafts, get_drafts
from tests.test_config_flow import ENTITIES_INPUT, _config_flow, _defaults, entity_registry

BASIC = {key: value for key, value in ENTITIES_INPUT.items() if key in cf._GUIDED_GROUPS["basics"]}
SCHEDULE = {CONF_START_TIME: "23:01:00", CONF_END_TIME: "04:58:00",
            CONF_USER_MIN_SOC: 8, CONF_USER_MAX_SOC: 95}


@pytest.fixture
def guide(mock_hass, entity_registry, draft_store):
    return _config_flow(mock_hass)


@pytest.mark.asyncio
async def test_basic_install_needs_only_two_short_forms_and_explicit_apply(guide, mock_hass):
    await guide.async_step_user()
    assert (await guide.async_step_basics({**BASIC, CONF_BATTERY_CAPACITY: 35.8}))["step_id"] == "setup"
    assert (await guide.async_step_schedule(SCHEDULE))["step_id"] == "setup"
    mock_hass.services.async_call.assert_not_awaited()
    mock_hass.config_entries.async_update_entry.assert_not_called()
    review = await guide.async_step_summary()
    assert review["description_placeholders"]["usable"] == "32.936"
    assert review["menu_options"] == ["finish", "setup"]
    result = await guide.async_step_finish()
    assert result["type"] == "create_entry"
    assert result["data"][CONF_START_TIME] == "23:01:00"
    assert result["data"][CONF_DEFAULT_MIN_SOC] == 8
    assert result["data"][CONF_AUTO_EFFICIENT_CHARGE] is False
    assert not await get_drafts(mock_hass).list("new")


@pytest.mark.asyncio
async def test_revisit_first_page_keeps_schedule_and_no_long_optional_page(guide):
    await guide.async_step_user()
    await guide.async_step_basics(BASIC)
    await guide.async_step_schedule(SCHEDULE)
    result = await guide.async_step_basics()
    assert set(result["data_schema"].schema) == set(BASIC)
    await guide.async_step_basics({**BASIC, CONF_BATTERY_CAPACITY: 35.8})
    assert guide._data[CONF_END_TIME] == "04:58:00"
    assert guide._data[CONF_BATTERY_CAPACITY] == 35.8
    for step in (guide.async_step_features, guide.async_step_planning_features, guide.async_step_advanced_features):
        menu = await step()
        assert "setup" in menu["menu_options"]
        assert len(menu["menu_options"]) <= 7


@pytest.mark.asyncio
async def test_close_and_resume_after_restart_keeps_only_confirmed_values(guide, mock_hass):
    await guide.async_step_user()
    await guide.async_step_basics({**BASIC, CONF_BATTERY_CAPACITY: 35.8})
    await guide.async_step_schedule(SCHEDULE)
    guide._data[CONF_RUNTIME_STATE] = {"private_runtime": "must not enter draft"}
    assert (await guide.async_step_save_draft())["reason"] == "draft_saved"
    # Recreate both the flow and store object, as after a full HA restart.
    mock_hass.data.pop(f"{DOMAIN}_setup_drafts")
    resumed = _config_flow(mock_hass)
    assert "resume_draft" in (await resumed.async_step_user())["menu_options"]
    picker = await resumed.async_step_resume_draft()
    choice = next(iter(picker["data_schema"].schema.values())).config["options"][0]["value"]
    assert (await resumed.async_step_resume_draft({"draft": choice}))["step_id"] == "setup"
    assert resumed._data[CONF_BATTERY_CAPACITY] == 35.8
    assert resumed._data[CONF_END_TIME] == "04:58:00"
    assert CONF_RUNTIME_STATE not in resumed._data
    assert (await resumed.async_step_resume_draft({"draft": "deleted"}))["errors"] == {"draft": "draft_not_found"}


@pytest.mark.asyncio
async def test_missing_basics_and_bad_time_cannot_activate(guide, mock_hass):
    await guide.async_step_user()
    result = await guide.async_step_finish()
    assert result["step_id"] == "basics"
    assert result["errors"][CONF_MIN_SOC_ENTITY] == "required_value"
    original = deepcopy(guide._data)
    result = await guide.async_step_schedule({**SCHEDULE, CONF_END_TIME: "23:01:00"})
    assert result["errors"][CONF_END_TIME] == "start_end_time_must_differ"
    assert guide._data == original
    result = await guide.async_step_basics({key: value for key, value in BASIC.items() if key != CONF_GRID_CHARGE_SWITCH})
    assert result["errors"][CONF_GRID_CHARGE_SWITCH] == "required_value"
    assert guide._data == original
    mock_hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_efficiency_is_enabled_only_with_independent_measurements(guide):
    await guide.async_step_user()
    result = await guide.async_step_efficiency()
    assert all(isinstance(marker, vol.Required) for marker in result["data_schema"].schema)
    bad = {CONF_CHARGE_POWER_ENTITY: "number.power", CONF_CHARGE_POWER_SENT_ENTITY: "sensor.same",
           CONF_CHARGE_POWER_RECEIVED_ENTITY: "sensor.same", CONF_MIN_CHARGE_POWER_W: 1000,
           CONF_MAX_CHARGE_POWER_W: 15000}
    result = await guide.async_step_efficiency(bad)
    assert result["errors"][CONF_CHARGE_POWER_RECEIVED_ENTITY] == "separate_power_measurements"
    assert guide._data[CONF_AUTO_EFFICIENT_CHARGE] is False
    assert (await guide.async_step_efficiency({**bad, CONF_CHARGE_POWER_RECEIVED_ENTITY: "sensor.dc"}))["step_id"] == "setup"
    assert guide._data[CONF_AUTO_EFFICIENT_CHARGE] is True
    assert (await guide.async_step_features())["description_placeholders"]["efficiency"] == "✓"
    await guide.async_step_efficiency_off()
    assert guide._data[CONF_AUTO_EFFICIENT_CHARGE] is False


@pytest.mark.asyncio
async def test_grid_budget_requires_sensor_control_and_explicit_budget(guide):
    await guide.async_step_user()
    schema = (await guide.async_step_grid())["data_schema"]
    assert all(isinstance(marker, vol.Required) for marker in schema.schema)
    assert CONF_GRID_MAX_CONTINUOUS_W not in _defaults({"data_schema": schema})
    values = {CONF_GRID_IMPORT_ENTITY: "sensor.grid", CONF_GRID_MAX_CONTINUOUS_W: 40000,
              CONF_GRID_HEADROOM_W: 500, CONF_CHARGE_POWER_ENTITY: "number.power"}
    assert (await guide.async_step_grid(values))["step_id"] == "setup"
    assert guide._data[CONF_GRID_MAX_CONTINUOUS_W] == 40000
    await guide.async_step_grid_off()
    assert CONF_GRID_IMPORT_ENTITY not in guide._data
    assert guide._data[CONF_CHARGE_POWER_ENTITY] == "number.power"
    # A prior fuse calculation is displayed without changing it merely by viewing.
    guide._data.pop(CONF_GRID_MAX_CONTINUOUS_W)
    guide._data.update({CONF_MAIN_FUSE_A: 63, CONF_GRID_PHASES: 3,
                       CONF_GRID_VOLTAGE_V: 230, CONF_GRID_CONTINUOUS_PCT: 80})
    assert _defaults(await guide.async_step_grid())[CONF_GRID_MAX_CONTINUOUS_W] == 34776


@pytest.mark.asyncio
async def test_shortfall_feature_and_optional_section_edits_remain_separate(guide):
    await guide.async_step_user()
    await guide.async_step_basics(BASIC)
    await guide.async_step_schedule(SCHEDULE)
    await guide.async_step_season({CONF_ACTIVE_START_DATE: "2026-10-01",
        CONF_ACTIVE_END_DATE: "2027-03-31", CONF_ACTIVE_RANGE_YEARLY: True})
    await guide.async_step_backup({CONF_BACKUP_MODE_ENTITY: "binary_sensor.island"})
    await guide.async_step_pv_shortfall({CONF_PV_FORECAST_TODAY_ENTITY: "sensor.forecast_today",
                                       CONF_PV_ENERGY_TODAY_ENTITY: "sensor.pv_daily"})
    assert guide._data[CONF_AUTO_PV_SHORTFALL] is True
    await guide.async_step_pv_shortfall_off()
    await guide.async_step_backup({})  # clear only this page's optional input
    assert guide._data[CONF_AUTO_PV_SHORTFALL] is False
    assert CONF_BACKUP_MODE_ENTITY not in guide._data
    assert guide._data[CONF_ACTIVE_START_DATE] == "2026-10-01"
    assert guide._data[CONF_PV_ENERGY_TODAY_ENTITY] == "sensor.pv_daily"


@pytest.mark.parametrize("group", list(cf._GUIDED_GROUPS))
@pytest.mark.asyncio
async def test_every_optional_page_has_exact_labels_and_descriptions(guide, group):
    import json
    from pathlib import Path
    await guide.async_step_user()
    result = await getattr(guide, "async_step_" + group)()
    keys = {marker.schema for marker in result["data_schema"].schema}
    assert len(keys) <= 8
    for language in ("en", "de"):
        catalog = json.loads((Path(cf.__file__).parent / "translations" / f"{language}.json").read_text(encoding="utf-8"))
        for namespace in ("config", "options"):
            page = catalog[namespace]["step"][group]
            assert set(page["data"]) == set(page["data_description"]) == keys
            assert all(page["data_description"].values())


@pytest.mark.asyncio
async def test_storage_failure_reports_unsaved_step_and_does_not_activate(guide, mock_hass):
    await guide.async_step_user()
    store = get_drafts(mock_hass)
    with patch.object(store, "save", new=AsyncMock(side_effect=OSError("disk full"))):
        result = await guide.async_step_basics(BASIC)
        assert result["errors"]["base"] == "draft_save_failed"
        assert _defaults(result)[CONF_MIN_SOC_ENTITY] == BASIC[CONF_MIN_SOC_ENTITY]
        assert (await guide.async_step_save_draft())["reason"] == "draft_save_failed"
    assert not await store.list("new")
    mock_hass.services.async_call.assert_not_awaited()


def registered(device, unique, entity_id, disabled=None, platform="kostal_kore"):
    return SimpleNamespace(platform=platform, device_id=device, unique_id=unique,
                           entity_id=entity_id, disabled_by=disabled)


def test_kostal_autofill_follows_renames_and_never_mixes_inverters(mock_hass, entity_registry):
    entries = [registered("one", "a_devices:local_Battery:MinSoc", "number.renamed_floor"),
               registered("one", "a_devices:local_Battery:ManualCharge", "switch.renamed_charge"),
               registered("one", "a_devices:local:battery_SoC", "sensor.renamed_soc"),
               registered("one", "a_modbus_bat_max_charge_limit", "number.disabled", "user"),
               registered("two", "other", "sensor.unrelated", platform="other")]
    entity_registry.entities = {entry.entity_id: entry for entry in entries}
    suggestions = cf._kostal_suggestions(mock_hass)
    assert suggestions == {CONF_MIN_SOC_ENTITY: "number.renamed_floor",
                           CONF_GRID_CHARGE_SWITCH: "switch.renamed_charge", CONF_BATTERY_SOC_ENTITY: "sensor.renamed_soc"}
    entries.append(registered("two", "b_devices:local_Battery:MinSoc", "number.other_floor"))
    entity_registry.entities = {entry.entity_id: entry for entry in entries}
    assert cf._kostal_suggestions(mock_hass) == {}
    assert cf._kostal_suggestions(mock_hass, "number.renamed_floor") == suggestions
    entries.append(registered("one", "a_devices:local_Battery:MinSocRel", "number.second_floor"))
    entity_registry.entities = {entry.entity_id: entry for entry in entries}
    assert cf._kostal_suggestions(mock_hass, "number.renamed_floor") == {}


@pytest.mark.asyncio
async def test_kostal_suggestions_prefill_only_reviewable_core_entities(guide, mock_hass, entity_registry):
    entity_registry.entities = {"floor": registered("one", "a_devices:local_Battery:MinSoc", "number.renamed_floor")}
    await guide.async_step_user()
    assert _defaults(await guide.async_step_basics())[CONF_MIN_SOC_ENTITY] == "number.renamed_floor"
    assert CONF_PV_FORECAST_ENTITY not in guide._data
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_drafts_and_expiry_survive_store_recreation(mock_hass, draft_store):
    repo = get_drafts(mock_hass)
    assert get_drafts(mock_hass) is repo
    await asyncio.gather(repo.save("one", "new", {"name": "One"}, {}),
                         repo.save("two", "entry", {"name": "Two"}, {}))
    assert set(await repo.list("new")) == {"one"}
    store_key = f"{DOMAIN}.setup_drafts"
    draft_store[store_key]["expired"] = {"scope": "new", "data": {},
        "updated": (datetime.now(UTC)-timedelta(days=31)).isoformat()}
    draft_store[store_key]["malformed"] = {"scope": "new", "data": [], "updated": 7}
    assert set(await SetupDrafts(mock_hass).list("new")) == {"one"}
    await repo.remove("one")
    assert not await repo.list("new")
    assert set(await repo.list("entry")) == {"two"}


@pytest.mark.asyncio
async def test_reconfigure_resumed_draft_preserves_current_runtime(guide, mock_hass, mock_config_entry):
    mock_hass.config_entries.async_get_known_entry.return_value = mock_config_entry
    flow = _config_flow(mock_hass, source=config_entries.SOURCE_RECONFIGURE,
                        entry_id=mock_config_entry.entry_id)
    await flow.async_step_reconfigure()
    await flow.async_step_basics({**BASIC, CONF_MIN_SOC_ENTITY: "number.kostal_min_soc", CONF_BATTERY_CAPACITY: 35.8})
    await flow.async_step_save_draft()
    mock_config_entry.data = {**mock_config_entry.data, CONF_RUNTIME_STATE: {"latest": "retained"},
                              CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE,
                              CONF_FORCE_DISCHARGE_SWITCH: "switch.discharge"}
    resumed = _config_flow(mock_hass, source=config_entries.SOURCE_RECONFIGURE,
                           entry_id=mock_config_entry.entry_id)
    await resumed.async_step_reconfigure()
    await resumed.async_step_resume_draft({"draft": flow._draft_id})
    result = await resumed.async_step_finish()
    assert result["type"] == "abort"
    stored = mock_hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert stored[CONF_RUNTIME_STATE] == {"latest": "retained"}
    assert stored[CONF_OPERATION_MODE] == MODE_MORNING_DISCHARGE
    assert stored[CONF_BATTERY_CAPACITY] == 35.8


@pytest.mark.asyncio
async def test_options_resume_changes_only_edited_fields_and_preserves_history(guide, mock_hass, mock_config_entry):
    mock_config_entry.options = {CONF_AUTO_EFFICIENCY_DATA: {"best_power": 8000}}
    flow = cf.OptionsFlowHandler(mock_config_entry); flow.hass = mock_hass
    await flow.async_step_init()
    await flow.async_step_schedule(SCHEDULE)
    await flow.async_step_save_draft()
    mock_config_entry.data = {**mock_config_entry.data, CONF_RUNTIME_STATE: {"still": "running"}}
    resumed = cf.OptionsFlowHandler(mock_config_entry); resumed.hass = mock_hass
    await resumed.async_step_init()
    await resumed.async_step_resume_draft({"draft": flow._draft_id})
    result = await resumed.async_step_finish()
    assert result["type"] == "create_entry"
    stored = mock_hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert stored[CONF_END_TIME] == "04:58:00"
    assert stored[CONF_RUNTIME_STATE] == {"still": "running"}
    assert result["data"] == mock_config_entry.options


@pytest.mark.asyncio
async def test_stale_draft_cleanup_failure_does_not_undo_valid_install(guide, mock_hass):
    await guide.async_step_user()
    await guide.async_step_basics(BASIC)
    await guide.async_step_schedule(SCHEDULE)
    with patch.object(get_drafts(mock_hass), "remove", new=AsyncMock(side_effect=OSError("disk full"))):
        assert (await guide.async_step_finish())["type"] == "create_entry"


@pytest.mark.asyncio
async def test_fuse_budget_form_replaces_direct_budget_and_retains_other_fields(guide):
    await guide.async_step_user()
    guide._data[CONF_GRID_MAX_CONTINUOUS_W] = 40000
    result = await guide.async_step_grid_fuse({CONF_GRID_IMPORT_ENTITY: "sensor.grid",
        CONF_CHARGE_POWER_ENTITY: "number.charge", CONF_MAIN_FUSE_A: 63,
        CONF_GRID_PHASES: 3, CONF_GRID_VOLTAGE_V: 230, CONF_GRID_CONTINUOUS_PCT: 80,
        CONF_GRID_HEADROOM_W: 500})
    assert result["step_id"] == "setup"
    assert CONF_GRID_MAX_CONTINUOUS_W not in guide._data
    assert guide._data[CONF_MAIN_FUSE_A] == 63
    assert "setup" in (await guide.async_step_disable_features())["menu_options"]


@pytest.mark.asyncio
async def test_silent_storage_failure_is_detected_by_readback(mock_hass, draft_store):
    repo = get_drafts(mock_hass)
    with patch.object(repo._store, "async_save", new=AsyncMock(return_value=None)):
        with pytest.raises(OSError, match="read back"):
            await repo.save("draft", "new", {"name": "Lost"}, {})
    assert not await repo.list("new")
    draft_store[f"{DOMAIN}.setup_drafts"] = ["corrupt payload"]
    assert not await repo.list("new")


@pytest.mark.parametrize("action", ["efficiency_off", "grid_off", "pv_shortfall_off"])
@pytest.mark.asyncio
async def test_disable_does_not_claim_success_when_draft_write_fails(guide, mock_hass, action):
    await guide.async_step_user()
    with patch.object(get_drafts(mock_hass), "save", new=AsyncMock(side_effect=OSError("disk full"))):
        assert (await getattr(guide, "async_step_" + action)())["reason"] == "draft_save_failed"
    mock_hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_inverter_is_identified_on_first_page(guide, mock_hass, mock_config_entry):
    await guide.async_step_user()
    mock_config_entry.data = {CONF_MIN_SOC_ENTITY: BASIC[CONF_MIN_SOC_ENTITY]}
    mock_hass.config_entries.async_entries.return_value = [mock_config_entry]
    result = await guide.async_step_basics(BASIC)
    assert result["errors"][CONF_MIN_SOC_ENTITY] == "entity_used_by_other_entry"


def test_forecast_is_marked_required_where_an_enabled_feature_needs_it():
    schema = cf._guided_schema("planning", {CONF_AUTO_PV_SHORTFALL: True})
    marker = next(key for key in schema.schema if key.schema == CONF_PV_FORECAST_TODAY_ENTITY)
    assert isinstance(marker, vol.Required)


@pytest.mark.asyncio
async def test_explicit_disable_survives_resume_and_a_concurrent_runtime_enable(guide, mock_hass, mock_config_entry):
    mock_config_entry.data = {**mock_config_entry.data, CONF_AUTO_EFFICIENT_CHARGE: False}
    flow = cf.OptionsFlowHandler(mock_config_entry); flow.hass = mock_hass
    await flow.async_step_init()
    await flow.async_step_efficiency_off()
    await flow.async_step_save_draft()
    mock_config_entry.data = {**mock_config_entry.data, CONF_AUTO_EFFICIENT_CHARGE: True}
    resumed = cf.OptionsFlowHandler(mock_config_entry); resumed.hass = mock_hass
    await resumed.async_step_init()
    await resumed.async_step_resume_draft({"draft": flow._draft_id})
    assert resumed._merged_data()[CONF_AUTO_EFFICIENT_CHARGE] is False
    assert (await resumed.async_step_finish())["type"] == "create_entry"
    assert mock_hass.config_entries.async_update_entry.call_args.kwargs["data"][CONF_AUTO_EFFICIENT_CHARGE] is False


@pytest.mark.asyncio
async def test_explicit_efficiency_setup_restarts_search_even_if_previous_search_finished(guide, mock_hass, mock_config_entry):
    mock_config_entry.data = {**mock_config_entry.data, CONF_AUTO_EFFICIENT_CHARGE: True}
    flow = cf.OptionsFlowHandler(mock_config_entry); flow.hass = mock_hass
    await flow.async_step_init()
    mock_config_entry.data = {**mock_config_entry.data, CONF_AUTO_EFFICIENT_CHARGE: False}
    await flow.async_step_efficiency({CONF_CHARGE_POWER_ENTITY: "number.charge",
        CONF_CHARGE_POWER_SENT_ENTITY: "sensor.ac", CONF_CHARGE_POWER_RECEIVED_ENTITY: "sensor.dc",
        CONF_MIN_CHARGE_POWER_W: 1000, CONF_MAX_CHARGE_POWER_W: 15000})
    assert flow._merged_data()[CONF_AUTO_EFFICIENT_CHARGE] is True


@pytest.mark.asyncio
async def test_schedule_edit_does_not_replace_a_separately_confirmed_discharge_floor(guide, mock_hass):
    await guide.async_step_user()
    await guide.async_step_basics(BASIC)
    await guide.async_step_schedule(SCHEDULE)
    schema = (await guide.async_step_planning())["data_schema"]
    await guide.async_step_planning(schema({CONF_DEFAULT_MIN_SOC: 20}))
    await guide.async_step_save_draft()
    resumed = _config_flow(mock_hass)
    await resumed.async_step_user()
    await resumed.async_step_resume_draft({"draft": guide._draft_id})
    await resumed.async_step_schedule({**SCHEDULE, CONF_START_TIME: "23:15:00"})
    assert resumed._data[CONF_DEFAULT_MIN_SOC] == 20
    assert (await resumed.async_step_finish())["data"][CONF_DEFAULT_MIN_SOC] == 20


@pytest.mark.asyncio
async def test_overlapping_dialog_does_not_restore_a_deleted_backup_input(guide, mock_hass, mock_config_entry):
    mock_config_entry.data = {**mock_config_entry.data, CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup"}
    flow = cf.OptionsFlowHandler(mock_config_entry); flow.hass = mock_hass
    await flow.async_step_init()
    mock_config_entry.data = {key: value for key, value in mock_config_entry.data.items() if key != CONF_BACKUP_MODE_ENTITY}
    await flow.async_step_schedule(SCHEDULE)
    assert (await flow.async_step_finish())["type"] == "create_entry"
    assert CONF_BACKUP_MODE_ENTITY not in mock_hass.config_entries.async_update_entry.call_args.kwargs["data"]


@pytest.mark.asyncio
async def test_options_payload_captures_runtime_state_written_during_draft_cleanup(guide, mock_hass, mock_config_entry):
    mock_config_entry.options = {CONF_RUNTIME_STATE: {"active": True}, CONF_AUTO_EFFICIENCY_DATA: {"best": 8000}}
    flow = cf.OptionsFlowHandler(mock_config_entry); flow.hass = mock_hass
    await flow.async_step_init()
    await flow.async_step_schedule(SCHEDULE)
    async def teardown_during_cleanup():
        await asyncio.sleep(0)
        mock_config_entry.options = {CONF_RUNTIME_STATE: {"active": False}, CONF_AUTO_EFFICIENCY_DATA: {"best": 8000}}
    with patch.object(flow, "_remove_setup_draft", side_effect=teardown_during_cleanup):
        result = await flow.async_step_finish()
    assert result["data"][CONF_RUNTIME_STATE] == {"active": False}
    assert result["data"][CONF_AUTO_EFFICIENCY_DATA] == {"best": 8000}


@pytest.mark.asyncio
async def test_unused_charging_control_can_be_removed_without_disabling_protection_silently(guide):
    await guide.async_step_user()
    guide._data.update({CONF_CHARGE_POWER_ENTITY: "number.old_control", CONF_AUTO_EFFICIENT_CHARGE: True})
    blocked = await guide.async_step_charging_off()
    assert blocked["errors"]["base"] == "disable_dependent_features"
    assert guide._data[CONF_CHARGE_POWER_ENTITY] == "number.old_control"
    assert (await guide.async_step_charging_off({}))["step_id"] == "disable_features"
    await guide.async_step_efficiency_off()
    await guide.async_step_grid_off()
    await guide.async_step_charging_off()
    assert CONF_CHARGE_POWER_ENTITY not in guide._data


@pytest.mark.asyncio
async def test_explicit_grid_disable_wins_over_a_concurrent_binding_addition(guide, mock_hass, mock_config_entry):
    flow = cf.OptionsFlowHandler(mock_config_entry); flow.hass = mock_hass
    await flow.async_step_init()
    mock_config_entry.data = {**mock_config_entry.data, CONF_GRID_IMPORT_ENTITY: "sensor.new_grid"}
    await flow.async_step_grid_off()
    assert CONF_GRID_IMPORT_ENTITY not in flow._merged_data()
