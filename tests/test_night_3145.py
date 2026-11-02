"""3.14.5: the night of 1/2 November 2026.

3.14.4 meant to turn the nightly 03:10 refusal into a warning, and the log
still carried the ERROR with its traceback. KOSTAL KORE raises its own
``ModbusWriteError``, a plain ``Exception``, not the ``HomeAssistantError``
that 3.14.4 and its tests assumed.
"""
from __future__ import annotations

import json
import logging
import socket
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol

from custom_components.inverter_charge_night.coordinator import _refused_by_device
from tests.test_night_3144 import LIMIT, _assert_zero_was_written, _refusing, _write_errors
from tests.test_failure_paths import _make_coordinator


class ModbusClientError(Exception):
    """KOSTAL KORE's base error, as in its modbus_client.py."""


class ModbusWriteError(ModbusClientError):
    """Raised by KORE's number entity when a register write fails."""


# Where Home Assistant loads them from
ModbusClientError.__module__ = ModbusWriteError.__module__ = "custom_components.kostal_kore.modbus_client"


@pytest.mark.asyncio
async def test_kore_s_own_write_error_is_a_warning_without_a_traceback(mock_hass, caplog):
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set(LIMIT, "9000", {"unit_of_measurement": "W"})
    mock_hass.services.async_call = _refusing(ModbusWriteError(
        "Write failed after 2 retries for bat_max_charge_limit: Modbus transient error "
        "(func=0x90, exc=0x04): Server device failure - inverter internal error"
    ))

    with caplog.at_level(logging.DEBUG), patch(
        "custom_components.inverter_charge_night.coordinator.asyncio.sleep", AsyncMock(),
    ):
        written = await coordinator._write_ac_charge_limit(LIMIT, "number", 0.0, "Set AC charge limit")

    assert written is False
    _assert_zero_was_written(mock_hass)
    records = _write_errors(caplog)
    assert [r.levelno for r in records] == [logging.WARNING]
    assert records[0].exc_info is None
    assert "ModbusWriteError: Write failed after 2 retries" in records[0].getMessage()


@pytest.mark.parametrize(("error", "refused"), [
    (ModbusWriteError("device refused"), True),
    (TimeoutError("no answer"), False),
    (socket.gaierror("name resolution failed"), False),
    (json.JSONDecodeError("bad reply", "", 0), False),
    (KeyError("value"), False),
    (vol.Invalid("expected float"), False),
])
def test_only_an_integration_s_own_error_counts_as_a_refusal(error, refused):
    """A standard library exception - its modules included - points at a bug, an invalid
    value at a wrong command (CodeRabbit on #51)."""
    assert _refused_by_device(error) is refused
