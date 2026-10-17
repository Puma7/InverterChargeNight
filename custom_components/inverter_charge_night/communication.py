"""Bounded communication incident history, independent of devices and HA I/O."""
from __future__ import annotations

from datetime import datetime
from typing import Any

FAULT_CODES = frozenset({
    "battery_soc", "min_soc", "grid_switch", "charge_limit", "grid_meter",
    "island_contact", "modbus", "modbus_partial", "rest", "proxy",
    "write_unconfirmed", "discharge_switch",
})
HISTORY_LIMIT = 32


class CommunicationState:
    """Count incidents rather than polls; retain recovery and suppress repeats."""

    def __init__(self, saved: Any = None) -> None:
        self.incidents = 0
        self.failed_samples = 0
        self.faults: tuple[str, ...] = ()
        self.stop_confirmed: bool | None = None
        self.last_success_at: str | None = None
        self.history: list[dict[str, Any]] = []
        self._announced = False
        self._notice_date: str | None = None
        if not isinstance(saved, dict):
            return
        for key in ("incidents", "failed_samples"):
            value = saved.get(key)
            if type(value) is int and value >= 0:
                setattr(self, key, value)
        history = saved.get("history")
        if isinstance(history, list):
            for item in history[-HISTORY_LIMIT:]:
                if not isinstance(item, dict):
                    continue
                stamp, faults = item.get("time"), item.get("faults")
                if (not isinstance(stamp, str) or not isinstance(faults, list)
                        or not all(isinstance(f, str) and f in FAULT_CODES for f in faults)):
                    continue
                try:
                    datetime.fromisoformat(stamp)
                except ValueError:
                    continue
                stop = item.get("stop_confirmed")
                self.history.append({"time": stamp, "faults": sorted(set(faults)),
                                     "stop_confirmed": stop if isinstance(stop, bool) else None})
        if self.history:
            last = self.history[-1]
            self.faults = tuple(last["faults"])
            self.stop_confirmed = last["stop_confirmed"]

    def observe(self, faults: list[str], now: datetime, stop_confirmed: bool | None = None) -> bool:
        """Return whether one existing user notification needs updating."""
        if not all(f in FAULT_CODES for f in faults):
            raise ValueError("Unknown communication fault code")
        current = tuple(sorted(set(faults)))
        changed = current != self.faults or (bool(current) and stop_confirmed != self.stop_confirmed)
        if current and not self.faults:
            self.incidents += 1
        if current:
            self.failed_samples += 1
        else:
            self.last_success_at = now.isoformat()
        day = now.date().isoformat()
        announce = changed or (bool(current) and (not self._announced or day != self._notice_date))
        self.faults, self.stop_confirmed = current, stop_confirmed
        if announce:
            self._announced = True
            self._notice_date = day
            self.history.append({"time": now.isoformat(), "faults": list(current),
                                 "stop_confirmed": stop_confirmed})
            self.history = self.history[-HISTORY_LIMIT:]
        return announce

    def snapshot(self) -> dict[str, Any]:
        """Machine-readable, JSON-safe data without entity IDs or exception text."""
        return {
            "state": "interrupted" if self.faults else "healthy",
            "faults": list(self.faults),
            "incidents": self.incidents,
            "failed_samples": self.failed_samples,
            "stop_confirmed": self.stop_confirmed,
            "last_success_at": self.last_success_at,
            "history": [{**item, "faults": list(item["faults"])} for item in self.history],
        }

    def message(self) -> str:
        """Keep recovery visible, and never equate a request with a safe device."""
        if not self.faults:
            return (f"Kommunikation wiederhergestellt. Bisher {self.incidents} Unterbrechung(en). "
                    "Die Historie bleibt in den Integrationsdiagnosen erhalten.")
        feedback = ("Home Assistant meldet Netzladen aus." if self.stop_confirmed is True
                    else "Ein Geräte-Stopp ist nicht bestätigt; die letzten Gerätesollwerte können weiterwirken.")
        return (f"Kommunikation gestört: {', '.join(self.faults)}. {feedback}\n\n"
                f"Bisher {self.incidents} Unterbrechung(en). Lokale Verbindung, Geräteverfügbarkeit "
                "und Integrationsdiagnosen prüfen. Erst mit aktuellen Daten wird neu geregelt.")
