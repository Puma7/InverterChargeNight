"""Tests for the integration's service actions.

Two kinds of test live here. The functional ones drive the handlers directly,
because that is where the interesting behaviour is: which entry a call lands
on, and what a caller is told when it lands on none. The structural ones tie
``services.yaml``, the translations and the registration together, so that a
service added later cannot arrive unnamed or undocumented.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
import yaml
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.inverter_charge_night import services
from custom_components.inverter_charge_night.const import (
    ATTR_CONFIG_ENTRY_ID,
    DOMAIN,
    SERVICE_PLAN_TARGET_SOC,
    SERVICE_RESET_INVERTER,
)
from custom_components.inverter_charge_night.planner import PlanInput, PlanResult

COMPONENT_DIR = Path(services.__file__).parent


def _entry(hass, *, entry_id="test_entry_id", state=ConfigEntryState.LOADED, domain=DOMAIN):
    """A config entry whose runtime_data is a stand-in coordinator."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = entry_id
    entry.domain = domain
    entry.state = state
    entry.title = "Inverter Charge Night"
    entry.runtime_data = MagicMock()
    entry.runtime_data.preview_plan = AsyncMock()
    entry.runtime_data.async_reset_inverter = AsyncMock(return_value=True)
    return entry


def _hass_with(entries):
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = list(entries)
    hass.config_entries.async_get_entry.side_effect = lambda entry_id: next(
        (entry for entry in entries if entry.entry_id == entry_id), None
    )
    return hass


def _call(hass, data=None):
    call = MagicMock()
    call.hass = hass
    call.data = data or {}
    return call


def _plan():
    now = datetime(2026, 9, 20, 5, 0)
    result = PlanResult(
        target_soc=42.0,
        bridge_kwh=3.0 / 7,
        surplus_kwh=8.0 / 3,
        lower_bound_soc=30.0,
        upper_bound_soc=60.0,
        reason="bridge",
    )
    plan_input = PlanInput(
        capacity_kwh=10.0,
        current_soc=17.5,
        user_min_soc=8.0,
        user_max_soc=100.0,
        forecast_kwh_next_day=22.0 / 3,
        forecast_available=True,
        error_margin_pct=10.0,
        window_end=now,
        pv_crossover=now + timedelta(hours=4),
        sunset=now + timedelta(hours=14),
        house_load_kw_profile=[0.4] * 24,
        reserve_kwh=1.0,
        charge_efficiency=0.95,
    )
    return result, plan_input


# Resolving the entry ---------------------------------------------------------


def test_a_single_loaded_entry_needs_no_id():
    entry = _entry(None)
    hass = _hass_with([entry])
    assert services._coordinator_for(hass, _call(hass)) is entry.runtime_data


def test_several_entries_require_the_id():
    hass = _hass_with([_entry(None, entry_id="a"), _entry(None, entry_id="b")])
    with pytest.raises(ServiceValidationError) as err:
        services._coordinator_for(hass, _call(hass))
    assert err.value.translation_key == "entry_id_required"
    assert err.value.translation_placeholders == {"count": "2"}


def test_no_entry_at_all_requires_the_id():
    """Nothing to guess from either - the message names the same remedy."""
    hass = _hass_with([])
    with pytest.raises(ServiceValidationError) as err:
        services._coordinator_for(hass, _call(hass))
    assert err.value.translation_key == "entry_id_required"


def test_an_unknown_id_is_reported_as_such():
    hass = _hass_with([_entry(None)])
    with pytest.raises(ServiceValidationError) as err:
        services._coordinator_for(hass, _call(hass, {ATTR_CONFIG_ENTRY_ID: "nope"}))
    assert err.value.translation_key == "entry_not_found"
    assert err.value.translation_placeholders == {"entry_id": "nope"}


def test_an_entry_of_another_integration_is_not_ours():
    """The id picks an entry, so it can pick one this integration never made."""
    other = _entry(None, entry_id="other", domain="light")
    hass = _hass_with([other])
    with pytest.raises(ServiceValidationError) as err:
        services._coordinator_for(hass, _call(hass, {ATTR_CONFIG_ENTRY_ID: "other"}))
    assert err.value.translation_key == "entry_not_found"


def test_an_unloaded_entry_says_so():
    entry = _entry(None, state=ConfigEntryState.SETUP_RETRY)
    hass = _hass_with([entry])
    with pytest.raises(ServiceValidationError) as err:
        services._coordinator_for(hass, _call(hass, {ATTR_CONFIG_ENTRY_ID: entry.entry_id}))
    assert err.value.translation_key == "entry_not_loaded"
    assert err.value.translation_placeholders == {"title": "Inverter Charge Night"}


def test_the_id_picks_one_entry_out_of_several():
    first, second = _entry(None, entry_id="a"), _entry(None, entry_id="b")
    hass = _hass_with([first, second])
    coordinator = services._coordinator_for(hass, _call(hass, {ATTR_CONFIG_ENTRY_ID: "b"}))
    assert coordinator is second.runtime_data


def test_only_loaded_entries_count_as_the_single_one():
    """A second, broken entry must not make the id suddenly mandatory."""
    loaded = _entry(None, entry_id="a")
    broken = _entry(None, entry_id="b", state=ConfigEntryState.SETUP_ERROR)
    hass = _hass_with([loaded, broken])
    assert services._coordinator_for(hass, _call(hass)) is loaded.runtime_data


# plan_target_soc -------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_target_soc_answers_with_the_plan():
    entry = _entry(None)
    result, plan_input = _plan()
    entry.runtime_data.preview_plan.return_value = (result, plan_input)
    hass = _hass_with([entry])

    response = await services._async_plan_target_soc(_call(hass))

    assert response == {
        "target_soc": 42.0,
        "reason": "bridge",
        "lower_bound_soc": 30.0,
        "upper_bound_soc": 60.0,
        "bridge_kwh": 0.429,
        "surplus_kwh": 2.667,
        "current_soc": 17.5,
        "forecast_kwh": 7.333,
        "forecast_available": True,
        "window_end": "2026-09-20T05:00:00",
        "pv_crossover": "2026-09-20T09:00:00",
        "sunset": "2026-09-20T19:00:00",
    }


@pytest.mark.asyncio
async def test_plan_target_soc_changes_nothing():
    """The action answers a question; it must not move the window's target."""
    entry = _entry(None)
    entry.runtime_data.preview_plan.return_value = _plan()
    hass = _hass_with([entry])

    await services._async_plan_target_soc(_call(hass))

    entry.runtime_data.preview_plan.assert_awaited_once_with()
    entry.runtime_data.async_reset_inverter.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_target_soc_reports_a_failed_plan():
    entry = _entry(None)
    entry.runtime_data.preview_plan.return_value = None
    hass = _hass_with([entry])

    with pytest.raises(HomeAssistantError) as err:
        await services._async_plan_target_soc(_call(hass))
    assert err.value.translation_key == "plan_failed"


# reset_inverter --------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_inverter_resets():
    entry = _entry(None)
    hass = _hass_with([entry])

    assert await services._async_reset_inverter(_call(hass)) is None

    entry.runtime_data.async_reset_inverter.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_reset_inverter_reports_a_refused_reset():
    """Backup mode and a failed write both come back as False here."""
    entry = _entry(None)
    entry.runtime_data.async_reset_inverter.return_value = False
    hass = _hass_with([entry])

    with pytest.raises(HomeAssistantError) as err:
        await services._async_reset_inverter(_call(hass))
    assert err.value.translation_key == "reset_failed"


# Registration and declarations ----------------------------------------------


def test_every_action_is_registered():
    hass = MagicMock()
    services.async_setup_services(hass)
    registered = {call.args[1]: call for call in hass.services.async_register.call_args_list}
    assert set(registered) == set(_declared_services())
    for service, call in registered.items():
        assert call.args[0] == DOMAIN
        assert call.kwargs["schema"] is not None
    assert (
        registered[SERVICE_PLAN_TARGET_SOC].kwargs["supports_response"] is SupportsResponse.ONLY
    ), "a service that only answers must declare it, or the caller gets nothing back"
    assert "supports_response" not in registered[SERVICE_RESET_INVERTER].kwargs


def test_the_schema_accepts_the_entry_id_and_nothing_else():
    assert services._ENTRY_SCHEMA({}) == {}
    assert services._ENTRY_SCHEMA({ATTR_CONFIG_ENTRY_ID: "abc"}) == {ATTR_CONFIG_ENTRY_ID: "abc"}
    with pytest.raises(Exception):
        services._ENTRY_SCHEMA({"target_soc": 50})


def _declared_services():
    return yaml.safe_load((COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8"))


def _load(name):
    return json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))


def test_services_yaml_declares_exactly_the_registered_actions():
    hass = MagicMock()
    services.async_setup_services(hass)
    registered = {call.args[1] for call in hass.services.async_register.call_args_list}
    assert set(_declared_services()) == registered


def test_every_action_and_field_is_named_in_every_language():
    declared = _declared_services()
    for name in ("strings.json", "translations/en.json", "translations/de.json"):
        block = _load(name)["services"]
        assert set(block) == set(declared), name
        for service, definition in declared.items():
            assert block[service]["name"] and block[service]["description"], (name, service)
            assert set(block[service]["fields"]) == set(definition["fields"]), (name, service)
            for field in definition["fields"]:
                labels = block[service]["fields"][field]
                assert labels["name"] and labels["description"], (name, service, field)


def test_every_field_offers_a_picker():
    """A raw entry id typed by hand is a support request waiting to happen."""
    for service, definition in _declared_services().items():
        selector = definition["fields"][ATTR_CONFIG_ENTRY_ID]["selector"]
        assert selector["config_entry"]["integration"] == DOMAIN, service


def test_every_exception_key_the_actions_raise_is_translated():
    source = (COMPONENT_DIR / "services.py").read_text(encoding="utf-8")
    raised = {
        key
        for key in _load("strings.json")["exceptions"]
        if f'translation_key="{key}"' in source
    }
    assert raised == {
        "adhoc_refused",
        "entry_id_required",
        "entry_not_found",
        "entry_not_loaded",
        "nothing_to_release",
        "plan_failed",
        "reset_failed",
    }


@pytest.mark.asyncio
async def test_async_setup_registers_the_actions_without_an_entry():
    """The actions must exist before - and after - any entry is loaded.

    An action that only lives while an entry happens to be set up disappears
    from an automation's reach exactly when something has gone wrong.
    """
    from custom_components.inverter_charge_night import async_setup

    hass = MagicMock()
    assert await async_setup(hass, {}) is True
    registered = {call.args[1] for call in hass.services.async_register.call_args_list}
    assert registered == set(_declared_services())


def test_every_exception_carries_a_message_object():
    """A bare string never reaches the user.

    Home Assistant looks up ``exceptions.<key>.message``; an exception whose
    value is the message itself is simply not found, and the raw key is shown
    instead - which is what the user then reads in the notification.
    """
    for name in ("strings.json", "translations/en.json", "translations/de.json"):
        for key, value in _load(name)["exceptions"].items():
            assert isinstance(value, dict), (name, key)
            assert set(value) == {"message"}, (name, key)
            assert value["message"], (name, key)


# The ad-hoc actions (plan 013) --------------------------------------------------


def _adhoc_entry():
    entry = _entry(None)
    entry.runtime_data.async_charge_to = AsyncMock(return_value=True)
    entry.runtime_data.async_block_discharge = AsyncMock(return_value=True)
    entry.runtime_data.async_allow_discharge = AsyncMock(return_value=True)
    return entry


@pytest.mark.asyncio
async def test_charge_to_passes_the_target_and_the_duration():
    entry = _adhoc_entry()
    hass = _hass_with([entry])
    data = services._CHARGE_TO_SCHEMA({"target_soc": 62})

    await services._async_charge_to(_call(hass, data))

    entry.runtime_data.async_charge_to.assert_awaited_once_with(62.0, timedelta(hours=2))


@pytest.mark.asyncio
async def test_charge_to_takes_a_duration_it_is_given():
    entry = _adhoc_entry()
    hass = _hass_with([entry])
    data = services._CHARGE_TO_SCHEMA({"target_soc": 50, "duration": {"minutes": 45}})

    await services._async_charge_to(_call(hass, data))

    entry.runtime_data.async_charge_to.assert_awaited_once_with(50.0, timedelta(minutes=45))


@pytest.mark.parametrize("target", [-1, 101])
def test_charge_to_refuses_an_impossible_level(target):
    with pytest.raises(vol.Invalid):
        services._CHARGE_TO_SCHEMA({"target_soc": target})


def test_charge_to_needs_a_level():
    with pytest.raises(vol.Invalid):
        services._CHARGE_TO_SCHEMA({})


def test_a_negative_duration_is_refused():
    with pytest.raises(vol.Invalid):
        services._DURATION_SCHEMA({"duration": {"minutes": -5}})


@pytest.mark.asyncio
async def test_a_refused_window_is_reported_as_an_error():
    """The configured window is running, or backup mode, or no readable battery."""
    entry = _adhoc_entry()
    entry.runtime_data.async_charge_to.return_value = False
    hass = _hass_with([entry])

    with pytest.raises(HomeAssistantError) as err:
        await services._async_charge_to(
            _call(hass, services._CHARGE_TO_SCHEMA({"target_soc": 62}))
        )
    assert err.value.translation_key == "adhoc_refused"


@pytest.mark.asyncio
async def test_block_discharge_holds_for_the_default_two_hours():
    entry = _adhoc_entry()
    hass = _hass_with([entry])

    await services._async_block_discharge(_call(hass, services._DURATION_SCHEMA({})))

    entry.runtime_data.async_block_discharge.assert_awaited_once_with(timedelta(hours=2))


@pytest.mark.asyncio
async def test_allow_discharge_releases():
    entry = _adhoc_entry()
    hass = _hass_with([entry])

    await services._async_allow_discharge(_call(hass))

    entry.runtime_data.async_allow_discharge.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_releasing_nothing_says_so():
    entry = _adhoc_entry()
    entry.runtime_data.async_allow_discharge.return_value = False
    hass = _hass_with([entry])

    with pytest.raises(HomeAssistantError) as err:
        await services._async_allow_discharge(_call(hass))
    assert err.value.translation_key == "nothing_to_release"
