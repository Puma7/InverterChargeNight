# Changelog

All notable changes to the **Inverter Charge Night** integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **House connection limit** - with a grid import sensor and the main fuse size (or a maximum
  continuous power) configured, the battery charge power is held so that the total grid import
  stays inside a continuous-load budget. Meant for the cheap-tariff window, where wallboxes,
  heat pump and battery run for hours at once and the meter terminals are the weak point. New
  sensor `grid_charge_headroom` shows what is left for the battery. The limit applies in both
  planner modes, never engages outside a window, and every failure path charges *less*.
- **Discharge block with a fallback that always works** - the battery no longer discharges into
  the house during the window. The integration uses the inverter's block switch if there is one,
  otherwise a discharge power limit, otherwise it raises the min SOC to the charge level the
  window started at and restores it at the window end. The last way needs no vendor feature at
  all. New `discharge_block_switch` and `discharge_block_mode` settings, new `inverter_floor_soc`
  and `discharge_block` attributes on `calculated_soc`.
- **Snow override** - `number.inverter_charge_night_snow_nights` charges the next N nights to the
  maximum SOC, for when snow on the modules makes the PV forecast wrong.
- **Efficiency search sensor** (`sensor.…_efficiency_search`) - what the search is doing, the
  whole series of measured losses, the search range, and why the last measurement was discarded.
- **Optional energy meters for the efficiency search** (`charge_energy_sent_entity`,
  `charge_energy_received_entity`) - two kWh meters measure the charging loss exactly.
- **Planner v2** (bridge mode): the target SOC bridges from the window end until PV covers the
  house load, and leaves headroom for the next day's forecast.
- Config wizard with explanations and a reconfigure flow, repair issues on a broken setup, state
  that survives a restart, and a CI matrix testing the minimum and the current Home Assistant
  plus an end-to-end test against a real HA core.

- **German translation** (`translations/de.json`) — the wizard, every field description, the
  entity names and the error messages, checked against `strings.json` by a test so it cannot
  fall behind.

### Changed

- **The efficiency search now measures what it claims to measure.** It waits out the ramp to a
  new setpoint, integrates the charge power between sensor readings instead of once per poll,
  and can use two kWh meters instead of the power sensors. A measurement is only recorded when
  the inverter really charged at the power under test, long enough, with enough energy, and
  with a loss a charger can physically have — a loss at or below zero used to be clamped to
  zero, which made a pair of sensors on the same side of the charger win the search for good.
  It also takes several measurements per window instead of one per night, so the search
  usually finishes inside a single window.
- **Entity names.** The operation mode select and the skip switch had no translated name, so
  Home Assistant showed them — next to the main switch — as three entries all called "Inverter
  Charge Night". They are now Automation, Efficiency search, Skip the next window, Target SOC
  override, Window active and Target SOC.
- The efficiency finder is subordinate to the house connection limit: it does not start a test
  the connection cannot carry, and a running test is abandoned when the house load rises.
- The min SOC written to the inverter and the charge target are now two separate values. With
  the min SOC discharge block in use the inverter shows the higher floor during the window; it
  is restored at the window end.
- Minimum supported Home Assistant version raised to 2025.2.0 (`runtime_data`, reconfigure flow).

## [2.0.0] - 2026-02-28

### Added

- **Morning Discharge mode** — new operation mode that discharges the battery before sunrise, feeding energy to the grid at high-price morning hours and making room for solar production during the day.
- **Operation Mode selector** (`select.inverter_charge_night_operation_mode`) — switch between `Night Charge` and `Morning Discharge` at runtime. Switching modes while active safely resets the inverter first.
- **Skip Next switch** (`switch.inverter_charge_night_skip_next`) — 24-hour override that skips the next window cycle. Auto-expires after 24 hours. Useful when e.g. charging an EV and you want to keep full battery.
- **PV Forecast Today entity** (`pv_forecast_today_entity`) — optional config field for today's PV forecast (e.g. `sensor.solcast_forecast_today`). Used automatically when the window runs after midnight.
- **Force Discharge switch** (`force_discharge_switch`) — optional config field for a switch entity that forces battery discharge to grid (e.g. via Kostal Modbus). Turned on during active discharge, turned off when target SOC is reached or window ends.
- **Time-based forecast selection** — the integration automatically picks the correct forecast entity based on time of day:
  - Before noon (00:00–11:59): today's forecast (solar production happens today)
  - After noon (12:00–23:59): tomorrow's forecast (planning for next solar day)
  - Falls back to whichever entity is configured if only one is set.
- **`_stop_force_discharge()` method** — safely turns off the force discharge switch when target SOC is reached.
- **`_control_discharge()` method** — new inverter control flow for discharge mode: sets min SOC as floor, ensures grid charge is off, activates force discharge switch.
- `operation_mode` and `skip_next` exposed as sensor attributes and in diagnostics.
- 41 new tests covering all new functionality (154 total, 100% coverage).

### Changed

- **`_async_update_data`** now dispatches to `_control_kostal` (charge) or `_control_discharge` (discharge) based on operation mode.
- **`_is_target_reached`** helper handles both directions: `current >= target` for charge, `current <= target` for discharge.
- **Battery SOC listener** is now mode-aware for target-reached detection.
- **`_reset_settings`** also turns off the force discharge switch during cleanup.
- **`_on_window_start` / `_on_window_end`** include mode-aware logging.
- **`_check_current_window`** checks `skip_next` flag before evaluating window state.
- Config flow and options flow now include `operation_mode`, `pv_forecast_today_entity`, and `force_discharge_switch` fields.
- `strings.json` updated with labels and descriptions for all new entities and config fields.
- Platform list now includes `Platform.SELECT`.

### Fixed

- All mypy and pyright strict-mode errors resolved.

## [1.0.3] - 2025-01-01

### Changed

- Audit hardening for production readiness.
- Improved type annotations for platinum compliance.

## [1.0.2] - 2025-01-01

### Changed

- Hardened nightly trigger reliability.

## [1.0.0] - 2025-01-01

### Added

- Initial release.
- SOC calculation based on PV forecast with configurable error margin.
- Kostal inverter control (min SOC and grid charge switch).
- Time-based activation with configurable window (supports overnight ranges).
- Automatic reset at end time with original value restoration.
- Manual SOC override via number entity.
- Auto Efficient Charge Finder with golden-section search.
- Backup/island mode detection.
- Optional active date range restriction.
- Periodic verification of inverter min SOC.
- Battery SOC listener for immediate target-reached detection.
- Restart recovery (preserves target SOC across HA restarts).
- Comprehensive error handling and safety mechanisms.
- Rate limiting to prevent excessive service calls / EEPROM wear.
- Support for multiple Solcast forecast data formats.
- Diagnostics support.
- UI-based configuration via config flow.
