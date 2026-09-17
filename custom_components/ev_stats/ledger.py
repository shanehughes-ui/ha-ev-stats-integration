"""The attribution ledger.

Five-ish buckets that charging energy is filed into, and the one operation that
moves energy between them.

This is the reason the integration exists. In YAML the same thing was a
`utility_meter` with tariffs, driven by `utility_meter.calibrate`, and it worked
— but a move touched only the energy. The home cost total, the solar total, the
unattributed floor and the session record were four separate `input_number`
writes in an automation, and when a correction came from anywhere other than the
notification it updated one ledger of five. Here a move is one method holding one
lock, and either all of it happens or none of it does.

Two invariants, asserted rather than hoped for:

  * the buckets always sum to the total energy ever metered
  * no bucket ever goes negative

`unknown` is the default and stays first. Every kWh lands there unless something
has *proven* where the car was, so if every other part of this fails the energy
is still counted — visibly unattributed, never silently misfiled.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from decimal import Decimal

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store

from .const import BUCKET_UNKNOWN, DOMAIN

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
# A move is allowed to leave the source this far below zero before it is
# refused, to absorb rounding rather than real error.
TOLERANCE = Decimal("0.001")


class Ledger:
    """Buckets, the energy in them, and the only thing that moves it."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        buckets: tuple[str, ...],
    ) -> None:
        self.hass = hass
        self._buckets = buckets
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.ledger")
        self._lock = asyncio.Lock()
        self._balances: dict[str, Decimal] = {b: Decimal(0) for b in buckets}
        # Where energy accrues right now. Switched to a real bucket only when
        # the location has been proven; otherwise it stays on `unknown`.
        self._route: str = BUCKET_UNKNOWN
        # Energy deliberately left unattributed by a past session. The tail
        # sweep never takes `unknown` below this, so a later confident session
        # cannot swallow an earlier unresolved one.
        self._floor: Decimal = Decimal(0)
        self._last_total: Decimal = Decimal(0)
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

    def balance(self, bucket: str) -> Decimal:
        return self._balances.get(bucket, Decimal(0))

    def total(self) -> Decimal:
        return sum(self._balances.values(), Decimal(0))

    def balance_check(self, metered_total: Decimal) -> Decimal:
        """Buckets minus the master meter. Anything but zero is a bug."""
        return self.total() - metered_total

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def _remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return _remove

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # ------------------------------------------------------------ persistence --
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        stored = data.get("balances", {})
        for bucket in self._buckets:
            self._balances[bucket] = Decimal(str(stored.get(bucket, "0")))
        # A bucket that existed before but is not configured now (a work zone
        # was removed) would otherwise strand its energy outside the sum and
        # break the invariant for ever. Fold it back into `unknown` and say so.
        for bucket, value in stored.items():
            if bucket not in self._balances and Decimal(str(value)) > 0:
                _LOGGER.warning(
                    "Bucket %r no longer configured; moving its %s kWh to unknown",
                    bucket,
                    value,
                )
                self._balances[BUCKET_UNKNOWN] += Decimal(str(value))
        self._route = data.get("route", BUCKET_UNKNOWN)
        if self._route not in self._balances:
            self._route = BUCKET_UNKNOWN
        self._floor = Decimal(str(data.get("floor", "0")))
        self._last_total = Decimal(str(data.get("last_total", "0")))

    async def _async_persist(self) -> None:
        await self._store.async_save(
            {
                "balances": {b: str(v) for b, v in self._balances.items()},
                "route": self._route,
                "floor": str(self._floor),
                "last_total": str(self._last_total),
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
        """Take the master meter's running total and file the new energy.

        Only ever the *delta*, and only ever forwards. The meter is a Riemann
        integral of a non-negative power reading, so it cannot legitimately go
        backwards; if it does, something restarted or was recalibrated and
        pretending otherwise would invent energy.
        """
        async with self._lock:
            delta = metered_total - self._last_total
            if delta <= 0:
                if delta < 0:
                    _LOGGER.debug(
                        "Master meter went backwards (%s -> %s); resyncing without accruing",
                        self._last_total,
                        metered_total,
                    )
                self._last_total = metered_total
                return
            self._balances[self._route] += delta
            self._last_total = metered_total
            await self._async_persist()
        self._notify()

    async def async_move(
        self,
        source: str,
        target: str,
        kwh: Decimal,
    ) -> Decimal:
        """Move energy between buckets. The only way energy changes bucket.

        Returns what was actually moved. Raises rather than clamping when the
        request is impossible, because silently moving less than asked is how a
        ledger ends up disagreeing with itself.
        """
        if source not in self._balances:
            raise ServiceValidationError(f"Unknown source bucket {source!r}")
        if target not in self._balances:
            raise ServiceValidationError(f"Unknown target bucket {target!r}")
        if source == target:
            raise ServiceValidationError("Source and target buckets are the same")
        if kwh <= 0:
            raise ServiceValidationError("Amount to move must be greater than zero")

        async with self._lock:
            available = self._balances[source]
            if available < kwh - TOLERANCE:
                raise ServiceValidationError(
                    f"Cannot move {kwh} kWh out of {source!r}: it holds {available} kWh"
                )
            # Never move more than is there, even inside the tolerance - the sum
            # invariant matters more than honouring the request exactly.
            moved = min(kwh, available)
            self._balances[source] -= moved
            self._balances[target] += moved

            # The floor protects energy a past session deliberately left
            # unattributed. Moving energy OUT of unknown means there is less to
            # protect, so the floor follows it down. It used to only ever rise,
            # which quietly pinned the tail sweep at zero for every later
            # session.
            if source == BUCKET_UNKNOWN and self._floor > self._balances[source]:
                self._floor = self._balances[source]

            await self._async_persist()

        self._notify()
        _LOGGER.info("Moved %s kWh from %s to %s", moved, source, target)
        return moved

    async def async_set_floor(self, value: Decimal) -> None:
        async with self._lock:
            self._floor = max(Decimal(0), value)
            await self._async_persist()
        self._notify()

    def sweepable(self, cap: Decimal) -> Decimal:
        """How much of `unknown` a confident session may claim."""
        spare = self._balances[BUCKET_UNKNOWN] - self._floor
        return max(Decimal(0), min(spare, cap))
