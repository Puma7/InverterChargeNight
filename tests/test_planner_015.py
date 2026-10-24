"""Plan 015: the night target with a safety buffer, learned need and weather.

Pascal's 23/24 October: 44 % on 35.8 kWh, and the next morning the evening
outlook already 0.85 kWh short. The target sat on the edge of what the day
needed. These tests run the coordinator end to end, from the options to the
plan the sensor shows.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.inverter_charge_night.const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_END_TIME,
    CONF_FORECAST_ERROR_MARGIN,
    CONF_MIN_SOC_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_SAFETY_BUFFER_KWH,
    CONF_START_TIME,
    CONF_USER_MAX_SOC,
    CONF_USER_MIN_SOC,
)
from custom_components.inverter_charge_night.coordinator import (
    InverterChargeNightCoordinator,
)

COORDINATOR = "custom_components.inverter_charge_night.coordinator"
NIGHT = datetime(2026, 1, 15, 2, 0)
SUN = (datetime(2026, 1, 15, 7, 30), datetime(2026, 1, 15, 17, 0))

CONFIG = {
    CONF_MIN_SOC_ENTITY: "number.min_soc",
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_PV_FORECAST_ENTITY: "sensor.pv",
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_USER_MIN_SOC: 8.0,
    CONF_USER_MAX_SOC: 100.0,
    CONF_FORECAST_ERROR_MARGIN: 10.0,
    CONF_START_TIME: "23:00",
    CONF_END_TIME: "05:00",
}


def _coordinator(hass, extra=None, profile=None):
    hass.states.async_set("sensor.soc", "40", {"unit_of_measurement": "%"})
    entry = MagicMock()
    entry.entry_id = "entry_015"
    entry.title = "Test"
    entry.data = CONFIG | (extra or {})
    entry.options = {}
    coordinator = InverterChargeNightCoordinator(hass, entry)
    coordinator._house_load_profile = AsyncMock(return_value=profile or [0.5] * 24)
    coordinator._sun_times = MagicMock(return_value=SUN)
    return coordinator


# --- stage A: the safety buffer ----------------------------------------------


@pytest.mark.asyncio
async def test_the_buffer_option_raises_the_night_target(mock_hass, caplog):
    plain = _coordinator(mock_hass)
    buffered = _coordinator(mock_hass, {CONF_SAFETY_BUFFER_KWH: 2.0})
    with patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        without = await plain._plan_target(3.0, True)
        caplog.clear()
        caplog.set_level("INFO")
        with_buffer = await buffered._plan_target(3.0, True)

    assert without is not None and with_buffer is not None
    assert with_buffer.lower_bound_soc == without.lower_bound_soc
    assert with_buffer.target_soc == pytest.approx(without.target_soc + 20.0, abs=0.11)
    assert "buffer 20.0%" in caplog.text
    attributes = buffered._plan_attributes()
    assert attributes["buffer_soc"] == pytest.approx(20.0, abs=0.11)
    assert attributes["buffer_capped"] is False


@pytest.mark.asyncio
async def test_a_sunny_tomorrow_caps_the_buffer(mock_hass, caplog):
    """9 kWh of sun need room: the buffer only fills what is left."""
    buffered = _coordinator(mock_hass, {CONF_SAFETY_BUFFER_KWH: 8.0})
    caplog.set_level("INFO")
    with patch(f"{COORDINATOR}.dt_util.now", return_value=NIGHT):
        plan = await buffered._plan_target(9.0, True)

    assert plan is not None
    assert plan.target_soc == plan.upper_bound_soc
    assert plan.buffer_capped
    assert "(capped)" in caplog.text
