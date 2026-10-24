# Morning discharge (experimental)

> **Experimental.** This mode sells stored energy instead of buying it, and whether that pays
> depends on a tariff, a forecast and an inverter that can be told to discharge on demand. It is
> off by default and nothing else in the integration needs it. If you are unsure, use **Night
> charge**.

## What it is for

Night charge answers one question: *how much grid energy does the battery need tonight so that
tomorrow's sun still fits?* Morning discharge answers the opposite one: *the sun is going to
cover everything today anyway, so what is the battery still holding for?*

On a summer day the answer is often "more than it should". The forecast is 30 kWh, the house
needs 15, and the battery starts the morning full. Every kilowatt-hour still in it at sunrise is
one the roof will not be able to put anywhere.

Between roughly **05:00 and 08:00** two things happen at once:

- households draw their daily peak — showers, kettles, the first appliances, the commute;
- the sun is not yet high enough to cover it.

That is when grid power is most expensive on a dynamic tariff, and when the grid is tightest.
A battery that empties into that window does two useful things at the same time: it earns the
spread between the morning price and whatever the energy cost to store, and it takes that load
off the grid at the hour it can least afford it. Then the roof refills it for free.

This is the mirror image of §14a: there you *buy* when the grid is relaxed and the tariff is
reduced; here you *sell* when the grid is strained and the tariff is high.

## What it needs

| | |
|---|---|
| **Force discharge switch** | Required. It is the only thing that actually discharges — without it the mode just raises the min SOC floor and waits. The wizard refuses to save the mode without it. |
| **A tariff that pays for it** | A dynamic tariff (Tibber, aWATTar, EPEX-based) or a feed-in arrangement worth more than what the energy cost you. At a flat feed-in rate the arithmetic usually does not work. |
| **A summer-shaped forecast** | It only makes sense when the day's PV will cover the house *and* refill the battery. In winter this mode empties a battery you will want. |
| **A window that matches your peak** | Start and end time are the same two fields as for night charge. 05:00–08:00 is a reasonable starting point for a German household; look at your own consumption first. |

## How it behaves

1. At the window start it captures the inverter's current min SOC, then writes the target SOC as
   a floor — the battery will not go below it.
2. It makes sure grid charging is off.
3. It turns the force discharge switch on.
4. When the battery reaches the target, it turns the switch off again.
5. At the window end it restores the min SOC it found and leaves the inverter as it was.

Two safety properties are worth knowing:

- **It never discharges against an unknown floor.** If the min SOC cannot be read or written, the
  discharge does not start. An inverter that is not answering is not one to empty.
- **It stands down during a power cut.** With a backup/island entity configured, the mode gets
  out of the way within seconds, because in that state the battery is running your house.

## What it does not do yet

It runs on the same fixed daily window as night charge. It does not read a price signal, so it
cannot pick the most expensive three hours by itself, and it does not know whether today's
forecast justifies emptying the battery — you decide that by switching the mode. Both are
tracked in `plans/README.md` (items 010 and 013).

## The evening reserve bounds it

Since 3.2.0 a configured high-price period (step 2) also floors this mode: the target the
battery is emptied to is raised by what that evening will need, minus what today's forecast says
the sun will still deliver. Selling at 06:00 what has to be bought back at 19:00 under a peak
tariff is the one trade this mode must not make. On a summer day, whose forecast covers the
evening anyway, nothing changes.
