# KostalKore: wish list from the Inverter Charge Night field nights (October 2026)

A to-do list for an agent working on the KostalKore repository (`Puma7/KostalKore`). Each item
names what was seen in the field on a PLENTICORE G3 (KostalKore 3.0.13, Home Assistant 2026.10,
night of 20/21 October 2026), what to change, and how to know it is done. Inverter Charge Night
(ICN) 3.10.8 works around all of them on its side; with these changes the workarounds become
unnecessary.

## 1. Readback tolerance for float power registers

**Seen.** `modbus_number.py` confirms a write only when the readback equals the written value
(`math.isclose(..., rel_tol=1e-6, abs_tol=1e-6)`). Register 1038 reads back as a float near, not
at, the written value:

```
wrote 1000.0,  read 997.8641967773438 / 998.6371459960938 / 999.0614013671875 / 999.9359130859375
wrote 12540.0, read 12539.4560546875
```

Every such write raises `ModbusClientError("Readback is unconfirmed for bat_max_charge_limit")`
and logs `Read-back mismatch` as a WARNING. Callers that treat the error as a failed write stop
grid charging; ICN did, every 15 minutes, all night.

**Change.** For the power registers (1034, 1038, 1040, 1280, 1282) accept a readback within
`max(5 W, 0.5 % of the written value)`. Keep the exact comparison for setpoints where a small
difference matters (for example min SOC, the curtailment percentage).

**Also.** Put the observed value into the exception (for example `ModbusClientError(...,
observed=997.86)`) and into the entity attributes (`last_readback`, `last_write_confirmed`), so
a caller can judge a mismatch itself.

**Done when.** A test writes 1000 to an emulated 1038 that reads back 998.5 and the service call
succeeds; 1000 against 1200 still raises.

## 2. Keepalive for 1034 / 1038 / 1040, as for the G3 registers 1280 / 1282

**Seen.** 1000 W written to 1038 and confirmed at 23:01:05; about 8 kW charging at 23:12, with no
other writer on the register in between (37 writes that night, all from ICN). The inverter hands
battery control back to its own management unless the Modbus values arrive again within its
timeout (30–60 s). The same happened at 03:10 (0 W confirmed, 22.2 kW read at 03:16). KORE keeps
1280/1282 alive (`_run_keepalive`) but not 1038.

**Change.** When a value is written to 1034, 1038 or 1040 through the HA number entity, rewrite
it cyclically until it is released, with the interval derived from the inverter's timeout (as
`_get_keepalive_interval` does from 1288). Release on: a write of the inverter's own value, a
dedicated release service (or writing a sentinel), entry unload, HA stop, and a lost connection
generation (as the G3 keepalive already does). Expose `keepalive_active`,
`keepalive_interval_s` and `last_keepalive` as attributes.

**Done when.** With an emulator that drops an unrefreshed 1038 value after 30 s, a single write
holds for an hour; after release the inverter's own value comes back within the timeout. ICN can
then detect `keepalive_active` and skip its own keepalive (`CHARGE_LIMIT_KEEPALIVE_S`).

## 3. Publish the readback into the entity state at once

**Seen.** 1038 is polled with the slow registers (`SLOW_GROUPS`, about every 30 s). After a write
the HA state shows the old value until the next slow poll, and every fast poll in between
republishes that old value with a new `last_reported`. A caller cannot tell a fresh reading
from a retained one; ICN now waits up to 12 s for a *changed* value.

**Change.** After the write's own readback, update the coordinator data for that register with
the read value (and its observation time), so the entity state reflects the device at once. Do
not refresh `last_reported` for retained slow values, or mark them (`retained: true`).

**Done when.** Right after `number.set_value` returns (or raises), the entity state shows the
value read back by the write.

## 4. `Battery:ManualCharge` is a forced charge

**Seen.** Within a second of `Battery:ManualCharge = 1` the 1038 limit read 22.3 kW (the
inverter's own) instead of the 6.7 kW just written, and a limit written while it was on was
ignored. Grid draw went up to 12 kW for the seconds it was on. The Modbus min SOC (1042) read one
point above the configured min SOC for a minute or two each time (44 → 45 %). With
`ManualCharge = 0` the same inverter charged from the grid up to a raised min SOC by itself,
under the written 1038 limit.

**Change.**
- Document the switch as "forced charge at full power", in the entity description and the
  README, and that it ignores 1038.
- Offer a controlled way to charge from the grid at a given power, for example a
  "battery charge setpoint" entity on 1034 (negative = charge) with the keepalive of item 2.
  ICN's efficiency search needs exactly that: a few minutes at a fixed charge power, measuring
  grid-to-battery against battery input.

**Done when.** The README says what ManualCharge does to 1038, and a setpoint entity charges
the emulated battery at the written power for as long as it is kept alive.

## 5. Log volume of repeated identical writes

**Seen.** Every write with a float readback logs a WARNING. With a keepalive every 20 s that is
180 warnings per hour of charging.

**Change.** Log a mismatch that is within the tolerance of item 1 at DEBUG, and rate-limit
repeated identical mismatches (once per value per hour).

## 6. Expose the timeouts

**Change.** Expose the G3 fallback time (1288) and the fallback limits (1284/1286) as sensors
or attributes, and the battery-management timeout that applies to 1034/1038/1040 if the
inverter offers it, so integrations can choose their keepalive interval from the device rather
than guess.

## 7. Nightly 03:10 fault and log noise (Puma7/KostalKore#85)

**Seen.** At about 03:10 every night the inverter answers some reads with *Server device
failure* (`exc=0x04`) for a few seconds, and KORE publishes zeros as readings: the battery SoC
drops to 0 % for one sample, and 0 Hz / 0 V raise two FIRE SAFETY [HIGH] alerts. The cycle
reports success. Separately, `safe_home_power_w` logs a WARNING for every negative
`home_from_grid` value - measurement noise between -0.07 and -12.75 W - three times per poll:
2202 lines in 12.7 hours.

**Change.** Hold the last good value (or report unavailable) for physically impossible samples
in a cycle that saw `exc=0x04`; raise the grid alerts only when the condition persists. Clamp
small negative home power silently and log a real one once, rate-limited.

**Why ICN cares.** The planner and the efficiency search read the battery SoC; a single 0 %
sample at 03:10 is a battery that looks empty.

## 8. Emulator and tests

Extend the KostalKore test emulator with the three field quirks, each behind a flag: float
readback offset on 1038, `ManualCharge` lifting 1038 to the inverter's own value and ignoring
writes while on, and a lapse of 1034/1038/1040 to the inverter's own value after the timeout.
ICN's lab already models them (`lab/custom_components/icn_simulator/kostal.py`:
`readback_offset_w`, `manual_charge_limit_w`, `limit_fallback_s`).
