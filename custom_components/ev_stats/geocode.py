"""Asking OpenStreetMap what a coordinate is called.

The only part of this integration that talks to anything outside the house, so
it is kept to the smallest shape that works: one GET, no account, no key,
nothing sent but a coordinate.

It is **off by default** and has to be turned on, for the same reason
positions themselves do. A geocoder learns where you have been by being asked,
and that is a choice to make deliberately rather than a default to discover
later. With it off, nothing here ever runs and trips keep saying "Elsewhere".

Two things keep the request count near zero without any rate limiter doing the
work. Only positions outside every defined zone are ever looked up, so the
commute that makes up most trips asks nothing. And answers are cached by
rounded coordinate, so the twentieth trip to the same place is free. The
limiter below is the backstop for a first run over a full log, not the
mechanism.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .suburb import name_from

_LOGGER = logging.getLogger(__name__)

URL = "https://nominatim.openstreetmap.org/reverse"

# Nominatim's usage policy asks for a User-Agent that identifies the
# application, and refuses anonymous or browser-spoofing ones.
USER_AGENT = "ha-ev-stats (Home Assistant custom integration)"

# The suburb level. Coarser returns a city, finer starts returning street
# names - both less useful here, and the second considerably more identifying.
ZOOM = 14

# The policy limit is one request a second. Above it rather than at it, because
# the limit applies on arrival and a request does not arrive when it was sent.
MIN_INTERVAL_S = 1.2

REQUEST_TIMEOUT_S = 30

# ~110 m. Two positions this close are in the same suburb under any definition
# worth having, so rounding is what turns a daily destination into one lookup
# for its lifetime rather than one per visit.
PRECISION = 3

STORE_VERSION = 1


class Geocoder:
    """Names coordinates, remembers the answers, and paces itself."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.geocode")
        self._cache: dict[str, str | None] = {}
        self._lock = asyncio.Lock()
        self._last_request: float = 0.0

    async def async_load(self) -> None:
        self._cache = dict(await self._store.async_load() or {})

    @staticmethod
    def _key(lat: float, lon: float) -> str:
        return f"{round(lat, PRECISION)},{round(lon, PRECISION)}"

    async def async_name(self, lat: float, lon: float) -> str | None:
        """What this coordinate is called, from cache or from Nominatim.

        A cached `None` is a real answer and is kept: it means the geocoder has
        already been asked about this spot and had no name for it. Retrying
        would be one more request for the same nothing, every trip, forever.
        """
        key = self._key(lat, lon)
        if key in self._cache:
            return self._cache[key]

        async with self._lock:
            # Re-checked under the lock: two trip ends resolving at once arrive
            # here together, and the second should take the first one's answer
            # rather than ask again.
            if key in self._cache:
                return self._cache[key]

            now = self.hass.loop.time()
            wait = MIN_INTERVAL_S - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = self.hass.loop.time()

            name = await self._async_fetch(lat, lon)

        # A failed request is NOT cached - it says nothing about the place, and
        # caching it would make one timeout permanent. `_async_fetch` returns a
        # sentinel for that case so it can be told apart from a real "no name".
        if name is _FAILED:
            return None
        self._cache[key] = name
        await self._store.async_save(self._cache)
        return name

    async def _async_fetch(self, lat: float, lon: float) -> Any:
        session = async_get_clientsession(self.hass)
        params = {
            "lat": f"{lat:.5f}",
            "lon": f"{lon:.5f}",
            "format": "jsonv2",
            "zoom": str(ZOOM),
            "addressdetails": "1",
        }
        try:
            async with session.get(
                URL,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_S,
            ) as response:
                if response.status != 200:
                    _LOGGER.warning(
                        "Reverse geocode returned HTTP %s", response.status
                    )
                    return _FAILED
                # Nominatim serves JSON under its own content type often enough
                # that strict parsing rejects a perfectly good answer.
                payload = await response.json(content_type=None)
        except (TimeoutError, asyncio.TimeoutError):
            _LOGGER.warning("Reverse geocode timed out")
            return _FAILED
        except Exception as err:  # noqa: BLE001 - a name is never worth raising over
            _LOGGER.warning("Reverse geocode failed: %s", err)
            return _FAILED

        if not isinstance(payload, dict):
            return None
        return name_from(payload.get("address"))


class _Failed:
    """Distinguishes "the request did not work" from "this place has no name"."""

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return "<geocode failed>"


_FAILED = _Failed()
