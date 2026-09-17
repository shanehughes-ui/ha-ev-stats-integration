"""Binary sensors for EV Stats.

Three states that are easy to conflate and are not the same thing:

  * **Charging** - the car is drawing power at this instant.
  * **Session running** - a charge has been going long enough to count, and is
    being measured. This is the debounced one, and it is what every session
    figure is scoped to.
  * **GPS fresh** - the car has not moved since its position was last reported,
    which is the only condition under which that position means anything.

Keeping them separate matters because the gap between the first two is where
the pre-session tail lives, and the third is what decides whether energy can be
filed live or has to wait for the closing verdict.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CHARGE_ON_KW, CONF_ODOMETER, DOMAIN
from .helpers import numeric_state
from .runtime import EvStatsRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: EvStatsRuntime = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = [
        Charging(entry, runtime),
        SessionActive(entry, runtime),
    ]
    # No tracker means no fix, and a sensor that can only ever read "off" is
    # worse than an absent one: it looks like an answer.
    if runtime.location.configured:
        entities.append(GpsFresh(entry, runtime))
    async_add_entities(entities)


class _EvStatsBinary(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime, key: str) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="EV Stats",
        )

    @callback
    def _changed(self, *_args) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class Charging(_EvStatsBinary):
    """The car is drawing power right now."""

    _attr_translation_key = "charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "charging")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._runtime.charging_power_entity], self._changed
            )
        )

    @property
    def is_on(self) -> bool | None:
        power = numeric_state(self.hass, self._runtime.charging_power_entity)
        # None rather than False when the car cannot be read. It drops off the
        # network several times a day, and "not charging" is a claim this has no
        # basis for making.
        return None if power is None else power > CHARGE_ON_KW


class SessionActive(_EvStatsBinary):
    """A charging session is open and being measured."""

    _attr_translation_key = "session_active"
    _attr_icon = "mdi:ev-station"

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "session_active")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.session.add_listener(self._changed))

    @property
    def is_on(self) -> bool:
        return self._runtime.session.state.active

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self._runtime.session.state
        return {
            "started": state.started,
            "routed_to": state.routed_to,
            "flags": ", ".join(state.flags) or None,
        }


class GpsFresh(_EvStatsBinary):
    """The car has not moved since its position was last reported.

    Not an age in minutes: a car parked for a week has a week-old position and
    it is perfectly correct. What makes a fix untrustworthy is the car having
    driven since, and the odometer says that exactly.
    """

    _attr_translation_key = "gps_fresh"
    _attr_icon = "mdi:crosshairs-gps"
    _attr_entity_category = None

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "gps_fresh")

    async def async_added_to_hass(self) -> None:
        watched = [self._runtime.config.required(CONF_ODOMETER)]
        self.async_on_remove(
            async_track_state_change_event(self.hass, watched, self._changed)
        )
        self.async_on_remove(self._runtime.session.add_listener(self._changed))

    @property
    def is_on(self) -> bool:
        return self._runtime.location.fresh

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        location = self._runtime.location
        return {
            "km_since_fix": location.staleness_km,
            **location.as_attributes(),
        }
