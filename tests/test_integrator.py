"""Arithmetic tests for the Riemann integrator.

These deliberately need no Home Assistant. The integration's whole energy figure
rests on this handful of lines, and it should be possible to check them without
standing up a test harness.

    python -m pytest tests/test_integrator.py
"""

from __future__ import annotations

from decimal import Decimal

import pure

# Loaded through the stand-in package in pure.py rather than imported: pulling
# in ev_stats proper would drag in Home Assistant, and the point of
# trapezoid.py is that it does not need any. If this ever starts requiring HA,
# the separation has been lost.
TrapezoidIntegrator = pure.load("trapezoid").TrapezoidIntegrator

HOUR = Decimal(3600)


def test_starts_at_zero() -> None:
    assert TrapezoidIntegrator().total == Decimal(0)


def test_first_sample_integrates_nothing() -> None:
    """With one reading there is no interval yet, so there is no area."""
    i = TrapezoidIntegrator()
    i.seed(Decimal("6.8"))
    assert i.total == Decimal(0)


def test_square_wave_is_power_times_time() -> None:
    """A constant 6.8 kW for one hour is 6.8 kWh."""
    i = TrapezoidIntegrator()
    i.seed(Decimal("6.8"))
    i.add_two_states(HOUR, Decimal("6.8"))
    assert i.total == Decimal("6.8")


def test_ramp_is_the_mean_of_the_ends() -> None:
    """0 -> 10 kW over an hour is 5 kWh, which is what trapezoidal means."""
    i = TrapezoidIntegrator()
    i.seed(Decimal(0))
    i.add_two_states(HOUR, Decimal(10))
    assert i.total == Decimal(5)


def test_ramp_down_matches_ramp_up() -> None:
    i = TrapezoidIntegrator()
    i.seed(Decimal(10))
    i.add_two_states(HOUR, Decimal(0))
    assert i.total == Decimal(5)


def test_flat_line_is_the_max_sub_interval_trick() -> None:
    """The case a pure state-change integrator misses entirely.

    A steady charge emits no state changes for hours. Without this the whole
    session would integrate to nearly nothing.
    """
    i = TrapezoidIntegrator()
    i.seed(Decimal("6.8"))
    i.add_one_state(HOUR)
    assert i.total == Decimal("6.8")


def test_one_state_and_two_state_agree_on_a_constant_signal() -> None:
    """Six timer ticks must equal one long trapezoid at the same power.

    Not to the last digit, and that is a property of the arithmetic rather than
    a defect: a sixth of an hour does not terminate in decimal, so six divisions
    drift from one. The drift is around 4e-27 kWh - twenty-four orders of
    magnitude below the milli-kWh anything here is reported to - so the test
    asserts what actually matters instead of demanding exactness it cannot have.
    """
    ticks = TrapezoidIntegrator()
    ticks.seed(Decimal("6.8"))
    for _ in range(6):
        ticks.add_one_state(HOUR / 6)

    once = TrapezoidIntegrator()
    once.seed(Decimal("6.8"))
    once.add_two_states(HOUR, Decimal("6.8"))

    assert once.total == Decimal("6.8")
    assert abs(ticks.total - once.total) < Decimal("1e-18")


def test_nothing_integrates_before_a_first_reading() -> None:
    """No left edge means no area - not an exception, and not a zero reading."""
    i = TrapezoidIntegrator()
    assert i.add_one_state(HOUR) == Decimal(0)
    assert i.add_two_states(HOUR, Decimal(5)) == Decimal(0)
    assert i.total == Decimal(0)


def test_zero_and_negative_elapsed_are_ignored() -> None:
    """Clock going backwards must not remove energy that was really used."""
    i = TrapezoidIntegrator()
    i.seed(Decimal(5))
    i.add_two_states(HOUR, Decimal(5))
    before = i.total
    i.add_two_states(Decimal(0), Decimal(5))
    i.add_two_states(Decimal(-60), Decimal(5))
    assert i.total == before


def test_a_realistic_session_lands_where_it_should() -> None:
    """Two and a half hours at about 6.8 kW is about 17 kWh.

    Modelled on a real session: a ramp up over a minute, a long steady middle
    carried by timer ticks, then a ramp down.
    """
    i = TrapezoidIntegrator()
    i.seed(Decimal(0))
    i.add_two_states(Decimal(60), Decimal("6.8"))       # ramp up
    for _ in range(148):                                # 148 minutes steady
        i.add_one_state(Decimal(60))
    i.add_two_states(Decimal(60), Decimal(0))           # ramp down

    assert Decimal("16.7") < i.total < Decimal("17.0")


def test_decimal_not_float() -> None:
    """A session is thousands of additions; float drift would break the ledger."""
    i = TrapezoidIntegrator()
    i.seed(Decimal("0.1"))
    for _ in range(1000):
        i.add_one_state(Decimal(36))  # 0.01 h each
    assert i.total == Decimal("1.0")
