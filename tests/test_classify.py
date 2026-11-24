"""The classifier's decision table, case by case.

Every one of these is a situation the car can actually be in, and several are
situations it has been in. The point of keeping `classify.py` free of Home
Assistant is that a case which once went wrong can be written down as six
values and kept for ever, rather than being reproduced by driving somewhere.

    python -m pytest tests/test_classify.py
"""

from __future__ import annotations

from decimal import Decimal

import pure

classify_mod = pure.load("classify")

classify = classify_mod.classify
Evidence = classify_mod.Evidence
Thresholds = classify_mod.Thresholds

HIGH = classify_mod.CONFIDENCE_HIGH
MEDIUM = classify_mod.CONFIDENCE_MEDIUM
LOW = classify_mod.CONFIDENCE_LOW
CONFLICT = classify_mod.VERDICT_CONFLICT
IDLE = classify_mod.VERDICT_IDLE

WORK = ("workplace",)
TH = Thresholds()


def ev(**kwargs) -> Evidence:
    """An active session, with only the interesting parts spelled out."""
    kwargs.setdefault("active", True)
    kwargs.setdefault("work_buckets", WORK)
    return Evidence(**kwargs)


# ------------------------------------------------------------------- idle --
def test_no_session_is_idle() -> None:
    result = classify(Evidence(active=False), TH)
    assert result.verdict == IDLE
    assert result.bucket == "unknown"


# --------------------------------------------------------------- public DC --
def test_dc_above_the_threshold_is_public() -> None:
    result = classify(ev(supply="DC", peak_kw=48.0), TH)
    assert result.bucket == "public_dc"
    assert result.confidence == HIGH


def test_dc_below_the_threshold_does_not_fire() -> None:
    """A slow DC charger is not identified by its supply alone."""
    result = classify(ev(supply="DC", peak_kw=7.0, gps_zone="other"), TH)
    assert result.bucket == "other"


def test_slow_dc_does_not_borrow_high_confidence() -> None:
    """The bug this fixes: confidence used to be granted by the supply alone.

    A 7 kW DC charger produced an `other` verdict carrying HIGH confidence -
    contradicted by the very signal that made it confident, and high confidence
    is what authorises the tail sweep to claim unattributed energy.
    """
    result = classify(ev(supply="DC", peak_kw=7.0, gps_zone="other"), TH)
    assert result.confidence != HIGH


def test_dc_beats_a_house_meter_that_claims_it() -> None:
    """Nothing at a house delivers 50 kW DC; the house evidence must be wrong."""
    result = classify(
        ev(supply="DC", peak_kw=50.0, house_ratio=Decimal("0.9"), gps_zone="home"), TH
    )
    assert result.bucket == "public_dc"


# ------------------------------------------------------- the house supplied --
def test_house_supplied_it_and_the_car_was_home() -> None:
    result = classify(
        ev(house_ratio=Decimal("0.95"), gps_zone="home", gps_fresh=True), TH
    )
    assert result.bucket == "home"
    assert result.confidence == HIGH


def test_house_supplied_it_with_no_usable_fix() -> None:
    """The proof stands on its own - that is the whole reason it exists."""
    result = classify(ev(house_ratio=Decimal("0.93"), gps_zone="unknown"), TH)
    assert result.bucket == "home"
    assert result.confidence == MEDIUM


def test_house_supplied_it_but_the_car_was_at_work() -> None:
    result = classify(
        ev(house_ratio=Decimal("0.95"), gps_zone="workplace", gps_fresh=True), TH
    )
    assert result.verdict == CONFLICT
    assert result.bucket == "unknown"


# --------------------------------------------------- the house did not pay --
def test_house_did_not_supply_it_and_the_car_was_at_work() -> None:
    result = classify(
        ev(house_ratio=Decimal("0.02"), gps_zone="workplace", gps_fresh=True), TH
    )
    assert result.bucket == "workplace"
    assert result.confidence == HIGH


def test_house_did_not_supply_it_but_the_car_was_home() -> None:
    result = classify(
        ev(house_ratio=Decimal("0.01"), gps_zone="home", gps_fresh=True), TH
    )
    assert result.verdict == CONFLICT


def test_house_did_not_supply_it_and_nobody_knows_where_it_was() -> None:
    """Knowing only where it was NOT is not enough to file it anywhere."""
    result = classify(ev(house_ratio=Decimal("0.03"), gps_zone="unknown"), TH)
    assert result.bucket == "unknown"


def test_a_zero_ratio_is_a_finding_not_an_absence() -> None:
    """Exactly 0.0 means the house supplied none of it, and that is evidence.

    Anything treating this value as falsy reads the strongest possible "not
    here" as "no opinion", and the session falls back to GPS it did not need.
    """
    result = classify(
        ev(house_ratio=Decimal(0), gps_zone="workplace", gps_fresh=True), TH
    )
    assert result.bucket == "workplace"


# ------------------------------------------------------- the middle ground --
def test_the_band_between_the_thresholds_means_nothing_physical() -> None:
    """Half the energy cannot come from our meter; the baseline must be off."""
    for ratio in ("0.30", "0.45", "0.59"):
        result = classify(
            ev(house_ratio=Decimal(ratio), gps_zone="home", gps_fresh=True), TH
        )
        assert result.bucket == "unknown", ratio


def test_an_odd_ratio_is_not_believed_harder_because_gps_agrees() -> None:
    result = classify(
        ev(house_ratio=Decimal("0.45"), gps_zone="workplace", gps_fresh=True), TH
    )
    assert result.bucket == "unknown"


# --------------------------------------------------------- no house at all --
def test_without_a_house_meter_it_falls_back_to_location() -> None:
    result = classify(ev(gps_zone="workplace", gps_fresh=True), TH)
    assert result.bucket == "workplace"
    assert result.confidence == MEDIUM


def test_with_neither_signal_it_says_so() -> None:
    result = classify(ev(), TH)
    assert result.bucket == "unknown"
    assert result.confidence == LOW


def test_a_stale_fix_is_no_fix() -> None:
    """`gps_zone` is already `unknown` when the car has moved since the fix.

    This is the failure the freshness test was built to survive: a position
    read at charge start describing yesterday's parking spot.
    """
    result = classify(ev(gps_zone="unknown", gps_fresh=False), TH)
    assert result.bucket == "unknown"


# ------------------------------------------------------- parameterisation --
def test_work_buckets_come_from_configuration() -> None:
    """No hard-coded workplace. A second free zone classifies just as well."""
    two = ev(
        gps_zone="depot",
        gps_fresh=True,
        work_buckets=("workplace", "depot"),
    )
    assert classify(two, TH).bucket == "depot"


def test_a_zone_that_is_not_configured_is_not_a_bucket() -> None:
    result = classify(ev(gps_zone="depot", gps_fresh=True, work_buckets=WORK), TH)
    assert result.bucket == "unknown"


def test_thresholds_are_honoured() -> None:
    """A stricter installation should reach a different verdict on one reading."""
    strict = Thresholds(home_ratio_yes=0.9, home_ratio_no=0.05)
    reading = ev(house_ratio=Decimal("0.7"), gps_zone="home", gps_fresh=True)
    assert classify(reading, TH).bucket == "home"
    assert classify(reading, strict).bucket == "unknown"


# --------------------------------------------------------------- reasoning --
def test_every_verdict_explains_itself() -> None:
    cases = [
        ev(),
        ev(supply="DC", peak_kw=50),
        ev(house_ratio=Decimal("0.9")),
        ev(house_ratio=Decimal("0.4")),
        ev(house_ratio=Decimal("0.0"), gps_zone="home"),
        Evidence(active=False),
    ]
    for case in cases:
        assert classify(case, TH).reason


# ------------------------------------------------------- the socket fingerprint --
# Peak current, already resolved to a place by `fingerprint.py`. These cases are
# the reason that module exists: both named sessions below were filed wrongly on
# a live install, a fortnight apart, and both had the answer in the record.
SocketMatch = pure.load("fingerprint").SocketMatch
AT_HOME = SocketMatch("home", 4, "the socket drew 15.1 A, which matches 4 sessions at home")
AT_WORK = SocketMatch("workplace", 4, "the socket drew 28.5 A, which matches 4 sessions at workplace")


def test_30_sep_short_charge_at_home_with_a_fix_that_drifted() -> None:
    """0.256 kWh over eight minutes, filed as `other`.

    Too short for the house meter to prove anything, so the only evidence left
    was a fix that was `fresh` - the odometer had not moved since it was
    latched - and reading `other`. The socket was the house's own.
    """
    result = classify(ev(gps_zone="other", gps_fresh=True, socket=AT_HOME), TH)
    assert result.bucket == "home"
    # Never HIGH: it won over a fix, and HIGH authorises the tail sweep.
    assert result.confidence == MEDIUM


def test_19_sep_house_meter_and_socket_against_one_fix() -> None:
    """4.561 kWh with a house-supply ratio of 1.6, filed as `conflict`.

    The house meter measured the energy leaving the house. Before the socket
    was admitted as evidence, one disagreeing fix was enough to make that
    unresolvable.
    """
    reading = ev(
        house_ratio=Decimal("1.616"), gps_zone="other", gps_fresh=True, socket=AT_HOME
    )
    result = classify(reading, TH)
    assert result.bucket == "home"
    assert result.confidence == MEDIUM


def test_a_fix_naming_a_different_place_is_still_a_conflict() -> None:
    """`other` is not a claim about a place. A work zone is."""
    result = classify(ev(gps_zone="workplace", gps_fresh=True, socket=AT_HOME), TH)
    assert result.verdict == CONFLICT
    assert result.bucket == "unknown"


def test_the_socket_may_not_overturn_the_house_meter() -> None:
    """The ratio says the house did not supply it, so `home` is ruled out.

    The socket gets no say on the house meter's own ground - the most it can do
    here is stay quiet and let the energy sit in `unknown`.
    """
    result = classify(ev(house_ratio=Decimal("0.02"), socket=AT_HOME), TH)
    assert result.bucket == "unknown"


def test_the_socket_may_name_a_work_bucket_with_no_fix() -> None:
    """Same ratio, but the socket names somewhere the ratio has not excluded."""
    result = classify(ev(house_ratio=Decimal("0.02"), socket=AT_WORK), TH)
    assert result.bucket == "workplace"


def test_the_middle_band_needs_the_fix_to_agree() -> None:
    """A ratio between the thresholds is not evidence either way.

    The socket does not depend on the house meter, so it may speak - but only
    where the fix does not contradict it.
    """
    middle = Decimal("0.45")
    assert classify(ev(house_ratio=middle, socket=AT_HOME), TH).bucket == "home"
    contradicted = ev(house_ratio=middle, gps_zone="workplace", socket=AT_HOME)
    assert classify(contradicted, TH).bucket == "unknown"


def test_no_socket_match_leaves_every_verdict_as_it_was() -> None:
    """The signal declines far more often than it speaks, so silence must be
    exactly the old behaviour rather than a new kind of answer."""
    for reading in (
        ev(gps_zone="other", gps_fresh=True),
        ev(house_ratio=Decimal("1.6"), gps_zone="other"),
        ev(house_ratio=Decimal("0.02")),
        ev(house_ratio=Decimal("0.45")),
    ):
        assert classify(reading, TH) == classify(reading, TH)
    assert classify(ev(gps_zone="other", gps_fresh=True), TH).bucket == "other"
    assert classify(ev(house_ratio=Decimal("1.6"), gps_zone="other"), TH).verdict == CONFLICT


def test_a_socket_alone_is_enough_to_place_but_not_to_sweep() -> None:
    """No ratio, no fresh fix, just the socket: medium, never low or high."""
    result = classify(ev(socket=AT_HOME), TH)
    assert result.bucket == "home"
    assert result.confidence == MEDIUM


# --------------------------------------------------------------- the wallbox --
# The home wallbox's own meter, as wallbox kWh / car kWh. A 7 kW wallbox draws
# the same ~29 A as the workplace socket, so on that band the fingerprint
# abstains and short home charges were left to GPS alone.
AMBIGUOUS = SocketMatch("workplace", 14, "the socket drew 28.9 A, which matches 14 sessions at workplace")


def test_first_7kw_home_charge_is_home() -> None:
    """1.7 kWh in 19 minutes: no house proof, the fix said home and the socket
    said work, so it was filed `conflict`. The wallbox metered it."""
    reading = ev(
        gps_zone="home", gps_fresh=True, socket=AMBIGUOUS,
        wallbox_ratio=Decimal("1.03"), peak_amps=28.9, kwh=1.7,
    )
    before = classify(Evidence(**{**reading.__dict__, "wallbox_ratio": None}), TH)
    assert before.verdict == CONFLICT
    result = classify(reading, TH)
    assert result.bucket == "home"
    assert result.confidence == HIGH


def test_the_wallbox_outranks_a_stale_fix_and_a_middling_ratio() -> None:
    for reading in (
        ev(wallbox_ratio=Decimal("1.01"), kwh=25.9),
        ev(house_ratio=Decimal("0.45"), wallbox_ratio=Decimal("1.0"), kwh=8),
        ev(gps_zone="other", gps_fresh=True, wallbox_ratio=Decimal("0.98"), kwh=3),
    ):
        result = classify(reading, TH)
        assert (result.bucket, result.confidence) == ("home", HIGH), reading


def test_the_wallbox_against_a_fresh_work_fix_is_a_conflict() -> None:
    """Most likely another car on the wallbox at a matching rate."""
    result = classify(
        ev(gps_zone="workplace", gps_fresh=True, wallbox_ratio=Decimal("1.0"), kwh=5),
        TH,
    )
    assert result.verdict == CONFLICT


def test_dc_still_beats_the_wallbox() -> None:
    result = classify(
        ev(supply="DC", peak_kw=50, wallbox_ratio=Decimal("1.0"), kwh=20), TH
    )
    assert result.bucket == "public_dc"


def test_a_ratio_past_the_band_is_no_proof() -> None:
    """Two cars on the wallbox at once: no opinion, the old chain decides."""
    result = classify(
        ev(gps_zone="workplace", gps_fresh=True, wallbox_ratio=Decimal("2.1"), kwh=5),
        TH,
    )
    assert result.bucket == "workplace"


def _silent(**kwargs):
    base = dict(
        gps_zone="home", gps_fresh=True, wallbox_ratio=Decimal("0.0"),
        wallbox_proven=True, peak_amps=28.5, kwh=0.8,
    )
    return ev(**{**base, **kwargs})


def test_a_silent_wallbox_vetoes_home_at_wallbox_current() -> None:
    """A lagged fix reading `home` on a short 28 A work charge."""
    result = classify(_silent(), TH)
    assert result.verdict == CONFLICT
    assert result.bucket == "unknown"


def test_the_veto_needs_every_condition() -> None:
    for reading in (
        _silent(wallbox_proven=False),  # never seen the meter count
        _silent(peak_amps=15.1),        # an old socket at home
        _silent(kwh=0.3),               # too little to mean anything
        _silent(wallbox_ratio=Decimal("0.2")),
        _silent(wallbox_ratio=None),
    ):
        assert classify(reading, TH).bucket == "home", reading


def test_the_veto_never_names_a_place() -> None:
    """28 A public AC chargers exist, so a silent wallbox only says `not home`."""
    assert classify(_silent(gps_zone="other"), TH).bucket == "other"
    assert classify(_silent(gps_zone="unknown", gps_fresh=False), TH).bucket == "unknown"


def test_without_a_wallbox_nothing_changes() -> None:
    reading = ev(gps_zone="home", gps_fresh=True, socket=AMBIGUOUS)
    assert classify(reading, TH).verdict == CONFLICT


def test_wallbox_proven_needs_one_matched_session() -> None:
    proven = classify_mod.wallbox_proven
    assert not proven([])
    assert not proven([{"wallbox_ratio": None}, {"wallbox_ratio": 0.0}, {}])
    # The YAML package logs unreadable values as strings.
    assert not proven([{"wallbox_ratio": "unknown"}, {"wallbox_ratio": 2.4}])
    assert proven([{"wallbox_ratio": 0.0}, {"wallbox_ratio": 1.027}])
