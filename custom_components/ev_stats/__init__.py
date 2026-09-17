"""EV Stats — where a charge happened, what it cost, and what it saved."""

from __future__ import annotations

import logging
from decimal import Decimal, DecimalException
from typing import Any

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
from homeassistant.loader import async_get_integration

from .buckets import ZERO
from .const import (
    BUCKET_HOME,
    BUCKET_PUBLIC_DC,
    DOMAIN,
    EVENT_SESSION_CORRECTED,
    SERVICE_CORRECT_SESSION,
    SERVICE_DASHBOARD,
    SERVICE_IMPORT_LEGACY,
    SERVICE_LOG_DC_SESSION,
    SERVICE_MOVE_ENERGY,
)
from . import lovelace, panel, websocket
from .runtime import EvStatsRuntime
from .websocket import entity_map

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SELECT,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_ENTRY = "config_entry_id"
ATTR_SOURCE = "source_bucket"
ATTR_TARGET = "target_bucket"
ATTR_BUCKET = "bucket"
ATTR_KWH = "kwh"
ATTR_COST = "cost"
ATTR_SOLAR = "solar_kwh"
ATTR_SESSION = "session_id"
ATTR_SESSIONS_ENTITY = "sessions_entity"
ATTR_TRIPS_ENTITY = "trips_entity"
ATTR_PACK_ENTITY = "pack_entity"


def _entry_field() -> Any:
    return selector.ConfigEntrySelector({"integration": DOMAIN})


MOVE_ENERGY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY): _entry_field(),
        vol.Required(ATTR_SOURCE): cv.string,
        vol.Required(ATTR_TARGET): cv.string,
        vol.Required(ATTR_KWH): vol.Coerce(float),
        vol.Optional(ATTR_COST): vol.Coerce(float),
        vol.Optional(ATTR_SOLAR): vol.Coerce(float),
    }
)

CORRECT_SESSION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY): _entry_field(),
        vol.Required(ATTR_SESSION): cv.string,
        vol.Required(ATTR_BUCKET): cv.string,
    }
)

LOG_DC_SESSION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY): _entry_field(),
        vol.Required(ATTR_COST): vol.Coerce(float),
        vol.Optional(ATTR_SESSION): cv.string,
    }
)

DASHBOARD_SCHEMA = vol.Schema({vol.Required(ATTR_ENTRY): _entry_field()})

IMPORT_LEGACY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY): _entry_field(),
        vol.Optional(ATTR_SESSIONS_ENTITY): cv.entity_id,
        vol.Optional(ATTR_TRIPS_ENTITY): cv.entity_id,
        vol.Optional(ATTR_PACK_ENTITY): cv.entity_id,
    }
)


def _runtime(hass: HomeAssistant, entry_id: str) -> EvStatsRuntime:
    runtime: EvStatsRuntime | None = hass.data.get(DOMAIN, {}).get(entry_id)
    if runtime is None:
        raise ServiceValidationError(f"EV Stats entry {entry_id} is not loaded")
    return runtime


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (DecimalException, ValueError) as err:
        raise ServiceValidationError(f"{field} is not a number: {value!r}") from err


def _optional_decimal(call: ServiceCall, key: str) -> Decimal | None:
    """None and zero are different here.

    Absent means "work it out pro rata"; zero means "this move carries no
    cost", which is the right answer for a charge that really was free.
    """
    if key not in call.data:
        return None
    return _decimal(call.data[key], key)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services.

    Here rather than in async_setup_entry so automations referencing them
    validate even when no entry is loaded.
    """

    async def _move_energy(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass, call.data[ATTR_ENTRY])
        # The guard lives in the ledger, not here, and raises rather than
        # clamping. Note that registering this as an admin service would NOT
        # provide that guard: with no user context - which is every automation
        # and script call - the admin check passes unconditionally.
        moved = await runtime.ledger.async_move(
            call.data[ATTR_SOURCE],
            call.data[ATTR_TARGET],
            _decimal(call.data[ATTR_KWH], ATTR_KWH),
            _optional_decimal(call, ATTR_COST),
            _optional_decimal(call, ATTR_SOLAR),
        )
        return {
            "moved_kwh": float(moved.kwh),
            "moved_cost": float(moved.cost),
            "source_balance": float(runtime.ledger.kwh(call.data[ATTR_SOURCE])),
            "target_balance": float(runtime.ledger.kwh(call.data[ATTR_TARGET])),
        }

    async def _correct_session(call: ServiceCall) -> ServiceResponse:
        """Reattribute a logged session, and everything that goes with it."""
        runtime = _runtime(hass, call.data[ATTR_ENTRY])
        session_id = call.data[ATTR_SESSION]
        target = call.data[ATTR_BUCKET]

        record = runtime.logs.session(session_id)
        if record is None:
            raise ServiceValidationError(f"No logged session with id {session_id!r}")

        source = record.get("bucket")
        if source is None:
            raise ServiceValidationError(
                f"Session {session_id!r} has no bucket recorded to move it out of"
            )
        if source == target:
            return {"changed": False, "reason": f"already attributed to {target}"}

        kwh = _decimal(record.get("kwh") or 0, "kwh")
        # What this session contributed to the source bucket's cost - zero
        # unless it was filed as home, because nowhere else bills through our
        # meter.
        held_cost = _decimal(record.get("cost") or 0, "cost")
        held_solar = _decimal(record.get("solar_kwh") or 0, "solar_kwh")
        # What it should contribute where it is going. The provisional figures
        # are what the session WOULD have cost at home, recorded at the time
        # precisely so a later correction has something to reach for.
        prov_cost = _decimal(record.get("prov_cost") or 0, "prov_cost")
        prov_solar = _decimal(record.get("prov_solar") or 0, "prov_solar")
        now_cost = prov_cost if target == BUCKET_HOME else ZERO
        now_solar = prov_solar if target == BUCKET_HOME else ZERO

        if kwh > ZERO:
            # One call, one lock. Moving the energy and restating its cost are
            # two steps that must not come apart - leaving the cost behind is
            # the exact failure this integration was written to stop.
            await runtime.ledger.async_reattribute(
                source, target, kwh, held_cost, held_solar, now_cost, now_solar
            )

        await runtime.logs.async_correct_session(
            session_id, target, now_cost, now_solar
        )
        hass.bus.async_fire(
            EVENT_SESSION_CORRECTED,
            {"id": session_id, "from": source, "to": target, "kwh": float(kwh)},
        )
        _LOGGER.info(
            "Session %s reattributed from %s to %s (%s kWh)",
            session_id,
            source,
            target,
            kwh,
        )
        return {
            "changed": True,
            "from": source,
            "to": target,
            "kwh": float(kwh),
            "cost": float(now_cost),
        }

    async def _log_dc_session(call: ServiceCall) -> ServiceResponse:
        """Attach a price to charging that was billed at the pump.

        The energy is already in the ledger - the car metered it like any
        other. What no sensor here can know is what it cost, so this is the one
        figure that has to be typed in.
        """
        runtime = _runtime(hass, call.data[ATTR_ENTRY])
        cost = _decimal(call.data[ATTR_COST], ATTR_COST)
        if cost <= ZERO:
            raise ServiceValidationError("A public charging session cost something")

        await runtime.ledger.async_adjust(BUCKET_PUBLIC_DC, cost=cost)

        session_id = call.data.get(ATTR_SESSION)
        if session_id:
            record = runtime.logs.session(session_id)
            if record is None:
                raise ServiceValidationError(
                    f"No logged session with id {session_id!r}"
                )
            # Priced, not reattributed. Marking this as a correction would
            # say the classifier got it wrong, when all that happened is that
            # somebody typed in what the charger billed.
            await runtime.logs.async_price_session(session_id, cost)

        balance = runtime.ledger.balance(BUCKET_PUBLIC_DC)
        return {
            "cost_total": float(balance.cost),
            "kwh_total": float(balance.kwh),
            "per_kwh": (
                round(float(balance.cost / balance.kwh), 3)
                if balance.kwh > ZERO
                else None
            ),
        }

    async def _import_legacy(call: ServiceCall) -> ServiceResponse:
        """Take the logs off a YAML install of this system.

        Reads the lists straight out of the old template sensors' attributes.
        Idempotent by id, so running it twice adds nothing the second time.

        It imports the LOGS only, never the bucket balances. Seeding those from
        the same records would double-count against energy this integration has
        already metered for itself, and there is no way to tell the two apart
        afterwards.
        """
        runtime = _runtime(hass, call.data[ATTR_ENTRY])
        return await runtime.async_import_legacy(
            call.data.get(ATTR_SESSIONS_ENTITY),
            call.data.get(ATTR_TRIPS_ENTITY),
            call.data.get(ATTR_PACK_ENTITY),
        )

    async def _dashboard(call: ServiceCall) -> ServiceResponse:
        """Build a Lovelace view carrying this installation's entity ids.

        Returned rather than written anywhere. A service that edited a
        dashboard in place would be rewriting something the user owns, and the
        useful thing here is the YAML - paste it into a dashboard's raw
        configuration editor, or take the cards you want.
        """
        entry_id = call.data[ATTR_ENTRY]
        runtime = _runtime(hass, entry_id)
        view = lovelace.build_view(
            runtime.entry.title, entity_map(hass, entry_id), runtime.buckets
        )
        return {"yaml": lovelace.to_yaml(view), "cards": sum(
            len(section["cards"]) for section in view["sections"]
        )}

    for name, handler, schema in (
        (SERVICE_MOVE_ENERGY, _move_energy, MOVE_ENERGY_SCHEMA),
        (SERVICE_DASHBOARD, _dashboard, DASHBOARD_SCHEMA),
        (SERVICE_CORRECT_SESSION, _correct_session, CORRECT_SESSION_SCHEMA),
        (SERVICE_LOG_DC_SESSION, _log_dc_session, LOG_DC_SESSION_SCHEMA),
        (SERVICE_IMPORT_LEGACY, _import_legacy, IMPORT_LEGACY_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )

    # Registered here rather than per entry: the panel lists every configured
    # car, so one command serves all of them and registering it twice raises.
    websocket.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Stats from a config entry."""
    runtime = EvStatsRuntime(hass, entry)
    await runtime.async_start()

    integration = await async_get_integration(hass, DOMAIN)
    await panel.async_register(hass, str(integration.version))

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
        # The panel belongs to the integration, not to one car, so it goes only
        # when the last one does.
        if not hass.data[DOMAIN]:
            panel.async_remove(hass)
    return unloaded
