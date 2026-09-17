"""What a configured entry owns while it is loaded."""

from __future__ import annotations

import logging
from decimal import Decimal

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .baseline import HouseBaseline
from .const import (
    BUCKET_HOME,
    BUCKET_OTHER,
    BUCKET_PUBLIC_DC,
    BUCKET_UNKNOWN,
    CONF_CHARGING_POWER,
    CONF_HOUSE_LOAD,
    CONF_WORK_ZONES,
    SECTION_HOUSE,
    SECTION_LOCATION,
)
from .helpers import Config
from .integrator import SourceIntegrator
from .ledger import Ledger
from .location import LocationTracker
from .session import SessionManager

_LOGGER = logging.getLogger(__name__)

# One minute. A steady charge emits no state changes, and at 6.8 kW a minute is
# 0.11 kWh - fine enough that the shape of a session survives, coarse enough
# that a slow car poll is not fighting a fast timer.
CHARGE_SUB_INTERVAL = 60.0
# The house meter updates on its own and matters less per-sample, so it gets a
# longer leash.
HOUSE_SUB_INTERVAL = 120.0


def work_bucket_id(zone_entity_id: str) -> str:
    """`zone.allison_work` -> `allison_work`.

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
    """Holds the meters, the ledger and the session lifecycle for one entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.config = Config(entry)
        self.buckets = derive_buckets(self.config)
        self.work_buckets = work_buckets(self.config)
        self.ledger = Ledger(hass, entry.entry_id, self.buckets)
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

        self.location = LocationTracker(hass, entry.entry_id, self.config)
        self.session = SessionManager(hass, entry.entry_id, self)

    async def async_start(self) -> None:
        await self.ledger.async_load()
        # The integrator holds its running total in memory only, so it comes
        # back from a restart at zero while the ledger remembers every kWh it
        # has filed. Left alone, the next reading looks like the meter running
        # backwards: the charge-energy sensor drops to nothing and the balance
        # check - the one number that says the rest can be trusted - reports
        # the entire lifetime energy as an error.
        self.charge_energy.restore(self.ledger.last_total)
        await self.location.async_load()
        await self.session.async_load()

        self.charge_energy.async_start()
        if self.house_energy:
            self.house_energy.async_start()
        # Before the session manager: the classifier asks the baseline whether
        # it has enough history to be believed, and a baseline that has not
        # backfilled yet answers no. Starting them the other way round would
        # make the first minutes after a restart quietly location-only.
        if self.baseline:
            await self.baseline.async_start()
        self.location.async_start()
        await self.session.async_start()

    @callback
    def async_stop(self) -> None:
        self.session.async_stop()
        self.location.async_stop()
        if self.baseline:
            self.baseline.async_stop()
        self.charge_energy.async_stop()
        if self.house_energy:
            self.house_energy.async_stop()

    @callback
    def _on_charge_energy(self, total: Decimal) -> None:
        """New energy metered: file it into whichever bucket is routed."""
        self.hass.async_create_task(self.ledger.async_accrue(total))
