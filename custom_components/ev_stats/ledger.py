"""The attribution ledger.

Five-ish buckets that charging energy is filed into, and the one operation that
moves it between them.

This is the reason the integration exists. In YAML the same thing was a
`utility_meter` with tariffs, driven by `utility_meter.calibrate`, and it worked
- but a move touched only the energy. The home cost total, the solar total, the
unattributed floor and the session record were four separate `input_number`
writes in an automation, and when a correction came from anywhere other than the
notification it updated one ledger of five. Here a move is one method holding one
lock, over a bucket that carries its energy, its cost and its solar together, and
either all of it happens or none of it does.

Two invariants, asserted rather than hoped for:

  * the buckets always sum to the total energy ever metered
  * no bucket ever goes negative

`unknown` is the default and stays first. Every kWh lands there unless something
has *proven* where the car was, so if every other part of this fails the energy
is still counted - visibly unattributed, never silently misfiled.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from decimal import Decimal

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store

from .buckets import ZERO, Balance, carried
from .const import BUCKET_UNKNOWN, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Version 1 stored a bare kWh string per bucket. Version 2 stores energy, cost
# and solar together, because a correction has to move all three or none.
STORE_VERSION = 2
# A move is allowed to leave the source this far below zero before it is
# refused, to absorb rounding rather than real error.
TOLERANCE = Decimal("0.001")


class _LedgerStore(Store):
    """Reads a version-1 ledger without losing it."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict
    ) -> dict:
        # Version 1 held `{"balances": {"home": "12.345"}}`. `Balance.from_dict`
        # reads a bare string as energy with no cost, which is exactly right -
        # there was no cost recorded to lose. So the shape needs no rewriting,
        # but the method still has to exist: without it, Store refuses to load
        # data written by an older version at all.
        return old_data


class Ledger:
    """Buckets, what is in them, and the only thing that moves it."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        buckets: tuple[str, ...],
    ) -> None:
        self.hass = hass
        self._buckets = buckets
        self._store = _LedgerStore(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.ledger")
        self._lock = asyncio.Lock()
        self._balances: dict[str, Balance] = {b: Balance() for b in buckets}
        # Where energy accrues right now. Switched to a real bucket only when
        # the location has been proven; otherwise it stays on `unknown`.
        self._route: str = BUCKET_UNKNOWN
        # Energy deliberately left unattributed by a past session. The tail
        # sweep never takes `unknown` below this, so a later confident session
        # cannot swallow an earlier unresolved one.
        self._floor: Decimal = ZERO
        self._last = Balance()
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------- reading --
    @property
    def buckets(self) -> tuple[str, ...]:
        return self._buckets

    @property
    def route(self) -> str:
        return self._route

    @property
    def floor(self) -> Decimal:
        return self._floor

    @property
    def last_total(self) -> Decimal:
        """The master meter reading this ledger has already accounted for.

        Persisted, which makes it the one durable record of how much the
        integrator had metered. The integrator itself starts from zero on every
        restart, so this is what it is restored from.
        """
        return self._last.kwh

    def balance(self, bucket: str) -> Balance:
        return self._balances.get(bucket, Balance())

    def kwh(self, bucket: str) -> Decimal:
        return self.balance(bucket).kwh

    def total(self) -> Balance:
        result = Balance()
        for value in self._balances.values():
            result = result + value
        return result

    def total_kwh(self) -> Decimal:
        return self.total().kwh

    def balance_check(self, metered_total: Decimal) -> Decimal:
        """Buckets minus the master meter. Anything but zero is a bug."""
        return self.total_kwh() - metered_total

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def _remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return _remove

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --------------------------------------------------------- persistence --
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        stored = data.get("balances", {})
        for bucket in self._buckets:
            if bucket in stored:
                self._balances[bucket] = Balance.from_dict(stored[bucket])
        # A bucket that existed before but is not configured now (a work zone
        # was removed) would otherwise strand its contents outside the sum and
        # break the invariant for ever. Fold it back into `unknown` and say so.
        for bucket, value in stored.items():
            if bucket not in self._balances:
                orphan = Balance.from_dict(value)
                if orphan.is_empty():
                    continue
                _LOGGER.warning(
                    "Bucket %r is no longer configured; moving its %s kWh to unknown",
                    bucket,
                    orphan.kwh,
                )
                self._balances[BUCKET_UNKNOWN] += orphan
        self._route = data.get("route", BUCKET_UNKNOWN)
        if self._route not in self._balances:
            self._route = BUCKET_UNKNOWN
        self._floor = Decimal(str(data.get("floor", "0")))
        self._last = Balance.from_dict(data.get("last", data.get("last_total", "0")))

    async def _async_persist(self) -> None:
        await self._store.async_save(
            {
                "balances": {b: v.as_dict() for b, v in self._balances.items()},
                "route": self._route,
                "floor": str(self._floor),
                "last": self._last.as_dict(),
            }
        )

    # -------------------------------------------------------------- writing --
    async def async_set_route(self, bucket: str) -> None:
        """Choose where energy accrues from now on."""
        if bucket not in self._balances:
            raise ServiceValidationError(f"Unknown bucket {bucket!r}")
        async with self._lock:
            self._route = bucket
            await self._async_persist()
        self._notify()

    async def async_accrue(self, metered_total: Decimal) -> None:
        """Take the master meter's running total and file what is new.

        Only ever the *delta*, and only ever forwards. The meter is a Riemann
        integral of a non-negative power reading, so it cannot legitimately go
        backwards; if it does, something restarted or was recalibrated and
        pretending otherwise would invent energy.

        Energy only. Cost never accrues live, because the rate meters are
        deliberately provisional - they price every charge as though it were on
        our supply - and filing that against whatever bucket happens to be
        routed would put two dollars on a charge that was free at a workplace.
        Cost enters the ledger once, at session close, when the place is known,
        and is restated by a correction if that turns out to be wrong. One path,
        not two.
        """
        async with self._lock:
            delta = metered_total - self._last.kwh
            if delta <= ZERO:
                if delta < ZERO:
                    _LOGGER.debug(
                        "Master meter went backwards (%s -> %s); resyncing without accruing",
                        self._last.kwh,
                        metered_total,
                    )
                self._last = Balance(metered_total)
                return

            self._balances[self._route] += Balance(kwh=delta)
            self._last = Balance(metered_total)
            await self._async_persist()
        self._notify()

    async def async_move(
        self,
        source: str,
        target: str,
        kwh: Decimal,
        cost: Decimal | None = None,
        solar_kwh: Decimal | None = None,
    ) -> Balance:
        """Move energy between buckets. The only way anything changes bucket.

        Returns what was actually moved, all three parts of it. Raises rather
        than clamping when the request is impossible, because silently moving
        less than asked is how a ledger ends up disagreeing with itself.

        `cost` and `solar_kwh` are for a caller that knows the real figures - a
        correction against a logged session does. Left out they are carried pro
        rata, which is an approximation but a bounded one; leaving them behind
        entirely is not, because nothing ever puts them back.
        """
        self._validate(source, target, kwh)

        async with self._lock:
            moving = self._move_locked(source, target, kwh, cost, solar_kwh)
            await self._async_persist()

        self._notify()
        _LOGGER.info("Moved %s kWh from %s to %s", moving.kwh, source, target)
        return moving

    async def async_reattribute(
        self,
        source: str,
        target: str,
        kwh: Decimal,
        held_cost: Decimal,
        held_solar: Decimal,
        new_cost: Decimal,
        new_solar: Decimal,
    ) -> Balance:
        """Refile a session: move it, then restate what it cost. One lock.

        This is the operation this whole file exists for, and it is two steps
        that must not come apart. The energy moves with what it contributed to
        the bucket it is leaving; then the figure is *replaced* at the bucket
        it arrives in, because the same charge is free at a workplace and
        priced at home. Carrying the cost across unchanged would say a charge
        at work cost two dollars.
        """
        self._validate(source, target, kwh)
        async with self._lock:
            moved = self._move_locked(source, target, kwh, held_cost, held_solar)
            current = self._balances[target]
            self._balances[target] = Balance(
                current.kwh,
                current.cost + (new_cost - held_cost),
                current.solar_kwh + (new_solar - held_solar),
            )
            await self._async_persist()

        self._notify()
        _LOGGER.info(
            "Reattributed %s kWh from %s to %s; it now costs %s",
            moved.kwh,
            source,
            target,
            new_cost,
        )
        return moved

    def _validate(self, source: str, target: str, kwh: Decimal) -> None:
        if source not in self._balances:
            raise ServiceValidationError(f"Unknown source bucket {source!r}")
        if target not in self._balances:
            raise ServiceValidationError(f"Unknown target bucket {target!r}")
        if source == target:
            raise ServiceValidationError("Source and target buckets are the same")
        if kwh <= ZERO:
            raise ServiceValidationError("Amount to move must be greater than zero")

    def _move_locked(
        self,
        source: str,
        target: str,
        kwh: Decimal,
        cost: Decimal | None,
        solar_kwh: Decimal | None,
    ) -> Balance:
        """The move itself. Caller holds the lock and persists afterwards."""
        available = self._balances[source]
        if available.kwh < kwh - TOLERANCE:
            raise ServiceValidationError(
                f"Cannot move {kwh} kWh out of {source!r}: "
                f"it holds {available.kwh} kWh"
            )
        # Never move more than is there, even inside the tolerance - the sum
        # invariant matters more than honouring the request exactly.
        moving = carried(available, min(kwh, available.kwh), cost, solar_kwh)
        self._balances[source] = available - moving
        self._balances[target] += moving

        # The floor protects energy a past session deliberately left
        # unattributed. Moving energy OUT of unknown means there is less to
        # protect, so the floor follows it down. It used to only ever rise,
        # which quietly pinned the tail sweep at zero for every later session.
        if source == BUCKET_UNKNOWN:
            self._floor = min(self._floor, self._balances[source].kwh)
        return moving

    async def async_adjust(
        self,
        bucket: str,
        cost: Decimal = ZERO,
        solar_kwh: Decimal = ZERO,
    ) -> None:
        """Change what a bucket cost without changing its energy.

        Two things need this. A public charger bills at the pump, so the only
        way that number ever reaches this ledger is somebody typing it in. And
        a correction has to restate what a session cost once it has moved: the
        same charge is free at a workplace and priced at home, so the figure
        does not travel with the energy - it is replaced.
        """
        if bucket not in self._balances:
            raise ServiceValidationError(f"Unknown bucket {bucket!r}")
        if cost == ZERO and solar_kwh == ZERO:
            return
        async with self._lock:
            current = self._balances[bucket]
            self._balances[bucket] = Balance(
                current.kwh, current.cost + cost, current.solar_kwh + solar_kwh
            )
            await self._async_persist()
        self._notify()

    async def async_set_floor(self, value: Decimal) -> None:
        async with self._lock:
            self._floor = max(ZERO, value)
            await self._async_persist()
        self._notify()

    def sweepable(self, cap: Decimal) -> Decimal:
        """How much of `unknown` a confident session may claim."""
        spare = self._balances[BUCKET_UNKNOWN].kwh - self._floor
        return max(ZERO, min(spare, cap))
