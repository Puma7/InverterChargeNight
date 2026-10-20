"""A scheduled night preserves its target, while energy above it supplies the house."""
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_night_command_confirmation import make


def night(hass, binding="none", soc="100", mode="auto"):
    options = {"user_min_soc": 8, "default_min_soc": 8, "discharge_block_mode": mode}
    if binding in ("switch", "both"):
        options["discharge_block_switch"] = "switch.synthetic_block"
    if binding in ("limit", "both"):
        options["discharge_limit_entity"] = "number.synthetic_discharge"
    c = make(hass, **options)
    hass.states.async_set("sensor.synthetic_soc", soc)
    hass.states.async_set("number.synthetic_floor", "8", {"min": 0, "max": 100, "step": 1})
    hass.states.async_set("switch.synthetic_block", "off")
    hass.states.async_set("number.synthetic_discharge", "8000")

    async def apply(domain, service, data, **kwargs):
        entity_id = data["entity_id"]
        old = hass.states.get(entity_id)
        value = str(data["value"]) if domain == "number" else ("on" if service == "turn_on" else "off")
        hass.states.async_set(entity_id, value, dict(old.attributes) if old else {})

    hass.services.async_call = AsyncMock(side_effect=apply)

    def store(entry, *, options=None, **kwargs):
        if options is not None:
            entry.options = options

    hass.config_entries.async_update_entry.side_effect = store
    c.async_request_refresh = AsyncMock()
    c._start_periodic_verification = AsyncMock()
    c._stop_periodic_verification = AsyncMock()

    async def calculate():
        c.initial_calculated_soc = c.calculated_soc = 30

    c._calculate_initial_soc = AsyncMock(side_effect=calculate)
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["none", "switch", "limit", "both"])
@pytest.mark.parametrize("mode", ["auto", "off"])
async def test_full_battery_gets_only_the_planned_floor_and_restores_eight(mock_hass, binding, mode):
    c = night(mock_hass, binding, mode=mode)
    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now", return_value=datetime(2026, 1, 15, 2)):
        await c._on_window_start(datetime(2026, 1, 15))
        assert float(mock_hass.states.get("number.synthetic_floor").state) == 30
        assert c.inverter_floor_soc() == 30
        assert c._window_floor_soc is None
        mock_hass.services.async_call.assert_any_await(
            "number", "set_value", {"entity_id": "number.synthetic_floor", "value": 30}, blocking=True,
        )
        mock_hass.states.async_set("sensor.synthetic_soc", "65")
        await c._verify_and_restore_min_soc()
        assert float(mock_hass.states.get("number.synthetic_floor").state) == 30
        assert mock_hass.states.get("switch.synthetic_grid").state == "off"
        assert mock_hass.states.get("switch.synthetic_block").state == "off"
        assert float(mock_hass.states.get("number.synthetic_discharge").state) == 8000
        mock_hass.states.async_set("sensor.synthetic_soc", "90")
        c._update_window_floor()
        assert c._window_floor_soc is None
        assert c.inverter_floor_soc() == 30
        assert all(x.args[2]["entity_id"] not in ("switch.synthetic_block", "number.synthetic_discharge")
                   for x in mock_hass.services.async_call.await_args_list)
        await c._on_window_end(datetime(2026, 1, 15, 5))
    assert float(mock_hass.states.get("number.synthetic_floor").state) == 8
    assert c.original_min_soc is None and not c._pending_reset


@pytest.mark.asyncio
async def test_regular_night_releases_restrictions_captured_by_an_older_version(mock_hass):
    c = night(mock_hass, "both")
    c.original_min_soc = 8
    c._original_discharge_block = False
    c._original_discharge_limit = 8000
    c._window_floor_soc = 100
    mock_hass.states.async_set("number.synthetic_floor", "100")
    mock_hass.states.async_set("switch.synthetic_block", "on")
    mock_hass.states.async_set("number.synthetic_discharge", "0")
    with patch("custom_components.inverter_charge_night.coordinator.dt_util.now", return_value=datetime(2026, 1, 15, 2)):
        await c._on_window_start(datetime(2026, 1, 15))
    assert mock_hass.states.get("switch.synthetic_block").state == "off"
    assert float(mock_hass.states.get("number.synthetic_discharge").state) == 8000
    assert float(mock_hass.states.get("number.synthetic_floor").state) == 30
    assert c.original_min_soc == 8
    assert c._original_discharge_block is None and c._original_discharge_limit is None


@pytest.mark.asyncio
async def test_failed_legacy_release_keeps_its_original_for_retry(mock_hass):
    c = night(mock_hass, "switch")
    c._original_discharge_block = False
    mock_hass.states.async_set("switch.synthetic_block", "unavailable")
    await c._apply_discharge_block(announce=True)
    assert c._original_discharge_block is False
    mock_hass.states.async_set("switch.synthetic_block", "on")
    # Not from every verification: a device that ignores it would be written
    # to every 15 minutes. The window end gives it back.
    await c._apply_discharge_block()
    assert c._original_discharge_block is False
    mock_hass.services.async_call.assert_not_awaited()
    assert await c._reset_discharge_block_switch() is True
    assert c._original_discharge_block is None
    assert mock_hass.states.get("switch.synthetic_block").state == "off"
    mock_hass.services.async_call.assert_awaited_once_with(
        "switch", "turn_off", {"entity_id": "switch.synthetic_block"}, blocking=True,
    )


@pytest.mark.asyncio
async def test_legacy_cleanup_does_not_activate_a_preexisting_block(mock_hass):
    c = night(mock_hass, "both")
    c._original_discharge_block = True
    c._original_discharge_limit = 0
    await c._apply_discharge_block(announce=True)
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["switch", "limit"])
@pytest.mark.parametrize("cached_match", [False, True])
async def test_ignored_release_retains_capture_even_when_cached_value_matches(mock_hass, binding, cached_match):
    c = night(mock_hass, binding)
    entity_id = "switch.synthetic_block" if binding == "switch" else "number.synthetic_discharge"
    original = False if binding == "switch" else 8000
    attribute = "_original_discharge_block" if binding == "switch" else "_original_discharge_limit"
    setattr(c, attribute, original)
    mock_hass.states.async_set(entity_id, ("off" if binding == "switch" else "8000") if cached_match
                               else ("on" if binding == "switch" else "0"))
    mock_hass.services.async_call = AsyncMock()  # Command and refresh are acknowledged, never observed.
    await c._apply_discharge_block(announce=True)
    assert getattr(c, attribute) == original
    mock_hass.services.async_call.assert_any_await(
        "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True,
    )
    c._persist_state()
    assert c.entry.options["runtime_state"][attribute.removeprefix("_")] == original
    # The next verification observes a real device update and can finish.
    async def observed(domain, service, data, **kwargs):
        mock_hass.states.async_set(entity_id, "off" if binding == "switch" else "8000")
    mock_hass.services.async_call = AsyncMock(side_effect=observed)
    await c._apply_discharge_block(announce=True)
    assert getattr(c, attribute) is None
    mock_hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["switch", "limit"])
async def test_release_can_be_confirmed_by_an_explicit_feedback_refresh(mock_hass, binding):
    c = night(mock_hass, binding)
    entity_id = "switch.synthetic_block" if binding == "switch" else "number.synthetic_discharge"
    attribute = "_original_discharge_block" if binding == "switch" else "_original_discharge_limit"
    setattr(c, attribute, False if binding == "switch" else 8000)
    mock_hass.states.async_set(entity_id, "on" if binding == "switch" else "0")
    async def delayed(domain, service, data, **kwargs):
        if domain == "homeassistant":
            mock_hass.states.async_set(entity_id, "off" if binding == "switch" else "8000")
    mock_hass.services.async_call = AsyncMock(side_effect=delayed)
    await c._apply_discharge_block(announce=True)
    assert getattr(c, attribute) is None
    mock_hass.services.async_call.assert_any_await(
        "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True,
    )


@pytest.mark.asyncio
async def test_feedback_refresh_failure_retains_capture_and_failed_end_is_pending(mock_hass):
    c = night(mock_hass, "limit")
    c._original_discharge_limit = 8000
    mock_hass.states.async_set("number.synthetic_discharge", "0")
    async def failed_refresh(domain, service, data, **kwargs):
        if domain == "homeassistant":
            raise RuntimeError("Synthetic feedback failure")
    mock_hass.services.async_call = AsyncMock(side_effect=failed_refresh)
    await c._on_window_end(datetime(2026, 1, 15, 5))
    assert c._original_discharge_limit == 8000 and c._pending_reset
    assert c.entry.options["runtime_state"]["original_discharge_limit"] == 8000
    mock_hass.services.async_call.assert_any_await(
        "number", "set_value", {"entity_id": "number.synthetic_discharge", "value": 8000}, blocking=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["switch", "limit"])
async def test_a_new_capture_is_not_cleared_by_an_older_restore(mock_hass, binding):
    c = night(mock_hass, binding)
    switch = binding == "switch"
    entity_id = "switch.synthetic_block" if switch else "number.synthetic_discharge"
    attribute = "_original_discharge_block" if switch else "_original_discharge_limit"
    setattr(c, attribute, False if switch else 8000)
    async def new_capture(domain, service, data, **kwargs):
        setattr(c, attribute, True if switch else 6000)
        mock_hass.states.async_set(entity_id, "off" if switch else "8000")
    mock_hass.services.async_call = AsyncMock(side_effect=new_capture)
    reset = c._reset_discharge_block_switch if switch else c._reset_discharge_limit
    assert await reset() is False
    assert getattr(c, attribute) == (True if switch else 6000)
    mock_hass.services.async_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_disappearing_feedback_cannot_confirm_a_release(mock_hass):
    c = night(mock_hass, "switch")
    c._original_discharge_block = False
    async def disappears(domain, service, data, **kwargs):
        mock_hass.states.get.side_effect = lambda entity_id: None
    mock_hass.services.async_call = AsyncMock(side_effect=disappears)
    assert await c._reset_discharge_block_switch() is False
    assert c._original_discharge_block is False
    mock_hass.services.async_call.assert_any_await(
        "homeassistant", "update_entity", {"entity_id": "switch.synthetic_block"}, blocking=True,
    )
