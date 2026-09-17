"""Sensors for EV Stats."""

from __future__ import annotations

from decimal import Decimal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfLength
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
from .runtime import EvStatsRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: EvStatsRuntime = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        ChargeEnergy(entry, runtime),
        EnergyTotal(entry, runtime),
        BalanceCheck(entry, runtime),
        UnattributedFloor(entry, runtime),
        DistanceSinceInstall(entry, runtime.config),
    ]
    entities += [BucketEnergy(entry, runtime, b) for b in runtime.buckets]
    async_add_entities(entities)


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
        )


class _LedgerEntity(EvStatsEntity):
    """Redraws whenever the ledger changes."""

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime, key: str) -> None:
        super().__init__(entry, key)
        self._runtime = runtime

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.ledger.add_listener(self._changed))
        self._changed()

    @callback
    def _changed(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class ChargeEnergy(_LedgerEntity):
    """Everything that went into the car, as metered at the wall."""

    _attr_translation_key = "charge_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    # TOTAL, not TOTAL_INCREASING - see BucketEnergy for why the distinction
    # matters so much here. This one only ever rises, but keeping the whole
    # ledger on one state class means no sensor here can be read as a meter
    # swap when it is really a correction.
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "charge_energy")

    @property
    def native_value(self) -> float:
        return float(round(self._runtime.charge_energy.total, 3))


class BucketEnergy(_LedgerEntity):
    """Energy attributed to one place."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    # This is the one that has to be TOTAL.
    #
    # Energy moves BETWEEN buckets, so a bucket goes down as well as up, and a
    # TOTAL_INCREASING series reads any decrease as a meter reset - the
    # long-term statistics keep climbing and a correction inflates the day it
    # was made. There is even a 10% tolerance in that check, which is why small
    # reclassifications looked fine and large ones did not.
    #
    # No last_reset either: for TOTAL without one, the first value seen becomes
    # the statistics zero-point, which is exactly what a ledger wants.
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3

    def __init__(
        self, entry: ConfigEntry, runtime: EvStatsRuntime, bucket: str
    ) -> None:
        super().__init__(entry, runtime, f"energy_{bucket}")
        self._bucket = bucket
        self._attr_translation_key = "bucket_energy"
        self._attr_translation_placeholders = {"bucket": bucket.replace("_", " ")}
        self._attr_name = bucket.replace("_", " ").title()

    @property
    def native_value(self) -> float:
        return float(round(self._runtime.ledger.balance(self._bucket), 3))

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "bucket": self._bucket,
            "receiving": str(self._runtime.ledger.route == self._bucket),
        }


class EnergyTotal(_LedgerEntity):
    """The buckets, summed."""

    _attr_translation_key = "energy_total"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "energy_total")

    @property
    def native_value(self) -> float:
        return float(round(self._runtime.ledger.total(), 3))


class BalanceCheck(_LedgerEntity):
    """The invariant, exposed.

    Buckets minus the master meter. Anything other than zero means attribution
    has lost or invented energy, and it is worth a sensor rather than a comment
    because it is the one number that proves the rest can be trusted.
    """

    _attr_translation_key = "balance_check"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:check-decagram"
    _attr_suggested_display_precision = 3

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "balance_check")

    @property
    def native_value(self) -> float:
        return float(
            round(self._runtime.ledger.balance_check(self._runtime.charge_energy.total), 3)
        )


class UnattributedFloor(_LedgerEntity):
    """Energy past sessions deliberately left unattributed."""

    _attr_translation_key = "unattributed_floor"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "unattributed_floor")

    @property
    def native_value(self) -> float:
        return float(round(self._runtime.ledger.floor, 3))


class DistanceSinceInstall(EvStatsEntity):
    """Odometer, less whatever it read when this was set up.

    Unavailable rather than falling back to the whole-of-life odometer when the
    install reading has not been set: a consumption figure computed against a
    car's entire history would be wrong by a factor of three and would look
    entirely plausible.
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
