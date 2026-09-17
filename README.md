# EV Stats

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Validate](https://github.com/shanehughes-ui/ha-ev-stats-integration/actions/workflows/validate.yml/badge.svg)](https://github.com/shanehughes-ui/ha-ev-stats-integration/actions/workflows/validate.yml)

Home Assistant integration that answers one question the Energy dashboard cannot:
**where** did your EV's energy come from, and what did it actually cost?

Most EVs charge in more than one place — free at a workplace, at home off your own
solar, occasionally at a public DC charger. The kilowatt-hours are identical; what
they cost is not. This splits them.

> **Status: early.** Working toward a first release. See [what works](#what-works-today).

---

## What it does

- **Splits charging energy by where it happened** — home, any number of free zones,
  public DC, and an explicit *unattributed* bucket for anything it cannot prove.
- **Prices home charging properly.** Solar sent to the car is priced at the feed-in it
  gave up, not at zero, so the saving is real rather than flattering.
- **Measures consumption** at the wall and at the pack, and says which is which.
- **Measures the usable battery** from wide charges, so degradation becomes a trend
  you can actually watch.
- **Logs every charge and every trip**, with the evidence behind each verdict.

## The hard part

Working out where a charge happened is harder than it sounds, because a car's
reported position is often stale. This integration never trusts it on its own:

- **A freshness gate** — a GPS fix counts only while the odometer has not moved since
  it was taken. A stale fix reports the *previous* parking spot with full confidence,
  and that failure is the reason everything else here exists.
- **A house-supply proof** — did *your* house actually consume the energy the car
  says it took? Completely independent of GPS, and the only signal that survives when
  the position is stale. Only trusted above a minimum size, because below that the
  household's own noise swamps it.
- **Explicit disagreement.** When a fresh fix says home but the house consumed
  nothing, it does not pick a side. It records `conflict`, leaves the energy
  unattributed, and asks.

Energy accrues to `unknown` and is moved exactly once. **If every part of this fails,
the energy is still counted** — just visibly unattributed, never silently misfiled.

## What works today

| | |
|---|---|
| Config flow, fully parameterised | ✅ |
| Distance since install | ✅ |
| Charge energy, cost and solar integrators | in progress |
| The attribution ledger | in progress |
| Session and trip logs | planned |
| Dashboards | planned |

Nothing here is hard-coded to a particular car. It was developed against a Geely EX2
read through [geely-connect](https://github.com/YossiKon/geely-connect), but every
entity is chosen in the config flow.

## Requirements

Four entities, and that is genuinely all:

| | |
|---|---|
| Charging power | kW, `device_class: power` |
| Odometer | km, `device_class: distance` |
| State of charge | %, `device_class: battery` |
| Home zone | any `zone.` entity |

Everything else — house load, solar, prices, carbon, tyres, service, a location
tracker — is optional. **An absent signal removes the sensors that depend on it
rather than reporting a confident zero.** No solar means no solar share; it does not
mean 0%.

## Install

**HACS** → ⋮ → Custom repositories → add
`https://github.com/shanehughes-ui/ha-ev-stats-integration`, category **Integration**.
Then install, restart, and add **EV Stats** from Settings → Devices & Services.

Or copy `custom_components/ev_stats/` into your `config/custom_components/` and
restart.

## Screenshots

<!-- Captured from a live install. Zone names and coordinates are redacted. -->

*Coming with the first release — the dashboards land in a later phase.*

## Design notes

Three decisions that are easy to get wrong, recorded because this project got each
of them wrong first:

- **The buckets are `state_class: total`, never `total_increasing`.** Moving energy
  between buckets means a bucket can go *down*, and a `total_increasing` series reads
  a decrease as a meter reset — so the long-term statistics keep climbing and a
  correction inflates the day it was made.
- **Charging happens before the driving it pays for.** Any net figure — consumption,
  carbon — starts out wrong and needs a minimum distance before it means anything.
  Those guards are deliberate, and the reason a figure sometimes shows a dash.
- **State of charge is never used as an energy meter.** On an LFP pack it implies
  wildly optimistic range and visibly bounces *upward* mid-drive. It is used only as
  a boundary between two points of a charge, which is the one thing the voltage
  plateau does not spoil.

## Licence

MIT.
