# Communication failure campaign

Run `python lab/check_communication.py` after stopping the interactive lab and
evcc (`lab/manage_evcc.ps1 stop`, `lab/manage.ps1 stop`). Ports 80/1502 must be
free. Restart both afterward. This campaign uses a disposable HA configuration
and database; it never disables the host's Internet or Wi-Fi.

The path is official evcc → KORE Modbus proxy → KORE upstream Modbus client →
virtual inverter. Grid power, phase currents/voltages and battery SoC use that
proxy; wallboxes and other measurements use the local device adapter. ICN uses
real KORE entities. The simulator separately interrupts REST and Modbus; losing
either link does **not** clear physical last setpoints.

Assertions cover real grid readings; both authenticated HA read-only diagnosis
actions; active ICN charging; Modbus loss and surviving REST OFF; REST loss and
surviving Modbus zero; stale display-cache unavailability; both links lost with
explicitly unconfirmed stop; retained captures instead of raising a limit during
failed cleanup; fresh observations and cleanup after recovery; proxy restart;
partial TCP client disappearance; expected evcc traffic loss and watchdog
recovery. `reports/communication.json` records every assertion. Other campaigns
cover current-window/deadline recovery, physical load cases and calendar edges.

ICN watches required entity loss/recovery, including non-finite numeric states,
and requests an immediate refresh instead of waiting for the normal poll. An
active window requests zero AC charging power and grid-charge OFF through the
remaining paths. Failed writes do not become successful setpoint records.
Configured forced export is stopped as well; loss of that control is explicit
in the incident state. The house/backup discharge ceiling is not zeroed by this
export-stop path. Unknown REST switch values remain unknown, and non-finite
numeric commands/readbacks cannot count as successful control or confirmation.
Window cleanup requires HA OFF feedback before restoring higher limits, retaining
the captured settings and retry state until cleanup can complete.

Each integration keeps one scoped HA persistent notification per affected
channel. Recovery remains visible, incidents are counted rather than every
failed poll, and repeating/ongoing problems remain diagnosable. A notification
can say **HA reports grid charging OFF**, or **device stop is unconfirmed**.
Neither service-call acknowledgement nor an old cached OFF is proof of physical
electrical isolation.

`inverter_charge_night.communication_diagnostics` and
`kostal_kore.communication_diagnostics` are read-only HA response actions. They
are useful for automations and future authenticated Claude/Codex connectors;
they do not expose an unauthenticated control endpoint. The integrations also
include communication health in downloaded diagnostics. Temporarily enable the
respective integration's debug logging for a reproduction, then disable it.

Internet-only forecast loss is separate from local inverter/charger transport
loss. The local loop can continue with fixed tariffs and existing conservative
forecast fallbacks while safety telemetry remains trustworthy. Missing EVCC
traffic alone is an alert, not proof of an overloaded connection: ICN still
needs its independent current grid measurement and all existing limits.

Software cannot guarantee a device stop after every control path or HA itself
fails. Circuit/inverter/BMS protection and a verified device-local command
timeout remain necessary. No real hardware behavior is certified by this lab.
