"""Independent UTC/calendar oracles and adversarial control inputs.

The clock belongs to these tests, never to the host or the interactive lab.
Electrical uncertainty must reduce permission to charge, not create headroom.
"""
from __future__ import annotations

import calendar
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.inverter_charge_night.const import (
    CONF_ACTIVE_END_DATE, CONF_ACTIVE_RANGE_YEARLY, CONF_ACTIVE_START_DATE,
    CONF_BACKUP_MODE_ENTITY, CONF_CHARGE_POWER_SENT_ENTITY, CONF_END_TIME,
    CONF_START_TIME,
    CONF_MAIN_FUSE_A,
)
from tests.test_grid_limit import (
    AC_LIMIT, BACKUP, BATTERY, GRID, GRID_IMPORT, LIMITED_CONFIG, NOW, SENT,
    _make_coordinator, _register,
)

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def rig(mock_hass):
    clock = SimpleNamespace(now=datetime(2026, 1, 15, 2, tzinfo=BERLIN))
    with patch(NOW, side_effect=lambda: clock.now):
        _register(mock_hass)
        coordinator = _make_coordinator(mock_hass, {
            **LIMITED_CONFIG, CONF_START_TIME: "23:00", CONF_END_TIME: "05:00",
        })
        coordinator._start_periodic_verification = AsyncMock()
        coordinator._stop_periodic_verification = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        state = mock_hass.states.get(GRID_IMPORT)
        state.last_reported = state.last_updated = state.last_changed = clock.now
        yield mock_hass, coordinator, clock


@pytest.mark.asyncio
@pytest.mark.parametrize("now,hours", [
    (datetime(2026, 3, 28, 23, tzinfo=BERLIN), 5),
    (datetime(2026, 3, 29, 1, tzinfo=BERLIN), 3),
    (datetime(2026, 10, 24, 23, tzinfo=BERLIN), 7),
    (datetime(2026, 10, 25, 1, tzinfo=BERLIN), 5),
    (datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=0), 3.5),
    (datetime(2026, 10, 25, 2, 30, tzinfo=BERLIN, fold=1), 2.5),
])
async def test_charge_power_uses_real_elapsed_hours_across_dst(rig, now, hours):
    hass, coordinator, clock = rig
    clock.now = now
    coordinator.config.pop("grid_import_entity")  # Test the time calculation alone.
    with patch(
        "custom_components.inverter_charge_night.coordinator.required_charge_power_w",
        return_value=1500,
    ) as required:
        await coordinator._plan_charge_power(80)
    assert required.call_args.args[3] == pytest.approx(hours)
    # Observe the resulting actual command as well as the planner input.
    assert any(call.args[2].get("entity_id") == AC_LIMIT
               and call.args[2].get("value") == 1500
               for call in hass.services.async_call.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("day", [(2026, 3, 29), (2026, 10, 25)])
async def test_ad_hoc_duration_is_elapsed_time_not_wall_time(rig, day):
    _, coordinator, clock = rig
    clock.now = datetime(*day, 1, 30, tzinfo=BERLIN)
    coordinator.async_open_adhoc_window = AsyncMock(return_value=True)
    await coordinator.async_charge_to(70, timedelta(hours=2))
    until = coordinator.async_open_adhoc_window.call_args.kwargs["until"]
    assert (until.astimezone(UTC) - clock.now.astimezone(UTC)).total_seconds() == 7200


@pytest.mark.asyncio
async def test_future_deadline_in_second_fold_is_not_mistaken_for_expired(rig):
    _, coordinator, clock = rig
    clock.now = datetime(2026, 10, 25, 2, 45, tzinfo=BERLIN, fold=0)
    until = datetime(2026, 10, 25, 2, 15, tzinfo=BERLIN, fold=1)
    coordinator._on_window_start = AsyncMock(side_effect=lambda now: setattr(coordinator, "is_active", True))
    assert await coordinator.async_open_adhoc_window(
        target_soc=70, until=until, reason="fold-test", allow_grid_charge=True,
    ) is True


@pytest.mark.asyncio
async def test_second_fold_does_not_reopen_an_already_ended_tariff_window(rig):
    _, coordinator, clock = rig
    coordinator.config.update({CONF_START_TIME: "00:00", CONF_END_TIME: "02:30"})
    clock.now = datetime(2026, 10, 25, 2, 15, tzinfo=BERLIN, fold=1)
    # The first 02:30 deadline is already 45 real minutes in the past.
    coordinator._on_window_start = AsyncMock()
    await coordinator._check_current_window()
    assert coordinator.is_active is False
    assert coordinator._on_window_start.await_count == 0


@pytest.mark.parametrize("reading,age_s", [
    ("unavailable", 0), ("unknown", 0), ("nan", 0), ("inf", 0),
    ("34000", 301), ("1000", -3600),
])
def test_untrusted_grid_meter_permits_zero_charging(rig, reading, age_s):
    hass, coordinator, clock = rig
    hass.states.async_set(GRID_IMPORT, reading, {"unit_of_measurement": "W"})
    state = hass.states.get(GRID_IMPORT)
    state.last_reported = state.last_updated = state.last_changed = clock.now - timedelta(seconds=age_s)
    coordinator.is_active = True
    coordinator._planned_setpoint_written_w = 5000
    assert coordinator._grid_limited_setpoint(5000) == 0


@pytest.mark.parametrize("reading", [None, "unavailable", "nan", "0"])
def test_missing_or_failed_own_draw_never_hides_foreign_load(rig, reading):
    hass, coordinator, clock = rig
    coordinator.config[CONF_CHARGE_POWER_SENT_ENTITY] = SENT
    if reading is not None:
        hass.states.async_set(SENT, reading, {"unit_of_measurement": "W"})
        hass.states.get(SENT).last_reported = clock.now
    # The inverter accepted a 5 kW limit but draws nothing. The house alone
    # consumes 34 kW. Crediting the limit as consumption invents 5 kW room.
    hass.states.get(GRID_IMPORT).state = "34000"
    coordinator.is_active = True
    coordinator._planned_setpoint_written_w = 5000
    assert coordinator._grid_limited_setpoint(5000) == 0


@pytest.mark.parametrize("status", [None, "unavailable", "unknown", "future-firmware-state"])
@pytest.mark.asyncio
async def test_unconfirmed_island_contact_cannot_start_a_charge_window(rig, status):
    hass, coordinator, clock = rig
    coordinator.config[CONF_BACKUP_MODE_ENTITY] = BACKUP
    if status is not None:
        hass.states.async_set(BACKUP, status)
    await coordinator._on_window_start(clock.now)
    assert coordinator.is_active is False
    assert coordinator.original_min_soc is None


@pytest.mark.asyncio
async def test_island_transition_between_commands_cannot_enable_grid_charge(rig):
    hass, coordinator, clock = rig
    coordinator.config[CONF_BACKUP_MODE_ENTITY] = BACKUP
    hass.states.async_set(BACKUP, "off")
    coordinator.is_active = True
    commands = []

    async def command(domain, service, data, **kwargs):
        commands.append((domain, service, data))
        if domain == "number" and data.get("entity_id") != AC_LIMIT:
            hass.states.async_set(BACKUP, "on")

    hass.services.async_call.side_effect = command
    await coordinator._control_charge(80)
    assert any(domain == "number" for domain, _, _ in commands)
    assert not any(domain == "switch" and service == "turn_on"
                   and data.get("entity_id") == GRID for domain, service, data in commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_soc", ["nan", "inf", "-1", "101", "unavailable"])
async def test_losing_battery_soc_stops_an_already_running_charge(rig, bad_soc):
    hass, coordinator, _ = rig
    coordinator.is_active = True
    coordinator.initial_calculated_soc = 80
    hass.states.async_set(GRID, "on")
    hass.states.async_set(BATTERY, bad_soc)
    await coordinator._async_update_data()
    assert any(call.args[:2] == ("switch", "turn_off")
               and call.args[2].get("entity_id") == GRID
               for call in hass.services.async_call.await_args_list)


@pytest.mark.asyncio
async def test_a_failed_emergency_limit_write_requests_an_independent_stop(rig):
    hass, coordinator, _ = rig
    coordinator.is_active = True
    coordinator.planned_charge_power_w = 5000
    coordinator._planned_setpoint_written_w = 5000
    hass.states.get(GRID_IMPORT).state = "45000"
    hass.states.async_set(GRID, "on")

    async def reject_limit(domain, service, data, **kwargs):
        if domain == "number" and data.get("entity_id") == AC_LIMIT:
            raise RuntimeError("device rejects protective limit")

    hass.services.async_call.side_effect = reject_limit
    await coordinator._react_to_grid_import()
    calls = hass.services.async_call.await_args_list
    assert any(call.args[0] == "number" and call.args[2].get("value") == 0 for call in calls)
    assert any(call.args[:2] == ("switch", "turn_off") for call in calls)


@pytest.mark.asyncio
async def test_readback_above_a_protective_limit_pauses_charging(rig):
    hass, coordinator, _ = rig
    coordinator.is_active = True
    coordinator._planned_setpoint_written_w = 500
    hass.states.async_set(GRID, "on")
    hass.states.get(AC_LIMIT).state = "6000"
    await coordinator._verify_ac_charge_limit()
    assert any(call.args[:2] == ("switch", "turn_off")
               for call in hass.services.async_call.await_args_list)


@pytest.mark.asyncio
async def test_accepted_but_ignored_limit_cannot_authorise_grid_activation(rig):
    hass, coordinator, _ = rig
    coordinator.is_active = True
    hass.states.get(GRID_IMPORT).state = "33000"
    # The service returns successfully; its device state retains the old 6 kW.
    await coordinator._control_charge(80)
    calls = hass.services.async_call.await_args_list
    assert any(call.args[0] == "number" and call.args[2].get("entity_id") == AC_LIMIT for call in calls)
    assert not any(call.args[:2] == ("switch", "turn_on") for call in calls)


@pytest.mark.asyncio
async def test_a_confirmed_limit_precedes_the_grid_activation(rig):
    hass, coordinator, _ = rig
    coordinator.is_active = True
    hass.states.get(GRID_IMPORT).state = "33000"
    seen = []

    async def device(domain, service, data, **kwargs):
        seen.append((domain, service, data))
        if domain == "number":
            hass.states.async_set(data["entity_id"], str(data["value"]), {"unit_of_measurement": "W"})

    hass.services.async_call.side_effect = device
    await coordinator._control_charge(80)
    cap_at = next(index for index, (_, _, data) in enumerate(seen) if data.get("entity_id") == AC_LIMIT)
    on_at = next(index for index, (domain, service, _) in enumerate(seen) if (domain, service) == ("switch", "turn_on"))
    assert cap_at < on_at
    assert float(hass.states.get(AC_LIMIT).state) <= 34776 - 500 - 33000


@pytest.mark.parametrize("bad_fuse", ["nan", "inf", "broken", -1, 0])
def test_corrupt_configured_fuse_never_disables_the_connection_guard(rig, bad_fuse):
    _, coordinator, _ = rig
    coordinator.config[CONF_MAIN_FUSE_A] = bad_fuse
    coordinator.is_active = True
    assert coordinator._grid_limited_setpoint(5000) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["deadline", "disabled", "inactive", "ending", "reconfiguring", "target-reached"])
async def test_permission_revoked_during_min_soc_write_cannot_start_charge(rig, change):
    hass, coordinator, clock = rig
    coordinator.is_active = True
    coordinator.config.pop("grid_import_entity")

    async def device(domain, service, data, **kwargs):
        if domain != "number":
            return
        if change == "deadline":
            clock.now = clock.now.replace(hour=5)
        elif change == "disabled":
            coordinator.is_enabled = False
        elif change == "inactive":
            coordinator.is_active = False
        elif change == "ending":
            coordinator._ending = True
        elif change == "reconfiguring":
            coordinator._reconfiguring = True
        else:
            hass.states.async_set(BATTERY, "80")

    hass.services.async_call.side_effect = device
    await coordinator._control_charge(80)
    calls = hass.services.async_call.await_args_list
    assert any(call.args[:2] == ("number", "set_value") for call in calls)
    assert not any(call.args[:2] == ("switch", "turn_on") for call in calls)


@pytest.mark.asyncio
async def test_permission_revoked_during_protective_write_stops_existing_charge(rig):
    hass, coordinator, _ = rig
    coordinator.is_active = True
    hass.states.get(GRID_IMPORT).state = "33000"
    hass.states.async_set(GRID, "on")

    async def device(domain, service, data, **kwargs):
        if domain == "number" and data.get("entity_id") == AC_LIMIT:
            hass.states.async_set(AC_LIMIT, str(data["value"]), {"unit_of_measurement": "W"})
            coordinator.is_enabled = False

    hass.services.async_call.side_effect = device
    assert await coordinator._grid_charge_activation_safe(80) is False
    assert any(call.args[:2] == ("switch", "turn_off")
               for call in hass.services.async_call.await_args_list)


def test_meter_age_uses_elapsed_time_between_two_identical_wall_times(rig):
    hass, coordinator, clock = rig
    clock.now = datetime(2026, 10, 25, 2, 15, tzinfo=BERLIN, fold=1)
    hass.states.get(GRID_IMPORT).last_reported = clock.now.replace(fold=0)
    assert coordinator._read_grid_import_w(GRID_IMPORT) is None


def test_backward_clock_cannot_leave_safety_write_debounced(rig):
    _, coordinator, clock = rig
    coordinator._grid_limit_last_write = clock.now + timedelta(hours=1)
    assert coordinator._grid_write_is_debounced() is False


@pytest.mark.parametrize("seed", range(32))
def test_random_load_and_telemetry_combinations_obey_physical_headroom(rig, seed):
    hass, coordinator, clock = rig
    rng = random.Random(seed)
    coordinator.is_active = True
    for step in range(128):
        foreign_load = rng.uniform(0, 55000)
        actual_draw = rng.uniform(0, 12000)
        previous_limit = rng.uniform(0, 10000)
        request = rng.uniform(0, 10000)
        telemetry = rng.choice(["fresh", "absent", "stale", "future", "nan", "bad-unit", "no-sensor"])
        coordinator._planned_setpoint_written_w = previous_limit
        coordinator.config[CONF_CHARGE_POWER_SENT_ENTITY] = SENT
        hass.states.async_set(GRID_IMPORT, str(foreign_load + actual_draw), {"unit_of_measurement": "W"})
        hass.states.get(GRID_IMPORT).last_reported = clock.now
        if telemetry == "no-sensor":
            coordinator.config.pop(CONF_CHARGE_POWER_SENT_ENTITY)
        else:
            value = "unavailable" if telemetry == "absent" else "nan" if telemetry == "nan" else str(actual_draw)
            unit = "A" if telemetry == "bad-unit" else "W"
            hass.states.async_set(SENT, value, {"unit_of_measurement": unit})
            offset = -301 if telemetry == "stale" else 3600 if telemetry == "future" else 0
            hass.states.get(SENT).last_reported = clock.now + timedelta(seconds=offset)
        allowed = coordinator._grid_limited_setpoint(request)
        physical_headroom = max(0, 34776 - 500 - foreign_load)
        assert 0 <= allowed <= min(request, physical_headroom) + 1e-7, (seed, step, telemetry)


@pytest.mark.parametrize("year", [2024, 2025, 2028, 2099, 2100, 2200, 2400])
@pytest.mark.parametrize("month,day", [(2, 28), (2, 29), (3, 1), (3, 31), (4, 1), (10, 1), (12, 31)])
def test_winter_season_survives_leap_and_century_years(rig, year, month, day):
    _, coordinator, clock = rig
    if month == 2 and day == 29 and not calendar.isleap(year):
        assert coordinator._parse_date_optional(f"{year}-02-29") is None
        return  # Rejected input, rather than inventing a non-existent date.
    coordinator.config.update({CONF_ACTIVE_START_DATE: "2024-10-01",
                               CONF_ACTIVE_END_DATE: "2025-03-31",
                               CONF_ACTIVE_RANGE_YEARLY: True})
    clock.now = datetime(year, month, day, 2, tzinfo=BERLIN)
    assert coordinator._is_within_date_range() is (month >= 10 or month <= 3)
