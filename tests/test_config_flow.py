"""Tests for the four-step config, reconfigure and options flows."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.inverter_charge_night import config_flow
from custom_components.inverter_charge_night import const
from custom_components.inverter_charge_night.config_flow import (
    InverterChargeNightConfigFlow,
    OptionsFlowHandler,
    validate_date_optional,
    validate_soc,
)
from custom_components.inverter_charge_night.const import (
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_START_DATE,
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_RUNTIME_STATE,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_BACKUP_MODE_ENTITY,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_KOSTAL_MIN_SOC_ENTITY,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
    CONF_OPERATION_MODE,
    CONF_PV_FORECAST_ENTITY,
    CONF_START_TIME,
    CONF_UPDATE_INTERVAL,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    MODE_MORNING_DISCHARGE,
    MODE_NIGHT_CHARGE,
)

COMPONENT_DIR = Path(config_flow.__file__).parent

# One valid submission per wizard step (values as the selectors deliver them).
ENTITIES_INPUT = {
    CONF_NAME: "Test",
    CONF_OPERATION_MODE: MODE_NIGHT_CHARGE,
    CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc",
    CONF_KOSTAL_GRID_CHARGE_SWITCH: "switch.grid",
    CONF_PV_FORECAST_ENTITY: "sensor.forecast",
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_BATTERY_CAPACITY: 10.0,
}
TIME_SOC_INPUT = {
    CONF_START_TIME: "00:00",
    CONF_END_TIME: "05:59",
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
}
POWER_INPUT = {
    CONF_MIN_CHARGE_POWER_W: 5000.0,
    CONF_MAX_CHARGE_POWER_W: 15000.0,
    CONF_AUTO_EFFICIENT_CHARGE: False,
}
ADVANCED_INPUT = {
    CONF_UPDATE_INTERVAL: 900.0,
    CONF_COMMAND_DELAY: 0.1,
}
# Same step 1 input, but keeping the inverter the mock entry is already set up
# for: changing it would trip the unique-id check of the reconfigure flow.
RECONFIGURE_ENTITIES_INPUT = {
    **ENTITIES_INPUT,
    CONF_KOSTAL_MIN_SOC_ENTITY: "number.kostal_min_soc",
}


@pytest.fixture
def entity_registry():
    """Patch ``er.async_get`` so that every entity id counts as existing."""
    registry = MagicMock()
    registry.async_get.return_value = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: registry)
        yield registry


def _config_flow(mock_hass, source=config_entries.SOURCE_USER, **context):
    flow = InverterChargeNightConfigFlow()
    flow.hass = mock_hass
    flow.context = {"source": source, **context}
    mock_hass.config_entries.flow.async_progress_by_handler.return_value = []
    mock_hass.config_entries.async_entry_for_domain_unique_id.return_value = None
    return flow


def _defaults(result) -> dict:
    """Return the prefilled defaults and suggested values of a shown form."""
    values = {}
    for marker in result["data_schema"].schema:
        if marker.default is not vol.UNDEFINED:
            values[marker.schema] = marker.default()
        elif marker.description and "suggested_value" in marker.description:
            values[marker.schema] = marker.description["suggested_value"]
    return values


def test_validate_soc():
    assert validate_soc(0.0) is True
    assert validate_soc(100.0) is True
    assert validate_soc(-1.0) is False
    assert validate_soc(101.0) is False


def test_validate_date_optional():
    assert validate_date_optional(None) is True
    assert validate_date_optional("") is True
    assert validate_date_optional("2025-12-31") is True
    assert validate_date_optional("2025-13-01") is False


# --- initial setup -----------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_shows_first_step(mock_hass):
    flow = _config_flow(mock_hass)

    result = await flow.async_step_user()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    assert _defaults(result)[CONF_OPERATION_MODE] == MODE_NIGHT_CHARGE


@pytest.mark.asyncio
async def test_config_flow_user_success(mock_hass, entity_registry):
    flow = _config_flow(mock_hass)

    result = await flow.async_step_user(ENTITIES_INPUT)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "time_soc"

    result = await flow.async_step_time_soc(TIME_SOC_INPUT)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "power"

    result = await flow.async_step_power(POWER_INPUT)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "advanced"

    result = await flow.async_step_advanced(ADVANCED_INPUT)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test"
    assert flow.unique_id == "number.min_soc"
    data = result["data"]
    for step_input in (ENTITIES_INPUT, TIME_SOC_INPUT, POWER_INPUT, ADVANCED_INPUT):
        for key, value in step_input.items():
            assert data[key] == value
    # number selectors deliver floats; integer settings stay integers in storage
    assert data[CONF_UPDATE_INTERVAL] == 900 and isinstance(data[CONF_UPDATE_INTERVAL], int)
    assert data[CONF_MIN_CHARGE_POWER_W] == 5000 and isinstance(data[CONF_MIN_CHARGE_POWER_W], int)
    assert data[CONF_ACTIVE_START_DATE] is None
    assert data[CONF_ACTIVE_END_DATE] is None


@pytest.mark.asyncio
async def test_config_flow_aborts_when_inverter_already_configured(mock_hass, entity_registry):
    flow = _config_flow(mock_hass)
    existing = MagicMock(source=config_entries.SOURCE_USER)
    mock_hass.config_entries.async_entry_for_domain_unique_id.return_value = existing

    await flow.async_step_user(ENTITIES_INPUT)
    await flow.async_step_time_soc(TIME_SOC_INPUT)
    await flow.async_step_power(POWER_INPUT)
    with pytest.raises(AbortFlow) as abort:
        await flow.async_step_advanced(ADVANCED_INPUT)

    assert abort.value.reason == "already_configured"
    mock_hass.config_entries.async_entry_for_domain_unique_id.assert_called_with(
        flow.handler, "number.min_soc"
    )


@pytest.mark.asyncio
async def test_config_flow_accepts_time_selector_format(mock_hass, entity_registry):
    flow = _config_flow(mock_hass)
    await flow.async_step_user(ENTITIES_INPUT)

    result = await flow.async_step_time_soc(
        {**TIME_SOC_INPUT, CONF_START_TIME: "22:00:00", CONF_END_TIME: "05:59:00"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "power"
    assert flow._data[CONF_START_TIME] == "22:00:00"


@pytest.mark.asyncio
async def test_config_flow_reports_errors_in_their_own_step(mock_hass, entity_registry):
    flow = _config_flow(mock_hass)
    entity_registry.async_get.side_effect = (
        lambda entity_id: None if entity_id == "number.missing" else MagicMock()
    )
    mock_hass.states.get.return_value = None

    result = await flow.async_step_user(
        {**ENTITIES_INPUT, CONF_KOSTAL_MIN_SOC_ENTITY: "number.missing"}
    )
    # only this step's field is flagged; the not-yet-entered time/power fields are not
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_KOSTAL_MIN_SOC_ENTITY: "invalid_entity"}
    # the submitted values are kept as defaults so nothing has to be retyped
    assert _defaults(result)[CONF_NAME] == "Test"

    await flow.async_step_user(ENTITIES_INPUT)
    result = await flow.async_step_time_soc(
        {**TIME_SOC_INPUT, CONF_START_TIME: "22:00", CONF_END_TIME: "22:00:00"}
    )
    assert result["step_id"] == "time_soc"
    assert result["errors"] == {CONF_END_TIME: "start_end_time_must_differ"}

    await flow.async_step_time_soc(TIME_SOC_INPUT)
    result = await flow.async_step_power({**POWER_INPUT, CONF_MAX_CHARGE_POWER_W: 5000.0})
    assert result["step_id"] == "power"
    assert result["errors"] == {CONF_MAX_CHARGE_POWER_W: "max_power_must_be_greater_than_min"}

    await flow.async_step_power(POWER_INPUT)
    result = await flow.async_step_advanced({**ADVANCED_INPUT, CONF_ACTIVE_START_DATE: "31.12.2025"})
    assert result["step_id"] == "advanced"
    assert result["errors"] == {CONF_ACTIVE_START_DATE: "invalid_date"}


# --- reconfigure -------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconfigure_flow_updates_entry(mock_hass, mock_config_entry, entity_registry):
    mock_config_entry.data = {
        **mock_config_entry.data,
        CONF_BACKUP_MODE_ENTITY: "binary_sensor.backup",
        CONF_ACTIVE_START_DATE: "2025-01-01",
    }
    mock_hass.config_entries.async_get_known_entry.return_value = mock_config_entry
    flow = _config_flow(
        mock_hass, source=config_entries.SOURCE_RECONFIGURE, entry_id=mock_config_entry.entry_id
    )

    result = await flow.async_step_reconfigure()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert _defaults(result)[CONF_KOSTAL_MIN_SOC_ENTITY] == "number.kostal_min_soc"
    assert _defaults(result)[CONF_BATTERY_CAPACITY] == 10.0

    result = await flow.async_step_reconfigure(
        {**RECONFIGURE_ENTITIES_INPUT, CONF_BATTERY_CAPACITY: 20.0}
    )
    assert result["step_id"] == "reconfigure_time_soc"
    assert _defaults(result)[CONF_END_TIME] == "05:59"

    result = await flow.async_step_reconfigure_time_soc(TIME_SOC_INPUT)
    assert result["step_id"] == "reconfigure_power"

    result = await flow.async_step_reconfigure_power(POWER_INPUT)
    assert result["step_id"] == "reconfigure_advanced"
    assert _defaults(result)[CONF_BACKUP_MODE_ENTITY] == "binary_sensor.backup"
    assert _defaults(result)[CONF_ACTIVE_START_DATE] == "2025-01-01"

    # the user clears the backup entity and the start date
    result = await flow.async_step_reconfigure_advanced(ADVANCED_INPUT)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    update = mock_hass.config_entries.async_update_entry
    update.assert_called_once()
    assert update.call_args.kwargs["entry"] is mock_config_entry
    stored = update.call_args.kwargs["data"]
    assert stored[CONF_BATTERY_CAPACITY] == 20.0
    assert stored[CONF_MIN_CHARGE_POWER_W] == 5000
    assert CONF_BACKUP_MODE_ENTITY not in stored
    assert stored[CONF_ACTIVE_START_DATE] is None
    mock_hass.config_entries.async_schedule_reload.assert_called_once_with(
        mock_config_entry.entry_id
    )


@pytest.mark.asyncio
async def test_reconfigure_flow_rejects_an_inverter_another_entry_drives(
    mock_hass, mock_config_entry, entity_registry
):
    """Two entries must never drive the same min SOC entity towards opposite targets."""
    other = MagicMock(spec=ConfigEntry)
    other.entry_id = "other_entry"
    other.unique_id = ENTITIES_INPUT[CONF_KOSTAL_MIN_SOC_ENTITY]
    other.data = {CONF_KOSTAL_MIN_SOC_ENTITY: ENTITIES_INPUT[CONF_KOSTAL_MIN_SOC_ENTITY]}
    mock_hass.config_entries.async_entries.return_value = [mock_config_entry, other]
    mock_hass.config_entries.async_get_known_entry.return_value = mock_config_entry
    flow = _config_flow(
        mock_hass, source=config_entries.SOURCE_RECONFIGURE, entry_id=mock_config_entry.entry_id
    )

    await flow.async_step_reconfigure(ENTITIES_INPUT)
    await flow.async_step_reconfigure_time_soc(TIME_SOC_INPUT)
    await flow.async_step_reconfigure_power(POWER_INPUT)
    result = await flow.async_step_reconfigure_advanced(ADVANCED_INPUT)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_KOSTAL_MIN_SOC_ENTITY: "entity_used_by_other_entry"}
    mock_hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_reconfigure_flow_accepts_a_free_inverter(
    mock_hass, mock_config_entry, entity_registry
):
    """Replacing the inverter (new device, renamed entity) must stay possible.

    _abort_if_unique_id_mismatch would refuse this, because it compares against
    this entry's own id rather than against the other entries.
    """
    mock_hass.config_entries.async_entries.return_value = [mock_config_entry]
    mock_hass.config_entries.async_get_known_entry.return_value = mock_config_entry
    flow = _config_flow(
        mock_hass, source=config_entries.SOURCE_RECONFIGURE, entry_id=mock_config_entry.entry_id
    )

    await flow.async_step_reconfigure(ENTITIES_INPUT)  # a different, unused entity
    await flow.async_step_reconfigure_time_soc(TIME_SOC_INPUT)
    await flow.async_step_reconfigure_power(POWER_INPUT)
    result = await flow.async_step_reconfigure_advanced(ADVANCED_INPUT)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    kwargs = mock_hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["data"][CONF_KOSTAL_MIN_SOC_ENTITY] == ENTITIES_INPUT[CONF_KOSTAL_MIN_SOC_ENTITY]
    # The unique id has to follow the inverter, otherwise a second entry for the
    # new entity is not caught and the freed old one is wrongly blocked.
    # async_set_unique_id alone only writes the flow context, not the entry.
    assert kwargs["unique_id"] == ENTITIES_INPUT[CONF_KOSTAL_MIN_SOC_ENTITY]


# --- options -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_flow_requires_auto_entities(mock_hass, mock_config_entry, entity_registry):
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass

    user_input = {
        **POWER_INPUT,
        CONF_AUTO_EFFICIENT_CHARGE: True,
        # Missing required entities
        CONF_CHARGE_POWER_ENTITY: None,
        CONF_CHARGE_POWER_SENT_ENTITY: None,
        CONF_CHARGE_POWER_RECEIVED_ENTITY: None,
    }

    result = await flow.async_step_power(user_input)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "power"
    assert result["errors"][CONF_CHARGE_POWER_ENTITY] == "required_entity"
    assert result["errors"][CONF_CHARGE_POWER_SENT_ENTITY] == "required_entity"
    assert result["errors"][CONF_CHARGE_POWER_RECEIVED_ENTITY] == "required_entity"


@pytest.mark.asyncio
async def test_options_flow_success_keeps_options(mock_hass, mock_config_entry, entity_registry):
    mock_config_entry.options = {CONF_AUTO_EFFICIENCY_DATA: {"history": [1, 2]}}
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass

    result = await flow.async_step_init()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    assert _defaults(result)[CONF_PV_FORECAST_ENTITY] == "sensor.pv_forecast"

    result = await flow.async_step_init(ENTITIES_INPUT)
    assert result["step_id"] == "time_soc"
    result = await flow.async_step_time_soc({**TIME_SOC_INPUT, CONF_USER_MAX_SOC: 90.0})
    assert result["step_id"] == "power"
    result = await flow.async_step_power(POWER_INPUT)
    assert result["step_id"] == "advanced"
    result = await flow.async_step_advanced(ADVANCED_INPUT)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == ""
    assert result["data"] == {CONF_AUTO_EFFICIENCY_DATA: {"history": [1, 2]}}
    update = mock_hass.config_entries.async_update_entry
    update.assert_called_once()
    assert update.call_args.args[0] is mock_config_entry
    stored = update.call_args.kwargs["data"]
    assert stored[CONF_USER_MAX_SOC] == 90.0
    assert stored[CONF_NAME] == "Test"
    assert stored[CONF_UPDATE_INTERVAL] == 900



@pytest.mark.asyncio
async def test_options_flow_rejects_entity_of_another_entry(
    mock_hass, mock_config_entry, entity_registry
):
    """Saving an inverter that a second entry already drives is refused."""
    other = MagicMock()
    other.entry_id = "other_entry_id"
    other.unique_id = "number.min_soc"
    other.data = {CONF_KOSTAL_MIN_SOC_ENTITY: "number.min_soc"}
    mock_hass.config_entries.async_entries.return_value = [mock_config_entry, other]
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass

    await flow.async_step_init(ENTITIES_INPUT)  # the other entry's min SOC entity
    await flow.async_step_time_soc(TIME_SOC_INPUT)
    await flow.async_step_power(POWER_INPUT)
    result = await flow.async_step_advanced(ADVANCED_INPUT)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {CONF_KOSTAL_MIN_SOC_ENTITY: "entity_used_by_other_entry"}
    mock_hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_options_flow_keeps_runtime_changes(mock_hass, mock_config_entry, entity_registry):
    """A field written while the dialog is open is not reverted on save.

    The select entity, the switch and the efficiency finder write into
    ``entry.data`` at runtime. Saving the dialog must keep such a change for
    every field the user did not edit, and still apply the fields they did.
    """
    mock_config_entry.data = {
        **mock_config_entry.data,
        CONF_OPERATION_MODE: MODE_NIGHT_CHARGE,
        CONF_AUTO_EFFICIENT_CHARGE: True,
    }
    flow = OptionsFlowHandler(mock_config_entry)
    flow.hass = mock_hass

    await flow.async_step_init({**ENTITIES_INPUT, CONF_OPERATION_MODE: MODE_NIGHT_CHARGE})
    await flow.async_step_time_soc(TIME_SOC_INPUT)
    # While the dialog is open the finder completes and disables itself, and the
    # user flips the operation mode from the dashboard.
    mock_config_entry.data = {
        **mock_config_entry.data,
        CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE,
        CONF_AUTO_EFFICIENT_CHARGE: False,
    }
    await flow.async_step_power({**POWER_INPUT, CONF_AUTO_EFFICIENT_CHARGE: True})
    await flow.async_step_advanced({**ADVANCED_INPUT, CONF_COMMAND_DELAY: 0.5})

    stored = mock_hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert stored[CONF_OPERATION_MODE] == MODE_MORNING_DISCHARGE
    assert stored[CONF_AUTO_EFFICIENT_CHARGE] is False
    # the value the user actually changed in the dialog still wins
    assert stored[CONF_COMMAND_DELAY] == 0.5

# --- shared validation -------------------------------------------------------


def _time_only_input(start: str, end: str) -> dict:
    return {
        CONF_START_TIME: start,
        CONF_END_TIME: end,
        CONF_USER_MIN_SOC: 8.0,
        CONF_USER_MAX_SOC: 100.0,
        CONF_BATTERY_CAPACITY: 10.0,
        CONF_DEFAULT_MIN_SOC: 8.0,
        CONF_MIN_CHARGE_POWER_W: 5000,
        CONF_MAX_CHARGE_POWER_W: 15000,
    }


def _time_errors(mock_hass, start: str, end: str) -> dict:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(er, "async_get", lambda hass: MagicMock())
        return config_flow._validate_user_input(_time_only_input(start, end), mock_hass)


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("00:00", "05:59", {}),
        ("22:00", "22:00", {CONF_END_TIME: "start_end_time_must_differ"}),
        # parsed values are compared, so differently spelled equal times are caught
        ("2:00", "02:00", {CONF_END_TIME: "start_end_time_must_differ"}),
        ("22:0", "22:00", {CONF_END_TIME: "start_end_time_must_differ"}),
        (" 6:00", "06:00", {CONF_END_TIME: "start_end_time_must_differ"}),
        # an invalid format is reported instead of the equality error
        ("ab:cd", "ab:cd", {CONF_START_TIME: "invalid_time", CONF_END_TIME: "invalid_time"}),
    ],
)
def test_validate_user_input_time_errors(mock_hass, start, end, expected):
    errors = _time_errors(mock_hass, start, end)
    time_errors = {k: v for k, v in errors.items() if k in (CONF_START_TIME, CONF_END_TIME)}
    assert time_errors == expected


# --- translations stay in sync with the schemas ------------------------------


def _load(name: str) -> dict:
    return json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))


def _schema_keys(build_schema) -> set:
    return {marker.schema for marker in build_schema({}).schema}


SCHEMAS = {
    "entities": config_flow._schema_entities,
    "time_soc": config_flow._schema_time_soc,
    "power": config_flow._schema_power,
    "advanced": config_flow._schema_advanced,
}
STEP_SCHEMAS = {
    ("config", "user"): "entities",
    ("config", "time_soc"): "time_soc",
    ("config", "power"): "power",
    ("config", "advanced"): "advanced",
    ("config", "reconfigure"): "entities",
    ("config", "reconfigure_time_soc"): "time_soc",
    ("config", "reconfigure_power"): "power",
    ("config", "reconfigure_advanced"): "advanced",
    ("options", "init"): "entities",
    ("options", "time_soc"): "time_soc",
    ("options", "power"): "power",
    ("options", "advanced"): "advanced",
}


def test_translations_are_a_copy_of_strings():
    assert _load("translations/en.json") == _load("strings.json")


def _string_paths(value, prefix=""):
    """Every leaf of a translation file, by path."""
    if isinstance(value, dict):
        paths = set()
        for key, child in value.items():
            paths |= _string_paths(child, f"{prefix}/{key}")
        return paths
    return {prefix}


def test_the_german_translation_covers_every_string():
    """A half-translated file shows English and German side by side.

    Home Assistant falls back per string, not per file, so a missing key is
    not a visible error - it just leaves that one label in English.
    """
    english = _string_paths(_load("strings.json"))
    german = _string_paths(_load("translations/de.json"))
    assert german == english


def test_every_step_labels_exactly_its_fields():
    strings = _load("strings.json")
    for (section, step_id), schema_name in STEP_SCHEMAS.items():
        step = strings[section]["step"][step_id]
        expected = _schema_keys(SCHEMAS[schema_name])
        assert set(step["data"]) == expected, (section, step_id)
        assert set(step["data_description"]) == expected, (section, step_id)
        assert step["title"] and step["description"], (section, step_id)
    assert set(strings["config"]["step"]) == {step for section, step in STEP_SCHEMAS if section == "config"}
    assert set(strings["options"]["step"]) == {step for section, step in STEP_SCHEMAS if section == "options"}


def test_every_config_key_is_collected_by_exactly_one_step():
    conf_keys = {value for name, value in vars(const).items() if name.startswith("CONF_")}
    # entry.options keys, not settings: auto-efficiency history and persisted runtime state
    conf_keys -= {CONF_AUTO_EFFICIENCY_DATA, CONF_RUNTIME_STATE}
    conf_keys.add(CONF_NAME)
    steps = ("user", "time_soc", "power", "advanced")
    data = _load("strings.json")["config"]["step"]
    for key in conf_keys:
        assert sum(key in data[step]["data"] for step in steps) == 1, key
    all_keys = set().union(*(_schema_keys(build) for build in SCHEMAS.values()))
    assert all_keys == conf_keys


def test_every_error_key_is_translated():
    strings = _load("strings.json")
    source = (COMPONENT_DIR / "config_flow.py").read_text(encoding="utf-8")
    for error in strings["config"]["error"]:
        assert f'"{error}"' in source, error
    assert strings["options"]["error"] == strings["config"]["error"]
