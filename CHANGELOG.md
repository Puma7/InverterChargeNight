# Changelog

All notable changes to this project are documented in this file.

## 1.0.3 - 2026-02-14

- Reduced trigger self-heal log noise during startup by skipping trigger-missing warnings before initial trigger bootstrap is complete.
- Improved forecast parsing robustness by preferring entity unit metadata (`Wh`/`kWh`) and keeping a fallback heuristic for entities without units.
- Fixed best charge power sensor typing to accept both integer and float values.
- Added missing options-flow translation key for `auto_efficiency_requires_power_entities` and clarified the message wording.
- Added explicit Home Assistant minimum version key to manifest for clearer compatibility signaling.

## 1.0.2 - 2026-02-14

- Added guaranteed min-SOC enforcement at window start. The integration now always applies target min-SOC at start time, even when battery SOC is already above target and charging is not started.
- Added trigger self-healing for start/end window scheduling. Missing time triggers are detected and re-registered automatically during setup, config updates, and coordinator refresh cycles.
- Improved min-SOC write robustness by centralizing enforcement and handling service-call failures without stopping the full control flow.
- Extended tests for trigger recovery and min-SOC enforcement paths.

## 1.0.1 - previous

- Major refactors for strict typing, full coverage, and coordinator stability improvements.
