"""Trapezoidal Riemann integration with a maximum sub-interval.

The wiring only. The arithmetic lives in `trapezoid.py`, which imports no Home
Assistant at all so it can be tested against a square wave and a ramp on its
own.

The max-sub-interval is the part that matters and the part that is easy to leave
out. A car drawing a steady 6.8 kW emits no state *changes* for hours, so a pure
state-change integrator records almost nothing for a session that plainly
happened. The fix is one idea: when the timer fires rather than a state change
there is only one reading, so integrate a flat line across the elapsed time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal, DecimalException

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .trapezoid import TrapezoidIntegrator, Trigger

_LOGGER = logging.getLogger(__name__)

# Everything is Decimal. A session is thousands of additions and float drift
# accumulates into the ledger, where the balance invariant would eventually
# fail by a few milli-kWh for no reason anyone could find.
_UNUSABLE = ("unknown", "unavailable", "", None)


class SourceIntegrator:
    """Integrates a Home Assistant entity, in whatever units it reports."""

    def __init__(
        self,
        hass: HomeAssistant,
        source_entity: str,
        max_sub_interval: float,
        on_change: Callable[[Decimal], None],
    ) -> None:
        self.hass = hass
        self.source_entity = source_entity
        self._max_sub_interval = max_sub_interval
        self._on_change = on_change
        self._integrator = TrapezoidIntegrator()
        self._last_ts: float | None = None
        self._last_trigger: Trigger | None = None
        self._cancel_timer: Callable[[], None] | None = None
        self._unsub: Callable[[], None] | None = None

    # ---------------------------------------------------------------- state --
    @property
    def total(self) -> Decimal:
        return self._integrator.total

    def restore(self, total: Decimal) -> None:
        self._integrator.total = Decimal(total)

    # ----------------------------------------------------------- lifecycle --
    @callback
    def async_start(self) -> None:
        self._unsub = async_track_state_change_event(
            self.hass, [self.source_entity], self._handle_state
        )
        if (value := self._read()) is not None:
            self._integrator.seed(value)
            self._last_ts = dt_util.utcnow().timestamp()
            self._last_trigger = Trigger.STATE
            self._arm_timer()

    @callback
    def async_stop(self) -> None:
        self._cancel_pending()
        if self._unsub:
            self._unsub()
            self._unsub = None

    # ------------------------------------------------------------ internals --
    def _read(self) -> Decimal | None:
        state = self.hass.states.get(self.source_entity)
        if state is None or state.state in _UNUSABLE:
            return None
        try:
            return Decimal(state.state)
        except (DecimalException, TypeError, ValueError):
            return None

    @callback
    def _cancel_pending(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _arm_timer(self) -> None:
        if self._max_sub_interval <= 0:
            return
        self._cancel_pending()
        self._cancel_timer = async_call_later(
            self.hass, self._max_sub_interval, self._handle_elapsed
        )

    @callback
    def _handle_state(self, event: Event[EventStateChangedData]) -> None:
        """A real reading arrived."""
        self._cancel_pending()
        now = dt_util.utcnow().timestamp()
        value = self._read()

        if value is None:
            # The source dropped out. Do NOT integrate across the gap - the car
            # entity here goes unavailable several times a day and inventing a
            # flat line across an outage is how a Riemann sum quietly invents
            # energy. Hold the last reading and wait.
            self._last_ts = now
            return

        if self._last_ts is not None and self._integrator.last_value is not None:
            elapsed = Decimal(str(now - self._last_ts))
            if self._last_trigger is Trigger.ELAPSED:
                # The left edge is synthetic: the timer already integrated a
                # flat line up to _last_ts, so only the remainder is owed, and
                # it is a trapezoid from that same held value to this one.
                self._integrator.add_two_states(elapsed, value)
            else:
                self._integrator.add_two_states(elapsed, value)
        else:
            self._integrator.seed(value)

        self._last_ts = now
        self._last_trigger = Trigger.STATE
        self._arm_timer()
        self._on_change(self._integrator.total)

    @callback
    def _handle_elapsed(self, _now) -> None:
        """No state change for max_sub_interval. Integrate a flat line."""
        self._cancel_timer = None
        now = dt_util.utcnow().timestamp()

        if self._last_ts is not None and self._integrator.last_value is not None:
            self._integrator.add_one_state(Decimal(str(now - self._last_ts)))
            self._on_change(self._integrator.total)

        self._last_ts = now
        self._last_trigger = Trigger.ELAPSED
        self._arm_timer()
