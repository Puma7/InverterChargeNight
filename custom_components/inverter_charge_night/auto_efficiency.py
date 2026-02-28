"""Auto efficient charge optimization helpers."""
from __future__ import annotations

import csv
import logging
import math
import os
from typing import TYPE_CHECKING, Any

import homeassistant.util.dt as dt_util

from .const import (
    AUTO_EFFICIENCY_CSV_FILENAME,
    AUTO_EFFICIENCY_MAX_HISTORY_ENTRIES,
    AUTO_EFFICIENCY_MIN_TEST_DURATION_S,
    AUTO_EFFICIENCY_STEP_W,
    CONF_AUTO_EFFICIENCY_DATA,
    CONF_AUTO_EFFICIENT_CHARGE,
    CONF_BATTERY_CHARGE_ENERGY_ENTITY,
    CONF_CHARGE_POWER_ENTITY,
    CONF_CHARGE_SESSION_DATA,
    CONF_GRID_IMPORT_ENERGY_ENTITY,
    CONF_HOME_CONSUMPTION_ENERGY_ENTITY,
    CONF_KOSTAL_GRID_CHARGE_SWITCH,
    CONF_MAX_CHARGE_POWER_W,
    CONF_MIN_CHARGE_POWER_W,
)

if TYPE_CHECKING:  # pragma: no cover
    from . import InverterChargeNightCoordinator

_LOGGER = logging.getLogger(__name__)

_CSV_HEADER = [
    "timestamp",
    "type",
    "power_setpoint_w",
    "grid_import_wh",
    "battery_charge_wh",
    "home_consumption_wh",
    "loss_wh",
    "duration_s",
    "efficiency_pct",
    "loss_pct",
]


def _build_record(
    record_type: str,
    power_setpoint_w: int,
    grid_import_wh: float,
    battery_charge_wh: float,
    home_consumption_wh: float,
    duration_s: float,
) -> dict[str, Any]:
    """Build a standardised efficiency record from energy-meter deltas.

    Charging loss = grid_import - battery_charge - home_consumption
    Efficiency    = battery_charge / (battery_charge + loss) * 100
    """
    loss_wh = grid_import_wh - battery_charge_wh - home_consumption_wh
    loss_wh = max(0.0, loss_wh)

    denominator = battery_charge_wh + loss_wh
    efficiency_pct = 0.0
    loss_pct = 0.0
    if denominator > 0:
        efficiency_pct = round((battery_charge_wh / denominator) * 100.0, 2)
        loss_pct = round(100.0 - efficiency_pct, 2)
    efficiency_pct = max(0.0, min(100.0, efficiency_pct))
    loss_pct = max(0.0, min(100.0, loss_pct))

    return {
        "timestamp": dt_util.now().isoformat(),
        "type": record_type,
        "power_setpoint_w": power_setpoint_w,
        "grid_import_wh": round(grid_import_wh, 2),
        "battery_charge_wh": round(battery_charge_wh, 2),
        "home_consumption_wh": round(home_consumption_wh, 2),
        "loss_wh": round(loss_wh, 2),
        "duration_s": int(duration_s),
        "efficiency_pct": efficiency_pct,
        "loss_pct": loss_pct,
    }


class AutoEfficiencyOptimizer:
    """Encapsulate auto efficient charge finder logic."""

    def __init__(self, coordinator: InverterChargeNightCoordinator) -> None:
        self._coordinator = coordinator

    # ------------------------------------------------------------------
    # Persistent data helpers
    # ------------------------------------------------------------------

    def get_data(self) -> dict[str, Any]:
        """Load persisted auto efficiency data from entry options."""
        data = dict(self._coordinator.entry.options.get(CONF_AUTO_EFFICIENCY_DATA, {}))
        data.setdefault("history", {})
        data.setdefault("detailed_log", [])
        return data

    def save_data(self, data: dict[str, Any]) -> None:
        """Persist auto efficiency data to entry options."""
        log: list[dict[str, Any]] = data.get("detailed_log", [])
        if len(log) > AUTO_EFFICIENCY_MAX_HISTORY_ENTRIES:
            data["detailed_log"] = log[-AUTO_EFFICIENCY_MAX_HISTORY_ENTRIES:]
        options = dict(self._coordinator.entry.options)
        options[CONF_AUTO_EFFICIENCY_DATA] = data
        self._coordinator.hass.config_entries.async_update_entry(
            self._coordinator.entry, options=options
        )

    def get_session_data(self) -> dict[str, Any]:
        """Load persisted charge session data from entry options."""
        return dict(
            self._coordinator.entry.options.get(CONF_CHARGE_SESSION_DATA, {})
        )

    def save_session_data(self, data: dict[str, Any]) -> None:
        """Persist charge session data to entry options."""
        options = dict(self._coordinator.entry.options)
        options[CONF_CHARGE_SESSION_DATA] = data
        self._coordinator.hass.config_entries.async_update_entry(
            self._coordinator.entry, options=options
        )

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def _csv_path(self) -> str:
        """Return the absolute path for the efficiency CSV log."""
        config_dir: str = self._coordinator.hass.config.path("")
        return os.path.join(config_dir, AUTO_EFFICIENCY_CSV_FILENAME)

    def append_csv(self, record: dict[str, Any]) -> None:
        """Append a single record to the CSV log file."""
        path = self._csv_path()
        write_header = not os.path.exists(path)
        try:
            with open(path, "a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=_CSV_HEADER)
                if write_header:
                    writer.writeheader()
                writer.writerow({k: record.get(k, "") for k in _CSV_HEADER})
        except OSError as err:
            _LOGGER.warning("Could not write efficiency CSV: %s", err)

    # ------------------------------------------------------------------
    # Energy meter reading helper
    # ------------------------------------------------------------------

    def _read_energy_kwh(self, entity_id: str | None) -> float | None:
        """Read a cumulative energy sensor value in kWh, return None if unavailable."""
        if not entity_id:
            return None
        state = self._coordinator.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _snapshot_meters(self) -> dict[str, float] | None:
        """Read all 3 energy meters and return snapshot dict, or None if any is unavailable."""
        grid = self._read_energy_kwh(
            self._coordinator.config.get(CONF_GRID_IMPORT_ENERGY_ENTITY)
        )
        battery = self._read_energy_kwh(
            self._coordinator.config.get(CONF_BATTERY_CHARGE_ENERGY_ENTITY)
        )
        home = self._read_energy_kwh(
            self._coordinator.config.get(CONF_HOME_CONSUMPTION_ENERGY_ENTITY)
        )
        if grid is None or battery is None or home is None:
            return None
        return {
            "grid_import_kwh": grid,
            "battery_charge_kwh": battery,
            "home_consumption_kwh": home,
        }

    # ------------------------------------------------------------------
    # Regular charge session tracking (meter-snapshot based)
    # ------------------------------------------------------------------

    def start_charge_session(self) -> None:
        """Begin tracking a regular (non-test) charge session."""
        snapshot = self._snapshot_meters()
        self._coordinator._session_active = True
        self._coordinator._session_start = dt_util.now()
        self._coordinator._session_start_snapshot = snapshot
        _LOGGER.debug("Charge session tracking started (snapshot=%s)", snapshot)

    def finalize_charge_session(self) -> None:
        """Finalize the regular charge session and persist the result."""
        if (
            not self._coordinator._session_active
            or not self._coordinator._session_start
        ):
            self.reset_charge_session()
            return

        duration = (dt_util.now() - self._coordinator._session_start).total_seconds()
        start_snap = self._coordinator._session_start_snapshot
        end_snap = self._snapshot_meters()

        if duration < 60 or start_snap is None or end_snap is None:
            _LOGGER.debug("Charge session too short or meter snapshots unavailable, discarding")
            self.reset_charge_session()
            return

        grid_wh = (end_snap["grid_import_kwh"] - start_snap["grid_import_kwh"]) * 1000.0
        battery_wh = (end_snap["battery_charge_kwh"] - start_snap["battery_charge_kwh"]) * 1000.0
        home_wh = (end_snap["home_consumption_kwh"] - start_snap["home_consumption_kwh"]) * 1000.0

        if grid_wh <= 0 and battery_wh <= 0:
            _LOGGER.debug("Charge session had no energy flow, discarding")
            self.reset_charge_session()
            return

        charge_entity = self._coordinator.config.get(CONF_CHARGE_POWER_ENTITY)
        power_w = 0
        if charge_entity:
            pw = self._coordinator._get_power_w(charge_entity)
            power_w = int(pw) if pw is not None else 0

        record = _build_record("regular_charge", power_w, grid_wh, battery_wh, home_wh, duration)
        _LOGGER.info(
            "Charge session finished: grid=%.1f Wh, battery=%.1f Wh, home=%.1f Wh, "
            "loss=%.1f Wh, %.2f%% efficiency over %d s",
            grid_wh,
            battery_wh,
            home_wh,
            record["loss_wh"],
            record["efficiency_pct"],
            int(duration),
        )

        session_data = self.get_session_data()
        session_data["last_session"] = record
        self.save_session_data(session_data)

        data = self.get_data()
        data["detailed_log"].append(record)
        self.save_data(data)

        self.append_csv(record)
        self.reset_charge_session()

    def reset_charge_session(self) -> None:
        """Reset regular charge session tracking state."""
        self._coordinator._session_active = False
        self._coordinator._session_start = None
        self._coordinator._session_start_snapshot = None

    # ------------------------------------------------------------------
    # Auto-efficiency test helpers
    # ------------------------------------------------------------------

    def round_power_step(self, value_w: float, step_w: int = AUTO_EFFICIENCY_STEP_W) -> int:
        """Round power to nearest step."""
        return int(round(value_w / step_w) * step_w)

    def select_next_test_power_w(self) -> int | None:
        """Select next power setpoint for auto efficiency testing."""
        min_w = int(self._coordinator.config.get(CONF_MIN_CHARGE_POWER_W, 1000))
        max_w = int(self._coordinator.config.get(CONF_MAX_CHARGE_POWER_W, 10000))
        if min_w >= max_w:
            return None

        step_w = AUTO_EFFICIENCY_STEP_W
        data = self.get_data()
        history = {int(k): v for k, v in data.get("history", {}).items()}
        range_min = int(data.get("range_min_w", min_w))
        range_max = int(data.get("range_max_w", max_w))
        range_min = max(min_w, range_min)
        range_max = min(max_w, range_max)
        if range_max - range_min < step_w:
            return None

        phi = (math.sqrt(5) - 1) / 2  # golden ratio
        for _ in range(5):
            c = self.round_power_step(range_max - phi * (range_max - range_min), step_w)
            d = self.round_power_step(range_min + phi * (range_max - range_min), step_w)
            c = max(range_min, min(range_max, c))
            d = max(range_min, min(range_max, d))
            if c == d:
                d = min(range_max, c + step_w)
            if c in history and d in history:
                if history[c] <= history[d]:
                    range_max = d
                else:
                    range_min = c
                data["range_min_w"] = range_min
                data["range_max_w"] = range_max
                self.save_data(data)
                continue
            if c not in history:
                return c
            if d not in history:
                return d
        return None

    def reset_test_state(self) -> None:
        """Reset current auto test state."""
        self._coordinator._auto_test_active = False
        self._coordinator._auto_test_power_w = None
        self._coordinator._auto_test_start = None
        self._coordinator._auto_test_start_snapshot = None
        self._coordinator._auto_energy_sent_wh = 0.0
        self._coordinator._auto_energy_received_wh = 0.0

    async def start_test(self, power_w: int) -> None:
        """Start auto efficiency test at given power."""
        await self._coordinator._set_ac_charge_limit_w(power_w)
        snapshot = self._snapshot_meters()
        self._coordinator._auto_test_active = True
        self._coordinator._auto_test_power_w = power_w
        self._coordinator._auto_test_start = dt_util.now()
        self._coordinator._auto_test_start_snapshot = snapshot
        self._coordinator._auto_energy_sent_wh = 0.0
        self._coordinator._auto_energy_received_wh = 0.0
        _LOGGER.info("Auto efficiency test started at %d W (snapshot=%s)", power_w, snapshot)

    def finalize_test(self) -> None:
        """Finalize auto test and persist efficiency result."""
        if (
            not self._coordinator._auto_test_active
            or not self._coordinator._auto_test_start
            or self._coordinator._auto_test_power_w is None
        ):
            self.reset_test_state()
            return

        duration = (dt_util.now() - self._coordinator._auto_test_start).total_seconds()
        start_snap = self._coordinator._auto_test_start_snapshot
        end_snap = self._snapshot_meters()

        if (
            duration < AUTO_EFFICIENCY_MIN_TEST_DURATION_S
            or start_snap is None
            or end_snap is None
        ):
            _LOGGER.info("Auto efficiency test discarded (duration < 30 min or no snapshots)")
            self.reset_test_state()
            return

        grid_wh = (end_snap["grid_import_kwh"] - start_snap["grid_import_kwh"]) * 1000.0
        battery_wh = (end_snap["battery_charge_kwh"] - start_snap["battery_charge_kwh"]) * 1000.0
        home_wh = (end_snap["home_consumption_kwh"] - start_snap["home_consumption_kwh"]) * 1000.0

        if grid_wh <= 0 and battery_wh <= 0:
            _LOGGER.info("Auto efficiency test discarded (no energy flow)")
            self.reset_test_state()
            return

        loss_wh = max(0.0, grid_wh - battery_wh - home_wh)
        denominator = battery_wh + loss_wh
        loss_ratio = (loss_wh / denominator) if denominator > 0 else 1.0
        loss_ratio = max(0.0, min(loss_ratio, 1.0))

        record = _build_record(
            "auto_test",
            self._coordinator._auto_test_power_w,
            grid_wh,
            battery_wh,
            home_wh,
            duration,
        )

        data = self.get_data()
        history = dict(data.get("history", {}))
        history[str(self._coordinator._auto_test_power_w)] = loss_ratio
        data["history"] = history
        data["detailed_log"].append(record)

        best_loss = data.get("best_loss")
        if best_loss is None or loss_ratio < best_loss:
            data["best_loss"] = loss_ratio
            data["best_power_w"] = self._coordinator._auto_test_power_w
            _LOGGER.info(
                "New best auto efficiency: %.2f%% at %d W (loss %.4f)",
                record["efficiency_pct"],
                self._coordinator._auto_test_power_w,
                loss_ratio,
            )
        else:
            _LOGGER.info(
                "Auto efficiency recorded: %.2f%% at %d W (loss %.4f)",
                record["efficiency_pct"],
                self._coordinator._auto_test_power_w,
                loss_ratio,
            )

        self.save_data(data)
        self.append_csv(record)
        self.reset_test_state()

    # ------------------------------------------------------------------
    # Main auto-charge handler
    # ------------------------------------------------------------------

    async def handle_auto_charge(self) -> None:
        """Handle auto efficient charging logic."""
        if not self._coordinator.auto_efficient_charge:
            if self._coordinator._auto_test_active:
                self.finalize_test()
            return
        if self._coordinator._is_backup_active():
            if self._coordinator._auto_test_active:
                self.reset_test_state()
            return

        charge_entity = self._coordinator.config.get(CONF_CHARGE_POWER_ENTITY)
        grid_entity = self._coordinator.config.get(CONF_GRID_IMPORT_ENERGY_ENTITY)
        battery_entity = self._coordinator.config.get(CONF_BATTERY_CHARGE_ENERGY_ENTITY)
        home_entity = self._coordinator.config.get(CONF_HOME_CONSUMPTION_ENERGY_ENTITY)
        if not charge_entity or not grid_entity or not battery_entity or not home_entity:
            if not self._coordinator._auto_missing_entities_logged:
                _LOGGER.warning(
                    "Auto efficient charge enabled but required entities are missing "
                    "(charge_power, grid_import, battery_charge, home_consumption)."
                )
                self._coordinator._auto_missing_entities_logged = True
            return

        grid_charge_switch = self._coordinator.config.get(CONF_KOSTAL_GRID_CHARGE_SWITCH)
        grid_on = False
        if grid_charge_switch:
            state = self._coordinator.hass.states.get(grid_charge_switch)
            grid_on = state is not None and state.state == "on"

        if not grid_on or self._coordinator.target_reached:
            if self._coordinator._auto_test_active:
                self.finalize_test()
            return

        if self._coordinator._auto_test_active:
            return

        candidate = self.select_next_test_power_w()
        if candidate is None:
            data = self.get_data()
            best_power = data.get("best_power_w")
            if isinstance(best_power, int):
                await self._coordinator._set_ac_charge_limit_w(best_power)
                if self._coordinator.auto_efficient_charge:
                    self._coordinator.auto_efficient_charge = False
                    data = dict(self._coordinator.entry.data)
                    data[CONF_AUTO_EFFICIENT_CHARGE] = False
                    self._coordinator.hass.config_entries.async_update_entry(
                        self._coordinator.entry, data=data
                    )
                    _LOGGER.info("Auto efficient charge finder completed, disabling switch")
            return

        await self.start_test(candidate)
