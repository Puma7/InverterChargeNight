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
