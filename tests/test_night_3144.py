"""3.14.4: the night of 30/31 October 2026.

The efficiency search finished in one night: 2.00 % loss at 9000 W. The only
ERROR came at 03:10, as on every night since 20 October: the Kostal reports an
internal error for about 15 seconds, the grid meter goes stale, the protection
writes 0 W into the fault and KOSTAL KORE gives up after its retries. The next
write, 3 seconds later, succeeded - and the log carried a 50-line traceback.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from tests.test_failure_paths import _make_coordinator

LIMIT = "number.ac_limit"


def _refusing(error: Exception) -> AsyncMock:
    """set_value fails the way the integration reports it; update_entity works."""
    async def call(domain: str, service: str, data: dict[str, Any], **_: Any) -> None:
        if service == "set_value":
            raise error
    return AsyncMock(side_effect=call)


def _write_errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("Error writing AC charge limit")]


@pytest.mark.asyncio
async def test_a_write_the_device_refuses_is_a_warning_without_a_traceback(mock_hass, caplog):
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set(LIMIT, "9000", {"unit_of_measurement": "W"})
    mock_hass.services.async_call = _refusing(HomeAssistantError(
        "Write failed after 2 retries for bat_max_charge_limit: Modbus transient error "
        "(func=0x90, exc=0x04): Server device failure - inverter internal error"
    ))

    with caplog.at_level(logging.DEBUG), patch(
        "custom_components.inverter_charge_night.coordinator.asyncio.sleep", AsyncMock(),
    ):
        written = await coordinator._write_ac_charge_limit(LIMIT, "number", 0.0, "Set AC charge limit")

    assert written is False
    records = _write_errors(caplog)
    assert [r.levelno for r in records] == [logging.WARNING]
    assert records[0].exc_info is None
    assert "Server device failure" in records[0].getMessage()


@pytest.mark.parametrize("error", [
    RuntimeError("bug in the call"),
    ServiceValidationError("Value 22063 is outside valid range 0 - 12540"),
])
@pytest.mark.asyncio
async def test_anything_else_stays_an_error_with_its_traceback(mock_hass, caplog, error):
    """A rejected value or an unexpected exception is ours to look at."""
    coordinator = _make_coordinator(mock_hass)
    mock_hass.states.async_set(LIMIT, "9000", {"unit_of_measurement": "W"})
    mock_hass.services.async_call = _refusing(error)

    with caplog.at_level(logging.DEBUG), patch(
        "custom_components.inverter_charge_night.coordinator.asyncio.sleep", AsyncMock(),
    ):
        written = await coordinator._write_ac_charge_limit(LIMIT, "number", 0.0, "Set AC charge limit")

    assert written is False
    records = _write_errors(caplog)
    assert [r.levelno for r in records] == [logging.ERROR]
    assert records[0].exc_info is not None
