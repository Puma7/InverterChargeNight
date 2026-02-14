# Async and Performance Audit

## Async audit (Phase 4)
- All service calls use `hass.services.async_call`
- Background work uses `hass.async_create_background_task`
- Periodic loops use `asyncio.sleep`
- No synchronous I/O (file/network) in runtime paths

## Performance considerations
- Update interval is configurable (`update_interval`)
- EEPROM wear guard: min SOC updates only when change > 0.5
- Command delay is configurable (`command_delay`) to reduce inverter checks
- Logs are informative but not noisy in normal paths

## Manual checks performed
- Reviewed coordinator update path for blocking calls
- Verified listeners are async and avoid blocking operations

## Measurements (2026-01-17)
Commands:
- `python scripts/perf_snapshot.py > artifacts/perf/snapshot.txt`
- `python -m cProfile -o artifacts/perf/profile.pstats scripts/perf_snapshot.py`
- `python -c "import pstats; pstats.Stats('artifacts/perf/profile.pstats').sort_stats('cumtime').print_stats(30)" > artifacts/perf/profile.txt`

Snapshot (see `artifacts/perf/snapshot.txt`):
- elapsed_s=0.116742
- mem_current_kib=312.49
- mem_peak_kib=336.92

Profile summary (see `artifacts/perf/profile.txt`):
- Dominant cost is module import and HA framework initialization; core integration logic is not a hotspot.
