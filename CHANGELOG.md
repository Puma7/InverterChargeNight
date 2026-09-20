# Changelog

All notable changes to the **Inverter Charge Night** integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
