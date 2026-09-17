# Changelog

## 0.7.0 — first release

Where your EV charged, what it cost, and what it saved.

### The ledger
- Charging energy is filed into buckets — home, each configured free-charging
  zone, public DC, elsewhere, and `unknown`. Every kWh lands in `unknown`
  unless something has *proven* where the car was.
- A bucket carries its energy, its cost and its solar share together, and the
  one operation that moves energy moves all three under one lock.
- Two invariants asserted rather than hoped for: the buckets always sum to the
  metered total, and no bucket goes negative. The first is a sensor.

### Deciding where
- The house-supply proof — did your own meter consume this? — which depends on
  no location data at all.
- A GPS fix, trusted only while the odometer says the car has not moved since
  it was taken. Not an age in minutes.
- Where they disagree the verdict is `conflict` and the energy stays
  unattributed, because an honest "I do not know" is correctable and a
  confident wrong answer is not.

### Also
- Trips, charging sessions and pack measurements in a `Store`, not in entity
  attributes the recorder rewrites on every state change.
- Usable pack capacity measured from any charge spanning 30% or more, published
  as a rolling mean.
- Cost, solar and carbon rates that all split the car's draw at the same
  boundary, so the kWh and the dollars cannot disagree.
- Tyres reported as drift against their own 30-day history, never pass or fail.
- A sidebar panel that needs no token, and a Lovelace view generated with your
  own entity ids.
- `import_legacy` for anyone upgrading from the YAML package this grew out of.

### Known limits
- History does not carry across from the YAML version: the statistics belong to
  the old entity ids.
- Long-term statistics start when the entities do.
- Nothing is ever sent to the car, by design.
