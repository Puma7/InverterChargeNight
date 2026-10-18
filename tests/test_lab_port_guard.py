"""A lab campaign must never connect to a different running emulator."""
from unittest.mock import MagicMock, patch

import pytest

from lab.check_kostal import main


@pytest.mark.parametrize("occupied", [80, 1502])
@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2"])
async def test_occupied_emulator_port_aborts_before_any_clients_start(occupied, host, monkeypatch):
    monkeypatch.setenv("ICN_LAB_INVERTER_HOST", host)
    probe = MagicMock()

    def bind(address):
        assert address[0] == host
        if address[1] == occupied:
            raise OSError("address in use")

    probe.bind.side_effect = bind
    with patch("lab.check_kostal.socket.socket") as socket:
        socket.return_value.__enter__.return_value = probe
        with pytest.raises(RuntimeError, match=f"Lab port {occupied} is occupied"):
            await main()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
@pytest.mark.asyncio
async def test_non_loopback_inverter_is_rejected_before_any_socket(host, monkeypatch):
    monkeypatch.setenv("ICN_LAB_INVERTER_HOST", host)
    with patch("lab.check_kostal.socket.socket") as socket:
        with pytest.raises(ValueError):
            await main()
    socket.assert_not_called()
