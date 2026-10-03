"""The socket fingerprint, and the two real sessions that prompted it.

The numbers in `LOG` are the measured ones from the install this grew out of,
with the places renamed: three current bands that do not overlap, 7.2 A and
15.1 A at one place and 28.5 A at another. The gaps between them are several
times wider than the bands themselves, which is the entire reason the signal is
usable - and `test_two_places_one_socket` is what happens when they are not.
"""

from __future__ import annotations

import pytest

import pure

fingerprint = pure.load("fingerprint")
match = fingerprint.match
bands = fingerprint.bands

HOME = "home"
WORK = "workplace"
PLACES = (HOME, WORK)


def session(bucket, amps, confidence="high", corrected=False):
    record = {"bucket": bucket, "peak_amps": amps, "confidence": confidence}
    if corrected:
        record["corrected"] = True
    return record


# A log shaped like a real one: a 10 A socket at home, replaced by a 15 A one
# part-way through, and a 28 A one at work throughout.
LOG = [
    session(WORK, 28.7),
    session(WORK, 28.4),
    session(WORK, 28.3),
    session(HOME, 7.3),
    session(HOME, 7.1),
    session(HOME, 7.2),
    session(WORK, 28.4),
    session(HOME, 7.3),
    session(HOME, 15.3),
    session(HOME, 15.0),
]


# ------------------------------------------------------------- the matching --
@pytest.mark.parametrize(
    ("amps", "expected"),
    [
        (7.2, HOME),
        (7.0, HOME),
        (15.1, HOME),
        (28.5, WORK),
        (28.9, WORK),
    ],
)
def test_names_a_socket_it_has_seen(amps, expected):
    found = match(amps, LOG, PLACES)
    assert found is not None
    assert found.bucket == expected


@pytest.mark.parametrize("amps", [5.0, 11.0, 20.0, 40.0])
def test_declines_a_socket_it_has_never_seen(amps):
    """Between the bands is not "probably the nearest one"."""
    assert match(amps, LOG, PLACES) is None


def test_two_places_one_socket():
    """The collision the signal was once dismissed over, and still is.

    A 10 A socket at work really does fingerprint identically to a 10 A socket
    at home. The answer is silence, not the more popular of the two.
    """
    log = [*LOG, session(WORK, 7.2), session(WORK, 7.2), session(WORK, 7.2)]
    assert match(7.2, log, PLACES) is None


def test_support_counts_only_the_band():
    found = match(7.2, LOG, PLACES)
    assert found.support == 4
    assert "4 sessions" in found.reason


def test_support_of_one_is_enough():
    """A socket used once already beats a fix that names nowhere.

    Not a hypothetical: the 15 A socket had exactly one high-confidence session
    behind it when the third charge through it was misfiled, so a rule needing
    two would have been too late for the case that prompted the rule.
    """
    found = match(15.1, [session(HOME, 15.0)], PLACES)
    assert found is not None and found.support == 1


# ------------------------------------------------------- what may be learned --
def test_only_confident_or_corrected_sessions_teach():
    log = [session(HOME, 15.0, confidence="medium")]
    assert match(15.1, log, PLACES) is None
    log = [session(HOME, 15.0, confidence="medium", corrected=True)]
    assert match(15.1, log, PLACES).bucket == HOME


def test_catch_all_buckets_teach_nothing():
    """`other` stands for every socket in the world and names none of them."""
    log = [session("other", 15.0), session("public_dc", 15.2), session("unknown", 15.1)]
    assert match(15.1, log, PLACES) is None


def test_a_place_not_offered_as_learnable_is_ignored():
    assert match(28.5, LOG, (HOME,)) is None


# -------------------------------------------------------- missing readings --
@pytest.mark.parametrize("record", [{}, {"peak_amps": None}, {"peak_amps": "n/a"}])
def test_records_without_a_current_are_skipped(record):
    """`peak_amps` postdates the earliest records and some installs never write
    it. A missing key and a null are both ordinary, and neither may raise."""
    log = [{"bucket": HOME, "confidence": "high", **record}, session(HOME, 15.0)]
    assert match(15.1, log, PLACES).support == 1


@pytest.mark.parametrize("amps", [None, 0.0, 0.4])
def test_no_usable_reading_declines(amps):
    """The helper parks at zero between sessions; that is not a measurement."""
    assert match(amps, LOG, PLACES) is None


def test_an_empty_log_declines():
    assert match(15.1, [], PLACES) is None


# ---------------------------------------------------------------- tolerance --
def test_tolerance_is_a_tenth_with_a_floor():
    assert fingerprint.tolerance(7.0) == pytest.approx(0.8)
    assert fingerprint.tolerance(15.0) == pytest.approx(1.5)
    assert fingerprint.tolerance(28.5) == pytest.approx(2.85)


def test_the_bands_stay_apart_under_tolerance():
    """The measured bands must not be able to reach each other.

    This is the assumption the whole signal rests on, so it is asserted rather
    than assumed: widen the tolerance far enough and `home` and `workplace`
    would start matching the same current, at which point everything above is
    answering the wrong question.
    """
    home_high = 15.3 + fingerprint.tolerance(15.3)
    work_low = 28.3 - fingerprint.tolerance(28.3)
    assert home_high < work_low


# -------------------------------------------------------------------- bands --
def test_bands_report_what_was_learned():
    learned = {b.bucket: b for b in bands(LOG, PLACES)}
    assert learned[HOME].low == 7.1
    assert learned[HOME].high == 15.3
    assert learned[HOME].count == 6
    assert learned[WORK].count == 4
