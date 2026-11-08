"""Conservative, daily PV shortfall observation; no claim about its cause."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
import math
from typing import Any


# A day counts as dark at 10 % of its morning forecast or less: 2 kWh of 20
# (Pascal, 8 November 2026; 15 % before 3.14.7)
LOW_YIELD_RATIO = 0.10
# Below this daily mean snow can lie on the modules. A fog day at 7 °C and
# more does not arm the full charge on its own (7 November 2026)
COLD_DAY_C = 7.0
STATUSES = {"learning", "normal", "low_yield", "low_yield_unconfirmed", "unavailable"}


class PVYieldMonitor:
    """Compare a morning full-day forecast with fresh energy after sunset.

    A partial first day, stale meter, tiny forecast or missing astronomy cannot
    arm an automatic full charge. A severe shortfall arms exactly the next
    solar day's regular night window - on a cold day (mean below 7 °C, snow
    possible) or when the day before was dark too. A single dark day of fog
    does not: tomorrow's forecast is not wrong because today's was. Without a
    temperature only the second dark day in a row arms it. Manual snow nights
    remain independent.
    """

    def __init__(self, raw: Any = None) -> None:
        self.day: date | None = None
        self.expected_kwh: float | None = None
        self.actual_kwh: float | None = None
        self.charge_day: date | None = None
        self.status = "learning"
        # The last dark day, so the next one can count as the second in a row
        self.low_day: date | None = None
        self.day_mean_c: float | None = None
        # Why the full charge was armed: "cold" or "second_day"
        self.trigger: str | None = None
        if not isinstance(raw, Mapping):
            return
        for key in ("day", "charge_day", "low_day"):
            try:
                setattr(self, key, date.fromisoformat(str(raw.get(key))))
            except ValueError:
                pass
        for key in ("expected_kwh", "actual_kwh"):
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                setattr(self, key, float(value))
        mean = raw.get("day_mean_c")
        if isinstance(mean, (int, float)) and not isinstance(mean, bool) and math.isfinite(mean):
            self.day_mean_c = float(mean)
        trigger = raw.get("trigger")
        if trigger in ("cold", "second_day"):
            self.trigger = str(trigger)
        status = raw.get("status")
        if isinstance(status, str) and status in STATUSES:
            self.status = status
        if self.charge_day is not None and self.trigger is None:
            # Armed by 3.14.6 or older, on one dark day at 15 %: judged again by
            # today's rule, which waits for a second day without a temperature
            # (Sourcery on #53)
            self.charge_day = None
            dark = (self.expected_kwh is not None and self.actual_kwh is not None
                    and self.actual_kwh <= self.expected_kwh * LOW_YIELD_RATIO)
            if dark and self.day is not None:
                self.status, self.low_day = "low_yield_unconfirmed", self.day
            else:
                self.status = "normal"

    def snapshot(self) -> dict[str, Any]:
        """Small, JSON-safe state, also used for frontend explanations."""
        return {
            "day": self.day.isoformat() if self.day else None,
            "expected_kwh": self.expected_kwh,
            "actual_kwh": self.actual_kwh,
            "charge_day": self.charge_day.isoformat() if self.charge_day else None,
            "status": self.status,
            "low_day": self.low_day.isoformat() if self.low_day else None,
            "day_mean_c": self.day_mean_c,
            "trigger": self.trigger,
        }

    def observe(
        self, now: datetime, sunrise: datetime, sunset: datetime,
        forecast_kwh: float | None, actual_kwh: float | None,
        day_mean_c: float | None = None,
    ) -> bool:
        """Return True only when a new severe shortfall has been established.

        ``day_mean_c`` is the day's mean outdoor temperature so far, None
        without a temperature source.
        """
        today = now.date()
        if self.day != today:
            self.day, self.expected_kwh, self.actual_kwh = today, None, None
            self.day_mean_c = None
            self.status = "learning"
            if self.charge_day is not None and self.charge_day < today:
                self.charge_day = None
                self.trigger = None
        if (self.status == "low_yield_unconfirmed" and self.charge_day is None
                and day_mean_c is not None and math.isfinite(day_mean_c) and day_mean_c < COLD_DAY_C):
            # The first look after sunset knew no mean yet; a later poll does
            # (Sourcery on #53)
            self.day_mean_c = round(day_mean_c, 1)
            self.status, self.trigger = "low_yield", "cold"
            self.charge_day = today + timedelta(days=1)
            return True
        if self.status in {"normal", "low_yield", "low_yield_unconfirmed"}:
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
        if day_mean_c is not None and math.isfinite(day_mean_c):
            self.day_mean_c = round(day_mean_c, 1)
        if actual_kwh <= self.expected_kwh * LOW_YIELD_RATIO:
            second = self.low_day == today - timedelta(days=1)
            cold = self.day_mean_c is not None and self.day_mean_c < COLD_DAY_C
            self.low_day = today
            if not (cold or second):
                self.status = "low_yield_unconfirmed"
                return False
            self.status = "low_yield"
            self.trigger = "cold" if cold else "second_day"
            self.charge_day = today + timedelta(days=1)
            return True
        self.status = "normal"
        return False

    def active_for(self, solar_day: date) -> bool:
        """Whether this observation applies to that window's following day."""
        return self.charge_day == solar_day
