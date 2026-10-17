# Setup, battery capacity and efficiency measurements

The native Home Assistant dialog collects four sections and then shows a review
menu. Reopen any section there, correct it and return to the review. Other
sections remain intact. **Save reviewed settings** validates the current entity
metadata again and only then applies the change. Closing the dialog beforehand
does not commit its edits. Home Assistant renders the buttons and field errors;
this integration uses its standard forms and menus rather than a custom frontend.

The baseline night-charge setup needs the inverter minimum-SoC number, its
grid-charge switch, the battery SoC sensor and an external daily PV energy
forecast. ICN's Evening outlook and Target SoC are outputs, not forecast inputs.
Configure today's forecast too for a window spanning midnight: the early-morning
part uses today's forecast for the same coming solar day.

## Capacity and time

Use the capacity corresponding to the battery's reported 0–100% SoC scale.
Do not subtract the configured lower SoC reserve first. On a 35.8 kWh scale with
an 8% floor, 2.864 kWh lie below that floor and 32.936 kWh remain above it.
An already usable/normalized manufacturer scale requires its corresponding
capacity; a marketing gross capacity is not automatically that scale.

All wall times use **Home Assistant's configured time zone**, not the browser
or operating-system time zone. For Germany select Europe/Berlin. The existing
calendar policy handles DST gaps/repeats and uses UTC for elapsed durations; see
`lab/EDGE_CASES.md`. An annually repeated 1 October–31 March range includes both
end dates and ignores the year. A 23:01–04:58 window deliberately leaves room
before the tariff boundaries.

**Bridge** adds consumption between the charging-window end and sufficient PV
production to the plan, using learned household load (or the configured average),
sunrise plus a delay, and the bridge reserve. **PV headroom** only reserves space
for forecast PV. Price reserves and house-connection limits are separate inputs.

## Power controls and measurements

| Purpose | KORE example | Requirement |
| --- | --- | --- |
| Normal charge ceiling | Battery Max Charge Limit (Modbus, 1038) | Readable, writable W or kW ceiling; ICN converts kW |
| AC power entering the battery | Grid to Battery Power (`devices:local/Grid2Bat_P`) | Dedicated charging measurement, not total inverter AC output |
| DC power reaching the battery | Battery Power (`devices:local:battery/P`) | Verify charging sign and device semantics; the lab reports positive charging |
| Additional total AC/DC ceiling | A **separate** documented device control | Optional numeric ceiling and entity must either both be set or both omitted |
| Unsuitable control | Active Power Setpoint (533, %) | Inverter power curtailment; rejected as a charge ceiling |

Efficiency search requires the normal ceiling and both separate power sensors.
Optional paired cumulative AC/DC energy meters improve measurement quality.
The additional AC/DC ceiling is not a prerequisite for efficiency search.
An incomplete submission re-renders feature dependencies as required fields;
disabling the feature makes those fields optional again. Explicit incompatible
units and KORE's total AC Power sensor are rejected. Unknown third-party sensor
semantics still require checking the manufacturer's documentation.

The search settles for 120 seconds, then requires at least 300 seconds and
0.3 kWh of measured AC energy per point. It rejects implausible losses and a
device that does not follow the tested setpoint. Its 100 W search resolution
does not imply 100 W accuracy on hardware. The search's smooth-loss assumption
and measurement noise limit the optimality claim; it is not an exhaustive
comparison of every possible operating point.

Configure a grid meter **and** a connection budget to constrain charging during
EV/household peaks. Merely selecting three phases does not enable load limiting.
For a manual transfer switch, configure a trustworthy island/backup contact;
ordinary control must not compete with backup supply. Use one active battery
controller per physical inverter, including integration instances that refer to
different aliases of the same device. evcc may own wallboxes while ICN owns the
battery; shared battery ownership is not certified.

## Evidence and limits

`lab/check_kostal.py` runs the actual HA config flow, KORE number/sensor entities,
pykoplenti REST authentication and Modbus against a loopback inverter. Its
efficiency campaign gives the virtual battery a known smooth conversion curve
with an 8 kW optimum, advances the physical energy balance and only accelerates
the finder's measurement clock. HA timers and the host clock are unchanged.
The recorded loss and chosen power must match the independent fixture, and
multiple actual device commands must stay inside the 15 kW ceiling. This
demonstrates algorithm/protocol behavior, not the efficiency of a physical
Kostal installation.
