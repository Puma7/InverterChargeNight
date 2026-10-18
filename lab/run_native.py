"""Experimental development runner for Windows; production uses HA Container.

Bootstraps the real Core and frontend without HA's POSIX process supervisor.
No Home Assistant source is patched. Bind only to loopback in this mode.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys


async def main() -> None:
    from homeassistant import bootstrap, config as ha_config, core, loader
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from windows_support import install
    install()

    directory = Path(__file__).resolve().parent / "runtime"
    os.environ.setdefault("UV_CACHE_DIR", str(directory / "uv-cache"))
    sys.path.insert(0, str(directory))
    hass = core.HomeAssistant(str(directory))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    hass.config.skip_pip_packages = []
    config = await ha_config.async_hass_config_yaml(hass)
    config["http"]["server_host"] = "127.0.0.1"
    config["http"]["server_port"] = int(os.environ.get("ICN_LAB_HA_PORT", config["http"].get("server_port", 8123)))
    config["icn_simulator"]["api_port"] = int(os.environ.get("ICN_LAB_DEVICE_PORT", config["icn_simulator"].get("api_port", 18080)))
    stopped = asyncio.Event()
    stop_file = directory / "native.stop"
    stop_file.unlink(missing_ok=True)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda event: stopped.set())
    try:
        if await bootstrap.async_from_config_dict(config, hass) is None:
            raise RuntimeError("Home Assistant bootstrap failed")
        await hass.async_start()
        # Load the same service descriptions the frontend requests after login.
        # Report dependency/schema failures before claiming the UI is ready.
        from homeassistant.helpers.service import async_get_all_descriptions
        await async_get_all_descriptions(hass)
        print(f"Home Assistant lab: http://127.0.0.1:{config['http']['server_port']}/icn-lab", flush=True)
        while not stopped.is_set():
            if stop_file.exists():
                stop_file.unlink()
                break
            try:
                await asyncio.wait_for(stopped.wait(), timeout=1)
            except TimeoutError:
                pass
    finally:
        await hass.async_stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Do not allow two native instances to write the same HA database/storage.
    lock_path = Path(__file__).resolve().parent / "runtime/native.lock"
    native_lock = lock_path.open("a+b")
    if native_lock.tell() == 0:
        native_lock.write(b"0")
        native_lock.flush()
    native_lock.seek(0)
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(native_lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("Another native lab instance is already using this configuration") from None
    else:
        import fcntl
        fcntl.flock(native_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    # CPython Windows' default Proactor loop is not compatible with all HA deps.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        try:
            runner.run(main())
        except KeyboardInterrupt:
            pass
