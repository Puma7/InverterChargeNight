"""A lab campaign must never connect to a different running emulator."""
from unittest.mock import MagicMock, patch

import pytest

from lab.check_kostal import main


@pytest.mark.parametrize("occupied", [80, 1502])
@pytest.mark.asyncio
async def test_occupied_emulator_port_aborts_before_any_clients_start(occupied):
    probe = MagicMock()

    def bind(address):
        if address[1] == occupied:
            raise OSError("address in use")

    probe.bind.side_effect = bind
    with patch("lab.check_kostal.socket.socket") as socket:
        socket.return_value.__enter__.return_value = probe
        with pytest.raises(RuntimeError, match=f"Lab port {occupied} is occupied"):
            await main()
