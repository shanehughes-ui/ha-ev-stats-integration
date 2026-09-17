"""Where the car is, and whether that is worth believing.

The car reports a position, but it reports it lazily: a fix read at the moment
a charge starts can describe the previous parking spot. Trusting it directly
attributes a whole session to wherever the car was yesterday, and does so
confidently.

The fix for that is not to ask how old the fix is in minutes - a car parked for
a week has a week-old fix that is perfectly correct. It is to ask whether the
car has *moved* since the fix was taken, and the odometer answers that exactly.
So a position is latched together with the odometer reading at the moment it
arrives, and the fix counts as fresh only while the odometer still reads the
same. A car that has driven anywhere has a stale fix by definition, however
recent it is.

The latch stores where the car was, never what that place is called. Naming the
zone at latch time would freeze the answer: moving a zone, or adding one, could
not then correct a fix already taken, and a parked car never re-latches. The
name is resolved at read time instead.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from .classify import GPS_OTHER, GPS_UNKNOWN
from .const import (
    BUCKET_HOME,
    CONF_DEVICE_TRACKER,
    CONF_HOME_RADIUS_M,
    CONF_HOME_ZONE,
    CONF_ODOMETER,
    CONF_WORK_RADIUS_M,
    CONF_WORK_ZONES,
    DEFAULT_HOME_RADIUS_M,
    DEFAULT_WORK_RADIUS_M,
    DOMAIN,
    GPS_MIN_ABS_DEG,
    GPS_MIN_DELTA_DEG,
    SECTION_CAR,
    SECTION_LOCATION,
)
from .helpers import Config, numeric_state

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1


class LocationTracker:
    """Latches a position against the odometer, and names it on demand."""

    def __init__(self, hass: HomeAssistant, entry_id: str, config: Config) -> None:
        self.hass = hass
        self.config = config
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.location")
        self._odometer = config.required(CONF_ODOMETER)
        self._tracker: str | None = config.opt(SECTION_CAR, CONF_DEVICE_TRACKER)
        self._home_zone: str = config.required(CONF_HOME_ZONE)
        self._lat: float | None = None
        self._lon: float | None = None
        self._fix_odometer: float | None = None
        self._fix_time: str | None = None
        self._unsub = None

    # ------------------------------------------------------------ lifecycle --
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        self._lat = data.get("latitude")
        self._lon = data.get("longitude")
        self._fix_odometer = data.get("fix_odometer")
        self._fix_time = data.get("fix_time")

    @callback
    def async_start(self) -> None:
        if not self._tracker:
            return
        self._unsub = async_track_state_change_event(
            self.hass, [self._tracker], self._handle_tracker
        )
        self._maybe_latch()

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    # --------------------------------------------------------------- state --
    @property
    def configured(self) -> bool:
        return self._tracker is not None

    @property
    def has_fix(self) -> bool:
        return self._lat is not None and self._fix_odometer is not None

    @property
    def fix_time(self) -> str | None:
        return self._fix_time

    @property
    def fresh(self) -> bool:
        """True only while the car has not moved since the fix was taken."""
        if not self.has_fix:
            return False
        odo = numeric_state(self.hass, self._odometer)
        if odo is None:
            return False
        return odo == self._fix_odometer

    @property
    def staleness_km(self) -> float | None:
        """How far the car has driven since the fix.

        None, not zero, when either reading is missing. Defaulting the fix to
        zero here published the entire odometer as the distance travelled since
        the fix - 2,892 km of staleness on a car that had done 2,892 km in its
        life.
        """
        odo = numeric_state(self.hass, self._odometer)
        if odo is None or self._fix_odometer is None:
            return None
        return round(odo - self._fix_odometer, 1)

    def zone(self) -> str:
        """Name the latched fix, or `unknown` if it cannot be trusted."""
        if not self.fresh or self._lat is None or self._lon is None:
            return GPS_UNKNOWN

        home_r = float(
            self.config.opt(SECTION_LOCATION, CONF_HOME_RADIUS_M, DEFAULT_HOME_RADIUS_M)
        )
        if self._within(self._home_zone, home_r):
            return BUCKET_HOME

        work_r = float(
            self.config.opt(SECTION_LOCATION, CONF_WORK_RADIUS_M, DEFAULT_WORK_RADIUS_M)
        )
        for zone_entity in self._work_zones():
            if self._within(zone_entity, work_r):
                return zone_entity.split(".", 1)[-1]

        return GPS_OTHER

    def metres_from(self, zone_entity: str) -> float | None:
        if self._lat is None or self._lon is None:
            return None
        return self._distance_to(zone_entity)

    def as_attributes(self) -> dict[str, Any]:
        """What the diagnostic sensors publish. Never the coordinates."""
        return {
            "fix_time": self._fix_time,
            "fix_odometer": self._fix_odometer,
            "metres_from_home": _round_or_none(self._distance_to(self._home_zone)),
        }

    # ----------------------------------------------------------- internals --
    def _work_zones(self) -> list[str]:
        zones = self.config.opt(SECTION_LOCATION, CONF_WORK_ZONES) or []
        return [zones] if isinstance(zones, str) else list(zones)

    def _distance_to(self, zone_entity: str) -> float | None:
        if self._lat is None or self._lon is None:
            return None
        state = self.hass.states.get(zone_entity)
        if state is None:
            return None
        lat = state.attributes.get("latitude")
        lon = state.attributes.get("longitude")
        if lat is None or lon is None:
            return None
        return distance(self._lat, self._lon, float(lat), float(lon))

    def _within(self, zone_entity: str, radius_m: float) -> bool:
        """Inside the configured radius of a zone.

        The configured radius rather than the zone's own, deliberately. A zone
        radius is set for presence detection and is generous on purpose so that
        arriving and leaving do not flap; this has to tell a driveway from the
        street outside it, and 100 metres of slack would put a neighbour's
        socket at home.
        """
        metres = self._distance_to(zone_entity)
        return metres is not None and metres <= radius_m

    @callback
    def _handle_tracker(self, _event: Event[EventStateChangedData]) -> None:
        self._maybe_latch()

    @callback
    def _maybe_latch(self) -> None:
        """Take a new fix, if this one is real and is somewhere new."""
        if not self._tracker:
            return
        state = self.hass.states.get(self._tracker)
        if state is None:
            return
        lat = state.attributes.get("latitude")
        lon = state.attributes.get("longitude")
        if lat is None or lon is None:
            return
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return
        # Null island. Several trackers report 0,0 rather than no position at
        # all, and it is 600 km off the coast of Ghana - a real coordinate that
        # every distance test answers confidently and wrongly.
        if abs(lat) < GPS_MIN_ABS_DEG or abs(lon) < GPS_MIN_ABS_DEG:
            return

        odo = numeric_state(self.hass, self._odometer)
        if odo is None:
            # A fix with no odometer to stamp it against can never be shown to
            # be fresh, so latching it would only overwrite one that can.
            return

        if self._lat is not None and self._lon is not None:
            moved = (
                abs(lat - self._lat) > GPS_MIN_DELTA_DEG
                or abs(lon - self._lon) > GPS_MIN_DELTA_DEG
            )
            # Both axes, not just latitude. Driving due east changes only the
            # longitude, and a check on latitude alone would call that arrival
            # jitter and keep the old fix.
            if not moved:
                return

        self._lat = lat
        self._lon = lon
        self._fix_odometer = odo
        self._fix_time = dt_util.utcnow().isoformat(timespec="seconds")
        self.hass.async_create_task(self._async_persist())

    async def _async_persist(self) -> None:
        await self._store.async_save(
            {
                "latitude": self._lat,
                "longitude": self._lon,
                "fix_odometer": self._fix_odometer,
                "fix_time": self._fix_time,
            }
        )


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value)
