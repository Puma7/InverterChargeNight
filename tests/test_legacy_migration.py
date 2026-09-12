"""What an entry collects over the life of an installation.

Written against a real diagnostics download from an installation that has been
through several releases of this integration: it carried three settings whose
keys no longer exist, 500 measurement records of a version that is gone (about
a hundred kilobytes that Home Assistant loads and rewrites on every change),
and a repair issue for an entity nobody configures any more - which cannot be
dismissed from the repairs page, so it stays red for ever.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from custom_components.inverter_charge_night import _clear_stale_entity_issues, _migrate_entry_data
from custom_components.inverter_charge_night.const import (
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_RUNTIME_STATE,
    DOMAIN,
)

LEGACY_DATA = {
    "battery_soc_entity": "sensor.wr_battery_soc",
    "battery_charge_energy_entity": "sensor.wr_battery_charge_from_grid_total",
    "grid_import_energy_entity": "sensor.wr_total_grid_consumption_total",
    "home_consumption_energy_entity": "sensor.wr_home_consumption_from_grid_total",
}
LEGACY_OPTIONS = {
    CONF_AUTO_EFFICIENCY_DATA: {
        "history": {"5000": 0.1},
        "detailed_log": [{"type": "regular_charge", "loss_pct": 0.0}] * 500,
    },
    "charge_session_data": {"last_session": {"loss_pct": 100.0}},
    CONF_RUNTIME_STATE: {"is_enabled": True},
}


def _entry(data=None, options=None):
    entry = MagicMock()
    entry.data = dict(data if data is not None else LEGACY_DATA)
    entry.options = dict(options if options is not None else LEGACY_OPTIONS)
    return entry


def _hass_that_stores(entry):
    hass = MagicMock()

    def _update(config_entry, data=None, options=None, **kwargs):
        if data is not None:
            entry.data = data
        if options is not None:
            entry.options = options

    hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    return hass


def test_the_house_meter_is_carried_over(caplog):
    """Same quantity, new key: a cumulative kWh meter of the house."""
    caplog.set_level(logging.INFO)
    entry = _entry()
    _migrate_entry_data(_hass_that_stores(entry), entry)

    assert entry.data[CONF_HOUSE_LOAD_ENTITY] == "sensor.wr_home_consumption_from_grid_total"
    assert "home_consumption_energy_entity" not in entry.data
    assert "Carried the house consumption meter" in caplog.text


def test_an_existing_setting_is_not_overwritten():
    entry = _entry({**LEGACY_DATA, CONF_HOUSE_LOAD_ENTITY: "sensor.chosen_by_the_user"})
    _migrate_entry_data(_hass_that_stores(entry), entry)

    assert entry.data[CONF_HOUSE_LOAD_ENTITY] == "sensor.chosen_by_the_user"


def test_settings_nobody_reads_are_dropped_but_named(caplog):
    """Dropping them silently would lose a useful pointer for the user."""
    caplog.set_level(logging.INFO)
    entry = _entry()
    _migrate_entry_data(_hass_that_stores(entry), entry)

    assert "battery_charge_energy_entity" not in entry.data
    assert "grid_import_energy_entity" not in entry.data
    assert "sensor.wr_battery_charge_from_grid_total" in caplog.text
    assert "efficiency search" in caplog.text


def test_measurements_of_a_version_that_is_gone_are_dropped(caplog):
    """500 dead records travel with every write of the config entry."""
    caplog.set_level(logging.INFO)
    entry = _entry()
    _migrate_entry_data(_hass_that_stores(entry), entry)

    efficiency = entry.options[CONF_AUTO_EFFICIENCY_DATA]
    assert "detailed_log" not in efficiency
    assert efficiency["history"] == {"5000": 0.1}, "our own data survives"
    assert "charge_session_data" not in entry.options
    assert entry.options[CONF_RUNTIME_STATE] == {"is_enabled": True}


def test_a_current_entry_is_not_written_at_all():
    """No pointless write, and no reload loop it could trigger."""
    entry = _entry(
        {"battery_soc_entity": "sensor.soc", CONF_HOUSE_LOAD_ENTITY: "sensor.house"},
        {CONF_AUTO_EFFICIENCY_DATA: {"history": {}}},
    )
    hass = _hass_that_stores(entry)

    _migrate_entry_data(hass, entry)

    hass.config_entries.async_update_entry.assert_not_called()


def test_a_repair_issue_for_an_entity_nobody_configures_is_cleared():
    """It is not fixable from the repairs page, so nothing else would clear it."""
    entry = _entry({"battery_soc_entity": "sensor.wr_battery_soc"}, {})
    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.states.get = MagicMock(return_value=None)
    registry = MagicMock()
    registry.issues = {
        (DOMAIN, "entity_not_available_sensor.wr_battery_soc"): MagicMock(),
        (DOMAIN, "entity_not_available_sensor.from_the_old_integration"): MagicMock(),
        ("kostal_kore", "entity_not_available_sensor.someone_elses"): MagicMock(),
    }
    with pytest.MonkeyPatch.context() as mp:
        import custom_components.inverter_charge_night as icn

        deleted: list[str] = []
        mp.setattr(icn.ir, "async_get", lambda hass: registry)
        mp.setattr(
            icn.ir, "async_delete_issue", lambda hass, domain, issue_id: deleted.append(issue_id)
        )
        _clear_stale_entity_issues(hass)

    # Still configured and still missing: the issue is the truth, it stays
    assert "entity_not_available_sensor.wr_battery_soc" not in deleted
    # Not configured any more: nobody can act on it
    assert "entity_not_available_sensor.from_the_old_integration" in deleted
    # Another integration's issue is none of our business
    assert "entity_not_available_sensor.someone_elses" not in deleted


def test_an_entity_that_came_back_clears_its_issue():
    entry = _entry({"battery_soc_entity": "sensor.wr_battery_soc"}, {})
    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.states.get = MagicMock(return_value=MagicMock())
    registry = MagicMock()
    registry.issues = {(DOMAIN, "entity_not_available_sensor.wr_battery_soc"): MagicMock()}
    with pytest.MonkeyPatch.context() as mp:
        import custom_components.inverter_charge_night as icn

        deleted: list[str] = []
        mp.setattr(icn.ir, "async_get", lambda hass: registry)
        mp.setattr(
            icn.ir, "async_delete_issue", lambda hass, domain, issue_id: deleted.append(issue_id)
        )
        _clear_stale_entity_issues(hass)

    assert deleted == ["entity_not_available_sensor.wr_battery_soc"]
