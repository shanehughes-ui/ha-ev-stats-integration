"""Which socket a charge is drawing from, learned from the ones already named.

Pure, like `classify.py` and for the same reason: this decides where energy
gets filed, so it should be arguable from a table of numbers rather than by
driving somewhere and plugging in.

Peak current was logged as corroboration only, explicitly barred from the
decision, on the reasoning that a 10 A socket at work fingerprints identically
to a 10 A socket at home. That is true in general, and it is why `match` below
returns nothing the moment one current band holds sessions from more than one
bucket. What the reasoning missed is that the bar cost something: the two
signals the classifier does trust fail *together*.

  * The house-supply ratio declines to answer on a short session - under 2 kWh
    or 45 minutes - because a few minutes of house load against a charge is
    noise, not proof.
  * The GPS fix can be `fresh` and still wrong. Freshness means the odometer
    has not moved since the fix was latched, which is a good test for a stale
    fix on a car that has driven away. It is no test at all when the car
    reports its position lazily: a lagged fix gets stamped against a current
    odometer, passes, and names yesterday's parking spot.

When the ratio is silent there is nothing left to contradict that fix. Two
sessions were filed wrongly that way inside two weeks, 19 and 30 Sep 2026, both
of them at home, both with the home socket's current sitting in the record.

LEARNED, not configured. The home socket in the install this grew out of went
from 10 A to 15 A on 30 Sep 2026; a hard-coded table written the week before
would have been wrong for every session after it. Nearest-neighbour over the
session log has no constant to retune and picks a new socket up on its second
use. It also means a brand-new install knows nothing and says so, rather than
starting out confidently wrong.

Only buckets that name a *place* can be learned. `other` and `public_dc` are
catch-alls standing for every socket in the world, so a session filed under
either says nothing whatsoever about what current to expect next time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# A tenth of the reading, floored so a low-current socket still gets a usable
# window. At 15 A that is +/-1.5 A, against a 7.6 A gap to the nearest other
# band in the install this was measured on; at 7 A the floor gives +/-0.8 A
# where a tenth would give 0.72.
TOLERANCE_FRACTION = 0.10
TOLERANCE_FLOOR_A = 0.8

# Below this there is no meaningful current reading: the helper parks at zero
# between sessions, and some installs have no current entity at all.
MIN_AMPS = 1.0

# Mirrors `classify.CONFIDENCE_HIGH`. Not imported from there, because
# `classify` imports this module and the reverse would be a cycle - and because
# this is reading a value out of a stored record, which is a file format rather
# than part of that module's API.
CONFIDENCE_HIGH = "high"


@dataclass(frozen=True)
class SocketMatch:
    """A current reading resolved to a place, and why."""

    bucket: str
    support: int
    reason: str


@dataclass(frozen=True)
class Band:
    """The spread of currents one place has actually been seen to draw."""

    bucket: str
    low: float
    high: float
    count: int


def _amps(record: Mapping[str, Any]) -> float | None:
    """The peak current on a stored session, or None if it has none.

    `peak_amps` did not always exist, and installs without a current entity
    never write it, so a missing key and a null are both ordinary and mean the
    same thing: this session cannot vote.
    """
    try:
        amps = float(record.get("peak_amps"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return amps if amps >= MIN_AMPS else None


def _confirmed(record: Mapping[str, Any], learnable: Iterable[str]) -> bool:
    """Whether this session is solid enough to teach anything.

    A human correction counts however the classifier originally scored it -
    that is the strongest evidence in the log, and the sessions that prompted
    this module were all corrections. Otherwise the verdict has to have been
    reached with high confidence, which means it had both a house-supply ratio
    and a fresh fix behind it.
    """
    if record.get("bucket") not in tuple(learnable):
        return False
    return bool(record.get("corrected")) or record.get("confidence") == CONFIDENCE_HIGH


def tolerance(peak_amps: float) -> float:
    return max(TOLERANCE_FLOOR_A, peak_amps * TOLERANCE_FRACTION)


def match(
    peak_amps: float | None,
    sessions: Iterable[Mapping[str, Any]],
    learnable: Iterable[str],
) -> SocketMatch | None:
    """Name the socket this current belongs to, or decline.

    Declines in three distinct situations that all mean "no evidence", and are
    worth keeping apart in the head even though they share a return value: no
    usable reading, no socket on record that draws anything like this, and a
    band that two different places both occupy. Only the third is a collision,
    and it is the one the whole signal was once dismissed over.
    """
    if peak_amps is None or peak_amps < MIN_AMPS:
        return None

    learnable = tuple(learnable)
    tol = tolerance(peak_amps)
    hits = [
        str(record["bucket"])
        for record in sessions
        if _confirmed(record, learnable)
        and (amps := _amps(record)) is not None
        and abs(amps - peak_amps) <= tol
    ]
    if not hits:
        return None

    distinct = sorted(set(hits))
    if len(distinct) > 1:
        # Two places draw the same current. This is the case the signal was
        # ruled out over, and the honest answer is still silence.
        return None

    return SocketMatch(
        bucket=distinct[0],
        support=len(hits),
        reason=(
            f"the socket drew {peak_amps:.1f} A, which matches "
            f"{len(hits)} session{'s' if len(hits) != 1 else ''} at {distinct[0]} "
            f"and nowhere else"
        ),
    )


def bands(
    sessions: Iterable[Mapping[str, Any]], learnable: Iterable[str]
) -> list[Band]:
    """Every current range the log can currently name.

    Diagnostic only - nothing decides anything from this. It exists so a wrong
    answer can be traced back to the sessions that taught it, without anyone
    having to re-derive the window by hand.
    """
    learnable = tuple(learnable)
    seen: dict[str, list[float]] = {}
    for record in sessions:
        if not _confirmed(record, learnable):
            continue
        amps = _amps(record)
        if amps is not None:
            seen.setdefault(str(record["bucket"]), []).append(amps)
    return [
        Band(bucket=bucket, low=min(values), high=max(values), count=len(values))
        for bucket, values in sorted(seen.items())
    ]
