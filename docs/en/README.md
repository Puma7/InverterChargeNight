# Inverter Charge Night – documentation

[🇩🇪 Deutsch](../de/README.md) · [Back to the project page](../../README.md)

![How the parts fit together: forecast, consumption, prices and weather go into Inverter Charge Night, which writes the minimum SOC, the grid charge switch and the charge limit through your inverter integration](../images/overview.en.svg)

## Getting started

1. [Installation and first setup](installation.md): requirements, HACS, the setup dialog.
2. [How it works](how-it-works.md): one night step by step, and how the target is calculated.
3. [Configuration reference](configuration.md): every setting, with suggested setups.

## In depth

- [Safety](safety.md): restoring settings, house connection, power cut, lost communication.
- [Efficiency search](efficiency-search.md): the charge power with the smallest loss.
- [What ICN learns by itself](learning.md): the forecast by season, the usable capacity, both
  efficiencies.
- [Exceptions and extras](extras.md): skip, override, snow nights, high-price rescue, PV shortfall,
  curtailment.
- [Morning discharge](morning-discharge.md): the experimental opposite mode.

## Reference

- [Entities and actions](entities-and-actions.md): all entities, attributes and actions, with
  automation examples.
- [Dashboard](dashboard.md): a ready-made dashboard view.
- [Troubleshooting and FAQ](troubleshooting.md): repair issues, debug logs, diagnostics.
- [Kostal with KOSTAL KORE](kostal-kore.md): which entity goes where, and the traps.
- [Development](development.md): checks, lab, architecture, releases.
- [Changelog](../../CHANGELOG.md).
