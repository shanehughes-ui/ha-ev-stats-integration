"""One WebSocket command, which is everything the dashboard needs.

The panel could read most of this out of `hass.states`, and for the live
numbers it does. What it cannot get that way is the *logs* - sessions, trips
and pack measurements live in a Store precisely so they are not being rewritten
to the recorder as entity attributes - and it cannot know which entity id
belongs to which figure.

That second problem is the whole reason this exists. Entity ids here are built
from the device name, so a car called "EX2" produces `sensor.ex2_free_share`
and one called "the van" produces something else entirely. A dashboard with
those ids typed into it would work on exactly one installation. So the ids are
resolved from the entity registry, by the unique id the integration itself
assigned, and handed to the page.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_BATTERY, DOMAIN
from .store import MAX_SESSIONS, MAX_TRIPS

WS_DASHBOARD = f"{DOMAIN}/dashboard"

# The page draws tables and a chart or two; it does not need every row ever
# recorded, and sending them all would make opening the panel slow on a phone.
PAGE_SESSIONS = 60
PAGE_TRIPS = 120
PAGE_ESTIMATES = 60


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_dashboard)


def entity_map(hass: HomeAssistant, entry_id: str) -> dict[str, str]:
    """Which entity id carries which figure.

    Keyed by the suffix of the unique id this integration set, so the mapping
    survives the user renaming the device, renaming an entity, or Home
    Assistant's own slug collisions - none of which change a unique id.
    """
    registry = er.async_get(hass)
    prefix = f"{entry_id}_"
    found: dict[str, str] = {}
    for entity in er.async_entries_for_config_entry(registry, entry_id):
        if entity.unique_id.startswith(prefix):
            found[entity.unique_id[len(prefix) :]] = entity.entity_id
    return found


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_DASHBOARD,
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_dashboard(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Everything the panel needs to draw itself, for every configured car."""
    runtimes = hass.data.get(DOMAIN, {})
    wanted = msg.get("entry_id")

    cars = []
    for entry_id, runtime in runtimes.items():
        if wanted and entry_id != wanted:
            continue
        logs = runtime.logs
        cars.append(
            {
                "entry_id": entry_id,
                "title": runtime.entry.title,
                "buckets": list(runtime.buckets),
                "work_buckets": list(runtime.work_buckets),
                "entities": entity_map(hass, entry_id),
                # The car's own entities, not this integration's. The panel
                # charts state of charge, and that one belongs to whichever
                # vehicle integration provides it - it cannot be guessed.
                "sources": {
                    "battery": runtime.config.required(CONF_BATTERY),
                },
                # Newest last, the order a table reads in. Trimmed to a page:
                # the store holds far more, and sending all of it would make
                # opening the panel slow for no benefit.
                "sessions": logs.sessions(PAGE_SESSIONS),
                "trips": logs.trips(PAGE_TRIPS),
                "estimates": logs.estimates(PAGE_ESTIMATES),
                "totals": {
                    "sessions": logs.session_count,
                    "trips": logs.trip_count,
                    "session_cap": MAX_SESSIONS,
                    "trip_cap": MAX_TRIPS,
                },
                "buckets_detail": {
                    bucket: {
                        "kwh": float(round(runtime.ledger.balance(bucket).kwh, 3)),
                        "cost": float(round(runtime.ledger.balance(bucket).cost, 4)),
                        "solar_kwh": float(
                            round(runtime.ledger.balance(bucket).solar_kwh, 3)
                        ),
                    }
                    for bucket in runtime.buckets
                },
            }
        )

    connection.send_result(msg["id"], {"cars": cars})
