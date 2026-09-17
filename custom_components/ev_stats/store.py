"""The logs: sessions, trips and pack measurements.

Kept in a `Store` rather than in entity attributes, which is the point of this
file. The YAML this replaces carried each log as a growing list on a
trigger-based template sensor, and the recorder rewrites an entity's whole
attribute blob on *every* state change - so twenty sessions were rewritten to
the database every time one number moved. That is why the lists were capped at
twenty and forty, and why the note in the YAML reads "the session log holds 20
rows and rolls off in about four weeks, so the data was on a timer".

A Store is not in the recorder at all. Nothing is rewritten when a state
changes, nothing is purged on a schedule, and the caps here exist only to bound
a file that is read into memory - they are more than an order of magnitude
larger than what they replace. The sensors still publish a recent slice as
attributes, because that is what a dashboard can read, but the slice is a view
now rather than the whole record.

One rule throughout: **ids are compared as strings.** Home Assistant's template
engine coerces an all-digit string to an int, so an id written by one path and
read by another can differ in type while looking identical in the log. In the
YAML that made every correction button on every session notification a silent
no-op for nine sessions - no error was raised anywhere, because `selectattr`
simply matched nothing.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .packsize import rolling_mean, trend_per_year

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1

# Bounds on a file that is read into memory whole, not a retention policy. A
# session record is a few hundred bytes, so these are well under a megabyte
# between them and still hold years of driving.
MAX_SESSIONS = 500
MAX_TRIPS = 1000
MAX_ESTIMATES = 200


def _same_id(left: Any, right: Any) -> bool:
    """Compare ids as strings, always.

    Casting one side to int would work today and break the first time an id is
    not all digits. Casting both to string cannot.
    """
    return str(left) == str(right)


class LogStore:
    """Sessions, trips and pack measurements, kept out of the recorder."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.logs")
        self._sessions: list[dict[str, Any]] = []
        self._trips: list[dict[str, Any]] = []
        self._estimates: list[dict[str, Any]] = []
        self._listeners: list = []

    # ------------------------------------------------------------ lifecycle --
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        self._sessions = list(data.get("sessions", []))
        self._trips = list(data.get("trips", []))
        self._estimates = list(data.get("estimates", []))

    async def _async_persist(self) -> None:
        await self._store.async_save(
            {
                "sessions": self._sessions,
                "trips": self._trips,
                "estimates": self._estimates,
            }
        )
        self._notify()

    def add_listener(self, cb) -> Any:
        self._listeners.append(cb)

        def _remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return _remove

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --------------------------------------------------------------- reading --
    def sessions(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Newest last, which is the order a table reads in."""
        return self._sessions[-limit:] if limit else list(self._sessions)

    def trips(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._trips[-limit:] if limit else list(self._trips)

    def estimates(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._estimates[-limit:] if limit else list(self._estimates)

    def session(self, session_id: Any) -> dict[str, Any] | None:
        for record in reversed(self._sessions):
            if _same_id(record.get("id"), session_id):
                return record
        return None

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def trip_count(self) -> int:
        return len(self._trips)

    @property
    def pack_rolling_mean(self) -> float | None:
        """The published pack size follows this, never the latest single one.

        Real measurements scatter about 1.5% while the thing being looked for
        is roughly 2% a year, so one wide session cannot be allowed to move a
        converged figure on its own.
        """
        return rolling_mean([e.get("implied") for e in self._estimates])

    @property
    def pack_trend(self) -> float | None:
        """kWh per year, once there are enough measurements over enough time."""
        points: list[tuple[float, float]] = []
        for estimate in self._estimates:
            when = dt_util.parse_datetime(str(estimate.get("at", "")))
            implied = estimate.get("implied")
            if when is None or implied is None:
                continue
            points.append((when.timestamp() / 86400, float(implied)))
        return trend_per_year(points)

    # --------------------------------------------------------------- writing --
    async def async_record_session(self, record: dict[str, Any]) -> None:
        if self.session(record.get("id")) is not None:
            _LOGGER.debug("Session %s is already logged; ignoring", record.get("id"))
            return
        self._sessions.append(dict(record))
        del self._sessions[:-MAX_SESSIONS]
        await self._async_persist()

    async def async_correct_session(
        self,
        session_id: Any,
        bucket: str,
        cost: Decimal | None = None,
        solar_kwh: Decimal | None = None,
    ) -> dict[str, Any] | None:
        """Record that a session was reattributed.

        The record keeps what it was corrected *from*, because the log is the
        tuning data for the classifier and a row that quietly becomes right is
        worth nothing to it. A verdict that had to be overridden is the only
        kind that teaches anything.
        """
        for record in self._sessions:
            if not _same_id(record.get("id"), session_id):
                continue
            record.setdefault("original_bucket", record.get("bucket"))
            record["bucket"] = bucket
            record["corrected"] = True
            record["corrected_at"] = dt_util.utcnow().isoformat(timespec="seconds")
            if cost is not None:
                record["cost"] = float(cost)
            if solar_kwh is not None:
                record["solar_kwh"] = float(solar_kwh)
            await self._async_persist()
            return record
        return None

    async def async_price_session(
        self,
        session_id: Any,
        cost: Decimal,
    ) -> dict[str, Any] | None:
        """Attach what a session was billed, without calling it a correction.

        A public charge filed as public DC and then priced was classified
        perfectly well. Marking it `corrected` would say the classifier got it
        wrong, and the log is the tuning data for that classifier.
        """
        for record in self._sessions:
            if not _same_id(record.get("id"), session_id):
                continue
            record["cost"] = float(cost)
            record["priced_at"] = dt_util.utcnow().isoformat(timespec="seconds")
            await self._async_persist()
            return record
        return None

    async def async_record_trip(self, record: dict[str, Any]) -> None:
        self._trips.append(dict(record))
        del self._trips[:-MAX_TRIPS]
        await self._async_persist()

    async def async_record_estimate(self, record: dict[str, Any]) -> None:
        self._estimates.append(dict(record))
        del self._estimates[:-MAX_ESTIMATES]
        await self._async_persist()

    async def async_import(
        self,
        sessions: list[dict[str, Any]] | None = None,
        trips: list[dict[str, Any]] | None = None,
        estimates: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Merge records from somewhere else, skipping ones already held.

        Idempotent by id, so running an import twice adds nothing the second
        time. Trips and estimates carry ids too; anything without one is taken
        on trust, because refusing it would lose the row entirely.
        """
        added = {"sessions": 0, "trips": 0, "estimates": 0}

        known = {str(s.get("id")) for s in self._sessions}
        for record in sessions or []:
            key = str(record.get("id"))
            if key in known:
                continue
            known.add(key)
            self._sessions.append(dict(record))
            added["sessions"] += 1

        known = {str(t.get("id")) for t in self._trips}
        for record in trips or []:
            key = str(record.get("id"))
            if key in known:
                continue
            known.add(key)
            self._trips.append(dict(record))
            added["trips"] += 1

        known = {str(e.get("session_id")) for e in self._estimates}
        for record in estimates or []:
            key = str(record.get("session_id"))
            if key in known:
                continue
            known.add(key)
            self._estimates.append(dict(record))
            added["estimates"] += 1

        # Imported history is older than what is already here, so sort before
        # trimming - otherwise a large import would push out the recent rows
        # and keep the ancient ones.
        self._sessions.sort(key=lambda r: str(r.get("start") or r.get("id") or ""))
        self._trips.sort(key=lambda r: str(r.get("start") or r.get("id") or ""))
        self._estimates.sort(key=lambda r: str(r.get("at") or ""))
        del self._sessions[:-MAX_SESSIONS]
        del self._trips[:-MAX_TRIPS]
        del self._estimates[:-MAX_ESTIMATES]

        await self._async_persist()
        return added
