"""What the house draws when the car is not charging.

This is the reference the house-supply proof measures against. During a
session, the house consumes the car's energy *plus* whatever it was going to
consume anyway; subtracting a baseline leaves the car's share, and comparing
that to what the car says it took answers a question GPS cannot: was it our
meter that supplied this?

Only sampled while the car is drawing nothing. A baseline that included
charging hours would rise towards the car's own draw and the proof would
quietly stop discriminating - the very sessions it is meant to judge would be
the ones inflating the number it judges them against.

Backfilled from the recorder on start. Without that, every restart blinds the
proof for twelve hours and the classifier silently falls back to GPS alone -
which is exactly the arrangement the proof exists to improve on, and it would
do it without saying so.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import partial

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    BASELINE_MAX_AGE_S,
    BASELINE_MAX_SAMPLES,
    BASELINE_MIN_COVERAGE,
    CHARGE_ON_KW,
)
from .helpers import numeric_state
from .rolling import RollingWindow

_LOGGER = logging.getLogger(__name__)


class HouseBaseline:
    """Rolling median of the house load, gated on the car being idle."""

    def __init__(
        self,
        hass: HomeAssistant,
        house_entity: str,
        power_entity: str,
    ) -> None:
        self.hass = hass
        self._house = house_entity
        self._power = power_entity
        self._window = RollingWindow(BASELINE_MAX_AGE_S, BASELINE_MAX_SAMPLES)
        self._unsub = None

    # ------------------------------------------------------------ lifecycle --
    async def async_start(self) -> None:
        await self._async_backfill()
        self._unsub = async_track_state_change_event(
            self.hass, [self._house], self._handle_sample
        )

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    # --------------------------------------------------------------- state --
    @property
    def median(self) -> float | None:
        return self._window.median(dt_util.utcnow().timestamp())

    @property
    def coverage(self) -> float:
        return self._window.coverage(dt_util.utcnow().timestamp())

    @property
    def samples(self) -> int:
        return len(self._window)

    @property
    def usable(self) -> bool:
        """Whether the classifier is allowed to lean on this."""
        return self.median is not None and self.coverage >= BASELINE_MIN_COVERAGE

    # ----------------------------------------------------------- internals --
    @callback
    def _handle_sample(self, _event: Event[EventStateChangedData]) -> None:
        load = numeric_state(self.hass, self._house)
        if load is None:
            return
        power = numeric_state(self.hass, self._power)
        # An unreadable car is not an idle car. The entity drops out several
        # times a day, and taking that for "not charging" would feed the middle
        # of a session straight into the baseline.
        if power is None or power > CHARGE_ON_KW:
            return
        self._window.add(dt_util.utcnow().timestamp(), load)

    async def _async_backfill(self) -> None:
        """Replay the last day of history through the same gate."""
        if "recorder" not in self.hass.config.components:
            _LOGGER.debug("No recorder; the baseline will fill from live samples only")
            return

        try:
            from homeassistant.components.recorder import get_instance, history
        except ImportError:  # pragma: no cover - recorder is a core component
            return

        end = dt_util.utcnow()
        start = end - timedelta(seconds=BASELINE_MAX_AGE_S)

        try:
            states = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.get_significant_states,
                    self.hass,
                    start,
                    end,
                    [self._house, self._power],
                    include_start_time_state=True,
                    significant_changes_only=False,
                    no_attributes=True,
                )
            )
        except Exception:  # noqa: BLE001 - a backfill must never block setup
            _LOGGER.exception("Could not read history for the house-load baseline")
            return

        power_series = _numeric_series(states.get(self._power, []))
        house_series = _numeric_series(states.get(self._house, []))
        if not house_series:
            return

        # Walk both in time order, carrying the most recent power reading
        # forward. A power meter that reports every thirty seconds and a house
        # meter that reports every five do not share timestamps, so each house
        # sample is judged against whatever the car was doing at that moment,
        # not against the nearest reading in either direction.
        idx = 0
        power_at = None
        kept = 0
        for ts, load in house_series:
            while idx < len(power_series) and power_series[idx][0] <= ts:
                power_at = power_series[idx][1]
                idx += 1
            if power_at is None or power_at > CHARGE_ON_KW:
                continue
            self._window.add(ts, load)
            kept += 1

        _LOGGER.debug(
            "Baseline backfilled: %s of %s house samples kept, coverage %.0f%%",
            kept,
            len(house_series),
            self.coverage * 100,
        )


def _numeric_series(states) -> list[tuple[float, float]]:
    """(timestamp, value) pairs, skipping everything unusable."""
    series: list[tuple[float, float]] = []
    for state in states:
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            continue
        series.append((state.last_updated.timestamp(), value))
    series.sort(key=lambda item: item[0])
    return series
