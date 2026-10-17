"""A completed tariff window stays completed across clock rollback and reboot."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.inverter_charge_night.const import CONF_RUNTIME_STATE
from tests.test_calendar_fault_campaign import BERLIN, rig
from tests.test_grid_limit import GRID, _make_coordinator


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_completed_window_cannot_reopen_after_clock_rollback(rig, restart):
    hass, coordinator, clock = rig
    coordinator.is_active = True
    coordinator.original_min_soc = 8
    coordinator._window_started_at = datetime(2026, 1, 14, 23, tzinfo=BERLIN)
    clock.now = datetime(2026, 1, 15, 5, tzinfo=BERLIN)
    hass.states.async_set(GRID, "on")
    await coordinator._on_window_end(clock.now)
    assert any(call.args[:2] == ("switch", "turn_off")
               for call in hass.services.async_call.await_args_list)
    saved = hass.config_entries.async_update_entry.call_args.kwargs["options"][CONF_RUNTIME_STATE]
    assert saved["last_scheduled_end"] == clock.now.isoformat()
    clock.now -= timedelta(hours=1)
    if restart:
        coordinator = _make_coordinator(hass, coordinator.config, {CONF_RUNTIME_STATE: saved})
    coordinator._on_window_start = AsyncMock()
    await coordinator._check_current_window()
    assert coordinator.is_active is False
    coordinator._on_window_start.assert_not_awaited()
    clock.now += timedelta(days=1)
    assert coordinator._scheduled_window_is_open(clock.now) is True


@pytest.mark.parametrize("stored", ["invalid-date", 123])
def test_invalid_completed_window_metadata_is_ignored(rig, stored):
    hass, coordinator, _ = rig
    restored = _make_coordinator(hass, coordinator.config,
                                {CONF_RUNTIME_STATE: {"last_scheduled_end": stored}})
    assert restored._last_scheduled_end is None


def test_restart_restores_a_deadline_in_the_second_fold(rig):
    hass, coordinator, clock = rig
    clock.now = datetime(2026, 10, 25, 2, 45, tzinfo=BERLIN, fold=0)
    deadline = datetime(2026, 10, 25, 2, 15, tzinfo=BERLIN, fold=1)
    previous_zone = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(BERLIN)
    try:
        restored = _make_coordinator(hass, coordinator.config, {CONF_RUNTIME_STATE: {
            "adhoc_until": deadline.isoformat(), "adhoc_target_soc": 70,
            "adhoc_allow_grid_charge": True,
        }})
        assert restored._adhoc_until is not None
        assert restored._adhoc_until.fold == 1
    finally:
        dt_util.set_default_time_zone(previous_zone)
