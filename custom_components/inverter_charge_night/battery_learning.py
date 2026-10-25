"""Plan 018 part 2: what the battery really holds, takes and gives back.

The nameplate capacity is what the battery had when it was new, and the two
efficiencies are estimates typed into a form. All three can be read from the
energy balance the recorder keeps anyway - five-minute means of the battery's
SOC and power, the house load and the grid import:

- every stretch of steady charging or discharging moves the SOC by some
  percent for some kWh on the battery's DC side: that is the capacity;
- with no sun on the panels, what the house and the grid did on the AC side
  against that DC energy is the charger's or the inverter's efficiency.

This module holds the arithmetic and nothing else. The coordinator reads the
statistics and fills in the numbers.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .forecast_learning import weighted_quantile

BUCKET_S = 300  # the recorder's short-term statistics
# Below this the battery is idling, and a sign means nothing
MIN_STEADY_W = 300.0
# A stretch has to move the SOC this far over at least this long, or the SOC
# resolution of one percent is the measurement
MIN_SEGMENT_BUCKETS = 7  # 30 minutes between the first and last bucket centres
MIN_SOC_CHANGE_PCT = 10.0
# The PV meter counted nothing in the stretch: the AC side is house and grid only
MAX_PV_KWH_PER_BUCKET = 0.001

KIND_CAPACITY = "capacity"
KIND_CHARGE = "charge_efficiency"
KIND_DISCHARGE = "discharge_efficiency"
SAMPLE_KINDS = (KIND_CAPACITY, KIND_CHARGE, KIND_DISCHARGE)

# A sample outside these says the sensors disagree, not the battery
CAPACITY_SAMPLE_RANGE = (0.4, 1.3)  # times the nameplate
EFFICIENCY_SAMPLE_RANGE = (0.6, 1.0)
# What a learned value may take, whatever the samples say
CAPACITY_RANGE = (0.6, 1.05)  # times the nameplate
EFFICIENCY_RANGE = (0.75, 0.99)

SAMPLES_KEPT_DAYS = 365
SAMPLE_HALF_LIFE_DAYS = 180.0
MIN_SAMPLES = 3

STATUS_OFF = "off"
STATUS_NO_SENSORS = "no_sensors"
STATUS_NO_STATISTICS = "no_statistics"
STATUS_LEARNING = "learning"
STATUS_ACTIVE = "active"


@dataclass(frozen=True)
class Bucket:
    """One five-minute mean of everything the balance needs."""

    start: float  # epoch seconds
    soc: float
    battery_w: float
    house_w: float | None = None
    grid_w: float | None = None
    pv_kwh: float | None = None  # what the PV meter counted in it; None: no meter


@dataclass(frozen=True)
class Sample:
    start: float
    kind: str
    value: float


def charging_sign(buckets: Sequence[Bucket]) -> float:
    """+1 when the sensor counts charging as positive, -1 when as negative.

    Read from the data: while the SOC rises, the battery charges. A Kostal
    reports its battery power the other way round (24/25 October 2026).
    """
    score = 0.0
    for prev, cur in zip(buckets, buckets[1:]):
        if cur.start - prev.start == BUCKET_S:
            score += cur.battery_w * (cur.soc - prev.soc)
    return -1.0 if score < 0 else 1.0


def _trapezoid_kwh(values: Sequence[float]) -> float:
    """Energy from bucket centre to bucket centre, in kWh."""
    return (sum(values) - (values[0] + values[-1]) / 2.0) * BUCKET_S / 3600.0 / 1000.0


def _runs(buckets: Sequence[Bucket], sign: float) -> list[list[Bucket]]:
    """Gapless stretches of steady charging or discharging, cut where they could go on.

    A run touching the first or the last bucket may have started earlier or
    may still be running: measured now it would be filed shorter than it is,
    and the full one would be dropped as a duplicate tomorrow.
    """
    runs: list[list[Bucket]] = []
    current: list[Bucket] = []
    direction = 0
    for bucket in buckets:
        power = sign * bucket.battery_w
        this = 1 if power >= MIN_STEADY_W else (-1 if power <= -MIN_STEADY_W else 0)
        if (this != 0 and current and this == direction
                and bucket.start - current[-1].start == BUCKET_S):
            current.append(bucket)
            continue
        if current:
            runs.append(current)
        current = [bucket] if this != 0 else []
        direction = this
    if current:
        runs.append(current)
    first, last = buckets[0], buckets[-1]
    return [run for run in runs if run[0] is not first and run[-1] is not last]


def segment_samples(buckets: Iterable[Bucket], nameplate_kwh: float) -> list[Sample]:
    """Capacity and efficiency samples from five-minute buckets, in time order."""
    data = sorted(buckets, key=lambda bucket: bucket.start)
    if len(data) < MIN_SEGMENT_BUCKETS + 2 or nameplate_kwh <= 0:
        return []
    sign = charging_sign(data)
    samples: list[Sample] = []
    for run in _runs(data, sign):
        if len(run) < MIN_SEGMENT_BUCKETS:
            continue
        soc_change = run[-1].soc - run[0].soc
        dc_kwh = _trapezoid_kwh([sign * bucket.battery_w for bucket in run])
        charging = dc_kwh > 0
        if abs(soc_change) < MIN_SOC_CHANGE_PCT or (soc_change > 0) != charging:
            continue
        capacity = abs(dc_kwh) / abs(soc_change) * 100.0
        low, high = CAPACITY_SAMPLE_RANGE
        if low * nameplate_kwh <= capacity <= high * nameplate_kwh:
            samples.append(Sample(run[0].start, KIND_CAPACITY, capacity))
        if not all(bucket.house_w is not None and bucket.grid_w is not None
                   and bucket.pv_kwh is not None and bucket.pv_kwh <= MAX_PV_KWH_PER_BUCKET
                   for bucket in run):
            continue
        house = [float(bucket.house_w or 0.0) for bucket in run]
        grid = [float(bucket.grid_w or 0.0) for bucket in run]
        if charging:
            ac_kwh = _trapezoid_kwh([g - h for g, h in zip(grid, house)])
            efficiency = dc_kwh / ac_kwh if ac_kwh > 0 else None
            kind = KIND_CHARGE
        else:
            ac_kwh = _trapezoid_kwh([h - g for g, h in zip(grid, house)])
            efficiency = ac_kwh / -dc_kwh if dc_kwh < 0 else None
            kind = KIND_DISCHARGE
        low, high = EFFICIENCY_SAMPLE_RANGE
        if efficiency is not None and low <= efficiency <= high:
            samples.append(Sample(run[0].start, kind, efficiency))
    return samples


def weighted_median(pairs: Iterable[tuple[float, float]]) -> float | None:
    return weighted_quantile(pairs, 0.5)


class BatteryLog:
    """The samples, kept a year, each stretch once."""

    def __init__(self, raw: Any = None) -> None:
        self.samples: list[Sample] = []
        entries = raw.get("samples") if isinstance(raw, Mapping) else None
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            start, kind, value = entry.get("ts"), entry.get("kind"), entry.get("v")
            if (kind in SAMPLE_KINDS and isinstance(start, (int, float)) and not isinstance(start, bool)
                    and isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(start) and math.isfinite(value)):
                self.samples.append(Sample(float(start), str(kind), float(value)))

    def snapshot(self) -> dict[str, Any]:
        return {"samples": [{"ts": s.start, "kind": s.kind, "v": round(s.value, 4)} for s in self.samples]}

    def add(self, new: Iterable[Sample]) -> bool:
        """Add samples not seen before; True when the log changed."""
        known = {(s.start, s.kind) for s in self.samples}
        added = [s for s in new if (s.start, s.kind) not in known]
        self.samples.extend(added)
        self.samples.sort(key=lambda s: s.start)
        return bool(added)

    def prune(self, now_ts: float) -> bool:
        cutoff = now_ts - SAMPLES_KEPT_DAYS * 86400.0
        kept = [s for s in self.samples if s.start >= cutoff]
        changed = len(kept) != len(self.samples)
        self.samples = kept
        return changed

    def clear(self, kinds: Iterable[str]) -> bool:
        """Forget the samples of these kinds, for sensors that have changed."""
        drop = set(kinds)
        kept = [s for s in self.samples if s.kind not in drop]
        changed = len(kept) != len(self.samples)
        self.samples = kept
        return changed

    def count(self, kind: str) -> int:
        return sum(1 for s in self.samples if s.kind == kind)

    def estimate(self, kind: str, now_ts: float) -> float | None:
        """The recency-weighted median, or None with fewer than three samples."""
        pairs = [(s.value, 0.5 ** (max(0.0, now_ts - s.start) / 86400.0 / SAMPLE_HALF_LIFE_DAYS))
                 for s in self.samples if s.kind == kind]
        if len(pairs) < MIN_SAMPLES:
            return None
        return weighted_median(pairs)


def bounded_capacity(estimate: float | None, nameplate_kwh: float) -> float | None:
    if estimate is None or nameplate_kwh <= 0:
        return None
    low, high = CAPACITY_RANGE
    return min(high * nameplate_kwh, max(low * nameplate_kwh, estimate))


def bounded_efficiency(estimate: float | None) -> float | None:
    if estimate is None:
        return None
    low, high = EFFICIENCY_RANGE
    return min(high, max(low, estimate))
