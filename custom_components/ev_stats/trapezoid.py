"""Trapezoidal Riemann integration — the arithmetic, and nothing else.

Deliberately free of any Home Assistant import so it can be tested on its own.
The integration's entire energy figure rests on these few lines, and it should
be possible to check them without standing up a test harness.

`integrator.py` holds the wiring that drives this from a live entity.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum


class Trigger(Enum):
    """What produced the last integration step.

    Needed because the next real state change has to know whether its left edge
    is a genuine reading or a synthetic one the timer invented.
    """

    STATE = "state"
    ELAPSED = "elapsed"


class TrapezoidIntegrator:
    """The arithmetic. Seconds in, unit-hours out."""

    def __init__(self, initial: Decimal | None = None) -> None:
        self.total: Decimal = Decimal(0) if initial is None else Decimal(initial)
        self._last_value: Decimal | None = None

    @property
    def last_value(self) -> Decimal | None:
        return self._last_value

    def seed(self, value: Decimal | None) -> None:
        """Set the left edge without integrating anything."""
        self._last_value = value

    def add_two_states(self, seconds: Decimal, right: Decimal) -> Decimal:
        """Trapezoid between the previous reading and this one."""
        left = self._last_value
        self._last_value = right
        if left is None or seconds <= 0:
            return Decimal(0)
        area = seconds / Decimal(3600) * (left + right) / Decimal(2)
        self.total += area
        return area

    def add_one_state(self, seconds: Decimal) -> Decimal:
        """Flat line across `seconds` at the last known reading.

        This is the whole max-sub-interval trick, and it lives here rather than
        in a subclass because trapezoidal and left-hand and right-hand all do
        the identical thing when there is only one reading to work with.
        """
        value = self._last_value
        if value is None or seconds <= 0:
            return Decimal(0)
        area = seconds / Decimal(3600) * value
        self.total += area
        return area
