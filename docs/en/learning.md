# What ICN learns by itself

[🇩🇪 Deutsch](../de/learning.md) · [Documentation](README.md)

Several numbers in the setup are estimates: the forecast margin, the battery capacity, the two
efficiencies. And the PV forecast does not know your roof: its pitch, the gap behind the panels,
how they behave in the cold, the tree in front of them, how far they have aged. Since 3.14.0 ICN
reads all of this from the house's own data and plans with it. The settings stay as the fallback.

All of it is **on by default** and works without any extra setting, once the sensors it needs are
there. Each value applies only once enough data stands behind it. Until then the setting applies.
The switch **Use learned values** (`switch.inverter_charge_night_use_learned_values`) turns the
use off. Collecting goes on while it is off.

## The PV forecast, by season

**What is collected:**

- The **evening forecast** for tomorrow: the last value of the *tomorrow* entity between 18:00 and
  midnight. That is the number the night plans with.
- The **morning forecast** for today: the last value of the *today* entity before 09:00. That is
  the number the high-price rescue decides with.
- The **yield**: the day's total of the PV meter (*PV energy today*), from the recorder's
  statistics.

At the first start ICN fills in what the recorder still knows of the forecasts, usually the last
ten days. The diary keeps three years. A day the PV meter's statistics cover for fewer than 20
hours stays open, because recording began or Home Assistant was down part of the way through it.

**What is calculated**, for the day being planned:

- **Which days count**: every day whose calendar date lies within 45 days of it, in every year.
  Nearer dates count more. A year further back counts half as much, so ageing panels and new
  shade come in by themselves.
- **When it starts**: once **14 days** in that window have both a forecast and a yield.
- **Factor**: the season's yield divided by the season's forecast, both summed in kWh. A dull day
  off by half says little about the roof; a sunny day off by a fifth says a lot.
- **Band**: how far the days scattered around the corrected forecast. Each edge is one-sided: on
  four days in five the sun delivered at least the lower edge, and on four days in five at most
  the upper one. Both held together on at least three days in five. The band is never narrower
  than **5 %** either way.

**How it is used:**

- The night plan multiplies the forecast by the factor.
- The side that needs less sun (bridge, evening reserve) takes the lower edge.
- The side that keeps room free for the sun takes the upper edge.
- The high-price rescue and the evening outlook take the morning forecast's factor, but no band:
  they reckon tight on purpose.

**Requirements:** the PV forecast entities and the **PV energy today** meter (see
[configuration](configuration.md)).

**Sensor:** *PV forecast correction* (`sensor.inverter_charge_night_pv_forecast_correction`). Its
state is the factor in percent: 92 means the sun delivered 8 % less than forecast. The attributes
are:

- `margin_low_pct` / `margin_high_pct`: the band;
- `days`: the days behind it;
- `error_raw_pct` / `error_corrected_pct`: the mean error of the raw and of the corrected forecast
  over those days;
- `today`: the same for the morning forecast;
- `status`: `learning`, `active` or `no_pv_meter`.

## The battery: capacity and both efficiencies

**Where it comes from:** the recorder keeps five-minute means of every measured sensor for about
ten days. Once a day ICN goes through them and looks for **stretches** of steady charging or
discharging. A stretch counts with at least 300 W, at least 30 minutes and at least 10 % SOC
change.

- **Capacity**: the energy on the battery side divided by the SOC change.
- **Charge efficiency**, only while the panels produce nothing: battery energy against what the
  charger took from the AC side (grid import minus house consumption).
- **Discharge efficiency**, only while the panels produce nothing: what reached the house from the
  battery (house consumption minus grid import) against the battery energy.

**How samples are checked:**

- The sign of the battery power is read from the data: a sensor that reports charging as negative
  (Kostal) is recognised.
- A sample that cannot be right is dropped. That is a capacity below 40 % or above 130 % of the
  nameplate, or an efficiency outside 0.6–1.0.
- The value is the median of the samples, recent ones weighted more. It applies from three
  samples.
- The capacity is bounded to 60–105 % of the nameplate, the efficiencies to 0.75–0.99.

**Charge efficiency first from the efficiency search:** the loss it measured at the best power is
exactly what the night charges at. The energy balance is used only without a search result.

**Requirements:**

- SOC and *Power received* (the battery side, as for the [efficiency search](efficiency-search.md))
  for the capacity.
- For the efficiencies also *House consumption*, *Grid import* and *PV energy today*.
- The sensors need long-term statistics (state class `measurement`), which almost every power
  sensor has.

**Sensor:** *Usable battery capacity* (`sensor.inverter_charge_night_usable_battery_capacity`) in
kWh. Its attributes are:

- `charge_efficiency` and `charge_efficiency_source` (`efficiency_search` or `energy_balance`);
- `discharge_efficiency`;
- the number of samples of each kind;
- `*_used`: what the plan works with right now;
- `status`: `learning`, `active`, `no_sensors` or `no_statistics`.

## Safety

- Every learned value is bounded, and it applies only with enough data behind it.
- Missing sensors or statistics leave the setting in place.
- A new forecast entity, PV meter or battery sensor starts what is learned from it afresh, and so
  does a new battery capacity setting for the capacity. A new
  *today* entity, for example, keeps the evening forecasts and the yields.
- A smaller capacity means more percent per kWh and a higher target: that is the safe direction.
  A larger one is capped at 105 % of the nameplate.
- The PV shortfall monitor ([extras](extras.md)) keeps comparing the raw forecast. It is there to
  catch the forecast itself.
- Everything is in the diagnostics under `state.learning`. The data lives in its own file in
  `.storage`, and removing the entry deletes it.
