"""Real socket cleanup must work while an inverter client is still connected."""
import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

from lab.custom_components.icn_simulator.kostal import KostalEmulator


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_header", [b"", b"\x00\x01"], ids=["idle", "partial_header"])
async def test_stop_closes_idle_modbus_client_before_waiting_for_server_shutdown(partial_header):
    emulator = KostalEmulator(SimpleNamespace())
    await emulator.start(http_port=0, modbus_port=0)
    host, port = emulator.server.sockets[0].getsockname()
    reader, writer = await asyncio.open_connection(host, port)
    try:
        if partial_header:
            writer.write(partial_header)
            await writer.drain()
        async with asyncio.timeout(1):
            while not emulator.clients:
                await asyncio.sleep(.001)
        await asyncio.wait_for(emulator.stop(), timeout=1)
        # Windows may reset a socket with an incomplete request instead of
        # returning EOF. Both prove that shutdown actually closed the peer.
        with suppress(ConnectionResetError):
            assert await asyncio.wait_for(reader.read(), timeout=1) == b""
        assert not emulator.clients
    finally:
        writer.close()
        with suppress(ConnectionResetError):
            await writer.wait_closed()
        await emulator.stop()
