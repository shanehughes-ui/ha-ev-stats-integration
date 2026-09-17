"""Trapezoidal Riemann integration with a maximum sub-interval.

The wiring only. The arithmetic lives in `trapezoid.py`, which imports no Home
Assistant at all so it can be tested against a square wave and a ramp on its
own.

The max-sub-interval is the part that matters and the part that is easy to leave
out. A car drawing a steady 6.8 kW emits no state *changes* for hours, so a pure
state-change integrator records almost nothing for a session that plainly
happened. The fix is one idea: when the timer fires rather than a state change
there is only one reading, so integrate a flat line across the elapsed time.

Two things are integrated here. Entity readings - the car's power, the house
load - and *computed* rates, which are what charging costs and what carbon it
incurs per hour. The second kind has no entity of its own to read, so the
general form takes a function and a list of entities to watch, and the entity
case is the special case of it.
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


def read_entity(hass: HomeAssistant, entity_id: str) -> Decimal | None:
    """An entity's state as a Decimal, or None if it cannot be read."""
    state = hass.states.get(entity_id)
    if state is None or state.state in _UNUSABLE:
        return None
    try:
        return Decimal(state.state)
    except (DecimalException, TypeError, ValueError):
        return None


class ValueIntegrator:
    """Integrates whatever `read` returns, in whatever units it returns it."""

    def __init__(
        self,
        hass: HomeAssistant,
        watch: list[str],
        read: Callable[[], Decimal | None],
        max_sub_interval: float,
        on_change: Callable[[Decimal], None],
    ) -> None:
        self.hass = hass
        self._watch = [e for e in watch if e]
        self._read = read
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

    @property
    def current(self) -> Decimal | None:
        """The rate right now, for a sensor that wants to publish it."""
        return self._read()

    def restore(self, total: Decimal) -> None:
        self._integrator.total = Decimal(total)

    # ----------------------------------------------------------- lifecycle --
    @callback
    def async_start(self) -> None:
        if self._watch:
            self._unsub = async_track_state_change_event(
                self.hass, self._watch, self._handle_state
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
        """A watched input changed, so the value may have."""
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
            # A trapezoid from the held value to this one, whether the left edge
            # was a real reading or one the timer invented - the timer has
            # already integrated the flat line up to _last_ts, so only the
            # remainder is owed either way.
            self._integrator.add_two_states(Decimal(str(now - self._last_ts)), value)
        else:
            self._integrator.seed(value)

        self._last_ts = now
        self._last_trigger = Trigger.STATE
        self._arm_timer()
        self._on_change(self._integrator.total)

    @callback
    def _handle_elapsed(self, _now) -> None:
        """No change for max_sub_interval. Integrate a flat line."""
        self._cancel_timer = None
        now = dt_util.utcnow().timestamp()

        if self._last_ts is not None and self._integrator.last_value is not None:
            self._integrator.add_one_state(Decimal(str(now - self._last_ts)))
            self._on_change(self._integrator.total)

        self._last_ts = now
        self._last_trigger = Trigger.ELAPSED
        self._arm_timer()


class SourceIntegrator(ValueIntegrator):
    """Integrates one Home Assistant entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        source_entity: str,
        max_sub_interval: float,
        on_change: Callable[[Decimal], None],
    ) -> None:
        self.source_entity = source_entity
        super().__init__(
            hass,
            [source_entity],
            lambda: read_entity(hass, source_entity),
            max_sub_interval,
            on_change,
        )
