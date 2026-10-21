# Changelog

All notable changes to the **Inverter Charge Night** integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.10.8] - 2026-10-21

### Fixed
- **Grid charging is no longer switched on and off every 15 minutes all night.** The night of
  20/21 October, from the field log: each poll switched `Battery:ManualCharge` on, and a
  second later switched it off again - 28 relay switchings, each with a grid draw of up to
  12 kW for a few seconds. Three causes worked together:
  - KORE compares its readback exactly, and the Kostal answers a written 1000 W with 997.9 to
    999.9 W. Every write counted as failed, and a failed protective write pauses grid
    charging. A write KORE calls unconfirmed now counts as held when the next reading has
    moved to within 100 W of it. KORE republishes the old value with a new timestamp on every
    fast poll, so only a changed value is evidence; the reading may take a few seconds
    (`LIMIT_FEEDBACK_WAIT_S`).
  - On the Kostal, `Battery:ManualCharge` is a forced charge: within a second of switching it
    on the charge limit read 22.3 kW, a limit written while it is on is ignored, and the min SOC
    register read one point higher for a minute or two (the 44/45 % steps every 15 minutes in
    the history - the integration wrote the min SOC three times that night). The limit
    is now written again right after switching on - KORE reads that one back directly - and
    rechecked `SWITCH_ON_RECHECK_S` later. If switching on lifted it, grid charging goes off at
    once, stays off for the rest of the window (a restart inside it included), and the
    planned limit is written back. The raised min SOC keeps charging the battery, under that
    limit, as it did through the whole of that night. Right after the switch-on KORE may still
    show the switch as off - it publishes it with its next settings poll - so the switch-off
    there does not rely on that state (found in the lab). The switch-off does not depend on the
    optional house connection limit either: without it, the switch was suspended but left on,
    and the forced charge ran on to the target (Codex and CodeRabbit on #38). Only evidence of a
    forced charge suspends the switch - a reading above the limit, or a failed rewrite right after
    switching on; a write lost to a Modbus timeout no longer costs an inverter that needs the
    switch its night (CodeRabbit on #39). The reading has to be above anything written, not only
    the new limit: a failed lowering write leaves the earlier, higher limit in it (Codex on #39).
  - The min SOC was raised before the charge limit was written, so the Kostal charged at its
    own 22 kW for the seconds in between (the spike at 23:01). A floor above the battery
    level now goes on only after the planned limit, and not at all in a poll where that limit
    could not be written; the next poll tries again (Codex on #38). A plan that writes nothing on
    purpose, or a limit already reported as not held, does not hold the floor back. The min SOC
    verification between polls holds it back the same way (Codex on #38). A plan that has nothing
    new to write reads the limit first: one the inverter dropped or someone raised is written
    again before the floor rises (Codex on #38).
- **The charge limit is kept alive while the battery can draw from the grid.** The Kostal hands
  battery control back to its own management unless the Modbus values are written again within
  its timeout (30-60 s): 1000 W written and confirmed at 23:01:05, about 8 kW charging at 23:12,
  and nobody else wrote the register in between. KORE and evcc write it cyclically; the window
  now does too, every `CHARGE_LIMIT_KEEPALIVE_S` (20 s - a third of the shortest timeout is left
  for the write itself; 30 s let the limit lapse for a moment every cycle, Codex on #38), while it holds a limit it wrote and the
  battery is not provably idle. The keepalive captures, records and pauses nothing - the
  verification and its bounded rewrites still judge what the inverter holds - and it stops with
  the window: after the window end nothing reaches the inverter. Before, the limit was rewritten every 15 minutes
  only by accident, because every write counted as failed; with the readback fixed above it
  would have been written twice a night and lapsed for the rest of it. A limit someone else has
  lowered (the user, evcc) is left alone, as the verification does (CodeRabbit on #38). A
  keepalive that is not delivered while the inverter no longer reads the limit runs the
  verification at once, not up to 15 minutes later (Codex on #39); so does a lapse the reading
  still shows 75 s after the keepalive first saw it, although the writes were acknowledged
  (Codex on #38). Only a control whose value lapses is kept alive - KostalKore's; a setting that
  persists is not written every 20 s all night (Codex on #38).

### Changed
- **The evening rescue acts only when switched on, holding included** (Pascal's decision of
  21 October). That morning the min SOC sat at 35 % and went back up every time it was set by
  hand - most likely the rescue holding the battery for the 16:30 period: an integration for the
  night changing the inverter in the middle of the day. `switch.…_evening_rescue_charge` is now "Evening rescue (hold and
  charge)", off by default; switching it off ends a running rescue at once, and a rescue window
  persisted from before the update ends at the next poll.
- **The forecast error margin takes the safe side for the evening.** It still widens the room
  kept free for tomorrow's sun, but the evening the battery has to carry now counts on the
  forecast *less* the margin. On 20 October the evening was credited 12.0 kWh of sun for a
  10.9 kWh forecast, and the night bought too little for it. The afternoon outlook that drives
  the evening rescue counts the rest of today's sun the same way (Codex on #38).

### Lab
- **New lab campaign: forced charge.** The night through real HA, real KORE and the emulated
  Kostal with all three quirks - float readback, a forced charge behind `ManualCharge` and a
  limit that lapses unless written again - and the efficiency search on, as in the field: the
  switch goes on at most once and off in the same poll, the limit precedes the floor and is
  kept alive, the battery charges no faster than the planned limit allows, no repair issue,
  and nothing reaches the inverter after the window end. Without the keepalive the same night
  draws 4.4 instead of at most 3.0 kWh.

## [3.10.7] - 2026-10-20

### Fixed
- **On reaching the target, the charge limits are handed back only after grid charging is
  confirmed off.** Without a house connection limit, `_stop_grid_charging` restored the AC
  charge limit (and the absolute charge power) even when `switch.turn_off` failed or the
  switch never reported off. Since 3.10.6 a captured ceiling above the entity maximum is
  restored as that maximum instead of being refused, so a failed stop could have raised the
  protective 0 W to the full ceiling while grid charging went on. Now the limits and their
  captures stay until the switch reports off; the window-end reset applies the same rule.
  Raised by CodeRabbit on #35, which #36 superseded. An unconfirmed stop is retried from the
  regular poll: a switch that reports off by then is confirmed without another command and
  the limits go back; one that still reports on gets `turn_off` at most twice more in the
  window, never on every SOC update (Codex and CodeRabbit on #37). The retry runs before the
  SOC is looked at, so a SOC that is unknown or back below the target neither skips it nor
  sends further stops past the bound. While a stop is pending, every `turn_off` counts against
  that bound, whichever path asks for it - the communication outage guard, which refreshes
  every 15 seconds, sent one on each refresh before (22 in five minutes in the regression test).
  A raised target (bridge plan, override, snow nights, ad-hoc) drops the pending stop of the
  old one, so the next poll charges towards the new target instead of first switching grid
  charging off once more (Codex on #37).
- **A protective charge limit above what the control accepts is written as its maximum.**
  After the Kostal falls back to its own 22 kW, a connection limit of, say, 18820 W was
  written as is - above the 12540 W the KORE control accepts. Home Assistant refused it and
  grid charging stayed paused. The control's maximum is lower and protects at least as well.
  Found by the new morning-silence lab campaign. The clamp sits at the single place a setpoint
  reaches the inverter, in W, so a unitless control inferred as kW is covered too; the value
  written is what the planner, the connection limit, the guard, the activation and the
  efficiency limiter record as standing there - the planner plans no more than the control
  takes, so its "already written" comparison and the sensor hold the real value;
  and the efficiency finder does not test a power above the control's maximum (Codex on #37).
  A unitless control above 1000 still has no known scale and is not written beyond its maximum.
- The lab campaign observes a native charge ceiling above KORE's accepted maximum through
  the real KORE services and checks the bounded, confirmed restore (from #35).
- **New lab campaign: nothing reaches the inverter after the window end.** Three nights
  through real HA, real KORE and the emulated Kostal - charging 50 % to 60 %, draining 50 %
  to 20 % on a 10 % original floor, holding 50 % - each with the field's quirks (a native
  ceiling above the KORE maximum, a Kostal that drops written limits back to it, a Modbus
  silence at 03:15 and again at 06:00). Every REST and Modbus command the emulated inverter
  receives is recorded: at 05:00 the min SOC goes back once, and from then until 13:00 it
  receives nothing at all. A unit test runs the same morning with the field configuration,
  house connection limit included.

## [3.10.6] - 2026-10-20

### Fixed
- **A reset that could never succeed rewrote the inverter every 15 minutes, for hours.**
  Field log, 20 October: the night window captured the AC charge limit as the device
  reported it, 22063 W, through a KORE control that accepts at most 12540 W. Home
  Assistant refused every restore, the reset counted as failed, and the retry ladder
  retried every 15 minutes without end - persisted, so across restarts too. Each attempt
  wrote the min SOC to the inverter again first, although it already read its original
  value. The mechanism is old; this night was the first to capture such a value.
  - A value about to be restored is limited to the entity's `min`/`max`. For a power
    limit the maximum stands for "unlimited". The value already persisted in the field
    is restored as 12540 W on the first reset after the update.
  - Every setting is finished on its own, while the reset as a whole stays on record as pending
    until all of it is confirmed - a crash in between still leaves the grid switch owed. A confirmed min SOC restore is not written
    again by a later retry, and a setting that already shows its original value on a
    fresh reading is only confirmed, not rewritten.
  - The ladder is bounded: 1, 2 and 4 minutes, then twice 15 minutes apart. After that
    nothing is written automatically; a repair issue names the entities still to check.
    The next window, the `reset_inverter` action or a successful reset takes over. The
    "settings still on the inverter" check, which the backup listener runs on every
    state change, respects that as well.
  - A pending reset no longer requests a refresh and persists the state every 15 seconds,
    neither directly nor through the health monitor's `write_unconfirmed` fault.
  - Deferring the reset during backup mode writes nothing and spends no attempt, so a long
    power cut does not give the restore up. An attempt counts when it runs, and the count is
    persisted: a restart neither hands out a fresh ladder nor loses an attempt still waiting.
  - With a reset already pending, the "settings still on the inverter" check - which the
    backup listener runs on every state change - leaves it to the bounded ladder instead of
    starting another full reset each time.
  - A restored AC charge limit, and likewise the absolute charge power, is confirmed by a fresh
    reading before its capture is dropped. Restored to the entity maximum, any reading at or
    above it counts for both - the device may go on reporting its own larger maximum. Clamped
    up to the entity minimum, that minimum is what has to read back. A legacy discharge limit
    restored as the entity maximum accepts a larger native reading the same way.
  - An AC charge limit that a fresh reading already shows at its original value is confirmed,
    not written again - a Kostal drops the window's limit by itself, and a late feedback can
    show that an earlier attempt landed. The absolute charge power is confirmed the same way.
  - Giving up is visible: `reset_abandoned` on the target sensor and in the diagnostics, next
    to `pending_reset`. Removing the entry removes its repair issue.
- **Why the limit was written that night at all, and why every 15 minutes.** The night log
  shows it: at 03:10 the inverter answered Modbus with "server device failure" for a few
  seconds, and the grid meter, min SOC and battery SOC were briefly untrusted. The target had
  been reached at 23:01 (68 % on a 21 % floor) and grid charging was off - nothing could draw
  from the connection. Still the communication guard wrote 0 W on every refresh while a fault
  lasted (twelve writes in three minutes), the house connection limit wrote 0 W "after the
  target was reached", and because the Kostal reported its own 22 kW again after each write,
  the verification wrote 0 W anew every 15 minutes until the window ended. The value captured
  before the first write was the 22063 W the reset then could not restore. Both mechanisms
  date back to 3.10.2; this was the first night with such a hiccup.
  - No protective charge limit is written while the battery cannot draw from the grid: target
    reached, grid charging off, and the battery at least 5 points above both the planned floor
    and the min SOC the inverter holds. Both count only if their entities reported within the
    last five minutes, which also bridges the seconds the same hiccup blanks them; a value that
    stopped updating or was never seen, like anything else unknown, keeps the limit.
  - The communication guard writes its 0 W once per outage, not on every refresh.
  - A limit the inverter does not hold is rewritten at most twice per window - a reading that
    matches for a while does not refill that, and a restart inside the window keeps what was
    spent (the window is named by its own deadline, an ad-hoc `charge_to` included). Only a
    rewrite actually issued counts. Then no
    writer at all - planner, connection limit, guard or efficiency finder - writes the charge
    limit again in that window, and the
    integration stops, files the repair issue "the inverter does not hold the charge limit" and
    leaves grid charging off, whose activation needs the limit to read back. The next window
    starts over. `charge_limit_not_held` shows it on the target sensor and in the diagnostics.
- **Every window holds the battery through the min SOC only.** The evening rescue and
  `block_discharge` no longer turn a discharge block switch on or write a discharge
  limit of zero. The two entities are only used to give back a value an older version
  captured, once at the window start and otherwise at the window end - no longer on
  every verification.

## [3.10.5] - 2026-10-19

### Fixed
- Scheduled night charging now sets only the planned target min SOC. Existing energy
  above it may supply the house; no discharge-block switch, zero discharge limit or
  floor at the initial battery SOC is applied. The original min SOC is restored at
  the end. Explicit reservations and evening rescue retain their holding behavior.
- Confirm fresh switch/limit feedback before discarding captured discharge restores.
  Delayed, missing, stale or ignored feedback retains the original for retry, including
  at the window end; a newer capture is not cleared by an older restore.
- Release captured legacy night restrictions on resume without losing originals on
  failure or overriding independent discharge locks. Document the distinction in
  setup descriptions, diagnostics guidance and the backup-detection warning.

## [3.10.4] - 2026-10-19

### Added
- Include live minimum-SoC and grid-charge feedback, the captured original floor
  and pending-reset state in diagnostics. Unavailable feedback is null, not a
  misleading zero or off value. Configuration and measurement history are retained.

## [3.10.3] - 2026-10-19

### Fixed
- Await minimum-SoC and grid-charge service results before reporting success or
  enabling charging. A rejected charging floor pauses grid charging and preserves
  the original settings for reset. Related discharge floor/stop calls also await
  their service result.
- Respect the number's advertised step and bounds for night-charge floors.
  Round upwards only while charging is needed and downwards while holding;
  preserve semantic discharge-block decisions and morning-discharge targets.
  Keep the calculated charge target separate and restore the original unchanged.
- Expose efficiency waiting reasons and a redacted control snapshot with planned,
  commanded and observed power values. Log a changed waiting reason once.
- Serialize charge-limit writes and reset, and update the accepted command when
  an efficiency setpoint arrives. Verification cannot restore an older plan
  during a new write or after control ownership changes while it pauses charging.

- Serialize charging, discharge and raised-floor Min-SoC writes with reset.
  An in-flight floor command must finish before the original value is restored;
  an ended or unloading window cannot issue a queued floor command.

## [3.10.2] - 2026-10-18

### Fixed
- Keep an efficiency trial alive while its initial HA charge-limit feedback
  catches up within the existing settling interval. Reject unresolved feedback
  at the deadline and discard subsequent downward drift immediately. Excess
  power and house-connection protection remain immediate.
  Measurement start independently confirms matching setpoint feedback, and
  finalization rejects unconfirmed results even with a long verifier interval.
  Read unitless kW controls with the same scale as writes, including protective
  activation and restoration. Finalize valid trials before restoring their
  setpoint at window end.
- Wait for observed control feedback in every KORE laboratory setup without
  draining unrelated HA background work; package the helper for Container use.
- Give parallel evcc laboratories distinct EEBUS/OCPP ports while preserving
  their certificates and pairing data, avoiding configured device/proxy ports.
  Initialize the proxy campaign's physical
  grid-import fixture explicitly instead of relying on startup timing.

## [3.10.1] - 2026-10-18

### Fixed
- Wait for the min-SOC restore and confirm its feedback before forgetting the original floor. An evening reserve opened immediately after night charging can no longer capture the previous night target as its permanent restore value. Unconfirmed restores retain the original value and use the existing retry mechanism.
- Report an unconfirmed discharge release as a pending reset instead of claiming that no hold existed. The coupled laboratory check verifies the delayed feedback and the subsequent reset retry before checking physical discharge.
- Verify a running efficiency trial and its charge activation against the current test power instead of an older planner setpoint. Connection-budget checks still interrupt the trial and reduce charging when headroom is exhausted or the meter is unreadable.
- Close simulator Modbus clients before awaiting server shutdown, preventing connected or partial-request clients from blocking the laboratory's clean shutdown.
- Require fresh min-SOC restore feedback, refresh an unchanged cached original, and stop/serialize floor verification before unload restoration. Discard efficiency trials after downward setpoint drift and distinguish an unrelated pending reset from a released hold.
- Wait for observed KORE control entities in the coupled lab and explicitly require its deliberately delayed release to report a pending reset.

### Added
- Expose the ad-hoc window reason, release time, original min-SOC and pending reset on the target SOC sensor, making an automatic evening reserve distinguishable from a failed night-window reset.

## [3.10.0] - 2026-10-17

### Changed
- Replace the forced four-page setup with an overview, two short basic pages and separate optional feature pages, shared by setup, options and reconfigure.
- Explain every input, gross battery capacity, time zone and the difference between control entities and measured power. Include concrete KostalKore entity examples in German and English.
- Suggest unambiguous KostalKore bindings using entity registry identities, preserving renamed entity IDs and avoiding guesses across multiple inverters.

### Added
- Persist confirmed steps as private Home Assistant setup drafts for 30 days. Close, reopen and resume without creating an active partial entry; apply only after review.
- Direct navigation back to the overview, individual feature configuration and explicit disable actions. Efficiency and house-limit pages show their prerequisites as required fields.

### Fixed
- Preserve runtime state and settings changed while a reconfigure draft is open, including resumed drafts. Explicit feature enable/disable actions still take effect when runtime switches change concurrently.
- Reject malformed draft metadata before offering resume, keeping a damaged record from interrupting an otherwise valid setup.
- Make the coupled inverter lab campaign independent of the real night-window clock: ad-hoc charge tests run outside the scheduled window.

## [3.9.0] - 2026-10-17

### Changed
- Prefer the measured efficient charging power, bounded by remaining house connection capacity. Increase power only when the remaining charging window requires it; stop at target SOC.
- Efficiency measurements reject interrupted/stale power data, daytime solar contamination and setpoints the inverter does not follow. Transient failures defer a measurement without permanently excluding unmeasured powers. Changing measurement bindings invalidates cached losses.
- Load and price integration use real elapsed time across daylight saving transitions.

### Added
- Optional severe PV shortfall detection: compare a morning full-day forecast with fresh daily PV energy after sunset. With at least 5 kWh expected and at most 15% delivered, charge to the configured maximum SOC during the next regular night. Expose the observation and a Home Assistant repair warning; do not claim to identify snow as the cause.

## [3.8.1] - 2026-09-22

A review of 3.8.0. Nine findings; six held up and are fixed, one only partly, one is a known
approximation now documented instead of changed, one is a cleanup folded into the fixes. Every fix
fails its own test when reverted.

### Fixed

- **The options and reconfigure flows validated values they were about to discard.** A field the
  user cleared was still checked at its old value, because validation ran on the stored entry
  merged with the form and the cleared field was only removed on saving. Clearing the price entity
  while keeping the feed-in price passed the new rule on the entity being removed. Older than this
  release, too: clearing the night price passed "all three prices or none" on the old night price
  and saved two of the three. Validation now sees exactly what is saved.
- **`conflict_prices_source` reported whichever calculation ran last.** Four paths build the
  planner's input — the real plan, the curtailment outlook on every poll, the evening-reserve floor
  and the read-only `plan_target_soc` action — and all of them set it. The source is now returned
  rather than stored, and only the real plan records it. The read-only action no longer changes
  anything.
- **It also named a source when no conflict had been decided.** It now describes the prices behind
  the last conflict decision, and is empty when there was none.
- **During an ad-hoc window the price pair described two different windows.** The night price is
  always the configured window's, but the bridge then starts at the ad-hoc deadline. The fixed
  prices decide in that case — also when the ad-hoc deadline happens to equal the configured end,
  which the first version of this fix let through (found by Codex).
- **The help texts named one reason for falling back to the fixed prices; there are three.** Before
  the next day's prices are out, during an ad-hoc window, and always in the morning-discharge mode.

### Changed

- The window's mean price is computed once per plan and shared by the conflict branch and the
  evening-reserve gate, so the two can no longer disagree.

### Known approximation, documented rather than changed

- The day price is the mean over the whole bridge, while the energy left uncovered is the end of
  the bridge. Pricing that tail needs the size of the conflict, which only the planner knows.
  Written up in `plans/012`.

### Fixed — a review of the whole repository

The review above only looked at the last merge. A second one read the whole package. Ten
findings: eight held up and are fixed, one only where it is safe to fix, one did not hold up.
Every fix fails its own test when reverted.

- **The evening rescue neither held nor bought.** Stage one's hold is reached the moment it
  starts, so the min SOC that does the holding was not written until the periodic verification
  came round, up to an hour later. Stage two raised the target but left `target_reached` set, so
  every later poll skipped control and the grid switch never went on. The 3.7.1 fix for this
  stage only moved the target. The hold is now written at once where the min SOC is the lever (a
  block over the min SOC, or a hold with the block switched off).
- **The configured window inherited a running ad-hoc window.** The start trigger bypassed the
  check that lets the configured window win, so the night took over the ad-hoc target, its
  deadline and its grid-charge setting, and was ended at that deadline. The configured window now
  takes over.
- **Replacing an inverter entity in the options mid-window left the old one at the night's
  settings.** The window ran on with the new entities and restored those at its end; the old min
  SOC kept the night's floor and the old grid switch stayed on. Such a change now ends the window
  on the entities it wrote to, and it starts again on the new ones. The window check stands down
  while that happens: the refresh at the end of the teardown would otherwise start the window
  again on the old entities before the swap (found by Codex, where this fix met the next one).
- **Switching the integration back on inside the window did nothing until the next night.** It
  now picks the window up again, as switching skip next off already did.
- **A window starting in the hour the clocks skip in spring did not start at all.** Home
  Assistant moves such a trigger to the next day. The polling update now starts a window whose
  start trigger was missed, as it already ended one whose end trigger was.
- **An efficiency test whose setpoint did not reach the inverter was recorded anyway**, under a
  power it was never taken at. It is not started now.

### Changed

- **The evening outlook needs a forecast entity for today.** Without one it read the tomorrow
  entity, which in the afternoon is tomorrow, and the rescue acted on the wrong day's sun. It now
  stays empty instead, like the curtailment outlook.
- **`charge_to`, `block_discharge` and the evening rescue are refused in the morning-discharge
  mode.** The window machinery drives the battery down there: a charge counted as reached at
  once, wrote nothing and reported success.

### Did not hold up

- The evening shortfall credits the day's surplus at `1 / discharge efficiency` instead of the
  charge efficiency. That is right for a DC-coupled hybrid inverter, where the PV reaches the
  battery without the AC conversion the charge efficiency describes. The reasoning is now next to
  the formula.

## [3.8.0] - 2026-09-22

### Added

- **The price entity now decides the planner's conflict too.** When the bridge and the PV
  headroom cannot both be satisfied, the planner weighs the day price against the night price.
  After 3.6.0 those still came from the three fixed fields, even with a price entity that knew
  what tonight and tomorrow morning actually cost. Now the night price is the window's own mean
  and the day price the mean over the bridge — the stretch from the window end to the PV
  crossover that the house would otherwise buy at.
- **Taken as a pair or not at all.** The decision turns on the difference between the two, and a
  difference between an entity's night and a hand-entered day is partly a difference between two
  ways of writing a price down. When the entity cannot cover both stretches, both come from the
  fixed fields. `sensor.…_price_signal` shows which source the last plan used, in
  `conflict_prices_source`.

The feed-in price stays a fixed field, because no price source publishes one; without it nothing
changes. **With a price entity, the feed-in price on its own is now a complete setup** — the form
used to demand all three fixed prices, so the path this release adds could not be configured
without inventing a night and a day price first. A lone night or day price is still refused: the
planner only ever uses the fixed pair together, so one of them alone would never take effect. In the morning-discharge mode the bridge is empty every day — the sun is up before the
window ends — so that mode always uses the fixed fields, which is the right answer when there is
nothing to bridge.

### Not in this release, deliberately

- **`set_charge_power_limit`.** Planned for this release and not built, because checking it
  against the code showed both ways of building it fail. As an ad-hoc window it would also raise
  the min SOC floor and so block discharging — a side effect nobody asked for. As a cap inside a
  running window it writes nothing at all in the default `headroom` planner mode without a house
  connection limit, and an action that silently does nothing is the worst kind. The version that
  works needs an ad-hoc window that caps power without setting a floor, which is the same building
  block curtailment stage two needs — so the two get planned together.
- **`force_discharge_to`**, for the same reason in a larger form: direction is entry
  configuration, not a window property, and about a dozen code paths branch on it.

## [3.7.1] - 2026-09-20

A review of code that shipped unreviewed. PR #5 was looked at once, at 13:00:14, and the earliest
of the twelve commits that followed it into `main` is from 13:03:15 — so 1582 lines of product
code went out with nobody having read them. Seven defects; each fix fails its own test when
reverted on its own.

### Fixed

- **The evening rescue could never reach stage 2.** Extending a running ad-hoc window set the new
  target and recalculated, but the recalculation decides between three cases in order and the
  first is "restarted during an active window, keep the persisted target". A single poll of
  stage 1 captures the inverter's min SOC, which makes that branch match — so stage 2's higher
  target was discarded and the grid top-up the feature exists for never happened. The same
  defeated a second `charge_to`, and a `charge_to` after a `block_discharge`.
- **A restart during an ad-hoc window poisoned every window after it.** `is_active` is not
  persisted, so the deadline came back but the window did not; the window-end path then
  early-returned and never cleared it. A timestamp left behind in the past answers every later
  question about when the current window ends, and the expiry check fires on entering a
  configured window — so the next night charge was ended one poll after it started.
- **Entering the feed-in cap aborted the config flow.** The key landed among the entity ids to
  validate, where every entry reaches `hass.states.get`, and Home Assistant lowercases what it is
  given — so `9000.0` reached `float.lower()`. It was also listed twice.
- **The price gate was inert for any night window.** Today and tomorrow arrive as two separate
  attributes on Nordpool, EPEX Spot and ENTSO-e; the parser stopped at the first, so the series
  ended at midnight and every stretch crossing it was refused. Matching attributes are now merged,
  and only ones that agree on the value key and the unit.
- **And it priced the wrong night.** At 02:00 inside a 23:00–05:00 window the window began
  *yesterday*, but the bounds came from "when does the next one start" and pointed at tonight —
  usually unpublished, so refused, so the reserve was held whatever the prices said. The bounds
  now come from the window's end, which is the same window the planner is given.
- **`model_vs_measured_pct` divided kW by kWh.** It is now `model_vs_envelope_pct`, and the
  envelope is named for what it is: each hour's highest value in 14 days, summed — an upper bound
  no real day can beat. A model above 100 % of it is claiming an overflow the inverter has never
  come close to.
- **`evening_reserve_dropped` named a decision no price had made.** It was inferred from a
  zero reserve, and a period whose load profile is zero gives exactly that. It now asks the gate.

### Hardened

- The persisted ad-hoc deadline is compared timezone-aware on restore. Not reachable through the
  actions, which all add a duration to an aware now — but an unguarded comparison there raises
  inside the constructor, and the integration then does not load at all.

## [3.7.0] - 2026-09-20

### Added

- **A permanent feed-in cap is now visible, and what it costs is a number.** A 60 % or 70 % EEG
  limit at the grid connection point is not something the grid operator switches on: it is always
  in force and only *bites* around midday, when production exceeds it. So there is no window to
  learn or configure — the hours it bites in follow from the forecast, the sun times and the load
  profile, and on a dull day the answer is correctly "it never bites".
  `sensor.…_curtailment_outlook` reports what today's cap throws away because the battery was
  already full.
- **The PV power curve, as the exact derivative of the energy curve.** 3.4.0 added the clear-sky
  bell as an *energy* fraction; a cap is about *power*, so the same model had to be readable the
  other way round. A property test integrates one and compares it to the other on every run: if
  they ever drift apart, a display built on one would contradict a decision built on the other,
  and nothing else in the suite would notice.
- **A reality check, not just a model.** With a feed-in power sensor configured, the hourly maxima
  of the last 14 days are read from the recorder and shown beside the modelled overflow as
  `model_vs_measured_pct`. This is the number the whole release exists to produce.

### Fixed

- **The absolute charge power limit could overwrite the user's own setting, permanently.** Nothing
  to do with curtailment — it was already reachable through the night window. Reaching the charge
  target restores the limit *while the window is still open*; the service call is not blocking,
  and an inverter whose limit register falls back on its own can still be reading our value
  moments later. The next charge pass then captured that as "the user's setting", and the window
  end put our own number back as if it were theirs. The mid-window restore now keeps remembering
  the captured value, and a capture that reads back exactly what we last wrote refuses to take it.

### Notes

**Nothing is throttled from any of this.** Stage two of plan 014 — capping the morning charge so
the battery still has room at midday — is deliberately not in this release. The model and the
observation currently disagree: at 100 kWh across a summer day the modelled peak is about 9.8 kW,
so a 60 % cap on anything above ~16 kWp would never bite, and yet it demonstrably does. Until that
is settled, throttling would fire on an unknown share of days that did not need it, and such a day
costs twice — the battery is short in the evening, and the evening rescue buys it back from the
grid.

Both new settings are optional and the feature is off without them. It also stays off unless a
PV forecast entity for **today** is configured: the forecast selection otherwise falls back to
tomorrow's entity, which for a morning decision is quietly the wrong day.

## [3.6.0] - 2026-09-20

### Added

- **A price entity can be read, and the integration does not need to know which one it is.**
  Tibber, aWATTar, EPEX Spot, Nordpool, ENTSO-e and anything shaped like them all publish
  tomorrow's hourly prices under their own attribute name with their own keys. Rather than a
  table of those names — which could not be checked against the real integrations and would
  quietly match nothing where it was wrong — the series is recognised by its **shape**: a list of
  items carrying a timestamp and a number, in order, evenly spaced, with values that could be a
  price. `sensor.…_price_signal` reports what was matched, which unit was resolved and which
  surcharge was applied, so a wrong pick is visible rather than silent.
- **Surcharge fields**, and they are load-bearing. An exchange price is not a consumer price:
  Tibber publishes the full price, the others usually publish the market alone. 6 ct and 32 ct
  are both plausible numbers, and the evening reserve decision comes out the *other way round* on
  the wrong one. A second field covers the reduced §14a grid fee inside the cheap window.
- **Nothing is ever guessed.** No unit from the entity, the attribute name or the setting means
  the price entity is refused and the fixed prices decide — a guess there is a factor of a
  hundred, or a thousand for EUR/MWh. Cents the size of euros, an exchange price with no
  surcharge configured, a stale series, a gap in the middle: all refused, all falling back to
  exactly the behaviour of an installation with no price entity. A test asserts that contract
  once per way it can go wrong.

- **The evening reserve became a decision instead of an assumption.** It used to hold energy back
  whenever two times were configured, whatever the evening actually cost. A kilowatt-hour put
  aside in the window passes through the inverter twice, so holding it costs `window price ÷
  efficiency`; an evening clearly cheaper than that (by more than 2 ct/kWh, so marginal
  differences move nothing) is an evening to buy rather than to save for. One gate, and because
  the morning-discharge floor reads the same reserve, it frees that too. Unknown prices hold the
  reserve exactly as before.

Without a price entity configured, none of this changes anything: the reserve is held as it was
in 3.2.0.

## [3.5.0] - 2026-09-20

### Added

- **The evening rescue acts now, not just reports.** When the outlook says the battery will not
  carry the high-price period, the discharge is blocked automatically — the house runs from the
  sun, and from the grid at the day tariff when the sun is not enough, rather than from a battery
  needed in three hours at the peak tariff. With the new
  `switch.…_evening_rescue_charge` on, the rest is bought from the grid in the last two hours
  before the period, when the forecast hardly turns any more. At the period's start the block is
  released and the inverter goes back to what it was. The switch is off by default: watch the
  outlook sensor for a season before letting it spend money.
- **Three more actions, now that the ad-hoc window exists to carry them.** `charge_to` charges
  to a level from the grid for a while; `block_discharge` holds what is in the battery without
  buying; `allow_discharge` ends either early. All three run as ad-hoc windows, so the house
  connection limit, the capture of your inverter's settings and the restore afterwards apply
  exactly as they do at night. This completes stage 1 of the masterplan, whose remaining actions
  had been waiting for precisely this lifecycle.
- **The ad-hoc window.** Both stages run as a window like any other — same capture of the
  inverter's settings, same restore, same retry ladder, same backup interlock, same house
  connection limit, same verification — whose start comes from a call rather than from the clock.
  A configured window always wins over it. The deadline is persisted, because a restart in the
  middle of a rescue must still release the block when the expensive hours start.

### Fixed

- **Holding locked out the buying that should follow it.** Stage one opens a window, and an
  active window switched the outlook off entirely — so from the next poll on there was no
  shortfall to see and stage two could never fire. The rescue's own window is now the one
  exception to that rule. Caught by writing the test through the real update path; the ones
  that call the rescue directly with an outlook in hand would never have noticed.
- **The outlook projected across the night charge.** After the day's period had begun, the next
  one is tomorrow's, while the sun times and the forecast were still today's: 23 hours of house
  load against a sliver of sun, a projected level of zero and an invented shortfall every evening
  and all night. And between the two sits the night charge, whose whole job is to buy for that
  evening. The outlook is now only made while it can still be acted on. Found by Codex on the
  pull request.

## [3.4.0] - 2026-09-20

### Added

- **`sensor.…_evening_outlook` — will the battery still carry the expensive hours tonight?**
  The night plan buys for the evening in advance. This answers the question the day *after* a
  forecast that was too good — snow on the panels with nobody having set the snow nights — while
  there is still time to do something about it. The state is the shortfall in kWh, normally 0, so
  a notification can hang off it. Attributes carry the whole projection: what the period needs,
  what the battery is heading for, what the sun is still expected to deliver and what the house
  will draw before then.
- The sun's remaining share of the day is modelled as a clear-sky bell (`sin(pi*x)` across the
  solar day) rather than linearly. At four in the afternoon a linear model still promises half
  the day's yield; that is the one error that matters here, because it would let the battery walk
  into the evening short while the projection says it is fine.

The outlook is only made while it can still be acted on: not while a charge window runs (that
window plans for the evening itself) and not once the day's period has begun, because the next
one lies on the far side of a night charge the projection knows nothing about. Without that
bound it integrated 23 hours of house load against a sliver of today's sun and invented a
shortfall every evening — found by Codex on the pull request.

This release is the read-only half and is useful on its own: it tells you, in the afternoon,
that tonight will be short. It changes nothing about what the integration writes. The
interventions — blocking the discharge during the day, topping up from the grid before the period
starts, with the top-up behind its own switch — follow in 3.5.0, designed in
[`plans/013`](plans/013-abendrettung-und-adhoc-fenster.md).

## [3.3.1] - 2026-09-20

### Fixed

- **The planner bought too little, every night, in the same direction.** The house load profile
  and the forecast are measured on the house side of the inverter; energy coming *out* of the
  battery is not. The bridge energy and the evening reserve were converted one to one all the
  same, as if discharging were free. To deliver 4 kWh to the house on a winter evening the
  battery has to hold roughly 4.2. New setting **Discharge efficiency** (step 4, default 0.95)
  with the arithmetic behind it; targets rise by about a point on a typical bridge. Raised by
  Pascal, who named exactly this ("bei den Entladeverlusten könnte man zu knapp agieren")
  — the code confirmed it: `charge_efficiency` was carried into the planner and then never used
  by it at all.

### Changed

- The energies in the plan (`bridge_kwh`, `evening_shortfall_kwh`, and the matching sensor
  attributes) are now stated as what the **battery** has to hold rather than what the house will
  draw. `evening_reserve_kwh` stays the house draw, so the pair reads as "the evening will take
  1.5 kWh; the battery has to gain 1.58 tonight to deliver it".

## [3.3.0] - 2026-09-20

### Added

- **The efficiency search measures per state of charge.** Losses depend on how full the battery
  is, not only on the charge power, but every measurement was filed as if it did not. Each one now
  goes under the 20-point band it was taken in, and the charge power plan asks for the band the
  battery is actually in. A band only overrules the battery-wide optimum once it has been searched
  rather than sampled (three distinct powers), so an installation that measured before this keeps
  its result until the bands fill in. A measurement running across more than two bands is a blend
  and is kept battery-wide only.
- `sensor.…_efficiency_search` gained `loss_by_band_pct` — what was measured where, how many
  powers each band has seen, and which bands are in use — plus `band_width_pct`.

### Fixed

- **The measurement never recorded the state of charge it ran at.** The backlog entry for this
  work assumed it did. It does not: the state of charge is now captured when the measurement
  starts and when it finishes, which is what makes the bands possible at all.

## [3.2.0] - 2026-09-20

A tariff season now behaves like a season. The optional date range was absolute-only, and the
one shape a §14a season usually has — winter, crossing the new year — did not work at all.

### Fixed

- **A date range crossing the new year no longer disables the restriction.** `2026-11-01` to
  `2027-03-31` was logged as invalid and then *ignored entirely*, so the window ran all year —
  the opposite of what was configured. Such a range is now read the way a time window crossing
  midnight has always been read: inside when today is on or after the start **or** on or before
  the end.

### Added

- **A high-price period, and the reserve that comes with it.** Many §14a tariffs do not only
  have a cheap window but also a peak period — commonly 18:00–21:00, the hours a household draws
  most and the sun delivers nothing — where a kilowatt-hour costs *more* than the normal day
  tariff. Set **High-price period, start / end** in step 2 and the planner works out what the
  house will draw in those hours, from the same load profile the Bridge planner uses, and makes
  sure it is in the battery by then. It buys only what tomorrow's sun will not cover: the
  evening's load minus the day's surplus, never below zero. A dull winter day therefore raises
  the night target by the whole evening; a summer day changes nothing. Without a usable forecast
  the surplus is not counted — a surplus nobody can see is one nobody may plan on.
- **Morning discharge stops at the evening reserve.** The experimental mode empties the battery
  into the 05:00–08:00 peak; with a high-price period configured it now keeps back what that
  evening will need — minus what today's sun is forecast to deliver, so a summer day is
  untouched. Selling in the morning what has to be bought back at the evening's peak tariff is
  the one trade that mode must not make.
- **Allowance on the evening's consumption** (step 2, default 0 %): the load profile is an
  average of the last days, and an evening with the oven on lies above it.
- **`sensor.…_next_high_price_window`**: when the next peak period starts, how long it lasts,
  the reserve put aside for it and how much of that the current window is buying. The target SOC
  sensor gained `evening_reserve_kwh` and `evening_shortfall_kwh` alongside it.
- **Repeat the date range every year** (step 4, on by default). Only day and month of the two
  dates count, so the season comes back every year; a price sheet holds until further notice.
  Switched off, the years count and the range expires as before. A range crossing the new year
  is always read as a season, because absolutely it could not contain a single day.
- **A repair issue when an absolute range has expired.** Until now the integration simply stopped
  one morning and nothing said why. It names the end date and the three ways out.

### Changed

- **A missing forecast no longer cuts the planner's target back to 50 %.** The guard that stops
  the headroom formula charging to the maximum when it read no forecast was applied to the
  bridge plan as well. Headroom charges to the maximum *because* it read no forecast — that is
  what the guard is for — while the planner derives its target from the house load and has its
  own fallback. On a cold night with a long bridge, a forecast entity that happened to be
  unavailable therefore dropped a target that had just been worked out properly.
- **A missing forecast no longer undercuts the bridge.** The safe fallback (50 %) was used as
  the target outright, even when the house load after the window needed more than that — so the
  rest was bought by day at the day tariff. The fallback now decides how much to buy *on top of*
  what is needed, not instead of it: the target is the higher of the two. What the house will
  draw after the window does not depend on the forecast.
- Entries written before 3.2.0 that have a date set **keep the absolute meaning**: a range
  nobody re-entered must not come back next winter on its own. The migration records that
  choice, and the log line says where to change it. Entries without dates get the new default.

## [3.1.0] - 2026-09-20

The integration can be talked to. Until now the only way in from an automation was to toggle
its entities; it registered no actions at all.

### Added

- **`inverter_charge_night.plan_target_soc`** — a response action. It runs the planner on the
  current inputs and answers with the target, the reason, both bounds and the energies they came
  from, **without writing anything to the inverter**. An automation can now decide whether
  tonight is worth charging at all.
- **`inverter_charge_night.reset_inverter`** — hands the inverter back: minimum SOC, charge
  limits and switches return to the values captured before the window. Inside a running window
  it ends that window and stays off the inverter until the window's end time, because a bare
  reset would not survive the second it was written in — the min SOC watchdog puts the window's
  floor straight back. The next window runs as usual, and switching the integration off and on
  again takes control back immediately. Backup mode refuses the call.
- Both actions are registered from `async_setup`, so they exist even while no entry is loaded,
  and both report a bad or unloaded entry, a failed plan and a refused reset as translated
  errors an automation can catch. `action-setup` and `action-exceptions` in `quality_scale.yaml`
  moved from `exempt` to `done`.
- Diagnostics gained `hands_off_until`, which answers "why is it not charging tonight" after a
  `reset_inverter` call.

### Fixed

- The end-to-end smoke test against a real Home Assistant now also calls both actions. It caught
  two things a unit test would not have: the exception strings were written as plain text rather
  than as `{"message": ...}` objects, so Home Assistant would have shown the raw translation key
  to the user, and a reset inside a window was undone within milliseconds by the integration's
  own watchdog.

## [3.0.2] - 2026-09-20

Follow-ups from a review of 3.0.1. One of them is a safety fix, and one setting changes.

### Added

- `sensor.…_planned_charge_power` gained an `applied` attribute. The plan is computed in every
  mode, but it is only written to the inverter in bridge mode or behind a house connection
  limit, and only with an AC charge limit entity configured. The attribute says which of the
  two it is, so a plan that reaches nothing is not read as a command.

### Changed

- **The two inverter entities lost their `kostal_` prefix.** `kostal_min_soc_entity` and
  `kostal_grid_charge_switch` are now `min_soc_entity` and `grid_charge_switch`; the labels, help
  texts and log messages say "inverter" instead of "Kostal". Nothing in this integration was ever
  Kostal-specific. **Existing entries are migrated on startup** and keep working untouched —
  verified against a real Home Assistant. Anything of your own that reads the config keys (a
  template, a script) has to follow.
- **Morning discharge is declared experimental** and its purpose is stated properly. It is not
  "make room for the sun": on a summer day whose forecast covers the house anyway, it empties the
  battery into the 05:00–08:00 household peak, for the spread on a dynamic tariff and to take
  that load off the grid at its tightest hour. New page:
  [docs/morning-discharge.md](docs/morning-discharge.md).
- **Morning discharge now requires the force discharge switch.** It is the only thing that
  actually discharges the battery; without it the mode raised the min SOC floor, turned grid
  charging off and then waited for a discharge that could never start. The field said
  "optional" while the grid charge switch, which that mode only ever turns off, was demanded.
  Existing entries keep working until the settings are saved again, which is where the
  requirement is now enforced.
- **`best_charge_power` and `efficiency_search` are disabled by default.** Both report on the
  efficiency search, which is off unless it is switched on, and the second carries the whole
  measurement series in its attributes. Existing installations are unaffected — the entities
  are already registered there; new ones enable them from the device page if they run a search.

### Fixed

- **Morning discharge could start against an unknown floor.** If reading and capturing the
  inverter's min SOC failed, the error was logged and the forced discharge was switched on
  anyway — with no idea where the battery's floor was, which is how a discharge runs past the
  user minimum. The failed *write* of the floor already blocked the discharge for exactly that
  reason; the failed *read* now does too.
- **hassfest** rejected the manifest a second time once the recorder dependency was declared:
  its keys have to be `domain`, `name`, then alphabetical. A test now checks the order.
- **mypy and pyright were pinned to Python 3.13** in their config files. Both parse Home
  Assistant's own sources, and 2026.9 uses an unparenthesised `except` expression — 3.14-only
  syntax — so mypy stopped on `homeassistant/core.py` before reaching this package. CI passes
  the matrix version to both now.
- `validate_date_optional` and `_normalize_date_value` spell the empty check out instead of
  using `value in (None, "")`, which only mypy 2.x narrows. On the older mypy that
  `requirements-dev.txt` still allows, both reported `str | None` reaching
  `date.fromisoformat`.

### Internal

- The brand icons are rebuilt to the image specification in the home-assistant/brands README:
  square, trimmed to the subject, 256 and 512 pixels. The duplicate `logo.png` files are gone,
  because that README says to add only the icon when the same image serves as both. No pull
  request against that repository: it marks `custom_integrations/` a legacy folder and states
  that since Home Assistant 2026.3.0 custom components carry their brand icons themselves.
- All 54 quality-scale rules are now done (39) or exempt (15), with nothing open.
- Coverage 95 % → 96 % (ratchet raised), with the new tests on the paths where a failure costs
  something: a reset that cannot report its own failure, a house connection limit that assumes
  a write landed, every service call in the discharge path, and a verification that could leave
  its own lock held.

## [3.0.1] - 2026-09-19

A maintenance release: no new settings, no changed behaviour on the inverter.

### Changed

- The coordinator moved from `__init__.py` into `coordinator.py`. `__init__.py` is now the
  setup, update and unload shim it is supposed to be. The moved code is unchanged line for
  line; only the import paths differ, which matters for anyone importing from this package
  (templates and custom code should use `custom_components.inverter_charge_night.coordinator`).
- "Required entity … is not yet available" is now a translated message rather than English
  text, so it reads the same as the repair issue shown next to it.
- As a consequence of the move, the control loop logs under
  `custom_components.inverter_charge_night.coordinator`. A `logger:` setting for
  `custom_components.inverter_charge_night` still covers it; a filter matching the logger name
  exactly does not.

### Fixed

- **CI never ran the tests.** `actions/setup-python` was told to cache pip but this repository
  has no `requirements.txt` or `pyproject.toml`, so the job failed at the cache step before
  installing anything. It now caches against `requirements-dev.txt`.
- **hassfest**: the integration reads the recorder (for the house load profile) without
  declaring it. `recorder` is now listed in `after_dependencies`, where an optional
  integration belongs.

### Internal

- Brand assets (`brand/icon.png`, `brand/logo.png` and their @2x variants) so HACS does not
  have to fall back to the Home Assistant brands repository.
- All 54 rules of Home Assistant's integration quality scale are met or documented as not
  applicable; `quality_scale.yaml` carries the reason for each one.
- 627 tests, 95 % coverage over the whole package (100 % on the config flow), enforced in CI.

## [3.0.0] - 2026-09-19

The release that makes this integration inverter-agnostic, safe around a house connection,
and honest about what it measures. **Read "Changed" before updating**: two defaults change
behaviour on an existing installation.

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

### Fixed — electrical safety review

A review pass over the whole change set, asking only where the integration could draw more
current than the connection carries or leave a setting on the inverter. Twelve findings, all
fixed; regression tests in `tests/test_electrical_safety.py`.

- **A restart past the window end left grid charging on and the raised min SOC stranded** —
  Home Assistant updating overnight meant the battery was bought full from the grid in
  daylight, every day, until somebody noticed. Settings still captured outside a window are now
  restored at the next window check.
- **A `nan` sensor reading disabled stop conditions** — it parses as a float and then makes
  every comparison false, so "target reached" could never happen and grid charging never
  stopped. A `nan` could also be captured as the inverter's original min SOC and written back
  at the window end. Non-finite values are now treated like "unavailable" everywhere.
- **A failed protective write was believed to have happened**, which suppressed every retry for
  the rest of the window.
- **Writing watts to a charge limit entity without a unit**: a kW entity receives 5000 instead
  of 5 and clamps to its maximum, turning a protective limit into full power. The entity's own
  maximum now decides the scale, and an impossible value is not written at all.
- **The house connection limit stopped reacting** between polls when the planner had produced
  no setpoint (unreadable battery SOC, almost no window left).
- 400 V was only corrected as a line-to-line voltage on three phases; on one phase it inflated
  the budget by 74 %. Any voltage outside 100–300 V now falls back to the documented default.
- Our own charge power can no longer be credited as more than the whole measured grid import.
- The limit no longer writes to the inverter while backup/island mode owns it.
- A target from a window that is long over is no longer reused for tonight.
- A window ended by the polling safety net now counts as a completed night (snow nights).
- The AC charge limit is read back on every verification run: an inverter integration can accept
  a write and drop it, and a protective limit that is believed but not in force is the failure
  this feature must not have.
- Feeding into the grid is no longer read as a negative house load, which would have widened the
  house connection budget.

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
