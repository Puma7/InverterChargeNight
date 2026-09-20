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

from custom_components.inverter_charge_night import (
    _clear_stale_entity_issues,
    _migrate_entry_data,
)
from custom_components.inverter_charge_night.const import (
    CONF_ACTIVE_END_DATE,
    CONF_ACTIVE_RANGE_YEARLY,
    CONF_ACTIVE_START_DATE,
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_GRID_CHARGE_SWITCH,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_MIN_SOC_ENTITY,
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
        {
            "battery_soc_entity": "sensor.soc",
            CONF_HOUSE_LOAD_ENTITY: "sensor.house",
            CONF_ACTIVE_RANGE_YEARLY: True,
        },
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


# --- the vendor name comes out of the two inverter keys (3.0.2) --------------


def test_the_inverter_entities_are_carried_over_to_their_new_keys(caplog):
    """An installation configured before 3.0.2 must keep working untouched.

    Both keys are read by every write path to the inverter. If the migration
    missed them, the next window would find no min SOC entity and no grid
    charge switch, and would quietly do nothing at all.
    """
    entry = _entry(
        {
            "kostal_min_soc_entity": "number.wr_min_soc",
            "kostal_grid_charge_switch": "switch.wr_grid_charge",
            "battery_soc_entity": "sensor.wr_battery_soc",
        },
        {},
    )
    hass = _hass_that_stores(entry)

    with caplog.at_level(logging.INFO):
        _migrate_entry_data(hass, entry)

    assert entry.data[CONF_MIN_SOC_ENTITY] == "number.wr_min_soc"
    assert entry.data[CONF_GRID_CHARGE_SWITCH] == "switch.wr_grid_charge"
    assert "kostal_min_soc_entity" not in entry.data
    assert "kostal_grid_charge_switch" not in entry.data
    assert entry.data["battery_soc_entity"] == "sensor.wr_battery_soc"
    assert "Carried kostal_min_soc_entity over to min_soc_entity" in caplog.text


def test_a_value_already_under_the_new_key_wins():
    """Reconfigured after the upgrade, then an old key turns up: keep the new one."""
    entry = _entry(
        {
            "kostal_min_soc_entity": "number.old",
            CONF_MIN_SOC_ENTITY: "number.chosen_after_the_upgrade",
        },
        {},
    )
    hass = _hass_that_stores(entry)

    _migrate_entry_data(hass, entry)

    assert entry.data[CONF_MIN_SOC_ENTITY] == "number.chosen_after_the_upgrade"
    assert "kostal_min_soc_entity" not in entry.data


def test_an_entry_without_the_old_keys_is_left_alone():
    entry = _entry(
        {CONF_MIN_SOC_ENTITY: "number.min_soc", CONF_ACTIVE_RANGE_YEARLY: True}, {}
    )
    hass = _hass_that_stores(entry)

    _migrate_entry_data(hass, entry)

    assert entry.data == {
        CONF_MIN_SOC_ENTITY: "number.min_soc",
        CONF_ACTIVE_RANGE_YEARLY: True,
    }
    hass.config_entries.async_update_entry.assert_not_called()


def test_an_entry_with_dates_keeps_them_absolute():
    """A range nobody re-enters must not come back next winter on its own."""
    entry = _entry(
        {
            CONF_MIN_SOC_ENTITY: "number.min_soc",
            CONF_ACTIVE_START_DATE: "2026-11-01",
            CONF_ACTIVE_END_DATE: "2027-03-31",
        },
        {},
    )
    hass = _hass_that_stores(entry)

    _migrate_entry_data(hass, entry)

    assert entry.data[CONF_ACTIVE_RANGE_YEARLY] is False


def test_an_entry_without_dates_gets_the_new_default():
    """Nothing is restricted, so nothing changes - but a range entered later
    behaves the way the field describes it."""
    entry = _entry({CONF_MIN_SOC_ENTITY: "number.min_soc"}, {})
    hass = _hass_that_stores(entry)

    _migrate_entry_data(hass, entry)

    assert entry.data[CONF_ACTIVE_RANGE_YEARLY] is True
