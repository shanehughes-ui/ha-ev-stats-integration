"""The routing select.

Which bucket charging energy accrues into right now.

Left on `unknown` it behaves exactly as the original YAML did: everything lands
unattributed and is moved once, at the end, when something has proven where the
car was. Pointed at a real bucket it accrues live, which is what the session
logic does once a location is provable.

Exposed as a select rather than kept internal because it is the single most
useful thing to be able to see and override by hand when the classifier gets it
wrong, and because a stuck route is visible at a glance.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .runtime import EvStatsRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: EvStatsRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RouteSelect(entry, runtime)])


class RouteSelect(SelectEntity):
    """Where new charging energy is filed."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "route"
    _attr_icon = "mdi:call-split"
    _attr_entity_category = None

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_route"
        self._attr_options = list(runtime.buckets)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="EV Stats",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.ledger.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def current_option(self) -> str:
        return self._runtime.ledger.route

    async def async_select_option(self, option: str) -> None:
        await self._runtime.ledger.async_set_route(option)
