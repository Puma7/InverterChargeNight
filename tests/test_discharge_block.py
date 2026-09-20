"""Plan 009: blocking the battery discharge during the window.

Inside the window grid energy is cheap and stored PV is not: a battery that
runs the house at night replaces cheap energy with expensive energy the next
day. Plan 006 added an optional discharge power limit, but no inverter
guarantees such an entity. Raising the inverter's **min SOC** works everywhere,
because a battery does not discharge below its min SOC - and that entity is the
one this integration controls anyway.

The value written to the min SOC entity (``inverter_floor_soc``) and the charge
target (``current_target_soc``) are therefore two different things. These tests
pin down that separation: everything that compares the *written* value uses the
floor, "target reached" keeps using the charge target, and the raised floor
never outlives its window.

The setup mirrors ``test_window_lifecycle.py``: a real coordinator against the
strict ``mock_hass`` fixture, the clock pinned inside the window.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio

from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)
from custom_components.inverter_charge_night.calculation import calculate_required_soc
from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_COMMAND_DELAY,
    CONF_DEFAULT_MIN_SOC,
    CONF_DISCHARGE_BLOCK_MODE,
    CONF_DISCHARGE_BLOCK_SWITCH,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
    CONF_OPERATION_MODE,
    CONF_PV_FORECAST_ENTITY,
    CONF_RUNTIME_STATE,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
    DISCHARGE_BLOCK_AUTO,
    DISCHARGE_BLOCK_OFF,
    DISCHARGE_BLOCK_VIA_LIMIT,
    DISCHARGE_BLOCK_VIA_MIN_SOC,
    DISCHARGE_BLOCK_VIA_SWITCH,
    MODE_MORNING_DISCHARGE,
)

MIN_SOC = "number.min_soc"
GRID = "switch.grid"
BATTERY = "sensor.soc"
PV = "sensor.pv"
BLOCK_SWITCH = "switch.block_discharge"
DISCHARGE_LIMIT = "number.discharge_limit"

CAPACITY_KWH = 10.0
MARGIN_PCT = 10.0
USER_MIN = 8.0
USER_MAX = 100.0
DEFAULT_MIN = 8.0
FORECAST_KWH = 5.0

CONFIG = {
    CONF_MIN_SOC_ENTITY: MIN_SOC,
    CONF_GRID_CHARGE_SWITCH: GRID,
    CONF_BATTERY_SOC_ENTITY: BATTERY,
    CONF_PV_FORECAST_ENTITY: PV,
    CONF_BATTERY_CAPACITY: CAPACITY_KWH,
    CONF_FORECAST_ERROR_MARGIN: MARGIN_PCT,
    CONF_USER_MIN_SOC: USER_MIN,
    CONF_USER_MAX_SOC: USER_MAX,
    CONF_DEFAULT_MIN_SOC: DEFAULT_MIN,
    CONF_START_TIME: "00:00",
    CONF_END_TIME: "05:59",
    CONF_COMMAND_DELAY: 0,
}

WINDOW_START = datetime(2026, 1, 15, 0, 0)
WINDOW_END = datetime(2026, 1, 15, 5, 59)
INSIDE_WINDOW = datetime(2026, 1, 15, 2, 0)

# 5 kWh forecast + 10 % margin -> 5.5 kWh of room needed in a 10 kWh battery
TARGET = calculate_required_soc(FORECAST_KWH, CAPACITY_KWH, MARGIN_PCT, USER_MIN, USER_MAX)
ABOVE_TARGET = 70.0  # the case the block exists for: more in the battery than planned


@pytest.fixture(autouse=True)
def _inside_window():
    """The polling update ends a window it finds itself outside of."""
    with patch(
        "custom_components.inverter_charge_night.coordinator.dt_util.now", return_value=INSIDE_WINDOW
    ):
        yield


def _make(hass, config=None, options=None) -> InverterChargeNightCoordinator:
    hass.async_create_background_task = MagicMock(
        side_effect=lambda coro, name=None, **kwargs: asyncio.ensure_future(coro)
    )
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = config if config is not None else CONFIG
    entry.options = options if options is not None else {}

    def _store(target_entry, options=None, **kwargs):
        if options is not None:
            target_entry.options = options

    hass.config_entries.async_update_entry = MagicMock(side_effect=_store)
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _register(hass, *, min_soc="8", grid="off", battery="40", pv="5"):
    hass.states.async_set(MIN_SOC, min_soc)
    hass.states.async_set(GRID, grid)
    hass.states.async_set(BATTERY, battery)
    hass.states.async_set(PV, pv, {"unit_of_measurement": "kWh"})


def _runtime_state(coordinator) -> dict:
    return coordinator.entry.options[CONF_RUNTIME_STATE]


def _min_soc_writes(hass) -> list[float]:
    return [
        awaited.args[2]["value"]
        for awaited in hass.services.async_call.await_args_list
        if awaited.args[0] == "number" and awaited.args[2]["entity_id"] == MIN_SOC
    ]


@pytest_asyncio.fixture
async def coordinator(mock_hass):
    """Default configuration: no switch, no power limit - the min SOC is the way."""
    _register(mock_hass, battery=str(ABOVE_TARGET))
    made = _make(mock_hass)
    yield made
    await made._stop_periodic_verification()


# 1. Choosing the way ---------------------------------------------------------


def test_switch_wins_over_limit_and_min_soc(mock_hass):
    _register(mock_hass)
    made = _make(
        mock_hass,
        {
            **CONFIG,
            CONF_DISCHARGE_BLOCK_SWITCH: BLOCK_SWITCH,
            CONF_DISCHARGE_LIMIT_ENTITY: DISCHARGE_LIMIT,
        },
    )
    assert made._discharge_block_method() == DISCHARGE_BLOCK_VIA_SWITCH
    assert made.discharge_block_state() == "switch"


def test_limit_wins_over_min_soc(mock_hass):
    _register(mock_hass)
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_LIMIT_ENTITY: DISCHARGE_LIMIT})
    assert made._discharge_block_method() == DISCHARGE_BLOCK_VIA_LIMIT


def test_min_soc_is_the_fallback_without_any_vendor_entity(mock_hass, coordinator):
    assert coordinator._discharge_block_method() == DISCHARGE_BLOCK_VIA_MIN_SOC


def test_mode_off_blocks_nothing(mock_hass):
    _register(mock_hass)
    made = _make(
        mock_hass,
        {
            **CONFIG,
            CONF_DISCHARGE_BLOCK_SWITCH: BLOCK_SWITCH,
            CONF_DISCHARGE_LIMIT_ENTITY: DISCHARGE_LIMIT,
            CONF_DISCHARGE_BLOCK_MODE: DISCHARGE_BLOCK_OFF,
        },
    )
    assert made._discharge_block_method() is None
    assert made.discharge_block_state() == "off"


def test_discharge_window_is_never_blocked(mock_hass):
    """Morning discharge is supposed to empty the battery; a block would be absurd."""
    _register(mock_hass)
    made = _make(mock_hass, {**CONFIG, CONF_OPERATION_MODE: MODE_MORNING_DISCHARGE})
    assert made._discharge_block_method() is None


# 2. The switch ---------------------------------------------------------------


@pytest_asyncio.fixture
async def switched(mock_hass):
    """A configuration with the inverter's own 'block discharge' switch, off."""
    _register(mock_hass, battery=str(ABOVE_TARGET))
    mock_hass.states.async_set(BLOCK_SWITCH, "off")
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_BLOCK_SWITCH: BLOCK_SWITCH})
    yield made
    await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_switch_is_turned_on_and_original_persisted(mock_hass, switched):
    await switched._on_window_start(WINDOW_START)

    assert call("switch", "turn_on", {"entity_id": BLOCK_SWITCH}) in (
        mock_hass.services.async_call.await_args_list
    )
    assert switched._original_discharge_block is False
    assert _runtime_state(switched)["original_discharge_block"] is False
    # The min SOC stays the charge target: the switch already blocks the discharge
    assert switched.inverter_floor_soc() == TARGET


@pytest.mark.asyncio
async def test_window_end_restores_the_switch(mock_hass, switched):
    await switched._on_window_start(WINDOW_START)
    mock_hass.states.async_set(BLOCK_SWITCH, "on")
    mock_hass.services.async_call.reset_mock()

    await switched._on_window_end(WINDOW_END)

    assert call("switch", "turn_off", {"entity_id": BLOCK_SWITCH}) in (
        mock_hass.services.async_call.await_args_list
    )
    assert switched._original_discharge_block is None
    assert _runtime_state(switched)["original_discharge_block"] is None
    assert switched._pending_reset is False


@pytest.mark.asyncio
async def test_switch_already_on_is_left_on_at_the_window_end(mock_hass):
    """A user who blocks the discharge permanently keeps that setting."""
    _register(mock_hass)
    mock_hass.states.async_set(BLOCK_SWITCH, "on")
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_BLOCK_SWITCH: BLOCK_SWITCH})
    try:
        await made._on_window_start(WINDOW_START)
        assert made._original_discharge_block is True
        for awaited in mock_hass.services.async_call.await_args_list:
            assert awaited.args[2]["entity_id"] != BLOCK_SWITCH

        await made._on_window_end(WINDOW_END)

        assert call("switch", "turn_on", {"entity_id": BLOCK_SWITCH}) in (
            mock_hass.services.async_call.await_args_list
        )
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_switch_unavailable_defers_the_block(mock_hass, caplog):
    _register(mock_hass)
    mock_hass.states.async_set(BLOCK_SWITCH, "unavailable")
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_BLOCK_SWITCH: BLOCK_SWITCH})
    made.is_active = True

    await made._apply_discharge_block()

    assert "block deferred" in caplog.text
    assert made._original_discharge_block is None


@pytest.mark.asyncio
async def test_failed_switch_reset_keeps_the_original_for_a_retry(mock_hass, switched):
    await switched._on_window_start(WINDOW_START)
    mock_hass.states.async_set(BLOCK_SWITCH, "unavailable")

    await switched._on_window_end(WINDOW_END)

    assert switched._pending_reset is True
    assert switched._original_discharge_block is False
    assert _runtime_state(switched)["original_discharge_block"] is False


@pytest.mark.asyncio
async def test_switch_with_unsupported_domain_is_refused(mock_hass, caplog):
    _register(mock_hass)
    mock_hass.states.async_set("sensor.block", "off")
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_BLOCK_SWITCH: "sensor.block"})
    made.is_active = True

    await made._apply_discharge_block()
    assert "unsupported domain sensor" in caplog.text
    mock_hass.services.async_call.assert_not_awaited()

    # Nothing can be restored through an unusable entity: the reset is done
    made._original_discharge_block = False
    assert await made._reset_discharge_block_switch() is True
    assert made._original_discharge_block is None


@pytest.mark.asyncio
async def test_switch_service_errors_are_reported(mock_hass, switched, caplog):
    switched.is_active = True
    mock_hass.services.async_call = AsyncMock(side_effect=RuntimeError("modbus timeout"))

    await switched._apply_discharge_block()
    assert "Error blocking discharge" in caplog.text

    switched._original_discharge_block = False
    assert await switched._reset_discharge_block_switch() is False
    assert switched._original_discharge_block is False
    assert "Error resetting discharge block switch" in caplog.text


# 3. The power limit: unchanged behaviour -------------------------------------


@pytest.mark.asyncio
async def test_power_limit_configured_behaves_exactly_as_before(mock_hass):
    """Plan 006's way is still chosen when the entity exists - and the floor is the target."""
    _register(mock_hass, battery=str(ABOVE_TARGET))
    mock_hass.states.async_set(DISCHARGE_LIMIT, "5000", {"unit_of_measurement": "W"})
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_LIMIT_ENTITY: DISCHARGE_LIMIT})
    try:
        await made._on_window_start(WINDOW_START)

        assert made._original_discharge_limit == 5000.0
        assert call("number", "set_value", {"entity_id": DISCHARGE_LIMIT, "value": 0.0}) in (
            mock_hass.services.async_call.await_args_list
        )
        # The min SOC is left at the charge target even though the battery is above it
        assert made.inverter_floor_soc() == TARGET
        assert made._window_floor_soc is None
        assert _min_soc_writes(mock_hass) == []
    finally:
        await made._stop_periodic_verification()


# 4. The min SOC fallback -----------------------------------------------------


@pytest.mark.asyncio
async def test_floor_is_raised_to_the_charge_level_above_the_target(mock_hass, coordinator):
    await coordinator._on_window_start(WINDOW_START)

    assert coordinator.current_target_soc() == TARGET
    assert coordinator.inverter_floor_soc() == ABOVE_TARGET
    assert coordinator._window_floor_soc == ABOVE_TARGET
    # The raised floor reaches the inverter at the window start, not only at the
    # first periodic verification: with the battery above the target the polling
    # update returns early and never calls _control_charge.
    assert _min_soc_writes(mock_hass) == [ABOVE_TARGET]
    # ... and the value found there is what the window end restores
    assert coordinator.original_min_soc == DEFAULT_MIN


@pytest.mark.asyncio
async def test_floor_stays_the_target_when_the_battery_is_below_it(mock_hass):
    """Nothing to protect below the target: the floor is the charge target."""
    _register(mock_hass, battery="20")
    made = _make(mock_hass)
    try:
        await made._on_window_start(WINDOW_START)

        assert made._window_floor_soc == 20.0
        assert made.inverter_floor_soc() == TARGET
        assert _min_soc_writes(mock_hass) == []

        await made._async_update_data()
        assert _min_soc_writes(mock_hass) == [TARGET]
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_floor_never_falls_when_the_charge_level_falls(mock_hass, coordinator):
    await coordinator._on_window_start(WINDOW_START)
    assert coordinator.inverter_floor_soc() == ABOVE_TARGET

    # The block did not take and the battery ran down a little
    mock_hass.states.async_set(BATTERY, "62")
    coordinator._update_window_floor()

    assert coordinator._window_floor_soc == ABOVE_TARGET
    assert coordinator.inverter_floor_soc() == ABOVE_TARGET


@pytest.mark.asyncio
async def test_floor_follows_the_charge_level_upwards(mock_hass, coordinator):
    await coordinator._on_window_start(WINDOW_START)

    mock_hass.states.async_set(BATTERY, "83")
    coordinator._update_window_floor()

    assert coordinator._window_floor_soc == 83.0
    assert coordinator.inverter_floor_soc() == 83.0
    assert _runtime_state(coordinator)["window_floor_soc"] == 83.0


@pytest.mark.asyncio
async def test_an_entry_from_before_the_block_is_told_what_changed(mock_hass, caplog):
    """The default is 'auto', so an upgrade starts raising the min SOC by itself."""
    caplog.set_level(logging.INFO)
    _register(mock_hass, battery=str(ABOVE_TARGET))
    made = _make(mock_hass, {k: v for k, v in CONFIG.items() if k != CONF_DISCHARGE_BLOCK_MODE})
    try:
        await made._on_window_start(WINDOW_START)

        assert "shows a higher min SOC than the charge target" in caplog.text
        # Once per window, not on every poll
        caplog.clear()
        await made._apply_discharge_block(announce=True)
        assert "shows a higher min SOC" not in caplog.text
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_configured_mode_says_nothing_about_an_upgrade(mock_hass, caplog):
    caplog.set_level(logging.INFO)
    _register(mock_hass, battery=str(ABOVE_TARGET))
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_BLOCK_MODE: DISCHARGE_BLOCK_AUTO})
    try:
        await made._on_window_start(WINDOW_START)
        assert "shows a higher min SOC" not in caplog.text
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_the_floor_does_not_follow_our_own_grid_charging(mock_hass, coordinator):
    """Otherwise the floor feeds back on itself and ratchets to the maximum.

    The floor is written to the min SOC, the inverter buys energy up to it and
    overshoots a little, the floor follows - and the next round starts one step
    higher. Over a six-hour window that walks the battery to the user maximum
    at full price, past the planned target and with no room for the next day's
    PV.
    """
    await coordinator._on_window_start(WINDOW_START)
    assert coordinator._window_floor_soc == ABOVE_TARGET

    # The inverter is buying on our order and has overshot the written floor
    mock_hass.states.async_set(GRID, "on")
    for overshoot in ("71", "72", "73"):
        mock_hass.states.async_set(BATTERY, overshoot)
        coordinator._update_window_floor()

    assert coordinator._window_floor_soc == ABOVE_TARGET
    assert coordinator.inverter_floor_soc() == ABOVE_TARGET


@pytest.mark.asyncio
async def test_the_floor_follows_a_rise_we_did_not_order(mock_hass, coordinator):
    """Charge that arrived from somewhere else is exactly what the block protects."""
    await coordinator._on_window_start(WINDOW_START)
    mock_hass.states.async_set(GRID, "off")

    mock_hass.states.async_set(BATTERY, "83")
    coordinator._update_window_floor()

    assert coordinator._window_floor_soc == 83.0


@pytest.mark.asyncio
async def test_without_a_grid_switch_the_charge_target_decides(mock_hass):
    """No switch to read: while the target is not reached the rise is ours."""
    _register(mock_hass, battery=str(ABOVE_TARGET))
    made = _make(mock_hass, {k: v for k, v in CONFIG.items() if k != CONF_GRID_CHARGE_SWITCH})
    try:
        await made._on_window_start(WINDOW_START)
        assert made._window_floor_soc == ABOVE_TARGET

        mock_hass.states.async_set(BATTERY, "80")
        made._update_window_floor()
        assert made._window_floor_soc == ABOVE_TARGET

        made.target_reached = True
        made._update_window_floor()
        assert made._window_floor_soc == 80.0
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_floor_stays_inside_the_user_bounds(mock_hass):
    """A charge level above user_max_soc must not be written to the inverter."""
    _register(mock_hass, battery="95")
    made = _make(mock_hass, {**CONFIG, CONF_USER_MAX_SOC: 60.0})
    try:
        await made._on_window_start(WINDOW_START)
        assert made._window_floor_soc == 95.0
        floor = made.inverter_floor_soc()
        assert floor == 60.0
        assert USER_MIN <= floor <= 60.0
        assert _min_soc_writes(mock_hass) == [60.0]
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_floor_is_the_target_outside_a_window(mock_hass, coordinator):
    coordinator.initial_calculated_soc = TARGET
    coordinator._window_floor_soc = ABOVE_TARGET
    assert coordinator.is_active is False
    assert coordinator.inverter_floor_soc() == TARGET


@pytest.mark.asyncio
async def test_verification_does_not_write_the_raised_floor_back_down(mock_hass, coordinator):
    """The core of plan 009: a check against the charge target would undo the block."""
    await coordinator._on_window_start(WINDOW_START)
    mock_hass.states.async_set(MIN_SOC, str(ABOVE_TARGET))
    mock_hass.services.async_call.reset_mock()

    await coordinator._verify_and_restore_min_soc()

    assert _min_soc_writes(mock_hass) == []

    # Something outside pushed the min SOC back to the charge target: restore the floor
    mock_hass.states.async_set(MIN_SOC, str(TARGET))
    await coordinator._verify_and_restore_min_soc()
    assert _min_soc_writes(mock_hass) == [ABOVE_TARGET]


@pytest.mark.asyncio
async def test_min_soc_listener_measures_against_the_floor(mock_hass, coordinator):
    """Against the charge target the listener would fire on every raised floor."""
    await coordinator._on_window_start(WINDOW_START)
    coordinator._verify_and_restore_min_soc = AsyncMock()

    captured: dict[str, object] = {}

    def _capture(hass, entity_id, callback):
        captured["callback"] = callback
        return MagicMock()

    with patch(
        "custom_components.inverter_charge_night.coordinator.async_track_state_change_event",
        side_effect=_capture,
    ):
        coordinator._setup_inverter_min_soc_listener()
    handler = captured["callback"]

    # The inverter holds the raised floor: nothing to restore
    event = MagicMock()
    event.data = {"new_state": MagicMock(state=str(ABOVE_TARGET))}
    await handler(event)  # type: ignore[operator]
    coordinator._verify_and_restore_min_soc.assert_not_awaited()

    # Someone put the charge target back on the inverter: that is a deviation now
    event.data = {"new_state": MagicMock(state=str(TARGET))}
    await handler(event)  # type: ignore[operator]
    coordinator._verify_and_restore_min_soc.assert_awaited()


@pytest.mark.asyncio
async def test_control_kostal_writes_the_floor_but_charges_to_the_target(mock_hass):
    """Grid charging follows the charge target; only the written min SOC is raised."""
    _register(mock_hass, battery="20")
    made = _make(mock_hass)
    try:
        await made._on_window_start(WINDOW_START)
        # A window that started high and then ran down keeps its floor
        made._window_floor_soc = 60.0
        mock_hass.services.async_call.reset_mock()

        await made._control_charge(TARGET)

        assert mock_hass.services.async_call.await_args_list == [
            call("number", "set_value", {"entity_id": MIN_SOC, "value": 60.0}),
            call("switch", "turn_on", {"entity_id": GRID}),
        ]
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_target_reached_still_follows_the_charge_target(mock_hass, coordinator):
    """70 % in the battery is above the 45 % target - reached, floor or no floor."""
    await coordinator._on_window_start(WINDOW_START)

    data = await coordinator._async_update_data()

    assert data["target_reached"] is True
    assert coordinator.current_target_soc() == TARGET
    assert coordinator.inverter_floor_soc() == ABOVE_TARGET


@pytest.mark.asyncio
async def test_target_not_reached_by_a_floor_above_the_battery(mock_hass):
    """The floor is not a target: a battery below the charge target keeps charging."""
    _register(mock_hass, battery="30")
    made = _make(mock_hass)
    try:
        await made._on_window_start(WINDOW_START)
        made._window_floor_soc = 90.0  # e.g. a window that started full

        data = await made._async_update_data()

        assert data["target_reached"] is False
        assert made.inverter_floor_soc() == 90.0
    finally:
        await made._stop_periodic_verification()


# 5. The floor never outlives the window --------------------------------------


@pytest.mark.asyncio
async def test_window_end_restores_the_original_min_soc_from_a_raised_floor(
    mock_hass, coordinator
):
    await coordinator._on_window_start(WINDOW_START)
    mock_hass.states.async_set(MIN_SOC, str(ABOVE_TARGET))
    mock_hass.states.async_set(GRID, "on")
    mock_hass.services.async_call.reset_mock()

    await coordinator._on_window_end(WINDOW_END)

    assert mock_hass.services.async_call.await_args_list == [
        call("number", "set_value", {"entity_id": MIN_SOC, "value": DEFAULT_MIN}),
        call("switch", "turn_off", {"entity_id": GRID}),
    ]
    assert coordinator._window_floor_soc is None
    assert _runtime_state(coordinator)["window_floor_soc"] is None
    assert coordinator.original_min_soc is None
    assert coordinator._pending_reset is False


@pytest.mark.asyncio
async def test_restart_in_the_window_keeps_the_floor(mock_hass):
    """The floor survives a Home Assistant restart through runtime_state."""
    _register(mock_hass, min_soc=str(ABOVE_TARGET), battery="62")
    options = {
        CONF_RUNTIME_STATE: {
            "is_enabled": True,
            "original_min_soc": DEFAULT_MIN,
            "initial_calculated_soc": TARGET,
            "window_floor_soc": ABOVE_TARGET,
            "pending_reset": True,
        }
    }
    made = _make(mock_hass, options=options)
    assert made._window_floor_soc == ABOVE_TARGET
    try:
        await made._on_window_start(WINDOW_START)

        # The battery ran down to 62 % during the downtime: the floor does not follow
        assert made._window_floor_soc == ABOVE_TARGET
        assert made.inverter_floor_soc() == ABOVE_TARGET
        assert _min_soc_writes(mock_hass) == []  # the inverter already holds it

        await made._on_window_end(WINDOW_END)

        assert call("number", "set_value", {"entity_id": MIN_SOC, "value": DEFAULT_MIN}) in (
            mock_hass.services.async_call.await_args_list
        )
        assert made._window_floor_soc is None
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_stale_floor_is_never_persisted_outside_a_window(mock_hass, coordinator):
    coordinator._window_floor_soc = ABOVE_TARGET
    coordinator.is_active = False

    coordinator._persist_state()

    assert _runtime_state(coordinator)["window_floor_soc"] is None


# 6. Mode "off": nothing changes ----------------------------------------------


@pytest_asyncio.fixture
async def unblocked(mock_hass):
    """Everything configured, but the block switched off by the user."""
    _register(mock_hass, battery=str(ABOVE_TARGET))
    mock_hass.states.async_set(DISCHARGE_LIMIT, "5000", {"unit_of_measurement": "W"})
    made = _make(
        mock_hass,
        {
            **CONFIG,
            CONF_DISCHARGE_LIMIT_ENTITY: DISCHARGE_LIMIT,
            CONF_DISCHARGE_BLOCK_MODE: DISCHARGE_BLOCK_OFF,
        },
    )
    yield made
    await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_mode_off_leaves_the_window_exactly_as_it_was(mock_hass, unblocked, caplog):
    with caplog.at_level("INFO"):
        await unblocked._on_window_start(WINDOW_START)

    assert "Discharge block is off" in caplog.text
    # Nothing is written at the window start, and nothing is captured for a reset
    mock_hass.services.async_call.assert_not_awaited()
    assert unblocked._window_floor_soc is None
    assert unblocked._original_discharge_limit is None
    assert unblocked._original_discharge_block is None
    # The written min SOC is the charge target, byte for byte as before plan 009
    assert unblocked.inverter_floor_soc() == unblocked.current_target_soc() == TARGET

    data = await unblocked._async_update_data()
    assert data["target_reached"] is True
    assert unblocked.inverter_floor_soc() == TARGET
    assert unblocked._window_floor_soc is None


@pytest.mark.asyncio
async def test_mode_off_keeps_the_floor_at_the_target_while_charging(mock_hass):
    _register(mock_hass, battery="20")
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_BLOCK_MODE: DISCHARGE_BLOCK_OFF})
    try:
        await made._on_window_start(WINDOW_START)
        made._window_floor_soc = 90.0  # even a leftover value is ignored

        await made._control_charge(TARGET)

        assert mock_hass.services.async_call.await_args_list == [
            call("number", "set_value", {"entity_id": MIN_SOC, "value": TARGET}),
            call("switch", "turn_on", {"entity_id": GRID}),
        ]
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_mode_off_never_writes_the_power_limit(mock_hass, unblocked):
    """'off' means the battery may discharge - through every way, not just the new ones."""
    unblocked.is_active = True

    await unblocked._apply_discharge_block()

    mock_hass.services.async_call.assert_not_awaited()
    assert unblocked._original_discharge_limit is None


@pytest.mark.asyncio
async def test_default_mode_is_auto(mock_hass, coordinator):
    assert CONF_DISCHARGE_BLOCK_MODE not in coordinator.config
    assert coordinator._discharge_block_method() == DISCHARGE_BLOCK_VIA_MIN_SOC
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_BLOCK_MODE: DISCHARGE_BLOCK_AUTO})
    assert made._discharge_block_method() == DISCHARGE_BLOCK_VIA_MIN_SOC


# 7. Visibility ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_sensor_attributes_explain_the_raised_min_soc(mock_hass, coordinator):
    from custom_components.inverter_charge_night.sensor import CalculatedSOCSensor

    await coordinator._on_window_start(WINDOW_START)
    coordinator.data = await coordinator._async_update_data()
    sensor = CalculatedSOCSensor(coordinator, coordinator.entry)

    attributes = sensor.extra_state_attributes
    assert attributes["inverter_floor_soc"] == ABOVE_TARGET
    assert attributes["discharge_block"] == "min_soc"
    assert sensor.native_value == TARGET


# 8. Edge cases of the min SOC write ------------------------------------------


@pytest.mark.asyncio
async def test_raised_floor_is_deferred_while_the_min_soc_entity_is_unavailable(
    mock_hass, caplog
):
    """The verification writes it as soon as the entity reports a value again."""
    _register(mock_hass, min_soc="unavailable", battery=str(ABOVE_TARGET))
    made = _make(mock_hass)
    try:
        with caplog.at_level("DEBUG"):
            await made._on_window_start(WINDOW_START)

        assert "Cannot raise the min SOC floor yet" in caplog.text
        assert made.original_min_soc is None  # nothing written, nothing to restore
        assert _min_soc_writes(mock_hass) == []

        mock_hass.states.async_set(MIN_SOC, str(DEFAULT_MIN))
        await made._verify_and_restore_min_soc()

        assert _min_soc_writes(mock_hass) == [ABOVE_TARGET]
        assert made.original_min_soc == DEFAULT_MIN
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_raised_floor_without_a_min_soc_entity_is_a_no_op(mock_hass):
    config = {key: value for key, value in CONFIG.items() if key != CONF_MIN_SOC_ENTITY}
    _register(mock_hass, battery=str(ABOVE_TARGET))
    made = _make(mock_hass, config)
    try:
        await made._on_window_start(WINDOW_START)
        assert _min_soc_writes(mock_hass) == []
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_raised_floor_already_on_the_inverter_is_not_written_again(mock_hass):
    """EEPROM wear: the 0.5 % dead band applies to the floor as it does to the target."""
    _register(mock_hass, min_soc=str(ABOVE_TARGET), battery=str(ABOVE_TARGET))
    made = _make(mock_hass)
    try:
        await made._on_window_start(WINDOW_START)
        assert _min_soc_writes(mock_hass) == []
        assert made._last_soc_set == ABOVE_TARGET
    finally:
        await made._stop_periodic_verification()


@pytest.mark.asyncio
async def test_a_failed_floor_write_is_reported_and_retried_later(mock_hass, caplog):
    _register(mock_hass, battery=str(ABOVE_TARGET))
    made = _make(mock_hass)
    made.is_active = True
    made.initial_calculated_soc = TARGET
    made._window_floor_soc = ABOVE_TARGET
    mock_hass.services.async_call = AsyncMock(side_effect=RuntimeError("modbus timeout"))

    await made._write_raised_min_soc_floor()

    assert "Error raising the min SOC floor" in caplog.text
    assert made._last_soc_set is None


@pytest.mark.asyncio
async def test_the_floor_is_not_updated_outside_a_window(mock_hass, coordinator):
    coordinator.is_active = False
    coordinator._update_window_floor()
    assert coordinator._window_floor_soc is None

    coordinator.is_active = True
    coordinator._ending = True
    try:
        coordinator._update_window_floor()
    finally:
        coordinator._ending = False
    assert coordinator._window_floor_soc is None


@pytest.mark.asyncio
async def test_an_unreadable_battery_soc_leaves_the_floor_alone(mock_hass, coordinator):
    coordinator.is_active = True
    mock_hass.states.async_set(BATTERY, "n/a")

    coordinator._update_window_floor()

    assert coordinator._window_floor_soc is None
    assert coordinator._discharge_block_switch_target() is None


@pytest.mark.asyncio
async def test_unload_during_the_window_drops_the_raised_floor(mock_hass, coordinator):
    """A successful reset takes the floor with it - it must not reach another window."""
    from custom_components.inverter_charge_night import (
    async_unload_entry,
)

    await coordinator._on_window_start(WINDOW_START)
    assert coordinator._window_floor_soc == ABOVE_TARGET
    mock_hass.states.async_set(MIN_SOC, str(ABOVE_TARGET))
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    coordinator.entry.runtime_data = coordinator
    mock_hass.services.async_call.reset_mock()

    assert await async_unload_entry(mock_hass, coordinator.entry) is True

    assert _min_soc_writes(mock_hass) == [DEFAULT_MIN]
    assert coordinator._window_floor_soc is None
    assert _runtime_state(coordinator)["window_floor_soc"] is None


# 9. The floor and what the user asks for -------------------------------------


@pytest.mark.asyncio
async def test_lowering_the_override_frees_the_floor(mock_hass, coordinator):
    """Otherwise the battery stays blocked at the old level until the window ends.

    The floor rises on its own to keep the charge the window bought, but the
    user lowering the target by hand is asking for the opposite - and nothing
    in the UI would explain why the battery still refuses to discharge.
    """
    from custom_components.inverter_charge_night.number import MinSOCOverrideNumber

    await coordinator._on_window_start(WINDOW_START)
    assert coordinator._window_floor_soc == ABOVE_TARGET

    number = MinSOCOverrideNumber(coordinator, coordinator.entry)
    number.hass = mock_hass
    number.async_write_ha_state = MagicMock()
    await number.async_set_native_value(30.0)

    assert coordinator._window_floor_soc == 30.0
    assert coordinator.inverter_floor_soc() == 30.0


@pytest.mark.asyncio
async def test_raising_the_override_leaves_the_floor_alone(mock_hass, coordinator):
    """Only a lowering frees it; upwards the charge target does the work."""
    from custom_components.inverter_charge_night.number import MinSOCOverrideNumber

    await coordinator._on_window_start(WINDOW_START)
    number = MinSOCOverrideNumber(coordinator, coordinator.entry)
    number.hass = mock_hass
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(90.0)

    assert coordinator._window_floor_soc == ABOVE_TARGET
    assert coordinator.inverter_floor_soc() == 90.0


@pytest.mark.asyncio
async def test_calling_off_snow_mode_frees_the_floor(mock_hass, coordinator):
    """Snow mode charges to the maximum and the floor follows it up."""
    from custom_components.inverter_charge_night.number import SnowNightsNumber

    await coordinator._on_window_start(WINDOW_START)
    coordinator.snow_nights = 2
    mock_hass.states.async_set(BATTERY, "100")
    coordinator.target_reached = True  # charging is over, so the floor may follow
    coordinator._update_window_floor()
    assert coordinator._window_floor_soc == 100.0

    number = SnowNightsNumber(coordinator, coordinator.entry)
    number.hass = mock_hass
    number.async_write_ha_state = MagicMock()
    await number.async_set_native_value(0)

    assert coordinator._window_floor_soc == coordinator.current_target_soc()
    assert coordinator._window_floor_soc < 100.0


# 10. The one failure the min SOC fallback cannot fix by itself ---------------


def test_the_min_soc_block_without_an_island_entity_is_flagged(mock_hass):
    """A battery does not discharge below its min SOC - not even in a power cut.

    The integration cannot tell it is happening unless something reports island
    operation, so it says so where the user will see it rather than in a log
    line at three in the morning.
    """
    import custom_components.inverter_charge_night as icn

    _register(mock_hass, battery=str(ABOVE_TARGET))
    made = _make(mock_hass, {k: v for k, v in CONFIG.items() if k != CONF_DISCHARGE_BLOCK_MODE})
    created: list[str] = []
    deleted: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(icn.ir, "async_create_issue", lambda *a, **k: created.append(a[2]))
        mp.setattr(icn.ir, "async_delete_issue", lambda *a, **k: deleted.append(a[2]))
        made.review_discharge_block_risk()

    assert created and created[0].startswith("discharge_block_without_backup")
    assert not deleted


def test_an_island_entity_clears_the_flag(mock_hass):
    import custom_components.inverter_charge_night as icn
    from custom_components.inverter_charge_night.const import CONF_BACKUP_MODE_ENTITY

    _register(mock_hass, battery=str(ABOVE_TARGET))
    made = _make(mock_hass, {**CONFIG, CONF_BACKUP_MODE_ENTITY: "sensor.inverter_state"})
    created: list[str] = []
    deleted: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(icn.ir, "async_create_issue", lambda *a, **k: created.append(a[2]))
        mp.setattr(icn.ir, "async_delete_issue", lambda *a, **k: deleted.append(a[2]))
        made.review_discharge_block_risk()

    assert not created
    assert deleted and deleted[0].startswith("discharge_block_without_backup")


def test_a_vendor_switch_needs_no_flag(mock_hass):
    """Only the min SOC fallback has this failure mode."""
    import custom_components.inverter_charge_night as icn

    _register(mock_hass, battery=str(ABOVE_TARGET))
    made = _make(mock_hass, {**CONFIG, CONF_DISCHARGE_BLOCK_SWITCH: BLOCK_SWITCH})
    created: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(icn.ir, "async_create_issue", lambda *a, **k: created.append(a[2]))
        mp.setattr(icn.ir, "async_delete_issue", lambda *a, **k: None)
        made.review_discharge_block_risk()

    assert not created
