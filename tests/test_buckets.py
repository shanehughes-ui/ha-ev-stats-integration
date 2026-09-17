"""What moves with energy when a charge is reattributed.

The failure these guard against is not loud. In the YAML this replaced, a
correction moved the energy and left the cost and the solar behind, so the
buckets stayed balanced - the one invariant being checked was the one that
still held - while free share, solar share and dollars per 100 km all drifted.

    python -m pytest tests/test_buckets.py
"""

from __future__ import annotations

from decimal import Decimal

import pure

buckets = pure.load("buckets")

Balance = buckets.Balance
share = buckets.share
carried = buckets.carried

D = Decimal


def full() -> Balance:
    """Ten kWh that cost two dollars, six of it from the panels."""
    return Balance(kwh=D(10), cost=D("2.00"), solar_kwh=D(6))


# -------------------------------------------------------------- arithmetic --
def test_an_empty_balance_is_empty() -> None:
    assert Balance().is_empty()
    assert not Balance(kwh=D("0.001")).is_empty()


def test_adding_adds_all_three() -> None:
    total = full() + Balance(kwh=D(5), cost=D(1), solar_kwh=D(1))
    assert (total.kwh, total.cost, total.solar_kwh) == (D(15), D("3.00"), D(7))


def test_subtracting_subtracts_all_three() -> None:
    left = full() - Balance(kwh=D(4), cost=D("0.80"), solar_kwh=D(2))
    assert (left.kwh, left.cost, left.solar_kwh) == (D(6), D("1.20"), D(4))


# ---------------------------------------------------------------- splitting --
def test_half_the_energy_carries_half_the_cost() -> None:
    part = share(full(), D(5))
    assert part.kwh == D(5)
    assert part.cost == D("1.00")
    assert part.solar_kwh == D(3)


def test_all_of_it_carries_all_of_it_exactly() -> None:
    """Not a fraction of 1.0 - that leaves a rounding crumb behind.

    A bucket emptied of energy but holding four cents of cost is a bucket that
    charged nothing and cost something, and nothing ever clears it.
    """
    part = share(full(), D(10))
    assert part == full()
    assert (full() - part).is_empty()


def test_more_than_is_there_carries_everything_not_more() -> None:
    part = share(full(), D(25))
    assert part.cost == D("2.00")


def test_an_empty_bucket_has_no_cost_to_apportion() -> None:
    part = share(Balance(), D(5))
    assert part.kwh == D(5)
    assert part.cost == 0


def test_a_bucket_with_energy_but_no_cost_carries_none() -> None:
    """Charging at work is free, and half of free is still free."""
    part = share(Balance(kwh=D(20)), D(10))
    assert part.kwh == D(10)
    assert part.cost == 0


def test_splitting_a_third_three_ways_loses_nothing() -> None:
    """Decimal, not float. A third is where a float ledger starts to drift."""
    source = Balance(kwh=D(3), cost=D(3), solar_kwh=D(3))
    parts = [share(source, D(1)) for _ in range(3)]
    assert sum((p.cost for p in parts), D(0)) == D(3)


# ----------------------------------------------------------------- carried --
def test_explicit_figures_win_over_pro_rata() -> None:
    """A correction knows what one session cost; the bucket average is not it.

    A bucket holding weeks of charges at different prices would otherwise hand
    a session the mean, which is right for no session in particular.
    """
    moved = carried(full(), D(5), cost=D("0.10"), solar_kwh=D("4.5"))
    assert moved.cost == D("0.10")
    assert moved.solar_kwh == D("4.5")
    assert moved.kwh == D(5)


def test_an_explicit_zero_is_not_the_same_as_leaving_it_out() -> None:
    """Zero means this move carries no cost - the right answer for a free charge."""
    assert carried(full(), D(5), cost=D(0)).cost == D(0)
    assert carried(full(), D(5)).cost == D("1.00")


def test_one_side_explicit_and_the_other_pro_rata() -> None:
    moved = carried(full(), D(5), cost=D("0.10"))
    assert moved.cost == D("0.10")
    assert moved.solar_kwh == D(3)


# ------------------------------------------------------------- persistence --
def test_a_balance_survives_the_round_trip() -> None:
    """Stored as strings: JSON has no decimal type, and floats drift."""
    original = Balance(kwh=D("12.345"), cost=D("2.4680"), solar_kwh=D("7.007"))
    assert Balance.from_dict(original.as_dict()) == original


def test_a_version_one_record_reads_as_energy_with_no_cost() -> None:
    """The old format stored a bare kWh string. There was no cost to lose."""
    old = Balance.from_dict("44.125")
    assert old.kwh == D("44.125")
    assert old.cost == 0


def test_rubbish_reads_as_nothing_rather_than_raising() -> None:
    assert Balance.from_dict(None).is_empty()
    assert Balance.from_dict([1, 2]).is_empty()
