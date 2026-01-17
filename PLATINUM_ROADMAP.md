# Platinum Roadmap

This document tracks the concrete steps required to reach **Platinum** quality scale.
Note: Platinum is only achievable for integrations accepted into Home Assistant Core.

## Phase 1: Gold Completion
- [x] End-user documentation with full entity list (README)
- [x] Example dashboards and automations (README)
- [x] Diagnostics coverage and troubleshooting guide (README + diagnostics.py)
- [x] Entity metadata completeness (device_class, state_class, category)
- [ ] Add screenshots (optional, manual)

## Phase 2: Test Coverage (100%)
- [ ] Full unit test coverage for all coordinator paths (in progress, expanded)
- [x] Config flow input validation tests
- [x] Options flow tests
- [x] Diagnostics tests for all fields
- [x] Edge cases: backup mode + date ranges
- [x] Coverage report (`pytest --cov`) at 100% (current: 100%)

## Phase 3: Full Typing
- [x] Type hints everywhere (no Any)
- [x] Mypy/pyright passes (strict mode)
- [x] Document typing rules

## Phase 4: Async & Performance Audit
- [x] Verify no blocking I/O
- [x] Ensure all service calls are async-safe
- [x] Measure CPU/memory footprint
- [x] Reduce log noise to safe levels

## Phase 5: Core Readiness
- [ ] CODEOWNERS in HA Core
- [ ] Submit integration to HA Core
- [ ] Address core review feedback

## Notes
- Platinum status requires HA Core acceptance + review.
- For custom integrations, Gold is typically the practical ceiling.
