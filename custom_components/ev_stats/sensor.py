"""Sensors for EV Stats."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfLength, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_INSTALL_ODOMETER,
    CONF_ODOMETER,
    DOMAIN,
    ESTIMATES_IN_ATTRIBUTES,
    SECTION_THRESHOLDS,
    SESSIONS_IN_ATTRIBUTES,
    TRIPS_IN_ATTRIBUTES,
)
from .derived import SPECS, DerivedSpec
from .helpers import Config, numeric_state
from .runtime import EvStatsRuntime
from .session import VOLTS_FLOOR, VOLTS_SENTINEL


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
        SessionEnergy(entry, runtime),
        SessionDuration(entry, runtime),
        SessionClassification(entry, runtime),
        ChargeSessions(entry, runtime),
        PackEstimate(entry, runtime),
    ]
    entities += [BucketEnergy(entry, runtime, b) for b in runtime.buckets]

    # Each optional signal removes its own sensors rather than publishing a
    # confident zero. A house-supply ratio with no house meter behind it would
    # read 0.00 and mean "charged somewhere else", every time.
    if runtime.baseline is not None:
        entities.append(HouseSupplyRatio(entry, runtime))
        entities.append(HouseBaselineSensor(entry, runtime))
    if runtime.location.configured:
        entities.append(TrustedLocation(entry, runtime))
        entities.append(GpsStaleness(entry, runtime))
    # No engine signal means no trips can be detected at all, so a trip log
    # that could only ever read zero is not published.
    if runtime.trips.configured:
        entities.append(Trips(entry, runtime))

    # The declared ones. A spec whose inputs are not configured produces no
    # entity at all rather than one reading zero - the same rule as everything
    # above, applied from a table instead of by hand.
    entities += [
        DerivedSensor(entry, runtime, spec) for spec in SPECS if spec.requires(runtime)
    ]

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
        return float(round(self._runtime.ledger.kwh(self._bucket), 3))

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        held = self._runtime.ledger.balance(self._bucket)
        return {
            "bucket": self._bucket,
            "receiving": str(self._runtime.ledger.route == self._bucket),
            # What this energy cost, and how much of it the panels covered.
            # They move with the energy when a session is reattributed, which
            # is the whole reason a bucket is a record rather than a number.
            "cost": float(round(held.cost, 4)),
            "solar_kwh": float(round(held.solar_kwh, 3)),
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
        return float(round(self._runtime.ledger.total_kwh(), 3))


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


class _SessionEntity(EvStatsEntity):
    """Redraws on a session change and on every metered kWh.

    Both, because they move independently: the ledger ticks whenever energy
    accrues, while opening, closing and each new piece of evidence come from
    the session manager. A sensor listening to only one of them would go stale
    for whole sessions at a time.
    """

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime, key: str) -> None:
        super().__init__(entry, key)
        self._runtime = runtime

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.session.add_listener(self._changed))
        self.async_on_remove(self._runtime.ledger.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class SessionEnergy(_SessionEntity):
    """What this session has taken so far.

    Deliberately carries no energy device class. It is a gauge that returns to
    zero when the session ends, and a device class would offer it to the energy
    dashboard as a meter, where a drop to zero reads as a meter reset.
    """

    _attr_translation_key = "session_energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"
    _attr_suggested_display_precision = 3

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "session_energy")

    @property
    def native_value(self) -> float:
        return float(round(self._runtime.session.session_kwh, 3))


class SessionDuration(_SessionEntity):
    """How long the current session has been running."""

    _attr_translation_key = "session_duration"
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_icon = "mdi:timer-outline"
    _attr_suggested_display_precision = 2

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "session_duration")

    @property
    def native_value(self) -> float:
        return round(self._runtime.session.duration_h, 4)


class HouseSupplyRatio(_SessionEntity):
    """How much of the car's energy our own house meter accounted for.

    The one signal that depends on no location data at all, which is exactly
    why it is worth having: ~1.0 means this house supplied it, ~0.0 means
    somewhere else did.

    Unknown - not zero - whenever it cannot honestly answer.
    """

    _attr_translation_key = "house_supply_ratio"
    _attr_icon = "mdi:home-lightning-bolt"
    _attr_suggested_display_precision = 3

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "house_supply_ratio")

    @property
    def native_value(self) -> float | None:
        ratio = self._runtime.session.house_ratio
        return None if ratio is None else float(ratio)


class HouseBaselineSensor(_SessionEntity):
    """What the house draws with the car idle, as a rolling median."""

    _attr_translation_key = "house_baseline"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "house_baseline")

    @property
    def native_value(self) -> float | None:
        return self._runtime.baseline.median if self._runtime.baseline else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        baseline = self._runtime.baseline
        if baseline is None:
            return {}
        return {
            "samples": baseline.samples,
            # A window still filling is not a baseline. This is what the
            # classifier checks before it is willing to lean on the median.
            "coverage": round(baseline.coverage, 3),
            "usable": baseline.usable,
        }


class SessionClassification(_SessionEntity):
    """Where this session is happening, and why that was concluded."""

    _attr_translation_key = "session_classification"
    _attr_icon = "mdi:scale-balance"

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "session_classification")

    @property
    def native_value(self) -> str:
        return self._runtime.session.classification.verdict

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        session = self._runtime.session
        verdict = session.classification
        location = self._runtime.location
        return {
            "bucket": verdict.bucket,
            "confidence": verdict.confidence,
            # The branch that fired, in words. Without it the only way to work
            # out why a session was called `conflict` was to reconstruct the
            # decision table by hand from the other attributes.
            "reason": verdict.reason,
            "gps_zone": location.zone(),
            "gps_fresh": location.fresh,
            "gps_stale_km": location.staleness_km,
            "peak_kw": session.state.peak_kw,
            # Corroboration only, never a classifier input: a 10 A socket at
            # work fingerprints identically to a 10 A socket at home.
            "peak_amps": session.state.peak_amps,
            "min_volts": (
                session.state.min_volts
                if VOLTS_FLOOR < session.state.min_volts < VOLTS_SENTINEL
                else None
            ),
            "supply": session.state.supply,
            "routed_to": session.state.routed_to,
        }


class TrustedLocation(_SessionEntity):
    """Where the car is, named only when that can be trusted."""

    _attr_translation_key = "trusted_location"
    _attr_icon = "mdi:map-marker-check"

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "trusted_location")

    @property
    def native_value(self) -> str:
        return self._runtime.location.zone()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return self._runtime.location.as_attributes()


class GpsStaleness(_SessionEntity):
    """How far the car has driven since its position was last reported."""

    _attr_translation_key = "gps_staleness"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_icon = "mdi:map-marker-question"
    _attr_suggested_display_precision = 1
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "gps_staleness")

    @property
    def native_value(self) -> float | None:
        # None, never zero. Defaulting the fix to zero here once published the
        # entire odometer as the distance travelled since the fix.
        return self._runtime.location.staleness_km


class _LogEntity(EvStatsEntity):
    """Redraws when a log changes, which is rarely."""

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime, key: str) -> None:
        super().__init__(entry, key)
        self._runtime = runtime

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.logs.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class ChargeSessions(_LogEntity):
    """Every charge, and how it was attributed.

    The state is a count rather than the last session's id, which is what the
    YAML published. An id is a timestamp and says nothing on a dashboard; a
    count of sessions does, and the id is still on the record.

    The log is the tuning data for the classifier, so a corrected row keeps
    what it was corrected *from*. A verdict that had to be overridden is the
    only kind that teaches anything.
    """

    _attr_translation_key = "charge_sessions"
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "charge_sessions")

    @property
    def native_value(self) -> int:
        return self._runtime.logs.session_count

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        logs = self._runtime.logs
        recent = logs.sessions(SESSIONS_IN_ATTRIBUTES)
        last = recent[-1] if recent else None
        return {
            # A slice, not the whole log. The recorder rewrites an entity's
            # entire attribute blob on every state change, so what goes here
            # has to stay small - the Store behind it holds the rest.
            "sessions": recent,
            "last_id": last.get("id") if last else None,
            "last_bucket": last.get("bucket") if last else None,
            "unattributed": sum(
                1 for s in logs.sessions() if s.get("bucket") == "unknown"
            ),
            "corrected": sum(1 for s in logs.sessions() if s.get("corrected")),
        }


class Trips(_LogEntity):
    """Every completed drive."""

    _attr_translation_key = "trips"
    _attr_icon = "mdi:road-variant"

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "trips")

    @property
    def native_value(self) -> int:
        return self._runtime.logs.trip_count

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        logs = self._runtime.logs
        return {
            "trips": logs.trips(TRIPS_IN_ATTRIBUTES),
            "total_km": round(sum(t.get("km") or 0 for t in logs.trips()), 1),
        }


class PackEstimate(_LogEntity):
    """Usable pack size, measured by charges that spanned a wide SoC range.

    The state is the rolling mean, never the latest single measurement. Real
    ones scatter about 1.5% while the thing being looked for is roughly 2% a
    year, so one wide session must not be able to move a converged figure on
    its own - the YAML wrote the latest reading over the top of the previous
    one and kept no history at all.
    """

    _attr_translation_key = "pack_estimate"
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-heart-variant"
    _attr_suggested_display_precision = 2

    def __init__(self, entry: ConfigEntry, runtime: EvStatsRuntime) -> None:
        super().__init__(entry, runtime, "pack_estimate")

    @property
    def native_value(self) -> float | None:
        return self._runtime.logs.pack_rolling_mean

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        logs = self._runtime.logs
        estimates = logs.estimates(ESTIMATES_IN_ATTRIBUTES)
        latest = estimates[-1] if estimates else None
        return {
            "estimates": estimates,
            "latest": latest.get("implied") if latest else None,
            "sample_count": len(logs.estimates()),
            # None until there are enough measurements spread over enough time.
            # A slope through four readings taken in one month extrapolates a
            # season into a decade.
            "kwh_per_year": logs.pack_trend,
        }


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


class DerivedSensor(EvStatsEntity):
    """Renders one `DerivedSpec`.

    Everything that was repeated forty times in Jinja - the guard, the unit,
    the rounding - happens once, here. A spec that returns None publishes
    `unknown`, which is the honest state for a figure that has not got enough
    data behind it yet, and is not the same claim as zero.
    """

    def __init__(
        self, entry: ConfigEntry, runtime: EvStatsRuntime, spec: DerivedSpec
    ) -> None:
        super().__init__(entry, spec.key)
        self._runtime = runtime
        self._spec = spec
        self._attr_translation_key = spec.key
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_device_class = spec.device_class
        self._attr_state_class = spec.state_class
        self._attr_icon = spec.icon
        self._attr_suggested_display_precision = spec.precision
        self._attr_entity_registry_enabled_default = spec.enabled

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.ledger.add_listener(self._changed))
        self.async_on_remove(self._runtime.logs.add_listener(self._changed))
        self.async_on_remove(self._runtime.session.add_listener(self._changed))
        watched = [e for e in self._spec.watch(self._runtime) if e]
        if watched:
            self.async_on_remove(
                async_track_state_change_event(self.hass, watched, self._changed)
            )

    @callback
    def _changed(self, *_args) -> None:
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def native_value(self):
        try:
            return self._spec.value(self._runtime)
        except (TypeError, ValueError, ZeroDivisionError, ArithmeticError):
            # One misbehaving input must not take the platform down with it.
            # Unknown is the right state for a figure that could not be worked
            # out, and the entity recovers on the next update.
            return None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        if self._spec.attributes is None:
            return None
        try:
            return self._spec.attributes(self._runtime)
        except (TypeError, ValueError, ZeroDivisionError, ArithmeticError):
            return None
