<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Puma7/InverterChargeNight/main/docs/images/logo-dark.png">
    <img src="https://raw.githubusercontent.com/Puma7/InverterChargeNight/main/docs/images/logo.png" alt="Inverter Charge Night" width="440">
  </picture>
</p>

<p align="center">
  <b>Charge your home battery in the cheap night window – exactly as much as tomorrow's sun will not cover.</b>
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/Puma7/InverterChargeNight/releases/latest"><img src="https://img.shields.io/github/v/release/Puma7/InverterChargeNight" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.2%2B-41BDF5" alt="Home Assistant 2025.2 or newer">
  <a href="https://github.com/Puma7/InverterChargeNight/actions/workflows/ci.yml"><img src="https://github.com/Puma7/InverterChargeNight/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Puma7/InverterChargeNight/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Puma7/InverterChargeNight" alt="MIT licence"></a>
</p>

<p align="center">
  <b>English</b> · <a href="https://github.com/Puma7/InverterChargeNight/blob/main/README.de.md">Deutsch</a>
</p>

---

**Inverter Charge Night** is a Home Assistant integration for households with a time-of-use
tariff: the German §14a EnWG reduced grid fee, a night tariff, or the cheap hours of a dynamic
tariff. Every night it decides **how full the battery has to be** when the cheap window closes,
charges it to exactly that level, and leaves the rest of the capacity free for tomorrow's sun.

<p align="center">
  <img src="https://raw.githubusercontent.com/Puma7/InverterChargeNight/main/docs/images/night-and-day.en.png" alt="A night and the following day: the battery is charged to the target in the cheap window, carries the house until the PV takes over, and the PV fills it during the day" width="820">
</p>

- **Too empty** and the house buys expensive energy in the morning or evening.
- **Too full** and the sun has nowhere to go: it is fed into the grid for a fraction of what
  the night energy cost.
- **The target** lies between these two: enough for the morning and the evening, room for the
  sun.

## Highlights

- 🔋 **Plans with tomorrow's forecast and your house's real consumption.** It learns your
  house's hourly consumption over the last two weeks and models tomorrow's PV curve.
- 🛟 **A safety buffer that never causes feed-in.** You choose how much reserve you want. It only
  fills room the sun leaves free, and it is bought once, not every night.
- 🌡️ **Weather-aware.** It is optional and learns how much more a heat pump draws on a cold day.
- 🌆 **Evening reserve.** It buys the energy for an expensive evening peak at night, and has an
  optional high-price rescue for days that turn out duller than forecast.
- ⚡ **House connection protection.** With wallboxes on the same connection, the battery gives way
  before the main fuse does.
- 📉 **Efficiency search.** It measures at which charge power your inverter loses the least
  energy.
- 🔌 **Power cut aware.** In island operation it lets go of the inverter at once.
- ↩️ **Everything it changes, it gives back.** It restores the minimum SOC, the switches and the
  limits at the end of the window, also after a restart.
- 🧩 **Works with any inverter.** All it needs is a minimum SOC, a grid charge switch and a state
  of charge as Home Assistant entities. It is developed on a Kostal Plenticore G3 with KOSTAL KORE.
- 🇬🇧🇩🇪 **Fully in English and German**, set up entirely in the user interface.

## Quick start

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Puma7&repository=InverterChargeNight&category=integration)

1. Add this repository to HACS with the button above (type *Integration*). Download
   **Inverter Charge Night** and restart Home Assistant.
2. **Settings → Devices & services → Add integration → Inverter Charge Night.**
3. Fill in the two required pages: the inverter's minimum SOC, grid charge switch, state of
   charge, battery capacity, tomorrow's PV forecast, and the charging window.
4. Watch the **Target SOC** sensor for a night or two. Then add the recommended options:
   planner mode *Bridge*, a house consumption meter, a safety buffer, the charge limit and the
   island detection.

Step by step: [Installation and first setup](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/installation.md).

## Documentation

| | |
|---|---|
| [Installation and first setup](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/installation.md) | Requirements, HACS, the setup dialog, recommended next steps |
| [How it works](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/how-it-works.md) | One night step by step, how the target is calculated, buffer, weather, evening reserve |
| [Configuration reference](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/configuration.md) | Every setting with its default, and suggested setups |
| [Safety](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/safety.md) | Restoring settings, house connection, power cut, lost communication |
| [Efficiency search](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/efficiency-search.md) | Finding the charge power with the smallest loss |
| [Exceptions and extras](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/extras.md) | Skip, override, snow nights, high-price rescue, PV shortfall, curtailment |
| [Entities and actions](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/entities-and-actions.md) | All entities and attributes, actions, automation examples |
| [Dashboard](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/dashboard.md) | A ready-made dashboard view |
| [Troubleshooting and FAQ](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/troubleshooting.md) | Repair issues, debug logs, diagnostics, common questions |
| [Kostal with KOSTAL KORE](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/kostal-kore.md) | Which entity goes where, and the traps |
| [Morning discharge](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/morning-discharge.md) | The experimental opposite mode |
| [Development](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/development.md) | Checks, lab, architecture, releases |

## Requirements

- Home Assistant **2025.2** or newer.
- An inverter integration that exposes a writable **minimum SOC** (number), a **grid charge
  switch** and the **state of charge** (sensor). Examples are
  [KOSTAL KORE](https://github.com/Puma7/KostalKore), Kostal Plenticore, SMA, Fronius, Solax or
  your own Modbus setup.
- A **PV forecast** for tomorrow in kWh, for example
  [Solcast](https://github.com/BJReplay/ha-solcast-solar) or
  [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/).

## Safety first

ICN writes to a device that powers your house. It changes nothing outside the window and gives
back everything it changed. It charges less rather than more when a reading is missing, and it
lets go of the inverter during a power cut.
[Read the safety page](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/safety.md)
before you let it run unattended, especially if a wallbox shares the house connection.

## Support and contributing

- Questions and problems: [GitHub issues](https://github.com/Puma7/InverterChargeNight/issues).
  Please attach the diagnostics, and a debug log of the night if you can
  ([how](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/troubleshooting.md#collecting-evidence-for-one-night)).
- Changes: see [Development](https://github.com/Puma7/InverterChargeNight/blob/main/docs/en/development.md).
  Pull requests are welcome.
- Release history: [CHANGELOG](https://github.com/Puma7/InverterChargeNight/blob/main/CHANGELOG.md).

## Licence

[MIT](https://github.com/Puma7/InverterChargeNight/blob/main/LICENSE), without warranty.
Controlling an inverter and a battery is at your own risk.
