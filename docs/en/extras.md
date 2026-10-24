# Exceptions and extras

[🇩🇪 Deutsch](../de/extras.md) · [Documentation overview](README.md)

## Skip one night

The switch **Skip the next window (24 h)** skips the coming window and switches itself off
24 hours after it was switched on. Switched on during a running window, it ends that window at
once; it then expires during the next night's window, which starts at that point. Use it for
guests, a long trip, or a night you want to charge the car instead.

## Set the target by hand

The number **Target SOC override** replaces the planner's target until the window ends. It is
rounded to a whole percent and kept inside your minimum and maximum SOC. Inside a running window
it takes effect at once, and it survives a restart.

## Snow nights

Snow on the modules makes any forecast wrong. Set **Snow nights** to the number of nights the
panels will stay covered (0–14). Each of those nights charges to the maximum SOC, ignoring the
forecast and the override, and the counter drops by one after each completed night-charge window.

## Very low PV yield

With **Account for very low PV yield** configured (forecast for today plus a daily PV energy
meter), ICN keeps the morning's forecast and compares it after sunset with what was produced. At
least 5 kWh expected and no more than 15 % delivered arms the **next** regular night at the maximum
SOC, like a snow night. A repair warning and the `pv_yield` attributes of the *Target SOC* sensor
explain the decision. Low yield can mean snow, shading, a string fault or a wrong forecast; ICN
does not try to tell which.

## High-price rescue

The night plan buys for the high-price period in advance. The rescue is for when that plan turns
out too optimistic, for example snow with no snow nights set. The sensor **Evening outlook**
shows the shortfall in kWh, normally 0. It needs the forecast for **today**.

The outlook reckons tightly on purpose: the period's average load, no allowance, and the full
forecast. Energy held beyond what the period needs is carried past it into the cheap night, while
falling a little short costs only the last half hour of the period at the peak tariff instead of
the day tariff.

With the switch **High-price rescue (hold and charge)** on, ICN looks again at every update
(every 15 minutes by default):

1. **A shortfall: hold what the period needs.** The minimum SOC is raised to what the period
   needs, or to the current level if the battery holds less. Above the need the battery keeps
   serving the house. Below it, the floor follows the sun up to the need and no further. The
   minimum SOC is never set above the battery's level, which some inverters answer with a grid
   charge.
2. **Buy only if it pays.** In the last two hours before the period the rest is bought from the
   grid, but only with a [price entity](configuration.md#account-for-electricity-prices) whose
   prices make the period dearer than buying now plus the loss through the battery (charge and
   discharge efficiency), by at least 2 ct. With a few cents between the day and the peak tariff
   that never happens, and the rescue only holds.
3. **Let go when the sun is enough.** Once the outlook shows the battery making it with 0.5 kWh to
   spare, the inverter gets its own settings back. A later shortfall engages the rescue again.
   Between the two nothing is written.
4. **At the start of the period** the inverter gets its own settings back in any case, and the
   battery carries the evening.

An example: 10 kWh in the battery, the period needs 5 kWh, and the afternoon stays dull. The
battery runs the house until 5 kWh are left and holds those for the period.

The switch is off by default on purpose: watch the outlook sensor for a while before you let it
act. Switching it off ends a running rescue at once. Up to version 3.11 the feature was called
the *evening rescue*; installations from then keep the entity id
`switch.inverter_charge_night_evening_rescue_hold_and_charge`.

## Curtailment outlook

If your feed-in is capped at the connection point (the German 60 % or 70 % rule), a full battery
on a sunny day means everything above the cap is lost. The sensor **Curtailment outlook**
(disabled by default) estimates how much. It needs the cap in W (page *Other settings*: 0.60 ×
your array in W, so 9000 for 15 kWp at 60 %) and the forecast for today. A feed-in power entity
lets it check the model: `measured_overflow_kwh_envelope` adds up, for every hour of the day, how
far the highest feed-in of the last 14 days went above the cap, and `model_vs_envelope_pct` sets
today's modelled spill against it. A model above 100 % of that envelope claims an overflow your inverter has never come
close to. Nothing is throttled from this; it only measures.

## Morning discharge (experimental)

The opposite of night charge: on a summer day whose forecast covers the house anyway, it empties
the battery into the morning peak. It needs a force discharge switch and a tariff that pays for
it. See [Morning discharge](morning-discharge.md).
