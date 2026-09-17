"""Measuring the usable pack from a wide charge.

    python -m pytest tests/test_packsize.py
"""

from __future__ import annotations

from decimal import Decimal

import pure

packsize = pure.load("packsize")

implied_capacity = packsize.implied_capacity
rolling_mean = packsize.rolling_mean
trend_per_year = packsize.trend_per_year

EFF = Decimal("0.88")
D = Decimal


# ----------------------------------------------------------- one measurement --
def test_a_real_measurement() -> None:
    """From a recorded session: 20.05 kWh across 40% of the pack."""
    assert implied_capacity(D("20.05"), D(40), EFF) == D("44.11")


def test_a_narrow_charge_measures_nothing() -> None:
    """Under 30% the division amplifies every error past usefulness.

    An 8% charge divides by 0.08, so a 1% error in either reading becomes a 12%
    error in the answer - and it lands in the list looking exactly as
    authoritative as a good measurement.
    """
    assert implied_capacity(D(4), D(8), EFF) is None


def test_the_boundary_is_included() -> None:
    assert implied_capacity(D(15), D(30), EFF) is not None
    assert implied_capacity(D(15), D("29.9"), EFF) is None


def test_nothing_from_nothing() -> None:
    assert implied_capacity(D(0), D(50), EFF) is None
    assert implied_capacity(D(20), D(50), D(0)) is None


def test_efficiency_scales_the_answer_but_cancels_out_of_a_trend() -> None:
    """Why an assumed 0.88 is acceptable for the thing this is used for.

    It shifts every reading by the same factor, and a constant factor cannot
    create a slope. The absolute figure is only as good as the assumption; the
    trend does not depend on it at all.
    """
    at_88 = implied_capacity(D(20), D(40), D("0.88"))
    at_95 = implied_capacity(D(20), D(40), D("0.95"))
    later_88 = implied_capacity(D(19), D(40), D("0.88"))
    later_95 = implied_capacity(D(19), D(40), D("0.95"))
    assert at_88 != at_95
    # Not exactly equal, and that is the quantisation to hundredths rather than
    # the maths: 45.125 lands on 45.12. The claim being made is that the
    # assumption does not bend the trend, and half a hundredth of a kWh on a
    # 5% decline is not bending anything.
    assert abs((later_88 / at_88) - (later_95 / at_95)) < D("0.001")


# ------------------------------------------------------------ rolling mean --
def test_the_rolling_mean_of_real_readings() -> None:
    assert rolling_mean([44.10, 44.58, 43.17, 44.57]) == 44.11


def test_nothing_measured_yet_is_not_zero() -> None:
    """A pack size of 0.0 kWh would make every range estimate nonsense."""
    assert rolling_mean([]) is None


def test_one_reading_cannot_move_a_converged_figure_far() -> None:
    """Measurements scatter ~1.5%; the signal being looked for is ~2% a year.

    So the published number follows the mean of the recent few rather than the
    latest single one, which the YAML wrote straight over the top.
    """
    settled = [44.1] * 8
    assert rolling_mean(settled) == 44.1
    assert rolling_mean(settled + [40.0]) == 43.59


def test_only_the_recent_window_counts() -> None:
    assert rolling_mean([10.0] * 20 + [44.0] * 8) == 44.0


# ------------------------------------------------------------------ trend --
def test_a_trend_needs_enough_points_over_enough_time() -> None:
    """Four readings in one month extrapolate a season into a decade."""
    one_month = [(float(d), 44.0) for d in (0, 10, 20, 30)]
    assert trend_per_year(one_month) is None


def test_a_flat_year_has_no_trend() -> None:
    flat = [(float(d), 44.0) for d in (0, 100, 200, 300, 365)]
    assert trend_per_year(flat) == 0.0


def test_a_year_of_decline_reads_as_decline() -> None:
    """One kWh lost over a year reads as one kWh per year."""
    losing = [(float(d), 44.0 - d / 365) for d in (0, 100, 200, 300, 365)]
    assert trend_per_year(losing) == -1.0


def test_two_readings_say_nothing() -> None:
    assert trend_per_year([(0.0, 44.0), (365.0, 43.0)]) is None
