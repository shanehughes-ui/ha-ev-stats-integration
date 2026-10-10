# Changelog

## 0.10.1 — the balance check stops printing -0.0

`Decimal` carries negative zero exactly as a float does, so a residue of
-1e-7 rounds to `Decimal("-0.000")` and reaches a tile as "-0.0" — which reads
as a fault on the one sensor whose entire job is to read zero. The value is
zero; the sign is a rounding artefact.

Latent here rather than observed: the YAML package this grew out of surfaced it
first and this side had the identical shape waiting. Anything genuinely
negative still shows its sign, which is the case that matters — a day earlier
that same sensor read -24.98 and was how a whole missing DC charge got found.

## 0.10.0 — charging the meter never saw

`log_dc_session` was written on the assumption that a public DC charge is
already in the ledger, because the car meters it like any other, and only the
price is missing. A real DC session proved that false: the car reported no
charging power at all, so nothing was integrated, nothing reached a bucket —
and the cost went into the headline figures anyway. Cost in, energy out, which
skews every kWh-weighted figure the same way. The installation it was found on
was overstating its free share by 5.8 points.

- **`log_dc_session` takes an optional `kwh`**, meaning "the meter could not see
  this". Omit it and the old behaviour stands, which is correct for any car that
  does report DC.
- **New `Ledger.async_add_unmetered`** — adds the energy to the bucket and moves
  the balance check's expectation by the same amount, in one lock. Both sides or
  neither.
- **The invariant still reads zero, and still means something.** The new term is
  not slack: it moves only through that one method. Any other path that puts
  energy in a bucket without the meter seeing it still shows up as an error,
  which is the entire point of the number.
- `unmetered` is persisted alongside the balances. Coming back as zero after a
  restart would report the whole DC charge as an error — the same shape of bug
  the charge integrator's `restore` exists to prevent.

## 0.9.0 — "Elsewhere" gets a name

A trips table that reads `Elsewhere → Elsewhere` is accurate and tells you
nothing. `not_home` means the car was outside every zone you have defined,
which is most of the places it goes.

- **New `suburb.py`** — pure: which field of a geocoder's answer to believe,
  what counts as a position, and which ends of a trip are worth asking about.
- **New `geocode.py`** — one GET to OpenStreetMap's Nominatim, no account and
  no key. Answers are cached by coordinate rounded to ~110 m, so a place is
  looked up once and a daily destination is free thereafter. Paced above
  Nominatim's one-request-a-second limit, which only ever matters on a first
  run over an existing log.
- **Off by default, and gated on two separate options.** `record_positions`
  keeps coordinates in the house; the new `name_suburbs` sends some of them out
  of it. They are different decisions and are asked as two.
- **Home and work coordinates are never sent anywhere.** Only an end that fell
  outside every defined zone is looked up — the others already have names, so
  there is nothing to ask. That is a test, not a comment.
- A trip is recorded first and named afterwards, through a new
  `async_annotate_trip`. A geocoder being slow or down must never delay or lose
  a trip.
- The panel shows the suburb in place of "Other" where one is known. A trip
  that finished at home still reads "Home", never the suburb of the house.

## 0.8.0 — the socket counts as evidence

Peak charging current was recorded from the start and explicitly barred from
the decision, on the grounds that two places can own identical sockets. They
can — and the new rule returns nothing when they do. What the original
reasoning missed is that the other two signals fail *together*: a session too
short for the house meter to prove anything is also one where a lagged GPS fix
goes uncontested. Two sessions were filed wrongly that way inside a fortnight,
both of them at home, both with the home socket's current sitting in the
record.

- **New `fingerprint.py`** — pure, like the rest of the decision logic. Matches
  this session's peak current against the sockets already identified, by
  nearest neighbour within a tolerance of 10% (floored at 0.8 A). It declines
  on no usable reading, on a current no known socket draws, and — the case the
  signal was once dismissed over — on a band that two different places both
  occupy.
- **Learned, never configured.** The install this was built from changed its
  home socket from 10 A to 15 A mid-measurement; a hard-coded table written the
  week before would have been wrong for every session after it. Only buckets
  that name a place can teach, so `other` and `public_dc` contribute nothing.
  A new install knows nothing and says so.
- **Where it speaks**, in the classifier: against a fix reading `other` — which
  is the fix landing in no known zone, not a claim about a place; alongside the
  house meter against a fix that disagrees; and in place of a ratio that landed
  between the thresholds, provided the fix does not contradict it. It never
  overturns the house meter on the house meter's own ground.
- **Confidence is capped at medium** whenever the socket had to win against a
  fix that named somewhere, and when the socket is the only evidence there is.
  `high` authorises the tail sweep — claiming energy that accrued before a
  session was recognised — and a verdict reached over a protest does not get it.
- Sessions now record `socket_fingerprint` and `socket_support`, so a verdict
  can be re-argued later.

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
