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


def _stored_number(value: Any) -> float | None:
    """A finite stored number, or None; an integer too large for a float is malformed (Sourcery on #54)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


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
        # The armed day's (actual, expected) kWh: the day's own figures are
        # reset at midnight, before a window after it reports them
        self.armed_kwh: tuple[float, float] | None = None
        if not isinstance(raw, Mapping):
            return
        for key in ("day", "charge_day", "low_day"):
            try:
                setattr(self, key, date.fromisoformat(str(raw.get(key))))
            except ValueError:
                pass
        for key in ("expected_kwh", "actual_kwh"):
            value = _stored_number(raw.get(key))
            if value is not None and value >= 0:
                setattr(self, key, value)
        self.day_mean_c = _stored_number(raw.get("day_mean_c"))
        trigger = raw.get("trigger")
        if trigger in ("cold", "second_day"):
            self.trigger = str(trigger)
        armed = raw.get("armed_kwh")
        if isinstance(armed, (list, tuple)) and len(armed) == 2:
            actual, expected = _stored_number(armed[0]), _stored_number(armed[1])
            if actual is not None and expected is not None and actual >= 0 and expected >= 0:
                self.armed_kwh = (actual, expected)
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
        elif (self.charge_day is not None and self.armed_kwh is None
              and self.actual_kwh is not None and self.expected_kwh is not None):
            # Armed by 3.14.7, which kept no figures of its own: the day's are
            # still the armed day's until midnight (Sourcery on #54)
            self.armed_kwh = (self.actual_kwh, self.expected_kwh)

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
            "armed_kwh": list(self.armed_kwh) if self.armed_kwh is not None else None,
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
                self.armed_kwh = None
        if (self.status == "low_yield_unconfirmed" and self.charge_day is None
                and self.day_mean_c is None  # only a day that had no mean at sunset
                and day_mean_c is not None and math.isfinite(day_mean_c) and day_mean_c < COLD_DAY_C):
            # The first look after sunset knew no mean yet; a later poll does
            # (Sourcery on #53)
            self.day_mean_c = round(day_mean_c, 1)
            self.status, self.trigger = "low_yield", "cold"
            self.charge_day = today + timedelta(days=1)
            self._arm_figures()
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
            self._arm_figures()
            return True
        self.status = "normal"
        return False

    def _arm_figures(self) -> None:
        if self.actual_kwh is not None and self.expected_kwh is not None:
            self.armed_kwh = (self.actual_kwh, self.expected_kwh)

    def active_for(self, solar_day: date) -> bool:
        """Whether this observation applies to that window's following day."""
        return self.charge_day == solar_day
