"""Sensors for EV Stats.

Phase one carries a single derived sensor. It exists to prove the whole chain —
config flow to entry to entity to device — before the ledger and the integrators
land on top of it, and because distance-since-install is genuinely needed: every
consumption figure in this integration is energy over *that* distance, not over
the car's whole-of-life odometer.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_INSTALL_ODOMETER,
    CONF_ODOMETER,
    DOMAIN,
    SECTION_THRESHOLDS,
)
from .helpers import Config, numeric_state


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors for a config entry."""
    config: Config = hass.data[DOMAIN][entry.entry_id]["config"]
    async_add_entities([DistanceSinceInstall(entry, config)])


class EvStatsEntity(SensorEntity):
    """Shared identity, so everything lands under one device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, key: str) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="EV Stats",
            entry_type=None,
        )


class DistanceSinceInstall(EvStatsEntity):
    """Odometer, less whatever it read when this was set up.

    The install odometer is optional. When it has not been set the sensor is
    unavailable rather than reporting the whole-of-life odometer, because a
    consumption figure computed against that would be wrong by a factor of
    three and would look entirely plausible.
    """

    _attr_translation_key = "distance_since_install"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:map-marker-distance"

    def __init__(self, entry: ConfigEntry, config: Config) -> None:
        super().__init__(entry, "distance_since_install")
        self._config = config
        self._odometer = config.required(CONF_ODOMETER)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._odometer], self._handle_update
            )
        )
        self._recalculate()

    @callback
    def _handle_update(self, _event) -> None:
        self._recalculate()
        self.async_write_ha_state()

    @callback
    def _recalculate(self) -> None:
        odo = numeric_state(self.hass, self._odometer)
        install = self._config.opt(SECTION_THRESHOLDS, CONF_INSTALL_ODOMETER)

        if odo is None or install is None:
            self._attr_available = False
            self._attr_native_value = None
            return

        self._attr_available = True
        self._attr_native_value = max(round(odo - float(install), 1), 0)
