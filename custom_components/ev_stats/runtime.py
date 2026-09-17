"""What a configured entry owns while it is loaded."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .baseline import HouseBaseline, TyreBaseline
from .derived import lowest_tyre
from .classify import GPS_UNKNOWN
from .const import (
    BUCKET_HOME,
    BUCKET_OTHER,
    BUCKET_PUBLIC_DC,
    BUCKET_UNKNOWN,
    CONF_AMBIENT_TEMP,
    CONF_CHARGE_EFFICIENCY,
    CONF_CHARGING_POWER,
    CONF_HOUSE_LOAD,
    CONF_CHARGER_PLUG,
    CONF_INSTALL_ODOMETER,
    CONF_ODOMETER,
    CONF_RECORD_POSITIONS,
    CONF_TYRE_PRESSURE,
    CONF_TYRE_TEMPERATURE,
    CONF_WORK_ZONES,
    DEFAULT_CHARGE_EFFICIENCY,
    EVENT_PACK_ESTIMATE,
    EVENT_SESSION_RECORDED,
    EVENT_TRIP_RECORDED,
    SECTION_CAR,
    SECTION_EXTRAS,
    SECTION_HOUSE,
    SECTION_LOCATION,
    SECTION_THRESHOLDS,
)
from .helpers import Config, numeric_state
from .integrator import SourceIntegrator
from .ledger import Ledger
from .location import LocationTracker
from .meters import RateMeters
from .packsize import implied_capacity
from .session import SessionManager
from .store import LogStore
from .trip import TripManager

_LOGGER = logging.getLogger(__name__)

# One minute. A steady charge emits no state changes, and at 6.8 kW a minute is
# 0.11 kWh - fine enough that the shape of a session survives, coarse enough
# that a slow car poll is not fighting a fast timer.
CHARGE_SUB_INTERVAL = 60.0
# The house meter updates on its own and matters less per-sample, so it gets a
# longer leash.
HOUSE_SUB_INTERVAL = 120.0

# Default entity ids of the YAML package this integration grew out of, so an
# import needs no arguments on the install it is most likely to be run against.
LEGACY_SESSIONS = "sensor.ev_charge_sessions"
LEGACY_TRIPS = "sensor.ev_trips"
LEGACY_PACK = "sensor.ev_pack_estimate"

# Old field name -> new one. The shapes are close because one grew out of the
# other, and where they differ the new name is the clearer of the two.
LEGACY_SESSION_FIELDS = {"amps": "peak_amps"}
LEGACY_TRIP_FIELDS = {"from_pos": "from_position", "to_pos": "to_position"}


def work_bucket_id(zone_entity_id: str) -> str:
    """`zone.workplace` -> `workplace`.

    The zone's own object id, so a bucket is named after the place rather than
    after a slug someone typed. Renaming the zone renames the bucket, which is
    the correct behaviour even though it means the old bucket's energy gets
    folded back into `unknown` on next load.
    """
    return zone_entity_id.split(".", 1)[-1]


def work_buckets(config: Config) -> tuple[str, ...]:
    zones = config.opt(SECTION_LOCATION, CONF_WORK_ZONES) or []
    if isinstance(zones, str):
        zones = [zones]
    return tuple(dict.fromkeys(work_bucket_id(z) for z in zones))


def derive_buckets(config: Config) -> tuple[str, ...]:
    """The buckets this configuration implies.

    `unknown` first and always. A free-charging zone only exists as a bucket if
    the user actually configured one, so an installation with no workplace
    charger simply has no such bucket rather than an empty one sitting at zero
    pretending to be meaningful.
    """
    return (
        BUCKET_UNKNOWN,
        BUCKET_HOME,
        *work_buckets(config),
        BUCKET_PUBLIC_DC,
        BUCKET_OTHER,
    )


class EvStatsRuntime:
    """Holds the meters, the ledger, the logs and the lifecycles for one entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.config = Config(entry)
        self.buckets = derive_buckets(self.config)
        self.work_buckets = work_buckets(self.config)
        self.ledger = Ledger(hass, entry.entry_id, self.buckets)
        self.logs = LogStore(hass, entry.entry_id)
        self.charging_power_entity: str = self.config.required(CONF_CHARGING_POWER)

        self.charge_energy = SourceIntegrator(
            hass,
            self.charging_power_entity,
            CHARGE_SUB_INTERVAL,
            self._on_charge_energy,
        )

        house_load = self.config.opt(SECTION_HOUSE, CONF_HOUSE_LOAD)
        # Optional, and its absence is not an error: without it the house-supply
        # proof simply cannot answer, and attribution falls back to location.
        self.house_energy: SourceIntegrator | None = (
            SourceIntegrator(hass, house_load, HOUSE_SUB_INTERVAL, lambda _t: None)
            if house_load
            else None
        )
        self.baseline: HouseBaseline | None = (
            HouseBaseline(hass, house_load, self.charging_power_entity)
            if house_load
            else None
        )

        self.tyre_baseline = TyreBaseline(hass)
        self.location = LocationTracker(hass, entry.entry_id, self.config)
        self.meters = RateMeters(hass, entry.entry_id, self)
        self.session = SessionManager(hass, entry.entry_id, self)
        self.trips = TripManager(hass, entry.entry_id, self)
        self._unsubs: list = []

    async def async_start(self) -> None:
        await self.ledger.async_load()
        # The integrator holds its running total in memory only, so it comes
        # back from a restart at zero while the ledger remembers every kWh it
        # has filed. Left alone, the next reading looks like the meter running
        # backwards: the charge-energy sensor drops to nothing and the balance
        # check - the one number that says the rest can be trusted - reports
        # the entire lifetime energy as an error.
        self.charge_energy.restore(self.ledger.last_total)
        await self.logs.async_load()
        await self.meters.async_load()
        await self.location.async_load()
        await self.session.async_load()
        await self.trips.async_load()

        # The logs are fed from the event bus rather than called into directly.
        # It costs one hop and buys two things: a user automation sees exactly
        # what the store sees, and an imported record takes the identical path
        # as a live one instead of a parallel one that can drift.
        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_SESSION_RECORDED, self._on_session)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_TRIP_RECORDED, self._on_trip)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_PACK_ESTIMATE, self._on_estimate)
        )

        self.charge_energy.async_start()
        self.meters.async_start()
        if self.house_energy:
            self.house_energy.async_start()
        # Before the session manager: the classifier asks the baseline whether
        # it has enough history to be believed, and a baseline that has not
        # backfilled yet answers no. Starting them the other way round would
        # make the first minutes after a restart quietly location-only.
        if self.baseline:
            await self.baseline.async_start()
        self.location.async_start()
        self.trips.async_start()
        self._start_tyre_sampling()
        await self.session.async_start()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self.trips.async_stop()
        self.session.async_stop()
        self.location.async_stop()
        if self.baseline:
            self.baseline.async_stop()
        self.meters.async_stop()
        self.charge_energy.async_stop()
        if self.house_energy:
            self.house_energy.async_stop()

    @callback
    def _on_charge_energy(self, total: Decimal) -> None:
        """New energy metered: file it into whichever bucket is routed."""
        self.hass.async_create_task(self.ledger.async_accrue(total))

    @callback
    def _start_tyre_sampling(self) -> None:
        """Feed the tyres' own 30-day history.

        Sampled from the normalised reading rather than the raw one, so the
        baseline is free of the thermal swing too - otherwise drift would be
        measured against a reference that moves with the weather, which is the
        very thing the normalisation exists to remove.
        """
        pressures = [
            e for e in (self.config.opt(SECTION_CAR, CONF_TYRE_PRESSURE) or []) if e
        ]
        if not pressures:
            return

        @callback
        def _sample(_event) -> None:
            value = lowest_tyre(self)
            if value is not None:
                self.tyre_baseline.add(value)

        self._unsubs.append(
            async_track_state_change_event(self.hass, pressures, _sample)
        )
        _sample(None)

    # ------------------------------------------------------- derived inputs --
    @property
    def distance_since_install(self) -> float | None:
        """Distance on the same basis as the energy.

        Unavailable rather than falling back to the whole-of-life odometer when
        the install reading has not been set. A consumption figure computed
        against a car's entire history would be wrong by a factor of three and
        would look entirely plausible.
        """
        odo = numeric_state(self.hass, self.config.required(CONF_ODOMETER))
        install = self.config.opt(SECTION_THRESHOLDS, CONF_INSTALL_ODOMETER)
        if odo is None or install is None:
            return None
        return max(round(odo - float(install), 1), 0.0)

    @property
    def days_since_install(self) -> float | None:
        """How long this has been running, from the config entry itself.

        No separate install-date setting to get out of step with the odometer
        reading beside it - the entry knows when it was created.
        """
        created = getattr(self.entry, "created_at", None)
        if created is None:
            return None
        return max((dt_util.utcnow() - created).total_seconds() / 86400, 0.0)

    @property
    def tyre_temperature_entities(self) -> list[str]:
        value = self.config.opt(SECTION_CAR, CONF_TYRE_TEMPERATURE) or []
        return [value] if isinstance(value, str) else list(value)

    def is_plugged_in(self) -> bool:
        plug = self.config.opt(SECTION_CAR, CONF_CHARGER_PLUG)
        state = self.hass.states.get(plug) if plug else None
        return state is not None and state.state in ("on", "connected", "plugged")

    def charging_at(self) -> str:
        """The best current belief about where the car is charging.

        Not the same question as the closing verdict, and it is asked live -
        by the carbon rate, which has no session end to correct it. The
        classifier first, because it weighs both signals; a trusted fix second,
        so that a charge nobody has classified yet is still placed if the car
        is provably somewhere known.

        It resolves rather than waits. Leaving this at `unknown` through an
        entire home charge would credit our own roof with nothing and report
        grid carbon for solar kilowatts.
        """
        known = (BUCKET_HOME, *self.work_buckets, BUCKET_OTHER, BUCKET_PUBLIC_DC)
        verdict = self.session.classification.bucket
        if verdict in known:
            return verdict
        zone = self.location.zone()
        return zone if zone in known else GPS_UNKNOWN

    # ----------------------------------------------------------------- logs --
    async def _on_session(self, event: Event) -> None:
        record = dict(event.data)
        await self.logs.async_record_session(record)
        await self._async_maybe_measure_pack(record)

    async def _on_trip(self, event: Event) -> None:
        await self.logs.async_record_trip(dict(event.data))

    async def _on_estimate(self, event: Event) -> None:
        await self.logs.async_record_estimate(dict(event.data))

    async def _async_maybe_measure_pack(self, record: dict[str, Any]) -> None:
        """A charge across a wide SoC range measures the usable pack.

        The only battery-health signal most cars can produce, and it falls out
        of a session that was happening anyway. `implied_capacity` returns None
        for a narrow charge rather than a number, because a bad measurement
        looks exactly as authoritative as a good one once it is in the list.
        """
        soc_delta = record.get("soc_delta")
        kwh = record.get("kwh")
        if soc_delta is None or kwh is None:
            return
        efficiency = Decimal(
            str(
                self.config.opt(
                    SECTION_THRESHOLDS,
                    CONF_CHARGE_EFFICIENCY,
                    DEFAULT_CHARGE_EFFICIENCY,
                )
            )
        )
        implied = implied_capacity(
            Decimal(str(kwh)), Decimal(str(soc_delta)), efficiency
        )
        if implied is None:
            return

        self.hass.bus.async_fire(
            EVENT_PACK_ESTIMATE,
            {
                "at": dt_util.utcnow().isoformat(timespec="seconds"),
                "session_id": record.get("id"),
                "kwh": kwh,
                "soc_delta": soc_delta,
                "implied": float(implied),
                # Recorded with every measurement because it is the confound
                # that actually limits this: a cold pack charges less
                # efficiently, and that seasonal swing can exceed the
                # degradation being looked for.
                "ambient_c": numeric_state(
                    self.hass, self.config.opt(SECTION_EXTRAS, CONF_AMBIENT_TEMP)
                ),
                "efficiency": float(efficiency),
            },
        )

    # --------------------------------------------------------------- import --
    async def async_import_legacy(
        self,
        sessions_entity: str | None = None,
        trips_entity: str | None = None,
        pack_entity: str | None = None,
    ) -> dict[str, int]:
        """Read the logs off a YAML install's template sensors."""
        sessions = self._legacy_list(
            sessions_entity or LEGACY_SESSIONS, "sessions", LEGACY_SESSION_FIELDS
        )
        trips = self._legacy_list(
            trips_entity or LEGACY_TRIPS, "trips", LEGACY_TRIP_FIELDS
        )
        estimates = self._legacy_list(pack_entity or LEGACY_PACK, "estimates", {})

        added = await self.logs.async_import(sessions, trips, estimates)
        _LOGGER.info(
            "Imported %s sessions, %s trips and %s pack measurements",
            added["sessions"],
            added["trips"],
            added["estimates"],
        )
        return added

    def _legacy_list(
        self,
        entity_id: str,
        attribute: str,
        renames: dict[str, str],
    ) -> list[dict[str, Any]]:
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.debug("Nothing to import: %s does not exist", entity_id)
            return []
        rows = state.attributes.get(attribute)
        if not isinstance(rows, list):
            _LOGGER.warning("%s has no %r attribute to import", entity_id, attribute)
            return []

        keep_positions = bool(
            self.config.opt(SECTION_LOCATION, CONF_RECORD_POSITIONS)
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            record = {renames.get(k, k): v for k, v in row.items()}
            if not keep_positions:
                # The same privacy default a live trip gets. An import is the
                # most likely way coordinates would arrive in a store the user
                # never chose to have them in.
                record.pop("from_position", None)
                record.pop("to_position", None)
            out.append(record)
        return out
