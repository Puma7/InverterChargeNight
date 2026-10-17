"""Persist confirmed setup steps without creating an active config entry."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN


class SetupDrafts:
    """Serialize read/modify/write operations shared by concurrent dialogs."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.setup_drafts", private=True, atomic_writes=True
        )
        self._lock = asyncio.Lock()

    async def _load(self) -> dict[str, Any]:
        loaded = cast(object, await self._store.async_load() or {})
        if not isinstance(loaded, dict):
            return {}
        raw = cast(dict[str, Any], loaded)
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        return {key: value for key, value in raw.items()
                if isinstance(value, dict) and isinstance(value.get("updated"), str)
                and value["updated"] >= cutoff and isinstance(value.get("data"), dict)
                and isinstance(value.get("baseline", {}), dict)
                and isinstance(value.get("explicit_features", []), list)
                and all(isinstance(field, str) for field in value.get("explicit_features", []))}

    async def list(self, scope: str) -> dict[str, Any]:
        async with self._lock:
            return {key: value for key, value in (await self._load()).items()
                    if value.get("scope") == scope}

    async def save(self, key: str, scope: str, data: dict[str, Any],
                   baseline: dict[str, Any], explicit_features: Sequence[str] | None = None) -> None:
        async with self._lock:
            drafts = await self._load()
            drafts[key] = {"scope": scope, "data": data, "baseline": baseline,
                           "updated": datetime.now(UTC).isoformat(),
                           "explicit_features": explicit_features or []}
            await self._write(drafts)

    async def _write(self, drafts: dict[str, Any]) -> None:
        # HA Store logs some write failures instead of raising. Verify durability
        # before telling the user that a confirmed step is safely stored.
        await self._store.async_save(drafts)
        if await self._store.async_load() != drafts:
            raise OSError("Setup draft could not be read back after saving")

    async def remove(self, key: str) -> None:
        async with self._lock:
            drafts = await self._load()
            drafts.pop(key, None)
            await self._write(drafts)


def get_drafts(hass: HomeAssistant) -> SetupDrafts:
    """Keep one lock and store per Home Assistant instance."""
    key = f"{DOMAIN}_setup_drafts"
    if key not in hass.data:
        hass.data[key] = SetupDrafts(hass)
    result: SetupDrafts = hass.data[key]
    return result
