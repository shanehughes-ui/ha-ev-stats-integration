# The two dashboards

Both ship with the integration. Neither needs a file copied anywhere, and
neither contains an entity id typed in by hand — which matters more than it
sounds, because entity ids here are built from the device name. A car called
*EX2* produces `sensor.ex2_free_share`; a car called *the van* produces
something else. A dashboard file with ids in it would work on exactly one
installation.

## The panel

Appears in the sidebar as **EV Stats** as soon as the integration is set up.
Nothing to install, nothing to configure.

It runs inside the Home Assistant frontend, so it is already authenticated as
whoever is looking at it. The version of this that ran as a static page in
`www/` needed a long-lived access token pasted into it, which then lived in the
page, in a bookmark and in the browser cache. There is no token here to leak.

It is deliberately not styled like Lovelace. It reads as the car's own
instrument cluster: one accent for free energy, one for paid, hairline rules,
tabular figures. If more than one car is configured it shows a switcher.

## The Lovelace view

For people who want these figures beside the rest of their house rather than on
a page of their own.

Call the **EV Stats: build a Lovelace view** action, with your car selected:

```yaml
action: ev_stats.dashboard
data:
  config_entry_id: <your entry>
```

It returns a `yaml` field. Paste it into a dashboard's raw configuration
editor. Nothing is written or changed by calling it — the YAML is the whole
output, and you are meant to read it before you use it.

Core cards only, so nothing has to be installed from HACS first.

The view adapts to what you actually configured. No solar means no solar-share
card; no engine sensor means no trips table. An absent signal removes its card
rather than leaving one that reads *unavailable* for ever and makes the
dashboard look broken.
