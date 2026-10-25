"""How the efficiency search measures (plan 017).

The night of 24/25 October 2026: fourteen trials at 1000 W, every one a loss of
200 %, and the search never got to a second power. KostalKore's "Grid to
Battery Power" is the inverter's own split of the battery's DC power, so
against the battery power - which counts charging as negative - the ratio was
exactly -1. Nothing about it was a measurement of the charger.

What the charger takes on the AC side is what the meter at the connection
point sees, less what the house uses: grid import minus house consumption.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import patch

import pytest

from custom_components.inverter_charge_night import config_flow as flow_module
from custom_components.inverter_charge_night.const import (
    AUTO_TEST_SETTLE_S,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_POWER_HOUSE_ENTITY,
    CONF_CHARGE_POWER_RECEIVED_ENTITY,
    CONF_CHARGE_POWER_SENT_ENTITY,
    CONF_GRID_IMPORT_ENTITY,
    CONF_MAIN_FUSE_A,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
)

from .test_config_flow import entity_registry  # noqa: F401 - fixture
from .test_efficiency_finder import (
    AC_LIMIT,
    CONFIG,
    NOW,
    RECEIVED,
    SENT,
    START,
    _make,
    _measure,
)

GRID = "sensor.grid_power"
HOUSE = "sensor.home_power"
ISSUE = "custom_components.inverter_charge_night.coordinator.ir"

DERIVED = {
    CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
    CONF_GRID_IMPORT_ENTITY: GRID,
    CONF_CHARGE_POWER_HOUSE_ENTITY: HOUSE,
    CONF_CHARGE_POWER_RECEIVED_ENTITY: RECEIVED,
    CONF_MIN_CHARGE_POWER_W: 1000,
    CONF_MAX_CHARGE_POWER_W: 20000,
}


def _derived(hass):
    hass.states.async_set("sun.sun", "below_horizon")
    hass.states.async_set(AC_LIMIT, "1000", {"unit_of_measurement": "W"})
    for entity in (GRID, HOUSE, RECEIVED):
        hass.states.async_set(entity, "0", {"unit_of_measurement": "W"})
    return _make(hass, DERIVED)


def _measure_derived(coordinator, hass, *, grid_w, house_w, battery_w, minutes=20):
    """A trial at 1000 W with the AC side taken from grid import and house load."""
    hass.states.async_set(AC_LIMIT, "1000", {"unit_of_measurement": "W"})
    coordinator._auto_test_active = True
    coordinator._auto_test_power_w = 1000
    coordinator._auto_test_start = START
    coordinator._auto_measure_start = None
    coordinator._auto_last_sample_time = None

    def _set():
        hass.states.async_set(GRID, str(grid_w), {"unit_of_measurement": "W"})
        hass.states.async_set(HOUSE, str(house_w), {"unit_of_measurement": "W"})
        hass.states.async_set(RECEIVED, str(battery_w), {"unit_of_measurement": "W"})

    _set()
    now = START + timedelta(seconds=AUTO_TEST_SETTLE_S + 1)
    with patch(NOW, return_value=now):
        coordinator._accumulate_auto_energy()
    for _ in range(minutes * 2):
        now += timedelta(seconds=30)
        _set()
        with patch(NOW, return_value=now):
            coordinator._accumulate_auto_energy()
    return now


# --- The AC side from grid import minus house consumption ---------------------


def test_the_ac_side_is_grid_import_minus_house_consumption(mock_hass):
    """Pascal's numbers: about 1570 W from the grid, 495 W in the house, 1000 W in the battery."""
    coordinator = _derived(mock_hass)

    end = _measure_derived(coordinator, mock_hass, grid_w=1575, house_w=495, battery_w=1000)
    with patch(NOW, return_value=end):
        coordinator._finalize_auto_test()

    loss = coordinator.get_auto_efficiency_data()["history"]["1000"]
    assert loss == pytest.approx(1 - 1000 / 1080)


def test_a_missing_house_reading_is_no_reading(mock_hass):
    coordinator = _derived(mock_hass)
    mock_hass.states.async_set(GRID, "1500", {"unit_of_measurement": "W"})
    mock_hass.states.async_set(HOUSE, "unavailable")

    assert coordinator._charge_input_w() is None


def test_house_consumption_without_grid_import_is_no_ac_side(mock_hass):
    """Only the pair is a measurement; the sent sensor is the fallback then."""
    coordinator = _make(mock_hass, {**CONFIG, CONF_CHARGE_POWER_HOUSE_ENTITY: HOUSE})

    assert coordinator._charge_input_entities() == (SENT,)


def test_the_sampling_listens_to_all_three_entities(mock_hass):
    coordinator = _derived(mock_hass)
    with patch(
        "custom_components.inverter_charge_night.coordinator.async_track_state_change_event"
    ) as track:
        coordinator._setup_auto_sample_listener()

    assert set(track.call_args.args[1]) == {GRID, HOUSE, RECEIVED}


def test_the_measured_losses_of_a_direct_sensor_are_kept(mock_hass):
    """The signature only grows for the new path, so nothing measured is lost."""
    direct = _make(mock_hass, CONFIG)
    before = direct._efficiency_signature()
    with_unused_house = _make(mock_hass, {**CONFIG, CONF_CHARGE_POWER_HOUSE_ENTITY: HOUSE})
    derived = _derived(mock_hass)

    assert with_unused_house._efficiency_signature() == before
    assert derived._efficiency_signature() != before
    # Another house sensor is another measurement path, even with no sent sensor
    other_house = _make(mock_hass, {**DERIVED, CONF_CHARGE_POWER_HOUSE_ENTITY: "sensor.other"})
    assert other_house._efficiency_signature() != derived._efficiency_signature()


def _fresh(hass, entity_id, value):
    hass.states.async_set(entity_id, value, {"unit_of_measurement": "W"})
    state = hass.states.get(entity_id)
    state.last_reported = state.last_updated = state.last_changed = START


def test_the_house_connection_limit_credits_the_derived_draw(mock_hass):
    """With no sent sensor, the own draw used to count as foreign load."""
    coordinator = _derived(mock_hass)
    coordinator._planned_setpoint_written_w = 3000
    _fresh(mock_hass, GRID, "2600")
    _fresh(mock_hass, HOUSE, "600")

    with patch(NOW, return_value=START):
        assert coordinator._own_charge_draw_w() == 2000
        _fresh(mock_hass, HOUSE, "unavailable")
        assert coordinator._own_charge_draw_w() == 0.0


# --- A sensor that counts charging as negative --------------------------------


def test_a_battery_sensor_that_counts_charging_negative_is_turned(mock_hass, caplog):
    caplog.set_level(logging.INFO)
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    coordinator = _make(mock_hass, CONFIG)

    end = _measure(coordinator, mock_hass, power_w=6000,
                   samples=[(6000, -5520), (6000, -5520)], minutes=20)
    with patch(NOW, return_value=end):
        coordinator._finalize_auto_test()

    assert coordinator.get_auto_efficiency_data()["history"]["6000"] == pytest.approx(1 - 5520 / 6000)
    assert "counts charging as negative" in caplog.text


def test_a_battery_that_discharges_briefly_is_not_turned(mock_hass):
    """Below a quarter of the trial's power it is a dip, not a sign convention."""
    coordinator = _derived(mock_hass)
    coordinator._auto_test_power_w = 1000

    assert coordinator._charge_orientation(-200.0, "received") == 1.0
    assert coordinator._charge_orientation(-900.0, "received") == -1.0


def test_grid_import_minus_house_is_never_turned(mock_hass):
    coordinator = _derived(mock_hass)
    coordinator._auto_test_power_w = 1000

    assert coordinator._charge_orientation(-900.0, "sent") == 1.0


# --- The same side twice, and stopping instead of repeating it ----------------


def _same_side_trial(coordinator, hass):
    """KostalKore's Grid to Battery Power against its Battery Power: exactly -1."""
    end = _measure(coordinator, hass, power_w=6000,
                   samples=[(6000, -6000), (6000, -6000)], minutes=20)
    with patch(NOW, return_value=end):
        coordinator._finalize_auto_test()


def test_the_same_power_twice_is_refused_and_then_ends_the_search(mock_hass, caplog):
    caplog.set_level(logging.INFO)
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    coordinator = _make(mock_hass, CONFIG)

    with patch(ISSUE) as ir:
        _same_side_trial(coordinator, mock_hass)
        with patch(NOW, return_value=START):
            assert coordinator._auto_search_stopped() is None
        ir.async_create_issue.assert_not_called()

        _same_side_trial(coordinator, mock_hass)

    assert coordinator.get_auto_efficiency_data()["history"] == {}
    with patch(NOW, return_value=START):
        assert coordinator._auto_search_stopped() == "sensors_implausible"
    issue = ir.async_create_issue.call_args
    assert issue.args[2] == "efficiency_sensors_entry_1"
    assert issue.kwargs["translation_key"] == "efficiency_sensors_implausible"
    assert issue.kwargs["translation_placeholders"]["ratio"] == "1.000"
    assert issue.kwargs["translation_placeholders"]["ac_side"] == SENT
    assert "see Repairs" in caplog.text


def test_a_loss_below_half_a_percent_is_refused(mock_hass):
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    coordinator = _make(mock_hass, CONFIG)

    end = _measure(coordinator, mock_hass, power_w=6000,
                   samples=[(6000, 5985), (6000, 5985)], minutes=20)
    with patch(NOW, return_value=end):
        coordinator._finalize_auto_test()

    assert coordinator.get_auto_efficiency_data()["history"] == {}
    assert coordinator._auto_implausible_trials is not None


def test_a_plausible_trial_clears_the_warning(mock_hass):
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    coordinator = _make(mock_hass, CONFIG)
    with patch(ISSUE):
        _same_side_trial(coordinator, mock_hass)

    with patch(ISSUE) as ir:
        end = _measure(coordinator, mock_hass, power_w=6000,
                       samples=[(6000, 5520), (6000, 5520)], minutes=20)
        with patch(NOW, return_value=end):
            coordinator._finalize_auto_test()

    assert coordinator._auto_implausible_trials is None
    ir.async_delete_issue.assert_called_once()
    assert ir.async_delete_issue.call_args.args[2] == "efficiency_sensors_entry_1"


def test_new_sensors_clear_the_stop_and_the_warning(mock_hass):
    mock_hass.states.async_set(AC_LIMIT, "6000", {"unit_of_measurement": "W"})
    coordinator = _make(mock_hass, CONFIG)
    with patch(ISSUE):
        _same_side_trial(coordinator, mock_hass)
        _same_side_trial(coordinator, mock_hass)
    previous = coordinator._efficiency_signature()

    coordinator.config = {**coordinator.config, CONF_CHARGE_POWER_SENT_ENTITY: None,
                          CONF_GRID_IMPORT_ENTITY: GRID, CONF_CHARGE_POWER_HOUSE_ENTITY: HOUSE}
    with patch(ISSUE) as ir, patch(NOW, return_value=START):
        coordinator.forget_auto_search_after_rebinding(previous)
        assert coordinator._auto_search_stopped() is None
    assert coordinator._auto_implausible_trials is None
    ir.async_delete_issue.assert_called_once()


# --- The setup ----------------------------------------------------------------


def _validate(hass, values):
    return flow_module._validate_user_input(values, hass)


def test_the_setup_accepts_grid_import_and_house_consumption(mock_hass, entity_registry):
    errors = _validate(mock_hass, {CONF_AUTO_EFFICIENT_CHARGE: True, **DERIVED})

    assert not {CONF_CHARGE_POWER_SENT_ENTITY, CONF_CHARGE_POWER_HOUSE_ENTITY,
                CONF_GRID_IMPORT_ENTITY, CONF_MAIN_FUSE_A} & set(errors)


def test_the_setup_asks_for_one_ac_side(mock_hass, entity_registry):
    values = {CONF_AUTO_EFFICIENT_CHARGE: True, CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
              CONF_CHARGE_POWER_RECEIVED_ENTITY: RECEIVED}

    assert _validate(mock_hass, values)[CONF_CHARGE_POWER_SENT_ENTITY] == "ac_side_required"


def test_the_setup_refuses_both_ac_sides(mock_hass, entity_registry):
    errors = _validate(mock_hass, {**DERIVED, CONF_CHARGE_POWER_SENT_ENTITY: SENT})

    assert errors[CONF_CHARGE_POWER_HOUSE_ENTITY] == "ac_side_twice"


def test_house_consumption_needs_grid_import(mock_hass, entity_registry):
    values = {key: value for key, value in DERIVED.items() if key != CONF_GRID_IMPORT_ENTITY}

    assert _validate(mock_hass, values)[CONF_GRID_IMPORT_ENTITY] == "required_entity"


def test_house_consumption_must_be_its_own_sensor(mock_hass, entity_registry):
    errors = _validate(mock_hass, {**DERIVED, CONF_CHARGE_POWER_HOUSE_ENTITY: RECEIVED})

    assert errors[CONF_CHARGE_POWER_HOUSE_ENTITY] == "separate_power_measurements"


def test_grid_import_without_house_still_needs_a_budget(mock_hass, entity_registry):
    """The house connection limit's own rule is unchanged."""
    errors = _validate(mock_hass, {CONF_GRID_IMPORT_ENTITY: GRID, CONF_CHARGE_POWER_ENTITY: AC_LIMIT})

    assert errors[CONF_MAIN_FUSE_A] == "required_value"


# --- Codex on #45 ---------------------------------------------------------------


@pytest.fixture
def guide(mock_hass, entity_registry, draft_store):  # noqa: F811
    from .test_config_flow import _config_flow

    return _config_flow(mock_hass)


@pytest.mark.asyncio
async def test_disabling_the_limit_keeps_the_meter_the_measurement_reads(guide):
    await guide.async_step_user()
    guide._data.update({**DERIVED, CONF_MAIN_FUSE_A: 63, "grid_max_continuous_w": 40000})

    await guide.async_step_grid_off()

    assert guide._data[CONF_GRID_IMPORT_ENTITY] == GRID
    assert CONF_MAIN_FUSE_A not in guide._data
    assert "grid_max_continuous_w" not in guide._data
    assert not flow_module._grid_limit_set(guide._data)


def test_a_meter_only_for_the_measurement_needs_no_charge_control(mock_hass, entity_registry):
    """CodeRabbit on #45: it writes nothing, so nothing to write to is missing."""
    values = {CONF_GRID_IMPORT_ENTITY: GRID, CONF_CHARGE_POWER_HOUSE_ENTITY: HOUSE}

    assert CONF_CHARGE_POWER_ENTITY not in _validate(mock_hass, values)
    assert _validate(mock_hass, {**values, CONF_MAIN_FUSE_A: 63})[CONF_CHARGE_POWER_ENTITY] == (
        "required_entity"
    )


@pytest.mark.asyncio
async def test_charging_can_be_turned_off_after_the_limit_and_the_search(guide, mock_hass):
    await guide.async_step_user()
    guide._data.update({CONF_AUTO_EFFICIENT_CHARGE: True, **DERIVED, CONF_MAIN_FUSE_A: 63})
    await guide.async_step_grid_off()
    await guide.async_step_efficiency_off()

    await guide.async_step_charging_off()

    assert CONF_CHARGE_POWER_ENTITY not in guide._data
    assert CONF_CHARGE_POWER_ENTITY not in _validate(mock_hass, guide._data)


@pytest.mark.asyncio
async def test_without_house_consumption_the_limit_takes_the_meter_along(guide):
    await guide.async_step_user()
    guide._data.update({CONF_GRID_IMPORT_ENTITY: GRID, CONF_MAIN_FUSE_A: 63})

    await guide.async_step_grid_off()

    assert CONF_GRID_IMPORT_ENTITY not in guide._data


def test_a_meter_read_only_for_the_measurement_stops_no_charging(mock_hass):
    """Lost, it costs a trial - not the night (no grid_meter fault without a budget)."""
    coordinator = _derived(mock_hass)
    mock_hass.states.async_set(GRID, "unavailable")

    assert "grid_meter" not in coordinator._communication_faults()

    limited = _make(mock_hass, {**DERIVED, CONF_MAIN_FUSE_A: 63})
    assert "grid_meter" in limited._communication_faults()


@pytest.mark.asyncio
async def test_the_diagnostics_show_the_derived_ac_side(mock_hass):
    """CodeRabbit on #45: with no sent sensor the AC power read None."""
    from custom_components.inverter_charge_night.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    coordinator = _derived(mock_hass)
    coordinator.entry.runtime_data = coordinator
    mock_hass.states.async_set(GRID, "1575", {"unit_of_measurement": "W"})
    mock_hass.states.async_set(HOUSE, "495", {"unit_of_measurement": "W"})

    report = await async_get_config_entry_diagnostics(mock_hass, coordinator.entry)

    control = report["state"]["charge_control"]
    assert control["ac_charge_power_w"] == 1080
    assert control["ac_side_derived"] is True
    assert control["input_configured"]["charge_power_house_entity"] is True
    assert control["input_configured"]["grid_import_entity"] is True
