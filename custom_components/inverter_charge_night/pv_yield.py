"""Conservative, daily PV shortfall observation; no claim about its cause."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
import math
from typing import Any


class PVYieldMonitor:
    """Compare a morning full-day forecast with fresh energy after sunset.

    A partial first day, stale meter, tiny forecast or missing astronomy cannot
    arm an automatic full charge. A severe shortfall arms exactly the next
    solar day's regular night window. Manual snow nights remain independent.
    """

    def __init__(self, raw: Any = None) -> None:
        self.day: date | None = None
        self.expected_kwh: float | None = None
        self.actual_kwh: float | None = None
        self.charge_day: date | None = None
        self.status = "learning"
        if not isinstance(raw, Mapping):
            return
        for key in ("day", "charge_day"):
            try:
                setattr(self, key, date.fromisoformat(str(raw.get(key))))
            except ValueError:
                pass
        for key in ("expected_kwh", "actual_kwh"):
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                setattr(self, key, float(value))
        status = raw.get("status")
        if isinstance(status, str) and status in {"learning", "normal", "low_yield", "unavailable"}:
            self.status = status

    def snapshot(self) -> dict[str, Any]:
        """Small, JSON-safe state, also used for frontend explanations."""
        return {
            "day": self.day.isoformat() if self.day else None,
            "expected_kwh": self.expected_kwh,
            "actual_kwh": self.actual_kwh,
            "charge_day": self.charge_day.isoformat() if self.charge_day else None,
            "status": self.status,
        }

    def observe(
        self, now: datetime, sunrise: datetime, sunset: datetime,
        forecast_kwh: float | None, actual_kwh: float | None,
    ) -> bool:
        """Return True only when a new severe shortfall has been established."""
        today = now.date()
        if self.day != today:
            self.day, self.expected_kwh, self.actual_kwh = today, None, None
            self.status = "learning"
            if self.charge_day is not None and self.charge_day < today:
                self.charge_day = None
        if self.status in {"normal", "low_yield"}:
            return False
        if forecast_kwh is not None and (not math.isfinite(forecast_kwh) or forecast_kwh < 0):
            forecast_kwh = None
        if actual_kwh is not None and (not math.isfinite(actual_kwh) or actual_kwh < 0):
            actual_kwh = None
        # The forecast must mean the complete day, not the remaining yield.
        # Both inputs must already be observed in the morning. Enabling the
        # feature after a bad day therefore cannot manufacture a diagnosis.
        if now <= sunrise + timedelta(hours=2) and forecast_kwh is not None and actual_kwh is not None:
            self.expected_kwh = max(self.expected_kwh or 0, forecast_kwh)
        if now < sunset:
            return False
        self.actual_kwh = actual_kwh
        if self.expected_kwh is None or self.expected_kwh < 5 or actual_kwh is None:
            self.status = "unavailable"
            return False
        if actual_kwh <= self.expected_kwh * .15:
            self.status = "low_yield"
            self.charge_day = today + timedelta(days=1)
            return True
        self.status = "normal"
        return False

    def active_for(self, solar_day: date) -> bool:
        """Whether this observation applies to that window's following day."""
        return self.charge_day == solar_day
