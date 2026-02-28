"""Root conftest – loaded before any test conftest or homeassistant import.

Fixes Windows-specific issue: homeassistant.runner.HassEventLoopPolicy forces
ProactorEventLoop which needs sockets that pytest-socket blocks.

We disable pytest-socket's monkey-patch of socket.socket so that event loop
creation works, then patch HassEventLoopPolicy to use SelectorEventLoop.
"""
import asyncio
import socket
import sys

# Ensure the *real* socket.socket is always available for event loop creation.
# pytest-socket replaces socket.socket with a wrapper that raises
# SocketBlockedError.  We save the original before it can be patched.
_real_socket = socket.socket


def _ensure_real_socket() -> None:
    """Restore the real socket.socket if it was monkey-patched."""
    if socket.socket is not _real_socket:
        socket.socket = _real_socket


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        import homeassistant.runner as _runner

        def _selector_new_event_loop(self):  # type: ignore[no-untyped-def]
            _ensure_real_socket()
            return asyncio.SelectorEventLoop()

        _runner.HassEventLoopPolicy.new_event_loop = _selector_new_event_loop  # type: ignore[assignment]
    except Exception:
        pass
