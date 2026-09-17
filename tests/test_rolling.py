"""The rolling window behind the house-load baseline.

    python -m pytest tests/test_rolling.py
"""

from __future__ import annotations

import pure

RollingWindow = pure.load("rolling").RollingWindow

DAY = 86400.0


def window(max_age: float = DAY, max_samples: int = 2000) -> RollingWindow:
    return RollingWindow(max_age, max_samples)


def test_empty_window_has_no_median() -> None:
    """None, not zero. A baseline of 0 kW makes every session look like home."""
    assert window().median() is None


def test_median_of_a_few_samples() -> None:
    w = window()
    for i, value in enumerate([0.3, 0.4, 5.0]):
        w.add(i, value)
    assert w.median() == 0.4


def test_the_median_ignores_one_enormous_load() -> None:
    """Why the median and not the mean.

    An oven for an hour lands in the same day as hours of standby. A mean would
    let one dinner raise the baseline enough to swallow a session's evidence.
    """
    w = window()
    for i in range(23):
        w.add(i * 3600, 0.35)
    w.add(23 * 3600, 9.0)
    assert w.median() == 0.35
    assert sum([0.35] * 23 + [9.0]) / 24 > 0.6


def test_samples_older_than_the_window_are_dropped() -> None:
    w = window(max_age=100)
    w.add(0, 1.0)
    w.add(50, 2.0)
    w.add(160, 3.0)
    assert len(w) == 1
    assert w.median() == 3.0


def test_coverage_needs_a_full_window() -> None:
    w = window(max_age=100)
    w.add(0, 1.0)
    w.add(50, 1.0)
    assert w.coverage(50) == 0.5
    w.add(100, 1.0)
    assert w.coverage(100) == 1.0


def test_coverage_of_one_sample_is_zero() -> None:
    """One reading spans no time, however recent it is."""
    w = window(max_age=100)
    w.add(0, 1.0)
    assert w.coverage(0) == 0.0


def test_coverage_decays_when_the_source_goes_quiet() -> None:
    """A window of stale readings must stop claiming to be full.

    A dead feed is the case that matters: the classifier leaning on a baseline
    that describes yesterday is worse than it declining to answer. Nothing new
    arrives at one end while the other keeps ageing out, so the span shrinks
    from both sides.
    """
    w = window(max_age=100)
    for t in range(0, 101, 10):
        w.add(t, 1.0)
    assert w.coverage(100) == 1.0

    # Silence. Half the samples have now aged out and none have replaced them.
    assert w.coverage(150) == 0.5
    assert w.coverage(190) == 0.1


def test_one_lone_survivor_covers_nothing() -> None:
    """Two samples an entire window apart leave one behind, and one spans no time."""
    w = window(max_age=100)
    w.add(0, 1.0)
    w.add(100, 1.0)
    assert w.coverage(150) == 0.0


def test_out_of_order_samples_are_dropped() -> None:
    """A recorder backfill racing the live feed must not corrupt the ageing."""
    w = window(max_age=100)
    w.add(100, 1.0)
    w.add(50, 99.0)
    assert len(w) == 1
    assert w.median() == 1.0


def test_the_sample_cap_holds() -> None:
    w = window(max_age=DAY, max_samples=10)
    for i in range(100):
        w.add(i, float(i))
    assert len(w) == 10
    assert w.median() == 94.5


def test_clearing_returns_it_to_having_no_opinion() -> None:
    w = window()
    w.add(0, 1.0)
    w.add(1, 2.0)
    w.clear()
    assert w.median() is None
    assert w.coverage() == 0.0
