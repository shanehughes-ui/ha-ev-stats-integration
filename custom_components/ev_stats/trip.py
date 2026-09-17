"""Trips: engine on, engine off, and what happened in between.

Two pieces of timing carry most of the correctness here, and they pull in
opposite directions.

**The start is not debounced.** The snapshot has to be taken before the car
moves; a late one loses the first kilometres and there is no way to recover
them afterwards.

**The end waits two minutes.** The odometer is polled and lags the engine going
off, so closing immediately records a real drive as 0.0 km. In the readings
this was built from, 13 of 57 engine cycles would have been written that way.
The distance floor is the second half of the same guard: below half a kilometre
it is odometer lag rather than a journey.

Everything a trip reports about energy is an *estimate*, computed from the
car's own consumption figure over the distance. None of it is ever added to a
bucket. The balance check exists precisely to catch plausible-looking additions
like that one, and a trip estimate is the most plausible-looking of all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY,
    CONF_DEVICE_TRACKER,
    CONF_ENGINE_STATE,
    CONF_ODOMETER,
    CONF_RECORD_POSITIONS,
    CONF_TRIP_CONSUMPTION,
    DOMAIN,
    EVENT_TRIP_RECORDED,
    SECTION_CAR,
    SECTION_LOCATION,
    TRIP_END_DEBOUNCE_S,
    TRIP_MAX_KM,
    TRIP_MIN_KM,
)
from .helpers import numeric_state, string_state

if TYPE_CHECKING:
    from .runtime import EvStatsRuntime

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1

# Vehicle integrations disagree about what an engine state looks like, so both
# are matched by value rather than one being assumed. A state in neither set is
# treated as no information: it neither starts nor ends a trip, because guessing
# wrong in either direction writes a bad row.
RUNNING_STATES = frozenset({"on", "engine-running", "running", "started", "true"})
STOPPED_STATES = frozenset(
    {"off", "engine-off", "stopped", "not-running", "idle", "false"}
)


@dataclass
class TripState:
    """The opening snapshot, held until the engine goes off."""

    active: bool = False
    started: str | None = None
    start_odometer: float | None = None
    start_soc: float | None = None
    start_zone: str | None = None
    start_position: str | None = None
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "started": self.started,
            "start_odometer": self.start_odometer,
            "start_soc": self.start_soc,
            "start_zone": self.start_zone,
            "start_position": self.start_position,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TripState:
        return cls(
            active=bool(data.get("active", False)),
            started=data.get("started"),
            start_odometer=data.get("start_odometer"),
            start_soc=data.get("start_soc"),
            start_zone=data.get("start_zone"),
            start_position=data.get("start_position"),
            flags=list(data.get("flags", [])),
        )


class TripManager:
    """Watches the engine and records each completed drive."""

    def __init__(self, hass: HomeAssistant, entry_id: str, runtime: EvStatsRuntime) -> None:
        self.hass = hass
        self.runtime = runtime
        self.config = runtime.config
        self.state = TripState()
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.trip")
        self._engine: str | None = self.config.opt(SECTION_CAR, CONF_ENGINE_STATE)
        self._odometer = self.config.required(CONF_ODOMETER)
        self._end_timer = None
        self._unsub = None
        self._listeners: list = []

    @property
    def configured(self) -> bool:
        """Without an engine signal there is nothing to key a trip off.

        Speed is not a substitute: it reads zero at every set of lights, so a
        commute becomes fourteen trips.
        """
        return self._engine is not None

    # ------------------------------------------------------------ lifecycle --
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self.state = TripState.from_dict(data)

    @callback
    def async_start(self) -> None:
        if not self._engine:
            return
        self._unsub = async_track_state_change_event(
            self.hass, [self._engine], self._handle_engine
        )

    @callback
    def async_stop(self) -> None:
        self._cancel_end()
        if self._unsub:
            self._unsub()
            self._unsub = None

    def add_listener(self, cb) -> Any:
        self._listeners.append(cb)

        def _remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return _remove

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --------------------------------------------------------------- state --
    @property
    def distance_so_far(self) -> float | None:
        if not self.state.active or self.state.start_odometer is None:
            return None
        odo = numeric_state(self.hass, self._odometer)
        return None if odo is None else round(odo - self.state.start_odometer, 1)

    # -------------------------------------------------------------- trigger --
    @callback
    def _handle_engine(self, _event: Event[EventStateChangedData]) -> None:
        value = string_state(self.hass, self._engine)
        if value is None:
            return
        value = value.strip().lower()

        if value in RUNNING_STATES:
            self._cancel_end()
            if not self.state.active:
                # Immediately, with no debounce: a snapshot taken a minute late
                # has already lost the first kilometre.
                self.hass.async_create_task(self.async_open())
        elif value in STOPPED_STATES:
            if self.state.active and self._end_timer is None:
                self._end_timer = async_call_later(
                    self.hass, TRIP_END_DEBOUNCE_S, self._fire_close
                )
        else:
            _LOGGER.debug("Unrecognised engine state %r; ignoring it", value)

    @callback
    def _cancel_end(self) -> None:
        if self._end_timer is not None:
            self._end_timer()
            self._end_timer = None

    @callback
    def _fire_close(self, _now) -> None:
        self._end_timer = None
        self.hass.async_create_task(self.async_close())

    # ----------------------------------------------------------- open/close --
    async def async_open(self) -> None:
        # Checked again here, not only at the trigger: opening is scheduled as
        # a task, so two engine-on events arriving together can both be queued
        # before either has set the flag, and the second would overwrite the
        # first snapshot with a later odometer.
        if self.state.active:
            return
        self.state = TripState(
            active=True,
            started=dt_util.utcnow().isoformat(timespec="seconds"),
            start_odometer=numeric_state(self.hass, self._odometer),
            start_soc=numeric_state(self.hass, self.config.required(CONF_BATTERY)),
            start_zone=self._zone_now(),
            start_position=self._position_now(),
        )
        await self._store.async_save(self.state.as_dict())
        self._notify()

    async def async_close(self) -> None:
        """Close the trip, and write it only if it was really a journey."""
        if not self.state.active:
            return
        snapshot = self.state
        self.state = TripState()
        await self._store.async_save(self.state.as_dict())

        odo = numeric_state(self.hass, self._odometer)
        started = dt_util.parse_datetime(snapshot.started or "")
        if odo is None or snapshot.start_odometer is None or started is None:
            _LOGGER.debug("Trip closed without the readings to measure it; discarded")
            self._notify()
            return

        km = round(odo - snapshot.start_odometer, 1)
        minutes = round((dt_util.utcnow() - started).total_seconds() / 60, 1)

        # The floor removes odometer-lag cycles; the ceiling removes a glitched
        # reading, which otherwise lands in the log as a 3,000 km commute.
        if not (TRIP_MIN_KM <= km < TRIP_MAX_KM) or minutes <= 0:
            _LOGGER.debug("Discarding a %.1f km, %.1f minute trip", km, minutes)
            self._notify()
            return

        consumption = numeric_state(
            self.hass, self.config.opt(SECTION_CAR, CONF_TRIP_CONSUMPTION)
        )
        soc_end = numeric_state(self.hass, self.config.required(CONF_BATTERY))

        record = {
            "id": dt_util.utcnow().strftime("%Y%m%d%H%M%S"),
            "start": snapshot.started,
            "end": dt_util.utcnow().isoformat(timespec="seconds"),
            "km": km,
            "minutes": minutes,
            "avg_kmh": round(km / (minutes / 60), 1),
            "soc_start": snapshot.start_soc,
            "soc_end": soc_end,
            # An ESTIMATE, from the car's own consumption figure, never
            # metered. It is deliberately not added to any bucket: the balance
            # check exists to catch exactly this kind of plausible addition.
            "kwh_est": (
                round(km * consumption / 100, 2) if consumption is not None else None
            ),
            "consumption": consumption,
            # Where it ended, under the same freshness gate as everything else.
            # The fix is usually stale mid-drive and fresh once parked, which is
            # the one moment this needs it.
            "ended_at": self.runtime.location.zone(),
            "from_zone": snapshot.start_zone,
            "to_zone": self._zone_now(),
        }
        if snapshot.start_position or self._position_now():
            record["from_position"] = snapshot.start_position
            record["to_position"] = self._position_now()

        self.hass.bus.async_fire(EVENT_TRIP_RECORDED, record)
        self._notify()

    # ----------------------------------------------------------- internals --
    def _zone_now(self) -> str | None:
        tracker = self.config.opt(SECTION_CAR, CONF_DEVICE_TRACKER)
        return string_state(self.hass, tracker) if tracker else None

    def _position_now(self) -> str | None:
        """Coordinates, and only when the user has asked for them.

        Off by default, and that is a deliberate choice rather than caution for
        its own sake. This integration ships dashboards meant to be shared, and
        a home address is the single most sensitive thing it could hold. A
        route map is worth having, but it should be something somebody turned
        on knowing what it records.
        """
        if not self.config.opt(SECTION_LOCATION, CONF_RECORD_POSITIONS):
            return None
        tracker = self.config.opt(SECTION_CAR, CONF_DEVICE_TRACKER)
        if not tracker:
            return None
        state = self.hass.states.get(tracker)
        if state is None:
            return None
        lat = state.attributes.get("latitude")
        lon = state.attributes.get("longitude")
        if lat is None or lon is None:
            return None
        return f"{float(lat):.5f},{float(lon):.5f}"

