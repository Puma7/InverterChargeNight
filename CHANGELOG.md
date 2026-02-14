# Changelog

All notable changes to this project are documented in this file.

## 1.0.2 - 2026-02-14

- Added guaranteed min-SOC enforcement at window start. The integration now always applies target min-SOC at start time, even when battery SOC is already above target and charging is not started.
- Added trigger self-healing for start/end window scheduling. Missing time triggers are detected and re-registered automatically during setup, config updates, and coordinator refresh cycles.
- Improved min-SOC write robustness by centralizing enforcement and handling service-call failures without stopping the full control flow.
- Extended tests for trigger recovery and min-SOC enforcement paths.

## 1.0.1 - previous

- Major refactors for strict typing, full coverage, and coordinator stability improvements.
