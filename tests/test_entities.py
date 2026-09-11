"""Tests for entity behavior."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.inverter_charge_night.binary_sensor import (
    ActiveWindowBinarySensor,
)
from custom_components.inverter_charge_night.number import MinSOCOverrideNumber
from custom_components.inverter_charge_night.sensor import (
    BestChargePowerSensor,
    CalculatedSOCSensor,
)
from custom_components.inverter_charge_night.switch import (
    AutoEfficientChargeSwitch,
    InverterChargeNightSwitch,
)
from custom_components.inverter_charge_night.const import CONF_USER_MAX_SOC, CONF_USER_MIN_SOC, DOMAIN


def _make_entry():
    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.title = "Test Entry"
    entry.data = {}
    return entry


def test_active_window_binary_sensor_is_on():
    coordinator = MagicMock()
    coordinator.data = {"is_active": True}
    entry = _make_entry()
    sensor = ActiveWindowBinarySensor(coordinator, entry)
    assert sensor.is_on is True
    coordinator.data = {"is_active": False}
    assert sensor.is_on is False


def test_inverter_charge_night_switch_is_on():
    coordinator = MagicMock()
    coordinator.is_enabled = True
    entry = _make_entry()
    switch = InverterChargeNightSwitch(coordinator, entry)
    assert switch.is_on is True


def test_calculated_soc_sensor_values():
    coordinator = MagicMock()
    coordinator.data = {
        "calculated_soc": 55.5,
        "is_active": True,
        "target_reached": False,
        "current_soc": 50.0,
    }
    entry = _make_entry()
    sensor = CalculatedSOCSensor(coordinator, entry)
    assert sensor.native_value == 55.5
    attrs = sensor.extra_state_attributes
    assert attrs["is_active"] is True
    assert attrs["target_reached"] is False
    assert attrs["current_soc"] == 50.0


def test_best_charge_power_sensor():
    coordinator = MagicMock()
    coordinator.get_auto_efficiency_data.return_value = {"best_power_w": 6000}
    entry = _make_entry()
    sensor = BestChargePowerSensor(coordinator, entry)
    assert sensor.native_value == 6000.0
    coordinator.get_auto_efficiency_data.return_value = {"best_power_w": "n/a"}
    assert sensor.native_value is None


def test_min_soc_override_number_native_value_paths():
    coordinator = MagicMock()
    coordinator.config = {CONF_USER_MIN_SOC: 10.0, CONF_USER_MAX_SOC: 90.0}
    coordinator.data = {"calculated_soc": 40.0}
    coordinator.override_soc = None
    entry = _make_entry()
    number = MinSOCOverrideNumber(coordinator, entry)
    number._override_value = 60.0

    assert number.native_value == 40.0

    coordinator.override_soc = 70.0
    number._override_value = 75.0
    assert number.native_value == 75.0

    coordinator.override_soc = 70.0
    number._override_value = None
    assert number.native_value == 40.0


@pytest.mark.asyncio
async def test_min_soc_override_number_set_value():
    coordinator = MagicMock()
    coordinator.config = {CONF_USER_MIN_SOC: 10.0, CONF_USER_MAX_SOC: 90.0}
    coordinator.data = {"calculated_soc": 50.0}
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = None
    coordinator._control_kostal = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = _make_entry()
    number = MinSOCOverrideNumber(coordinator, entry)
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(95.0)

    assert coordinator.override_soc == 90.0
    assert coordinator.target_reached is False
    # The override is applied by the refresh through the mode-correct control
    # path; the entity never calls the charge path directly
    coordinator._control_kostal.assert_not_awaited()
    coordinator.async_request_refresh.assert_awaited()


@pytest.mark.asyncio
async def test_min_soc_override_number_set_value_in_discharge_mode():
    coordinator = MagicMock()
    coordinator.config = {CONF_USER_MIN_SOC: 10.0, CONF_USER_MAX_SOC: 90.0}
    coordinator.data = {"calculated_soc": 50.0}
    coordinator.is_active = True
    coordinator.is_enabled = True
    coordinator.is_discharge_mode = True
    coordinator.minimum_calculated_soc = None
    coordinator._control_kostal = AsyncMock()
    coordinator._control_discharge = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = _make_entry()
    number = MinSOCOverrideNumber(coordinator, entry)
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(30.0)

    assert coordinator.override_soc == 30.0
    assert coordinator.target_reached is False
    coordinator._control_kostal.assert_not_awaited()
    coordinator._control_discharge.assert_not_awaited()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_min_soc_override_number_inactive_window_only_stores_value():
    coordinator = MagicMock()
    coordinator.config = {CONF_USER_MIN_SOC: 10.0, CONF_USER_MAX_SOC: 90.0}
    coordinator.data = {"calculated_soc": 50.0}
    coordinator.is_active = False
    coordinator.is_enabled = True
    coordinator.minimum_calculated_soc = None
    coordinator.async_request_refresh = AsyncMock()
    entry = _make_entry()
    number = MinSOCOverrideNumber(coordinator, entry)
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(60.0)

    assert coordinator.override_soc == 60.0
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_inverter_charge_night_switch_on_off():
    coordinator = MagicMock()
    coordinator.is_enabled = False
    coordinator._reset_settings = AsyncMock()
    coordinator._remove_battery_soc_listener = MagicMock()
    coordinator._remove_inverter_min_soc_listener = MagicMock()
    coordinator._stop_periodic_verification = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = _make_entry()
    switch = InverterChargeNightSwitch(coordinator, entry)
    switch.hass = MagicMock()
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    assert coordinator.is_enabled is True
    coordinator.async_request_refresh.assert_awaited()

    await switch.async_turn_off()
    coordinator._reset_settings.assert_awaited()
    coordinator._remove_battery_soc_listener.assert_called_once()
    coordinator._remove_inverter_min_soc_listener.assert_called_once()
    coordinator._stop_periodic_verification.assert_called_once()

    await switch.async_turn_on()
    await switch.async_turn_on()


@pytest.mark.asyncio
async def test_inverter_charge_night_switch_turn_off_when_disabled():
    coordinator = MagicMock()
    coordinator.is_enabled = False
    coordinator._reset_settings = AsyncMock()
    entry = _make_entry()
    switch = InverterChargeNightSwitch(coordinator, entry)
    switch.hass = MagicMock()
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()

    assert not coordinator._reset_settings.called


@pytest.mark.asyncio
async def test_inverter_charge_night_switch_reset_error_handled():
    coordinator = MagicMock()
    coordinator.is_enabled = True
    coordinator._reset_settings = AsyncMock(side_effect=Exception("boom"))
    coordinator._remove_battery_soc_listener = MagicMock()
    coordinator._remove_inverter_min_soc_listener = MagicMock()
    coordinator._stop_periodic_verification = MagicMock()
    entry = _make_entry()
    switch = InverterChargeNightSwitch(coordinator, entry)
    switch.hass = MagicMock()
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()

    assert coordinator.is_enabled is False


@pytest.mark.asyncio
async def test_auto_efficient_charge_switch_already_on_off(mock_hass):
    coordinator = MagicMock()
    coordinator.auto_efficient_charge = True
    coordinator._reset_auto_test_state = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = _make_entry()
    entry.data = {"auto_efficient_charge": True}
    switch = AutoEfficientChargeSwitch(coordinator, entry)
    switch.hass = mock_hass
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()
    assert coordinator.auto_efficient_charge is True


@pytest.mark.asyncio
async def test_auto_efficient_charge_switch_on_off(mock_hass):
    coordinator = MagicMock()
    coordinator.auto_efficient_charge = False
    coordinator._reset_auto_test_state = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = _make_entry()
    entry.data = {"auto_efficient_charge": False}
    switch = AutoEfficientChargeSwitch(coordinator, entry)
    switch.hass = mock_hass
    switch.async_write_ha_state = MagicMock()
    mock_hass.config_entries.async_update_entry = MagicMock()

    await switch.async_turn_on()
    assert coordinator.auto_efficient_charge is True
    mock_hass.config_entries.async_update_entry.assert_called()

    await switch.async_turn_off()
    assert coordinator.auto_efficient_charge is False
    coordinator._reset_auto_test_state.assert_called_once()

    await switch.async_turn_off()


def test_auto_efficient_charge_switch_is_on():
    coordinator = MagicMock()
    coordinator.auto_efficient_charge = True
    entry = _make_entry()
    switch = AutoEfficientChargeSwitch(coordinator, entry)
    assert switch.is_on is True


def test_entities_share_device_info_and_unique_id():
    coordinator = MagicMock()
    coordinator.data = {}
    entry = _make_entry()

    sensor = ActiveWindowBinarySensor(coordinator, entry)
    assert sensor.unique_id == "entry_1_active"
    assert sensor.device_info["identifiers"] == {(DOMAIN, "entry_1")}
    assert sensor.device_info["name"] == "Test Entry"
    assert sensor.has_entity_name is True

    entry.title = ""
    power_sensor = BestChargePowerSensor(coordinator, entry)
    assert power_sensor.unique_id == "entry_1_best_charge_power"
    assert power_sensor.device_info == sensor.device_info | {"name": "Inverter Charge Night"}
