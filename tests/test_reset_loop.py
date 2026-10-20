"""The endless reset retry of 20 October 2026, reproduced from the field log.

The night window had written the AC charge limit. Before that it captured
what the inverter reported, 22063.31 W - a value the KORE number entity does
not accept (0 - 12540 W). Every restore was refused by Home Assistant, the
reset counted as failed, and the retry ladder rewrote the min SOC every
15 minutes for as long as Home Assistant ran, across restarts as well.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.inverter_charge_night.const import (
    CONF_CHARGE_POWER_ENTITY,
    CONF_DEFAULT_MIN_SOC,
    CONF_DISCHARGE_BLOCK_SWITCH,
    CONF_DISCHARGE_LIMIT_ENTITY,
    CONF_END_TIME,
    CONF_GRID_CHARGE_SWITCH,
    CONF_MIN_SOC_ENTITY,
    CONF_RUNTIME_STATE,
    CONF_START_TIME,
)
from custom_components.inverter_charge_night.coordinator import (
    RESET_RETRY_DELAYS,
    InverterChargeNightCoordinator,
)

COORDINATOR = "custom_components.inverter_charge_night.coordinator"
CALL_LATER = f"{COORDINATOR}.async_call_later"
MIN_SOC = "number.kostal_g3_battery_min_soc"
GRID = "switch.kostal_g3_grid_charge"
AC_LIMIT = "number.kostal_g3_battery_max_charge_limit_modbus"
LIMIT = "number.kostal_g3_battery_max_discharge_limit_modbus"
BLOCK = "switch.kostal_g3_discharge_block"
AFTER_THE_OUTAGE = datetime(2026, 10, 20, 13, 42)

CONFIG = {
    CONF_MIN_SOC_ENTITY: MIN_SOC,
    CONF_GRID_CHARGE_SWITCH: GRID,
    CONF_CHARGE_POWER_ENTITY: AC_LIMIT,
    CONF_DEFAULT_MIN_SOC: 8.0,
    CONF_START_TIME: "22:00",
    CONF_END_TIME: "05:00",
}
# What the field installation had persisted when Home Assistant came back
FIELD_STATE = {
    "original_min_soc": 8.0,
    "original_ac_charge_power": 22063.310546875,
    "pending_reset": True,
}


def _validating_device(hass: Any) -> AsyncMock:
    """Home Assistant's number validation in front of a device that obeys.

    ``number.set_value`` outside the entity's ``min``/``max`` raises
    ``ServiceValidationError``, as it did in the field log. Accepted writes and
    ``update_entity`` produce a fresh observation.
    """
    states = hass.states

    async def call(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        state = states.get(data.get("entity_id"))
        if domain == "number" and service == "set_value" and state is not None:
            low = state.attributes.get("min")
            high = state.attributes.get("max")
            value = float(data["value"])
            if (low is not None and value < low) or (high is not None and value > high):
                raise ServiceValidationError(
                    f"Value {value} for {data['entity_id']} is outside valid range {low} - {high}"
                )
            state.state = str(value)
        elif domain in ("switch", "input_boolean") and state is not None:
            state.state = "on" if service == "turn_on" else "off"
        if state is not None:
            state.last_reported = datetime.now(UTC)

    return AsyncMock(side_effect=call)


def _register_field_entities(hass: Any) -> None:
    hass.states.async_set(MIN_SOC, "8.0", {"min": 0, "max": 100, "unit_of_measurement": "%"})
    hass.states.async_set(GRID, "off")
    hass.states.async_set(AC_LIMIT, "12540.0", {"min": 0.0, "max": 12540.0, "unit_of_measurement": "W"})
    for entity_id in (MIN_SOC, GRID, AC_LIMIT):
        hass.states.get(entity_id).last_reported = datetime(2026, 10, 20, 13, 40, tzinfo=UTC)


def _make_coordinator(hass: Any, data: dict[str, Any], runtime: dict[str, Any]) -> InverterChargeNightCoordinator:
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test"
    entry.data = data
    entry.options = {CONF_RUNTIME_STATE: dict(runtime)}

    def _update(entry_: Any, options: Any = None, **kwargs: Any) -> bool:
        if options is not None:
            entry_.options = options
        return True

    hass.config_entries.async_update_entry = MagicMock(side_effect=_update)
    with patch(f"{COORDINATOR}.dt_util.now", return_value=AFTER_THE_OUTAGE):
        coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _writes(hass: Any, entity_id: str) -> list[Any]:
    return [
        c.args[2]["value"]
        for c in hass.services.async_call.await_args_list
        if c.args[1] == "set_value" and c.args[2].get("entity_id") == entity_id
    ]


@pytest.mark.asyncio
async def test_the_field_state_finishes_its_reset_in_one_go(mock_hass):
    """The persisted 22063 W original no longer locks the reset into a loop."""
    _register_field_entities(mock_hass)
    mock_hass.services.async_call = _validating_device(mock_hass)
    coordinator = _make_coordinator(mock_hass, CONFIG, FIELD_STATE)

    with patch(CALL_LATER, return_value=MagicMock()) as later, patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTER_THE_OUTAGE
    ):
        await coordinator._check_current_window()

    assert coordinator._pending_reset is False
    assert coordinator._original_ac_charge_power is None
    assert coordinator.original_min_soc is None
    assert _writes(mock_hass, AC_LIMIT) == [12540.0], "the entity's maximum stands for 'unlimited'"
    assert later.call_count == 0, "nothing is left to retry"


@pytest.mark.asyncio
async def test_a_retry_does_not_rewrite_what_is_already_restored(mock_hass):
    """Every retry wrote the min SOC to the inverter again, for hours.

    The min SOC was back at 8 % after the first attempt. Only the AC limit was
    still missing, and that one could never succeed - but the min SOC capture
    was kept until *everything* had worked, so each retry wrote it again.
    """
    _register_field_entities(mock_hass)
    device = _validating_device(mock_hass)

    async def refuse_the_ac_limit(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if data.get("entity_id") == AC_LIMIT and service == "set_value":
            raise RuntimeError("Modbus timeout")
        await device(domain, service, data, **kwargs)

    mock_hass.services.async_call = AsyncMock(side_effect=refuse_the_ac_limit)
    coordinator = _make_coordinator(
        mock_hass, CONFIG, {**FIELD_STATE, "original_min_soc": 30.0, "original_ac_charge_power": 4000.0}
    )
    mock_hass.states.async_set(MIN_SOC, "45.0", {"min": 0, "max": 100, "unit_of_measurement": "%"})

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTER_THE_OUTAGE
    ):
        for _ in range(4):
            await coordinator._retry_reset()

    assert _writes(mock_hass, MIN_SOC) == [30.0], "written once, then left alone"
    assert coordinator.original_min_soc is None
    assert coordinator._original_ac_charge_power == 4000.0, "still owed, still kept"


@pytest.mark.asyncio
async def test_the_retry_ladder_gives_up_and_says_so(mock_hass):
    """No write may repeat every 15 minutes for ever.

    After the quick attempts and two slow ones the integration stops writing,
    files a repair issue naming the entity, and keeps the capture for the next
    window start or a manual reset.
    """
    _register_field_entities(mock_hass)
    device = _validating_device(mock_hass)

    async def refuse_the_ac_limit(domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> None:
        if data.get("entity_id") == AC_LIMIT and service == "set_value":
            raise RuntimeError("Modbus timeout")
        await device(domain, service, data, **kwargs)

    mock_hass.services.async_call = AsyncMock(side_effect=refuse_the_ac_limit)
    coordinator = _make_coordinator(mock_hass, CONFIG, {**FIELD_STATE, "original_ac_charge_power": 4000.0})

    with patch(CALL_LATER, return_value=MagicMock()) as later, patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTER_THE_OUTAGE
    ), patch(f"{COORDINATOR}.ir.async_create_issue") as issue:
        coordinator.async_start_pending_reset_retry()
        for _ in range(20):
            await coordinator._retry_reset()

    attempts = len(_writes(mock_hass, AC_LIMIT))
    assert attempts == len(RESET_RETRY_DELAYS) + 2, f"{attempts} writes instead of a bounded ladder"
    assert later.call_count == len(RESET_RETRY_DELAYS) + 2
    assert coordinator._pending_reset is False, "no more automatic writes"
    assert coordinator._original_ac_charge_power == 4000.0, "kept for the next window or a manual reset"
    issue.assert_called_once()
    assert issue.call_args.kwargs["translation_placeholders"]["entities"] == AC_LIMIT

    # A restart must not start the ladder over again on its own
    restarted = _make_coordinator(mock_hass, CONFIG, coordinator.entry.options[CONF_RUNTIME_STATE])
    with patch(CALL_LATER, return_value=MagicMock()) as later_after_restart:
        restarted.async_start_pending_reset_retry()
    assert later_after_restart.call_count == 0


@pytest.mark.asyncio
async def test_the_settings_still_on_the_inverter_branch_respects_the_give_up(mock_hass):
    """That branch runs on every window check - the backup listener calls it on
    each state change - and used to start a full reset outside the ladder."""
    _register_field_entities(mock_hass)
    mock_hass.services.async_call = _validating_device(mock_hass)
    coordinator = _make_coordinator(
        mock_hass,
        CONFIG,
        {**FIELD_STATE, "original_ac_charge_power": 4000.0, "pending_reset": False, "reset_abandoned": True},
    )

    with patch(CALL_LATER, return_value=MagicMock()), patch(
        f"{COORDINATOR}.dt_util.now", return_value=AFTER_THE_OUTAGE
    ):
        await coordinator._check_current_window()

    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_pending_reset_does_not_poll_every_fifteen_seconds(mock_hass):
    """The retry timer owns the next attempt; the staleness check must not refresh for it."""
    _register_field_entities(mock_hass)
    coordinator = _make_coordinator(mock_hass, CONFIG, FIELD_STATE)
    assert coordinator._pending_reset is True
    callbacks: list[Any] = []
    with patch(
        f"{COORDINATOR}.async_track_time_interval",
        side_effect=lambda hass, action, interval: callbacks.append(action) or (lambda: None),
    ):
        coordinator.setup_communication_listener()
    assert callbacks, "the staleness check was not armed"

    await callbacks[0](AFTER_THE_OUTAGE)

    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_explicit_reservation_holds_only_through_the_min_soc(mock_hass):
    """The evening rescue and block_discharge no longer touch switch or limit.

    Pascal's rule: the battery is held through the min SOC and nothing else.
    A discharge block switch or a zero discharge limit stops the battery from
    serving the house altogether.
    """
    _register_field_entities(mock_hass)
    mock_hass.states.async_set(BLOCK, "off")
    mock_hass.states.async_set(LIMIT, "12000", {"min": 0, "max": 12540, "unit_of_measurement": "W"})
    mock_hass.services.async_call = _validating_device(mock_hass)
    coordinator = _make_coordinator(
        mock_hass,
        {**CONFIG, CONF_DISCHARGE_BLOCK_SWITCH: BLOCK, CONF_DISCHARGE_LIMIT_ENTITY: LIMIT},
        {},
    )
    coordinator._adhoc_until = datetime(2026, 10, 20, 18, 0)
    coordinator.is_active = True

    assert coordinator._discharge_block_method() == "min_soc"
    await coordinator._apply_discharge_block(announce=True)

    touched = {c.args[2].get("entity_id") for c in mock_hass.services.async_call.await_args_list}
    assert BLOCK not in touched and LIMIT not in touched


@pytest.mark.asyncio
async def test_a_min_soc_already_at_its_original_is_confirmed_not_written(mock_hass):
    """A fresh reading that matches is enough: no command goes to the inverter."""
    _register_field_entities(mock_hass)
    mock_hass.services.async_call = _validating_device(mock_hass)
    coordinator = _make_coordinator(mock_hass, {**CONFIG, CONF_CHARGE_POWER_ENTITY: None}, {})
    coordinator.original_min_soc = 8.0

    assert await coordinator._reset_settings() is True

    assert _writes(mock_hass, MIN_SOC) == []
    assert any(
        c.args[:2] == ("homeassistant", "update_entity") and c.args[2]["entity_id"] == MIN_SOC
        for c in mock_hass.services.async_call.await_args_list
    )
    assert coordinator.original_min_soc is None


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["manual reset", "window start", "success"])
async def test_a_given_up_reset_comes_back_through_the_ways_the_issue_names(mock_hass, how):
    _register_field_entities(mock_hass)
    mock_hass.services.async_call = _validating_device(mock_hass)
    coordinator = _make_coordinator(
        mock_hass, CONFIG, {**FIELD_STATE, "pending_reset": False, "reset_abandoned": True}
    )
    coordinator._reset_retry_count = 5
    with patch(f"{COORDINATOR}.ir.async_delete_issue") as deleted, patch(
        CALL_LATER, return_value=MagicMock()
    ), patch(f"{COORDINATOR}.dt_util.now", return_value=AFTER_THE_OUTAGE):
        if how == "manual reset":
            assert await coordinator.async_reset_inverter() is True
        elif how == "window start":
            coordinator._clear_reset_abandoned()
        else:
            coordinator._record_reset_outcome(True)

    assert coordinator._reset_abandoned is False
    assert coordinator._reset_retry_count == 0
    deleted.assert_called_once()
    assert deleted.call_args.args[2] == "reset_incomplete_entry_1"


@pytest.mark.asyncio
async def test_a_failure_after_giving_up_arms_nothing(mock_hass):
    _register_field_entities(mock_hass)
    coordinator = _make_coordinator(
        mock_hass, CONFIG, {**FIELD_STATE, "pending_reset": False, "reset_abandoned": True}
    )
    with patch(CALL_LATER, return_value=MagicMock()) as later:
        coordinator._record_reset_outcome(False)

    assert later.call_count == 0
    assert coordinator._pending_reset is False


def test_the_issue_names_a_grid_switch_that_is_still_on(mock_hass):
    _register_field_entities(mock_hass)
    mock_hass.states.async_set(GRID, "on")
    coordinator = _make_coordinator(mock_hass, CONFIG, {"original_ac_charge_power": 4000.0})

    assert coordinator._entities_still_owed() == [AC_LIMIT, GRID]


def test_a_value_below_the_entity_minimum_is_raised_to_it(caplog):
    from custom_components.inverter_charge_night.coordinator import _within_entity_range

    state = MagicMock()
    state.attributes = {"min": 5, "max": 100}
    assert _within_entity_range(state, 2.0, MIN_SOC, "Min SOC restore") == 5.0
    assert _within_entity_range(state, 40.0, MIN_SOC, "Min SOC restore") == 40.0
    assert "outside what" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unit", "maximum", "expected"),
    [("kW", 12.54, 12.54), ("", 12540.0, 12540.0)],
)
async def test_the_ac_limit_restore_is_clamped_in_the_entity_unit(mock_hass, unit, maximum, expected):
    attributes: dict[str, Any] = {"min": 0.0, "max": maximum}
    if unit:
        attributes["unit_of_measurement"] = unit
    mock_hass.states.async_set(AC_LIMIT, str(maximum), attributes)
    mock_hass.services.async_call = _validating_device(mock_hass)
    coordinator = _make_coordinator(mock_hass, {CONF_CHARGE_POWER_ENTITY: AC_LIMIT}, {})
    coordinator._original_ac_charge_power = 22063.310546875

    assert await coordinator._reset_ac_charge_limit() is True

    assert _writes(mock_hass, AC_LIMIT) == [expected]
    assert coordinator._original_ac_charge_power is None


def test_giving_up_is_not_reported_as_a_recovery(mock_hass):
    _register_field_entities(mock_hass)
    coordinator = _make_coordinator(
        mock_hass, CONFIG, {**FIELD_STATE, "pending_reset": False, "reset_abandoned": True}
    )
    coordinator.enable_communication_monitoring()
    assert coordinator._communication is not None
    with patch(f"{COORDINATOR}.dt_util.now", return_value=AFTER_THE_OUTAGE), patch(
        "homeassistant.components.persistent_notification.async_create"
    ):
        coordinator._update_communication_health()

    assert "write_unconfirmed" in coordinator._communication.faults
