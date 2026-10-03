"""Which place a charging session happened at.

Pure decision logic, with no Home Assistant import, for the same reason
`trapezoid.py` has none: this is the judgement the whole attribution rests on,
and it should be possible to put a table of cases in front of it rather than
standing up a test harness and waiting for a real charge.

Three signals, deliberately independent:

  * **the house-supply proof** - did our own house meter actually consume the
    energy the car says it took? This depends on no location data at all, which
    is precisely why it is worth having. ~1.0 means the house supplied it, ~0.0
    means somewhere else did.
  * **the GPS fix**, and only when it is provably fresh - see `location.py`.
  * **the socket fingerprint** - see `fingerprint.py`. Peak current, matched
    against the sockets already identified. This one is a late addition and a
    reluctant one: it was deliberately excluded at first, on the grounds that
    two places can own identical sockets. They can, and it refuses to answer
    when they do. What that reasoning missed is that the other two signals fail
    *together* - a session too short to prove anything through the house meter
    is also one where a lagged GPS fix goes uncontested - and the current was
    in the record the whole time.

Where they disagree the answer is `conflict`, which files the energy as
`unknown` rather than picking a winner. That is the point: an honest "I do not
know" is recoverable with one tap on a notification, while a confident wrong
answer is not, because nobody goes looking for it.

The socket is the one exception, and only against `other`. A fix reading
`other` is not a competing claim about a place - it says the fix landed in no
zone we know, which is exactly what a lagged fix produces - so a socket we can
name beats it. Against a fix that names a *different* place, the socket does
not win; that stays a conflict.

The middle band between the two ratio thresholds is also `unknown` on purpose.
A ratio of 0.4 means the house supplied roughly half the energy, which is not a
thing that can physically happen - it means the baseline is off, or another
large load ran during the session - and a signal that is behaving strangely
should not be believed harder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .const import (
    BUCKET_HOME,
    BUCKET_OTHER,
    BUCKET_PUBLIC_DC,
    BUCKET_UNKNOWN,
)
from .fingerprint import SocketMatch

# Verdicts that are not buckets. Both file their energy as `unknown`, but they
# mean different things and the distinction is worth keeping in the session log:
# `idle` is nothing happening, `conflict` is two signals that disagree.
VERDICT_IDLE = "idle"
VERDICT_CONFLICT = "conflict"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# A GPS resolution that is not a place.
GPS_UNKNOWN = "unknown"
GPS_OTHER = BUCKET_OTHER

SUPPLY_AC = "AC"
SUPPLY_DC = "DC"


@dataclass(frozen=True)
class Thresholds:
    """The tunables, all of which come from the config entry."""

    home_ratio_yes: float = 0.60
    home_ratio_no: float = 0.25
    dc_min_kw: float = 15.0


@dataclass(frozen=True)
class Evidence:
    """Everything the classifier is allowed to look at.

    Assembled by `session.py`. Keeping it a plain frozen record means a case
    that once went wrong can be written down as six values and kept for ever as
    a test, instead of being reproduced by driving somewhere.
    """

    active: bool = False
    supply: str | None = None
    peak_kw: float = 0.0
    gps_zone: str = GPS_UNKNOWN
    gps_fresh: bool = False
    # None means the proof declined to answer - too little energy, too short a
    # session, or a baseline that has not filled its window yet. It is not zero.
    # Defaulting this value to zero is what once turned "no opinion" into
    # "definitely not the house".
    house_ratio: Decimal | None = None
    # Already resolved to a place by `fingerprint.match`, which is pure and
    # takes the session log. None means it declined - no usable current, no
    # socket on record that draws this, or two places that both do.
    socket: SocketMatch | None = None
    work_buckets: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Classification:
    """The verdict, and enough of the reasoning to argue with it."""

    verdict: str
    bucket: str
    confidence: str
    reason: str

    @property
    def is_conflict(self) -> bool:
        return self.verdict == VERDICT_CONFLICT


def _confidence(ev: Evidence, dc_rule_fired: bool) -> str:
    """How much the verdict should be trusted.

    This gates the tail sweep, so it is not decoration: `high` is what allows a
    session to claim energy that accrued before it was recognised.

    Note the DC test is whether the DC *rule fired*, not merely whether the
    supply attribute says DC. The original asked the latter, which meant a slow
    DC charger below the power threshold produced a `home` or `other` verdict
    carrying high confidence - a verdict contradicted by the very signal that
    made it confident, and one authorised to sweep.
    """
    if dc_rule_fired:
        return CONFIDENCE_HIGH
    has_ratio = ev.house_ratio is not None
    if has_ratio and ev.gps_fresh:
        return CONFIDENCE_HIGH
    if has_ratio or ev.gps_fresh:
        return CONFIDENCE_MEDIUM
    # A socket match on its own is enough to file the energy somewhere, and
    # never enough to sweep: `low` would be a lie about a session we can in
    # fact place, `high` would hand it the pre-session tail on one signal.
    if ev.socket is not None:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _capped(confidence: str) -> str:
    """Confidence for a verdict the socket had to win against a named fix.

    Still the better bet - two independent signals against one - but not a sure
    thing, and `high` is not decoration here: it is what authorises the tail
    sweep, which lets a session claim energy that accrued before it was even
    recognised. A verdict reached over a protest does not get that.
    """
    return CONFIDENCE_MEDIUM if confidence == CONFIDENCE_HIGH else confidence


def classify(ev: Evidence, th: Thresholds) -> Classification:
    """Decide where a session happened.

    Order matters. DC is checked first because it is the only signal that
    identifies a place by what the place *is* rather than by where the car is:
    nothing at a house delivers DC at 15 kW.
    """
    if not ev.active:
        return Classification(
            VERDICT_IDLE, BUCKET_UNKNOWN, CONFIDENCE_LOW, "no session running"
        )

    known_places = (BUCKET_HOME, *ev.work_buckets, GPS_OTHER)

    if ev.supply == SUPPLY_DC and ev.peak_kw > th.dc_min_kw:
        return Classification(
            BUCKET_PUBLIC_DC,
            BUCKET_PUBLIC_DC,
            CONFIDENCE_HIGH,
            f"DC supply at {ev.peak_kw:.1f} kW, above the {th.dc_min_kw:.0f} kW threshold",
        )

    conf = _confidence(ev, dc_rule_fired=False)
    # `fingerprint.match` only ever returns a bucket that names a place, so
    # this is `home`, a work bucket, or nothing.
    socket = ev.socket.bucket if ev.socket is not None else None

    if ev.house_ratio is not None:
        ratio = float(ev.house_ratio)

        if ratio > th.home_ratio_yes:
            # Our meter saw the energy. A fresh fix somewhere else is a genuine
            # contradiction and must not be resolved by preferring one signal.
            if ev.gps_zone in (BUCKET_HOME, GPS_UNKNOWN):
                return Classification(
                    BUCKET_HOME,
                    BUCKET_HOME,
                    conf,
                    f"house supplied {ratio:.0%} of it",
                )
            if socket == BUCKET_HOME:
                # Our own meter measured the energy leaving the house, and the
                # socket is one we have only ever seen at home. Two independent
                # signals against a fix that is provably unmoved but not
                # provably right. Filed as `conflict` before this existed:
                # 19 Sep 2026, 4.561 kWh.
                return Classification(
                    BUCKET_HOME,
                    BUCKET_HOME,
                    _capped(conf),
                    f"house supplied {ratio:.0%} of it and {ev.socket.reason}, "
                    f"against a fix at {ev.gps_zone}",
                )
            return Classification(
                VERDICT_CONFLICT,
                BUCKET_UNKNOWN,
                conf,
                f"house supplied {ratio:.0%} of it but the car was at {ev.gps_zone}",
            )

        if ratio < th.home_ratio_no:
            if ev.gps_zone == BUCKET_HOME:
                return Classification(
                    VERDICT_CONFLICT,
                    BUCKET_UNKNOWN,
                    conf,
                    f"car was at home but the house supplied only {ratio:.0%}",
                )
            if ev.gps_zone in known_places:
                return Classification(
                    ev.gps_zone,
                    ev.gps_zone,
                    conf,
                    f"house supplied only {ratio:.0%}, and the car was at {ev.gps_zone}",
                )
            if socket is not None and socket != BUCKET_HOME:
                # No usable fix, but the socket names somewhere. Never home:
                # the ratio has just ruled that out, and the socket does not
                # get to overturn the house meter on the house meter's own
                # ground.
                return Classification(
                    socket,
                    socket,
                    conf,
                    f"house supplied only {ratio:.0%}, and {ev.socket.reason}",
                )
            # Not our house, and no idea where instead. Knowing only where it
            # was NOT is not enough to file it anywhere.
            return Classification(
                BUCKET_UNKNOWN,
                BUCKET_UNKNOWN,
                conf,
                f"house supplied only {ratio:.0%}, but there is no usable fix",
            )

        if socket is not None and ev.gps_zone in (socket, GPS_UNKNOWN):
            # The ratio is behaving strangely, so it is not evidence either
            # way - but the socket does not depend on the house meter at all.
            # It still only gets to speak where the fix does not contradict it.
            return Classification(
                socket,
                socket,
                conf,
                f"house-supply ratio {ratio:.0%} means nothing physical, "
                f"but {ev.socket.reason}",
            )
        return Classification(
            BUCKET_UNKNOWN,
            BUCKET_UNKNOWN,
            conf,
            f"house-supply ratio {ratio:.0%} sits between the thresholds "
            "and means nothing physical",
        )

    # No house-supply proof: either it is not configured at all, or it declined
    # to answer. Location alone, which is exactly the fallback the thresholds
    # exist to avoid needing.
    if socket is not None:
        if ev.gps_zone in (socket, GPS_UNKNOWN, GPS_OTHER):
            # `other` is not a competing claim; it is the fix failing to land
            # anywhere known, which is what a lagged fix looks like. Filed as
            # `other` before this existed: 30 Sep 2026, 0.256 kWh.
            return Classification(
                socket,
                socket,
                _capped(conf) if ev.gps_zone == GPS_OTHER else conf,
                f"no house-supply proof, but {ev.socket.reason}",
            )
        # A fix naming a DIFFERENT known place is a real disagreement, and
        # there is no third signal left to break it.
        return Classification(
            VERDICT_CONFLICT,
            BUCKET_UNKNOWN,
            conf,
            f"{ev.socket.reason}, but the fix places the car at {ev.gps_zone}",
        )
    if ev.gps_zone in known_places:
        return Classification(
            ev.gps_zone,
            ev.gps_zone,
            conf,
            f"no house-supply proof; fix places the car at {ev.gps_zone}",
        )
    return Classification(
        BUCKET_UNKNOWN,
        BUCKET_UNKNOWN,
        conf,
        "neither the house meter nor a fresh fix could say",
    )
