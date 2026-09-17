"""EV Stats — where a charge happened, what it cost, and what it saved."""

from __future__ import annotations

import logging
from decimal import Decimal, DecimalException

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_MOVE_ENERGY
from .runtime import EvStatsRuntime

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_ENTRY = "config_entry_id"
ATTR_SOURCE = "source_bucket"
ATTR_TARGET = "target_bucket"
ATTR_KWH = "kwh"

MOVE_ENERGY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY): selector.ConfigEntrySelector({"integration": DOMAIN}),
        vol.Required(ATTR_SOURCE): cv.string,
        vol.Required(ATTR_TARGET): cv.string,
        vol.Required(ATTR_KWH): vol.Coerce(float),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services.

    Here rather than in async_setup_entry so automations referencing them
    validate even when no entry is loaded.
    """

    async def _move_energy(call: ServiceCall) -> ServiceResponse:
        entry_id = call.data[ATTR_ENTRY]
        runtime: EvStatsRuntime | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if runtime is None:
            raise ServiceValidationError(f"EV Stats entry {entry_id} is not loaded")

        try:
            amount = Decimal(str(call.data[ATTR_KWH]))
        except (DecimalException, ValueError) as err:
            raise ServiceValidationError(f"{call.data[ATTR_KWH]!r} is not a number") from err

        # The guard lives in the ledger, not here, and raises rather than
        # clamping. Note that registering this as an admin service would NOT
        # provide that guard: with no user context - which is every automation
        # and script call - the admin check passes unconditionally.
        moved = await runtime.ledger.async_move(
            call.data[ATTR_SOURCE], call.data[ATTR_TARGET], amount
        )
        return {
            "moved_kwh": float(moved),
            "source_balance": float(runtime.ledger.balance(call.data[ATTR_SOURCE])),
            "target_balance": float(runtime.ledger.balance(call.data[ATTR_TARGET])),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_MOVE_ENERGY,
        _move_energy,
        schema=MOVE_ENERGY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Stats from a config entry."""
    runtime = EvStatsRuntime(hass, entry)
    await runtime.async_start()

    # Populated BEFORE forwarding: each platform's async_setup_entry reads this
    # immediately, so the order is load-bearing rather than stylistic.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime: EvStatsRuntime | None = hass.data[DOMAIN].pop(entry.entry_id, None)
        if runtime:
            runtime.async_stop()
    return unloaded
