"""The figures worked out from other figures, as a table.

In the YAML these were forty-odd template sensors, each repeating its own
availability guard, its own unit conversion and its own rounding in Jinja. Most
of the bugs in this project's history lived in that repetition: a `| float(0)`
that turned "no reading" into "zero", a device class that silently converted a
unit, a share that rounded 99.77 up to a flat 100 and then could not move off
it.

So the arithmetic lives in `derive.py`, pure and tested, and everything here is
a declaration: what it is called, what it reads, and when it declines to answer.
Each spec's `requires` decides whether the sensor exists at all - an absent
signal removes its sensors rather than publishing a confident zero.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfMass,
    UnitOfPower,
)

from . import derive
from .const import (
    BUCKET_HOME,
    BUCKET_PUBLIC_DC,
    CONF_DISTANCE_TO_SERVICE,
    CONF_DAYS_TO_SERVICE,
    CONF_EFFICIENCY,
    CONF_ENGINE_STATE,
    CONF_PETROL_L_PER_100KM,
    CONF_SECURITY_ENTITIES,
    CONF_TRIP_CONSUMPTION,
    CONF_TYRE_PRESSURE,
    DEFAULT_PETROL_L_PER_100KM,
    KELVIN_0C,
    PETROL_KG_CO2_PER_L,
    SECTION_CAR,
    SECTION_THRESHOLDS,
    TYRE_REFERENCE_K,
)
from .helpers import numeric_state, string_state
from .trip import RUNNING_STATES

CURRENCY = "AUD"
KWH_PER_100KM = "kWh/100km"


@dataclass(frozen=True)
class DerivedSpec:
    """One derived sensor, declared rather than written out."""

    key: str
    value: Callable[[Any], Any]
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    icon: str | None = None
    precision: int | None = None
    attributes: Callable[[Any], dict[str, Any]] | None = None
    # Whether this sensor exists at all for a given configuration.
    requires: Callable[[Any], bool] = lambda _r: True
    # Entities whose changes should redraw it, beyond the ledger and session.
    watch: Callable[[Any], list[str]] = field(default=lambda _r: [])
    enabled: bool = True


# --------------------------------------------------------------- accessors --
def _distance(runtime) -> float | None:
    """Distance on the same basis as the energy: since installation.

    Never the whole-of-life odometer. Pairing that with since-install energy is
    what once put "energy used 394 kWh" beside "charged 141 kWh" on the same
    card.
    """
    return runtime.distance_since_install


def _total_kwh(runtime) -> float:
    return float(runtime.ledger.total_kwh())


def _home_kwh(runtime) -> float:
    return float(runtime.ledger.kwh(BUCKET_HOME))


def _home_solar(runtime) -> float:
    return float(runtime.ledger.balance(BUCKET_HOME).solar_kwh)


def _work_kwh(runtime) -> float:
    return sum(float(runtime.ledger.kwh(b)) for b in runtime.work_buckets)


def _free_kwh(runtime) -> float:
    return derive.free_energy(_work_kwh(runtime), _home_solar(runtime))


def _cost_total(runtime) -> float:
    return float(sum(runtime.ledger.balance(b).cost for b in runtime.buckets))


def _tyre_cold(runtime) -> dict[str, float]:
    """Each corner, corrected to a common temperature."""
    corners: dict[str, float] = {}
    pressures = runtime.config.opt(SECTION_CAR, CONF_TYRE_PRESSURE) or []
    temperatures = runtime.tyre_temperature_entities
    for index, entity in enumerate(pressures):
        temperature = (
            numeric_state(runtime.hass, temperatures[index])
            if index < len(temperatures)
            else None
        )
        value = derive.cold_pressure(
            numeric_state(runtime.hass, entity),
            temperature,
            TYRE_REFERENCE_K,
            KELVIN_0C,
        )
        if value is not None:
            corners[entity.rsplit("_", 1)[-1] or entity] = value
    return corners


def lowest_tyre(runtime) -> float | None:
    corners = _tyre_cold(runtime)
    return min(corners.values()) if corners else None


def _open_items(runtime) -> list[str]:
    """Anything unlocked or ajar, by name.

    Deliberately a read-only summary rather than a set of controls. This
    integration never sends a command to a car, and a lock entity rendered on a
    dashboard is a button that unlocks it.
    """
    entities = runtime.config.opt(SECTION_CAR, CONF_SECURITY_ENTITIES) or []
    if isinstance(entities, str):
        entities = [entities]
    open_now: list[str] = []
    for entity in entities:
        state = runtime.hass.states.get(entity)
        if state is None or state.state in ("unknown", "unavailable"):
            continue
        if state.state in ("on", "open", "unlocked", "opening"):
            open_now.append(state.attributes.get("friendly_name") or entity)
    return open_now


def _consumption_now(runtime) -> float | None:
    """The trip computer, but only while the car is actually moving.

    Gated so that a daily mean is weighted by time spent driving rather than by
    the parked hours in between. A trip computer parks at 0.0 between drives
    and that zero does most of the damage to a raw daily mean, so the guard has
    to exclude it rather than only excluding negatives. Engine state is the
    clean gate; speed is not, because it reads zero at every set of lights.
    """
    engine = string_state(runtime.hass, runtime.config.opt(SECTION_CAR, CONF_ENGINE_STATE))
    if engine is None or engine.strip().lower() not in RUNNING_STATES:
        return None
    value = numeric_state(
        runtime.hass, runtime.config.opt(SECTION_CAR, CONF_TRIP_CONSUMPTION)
    )
    # Outside this range it is a no-data sentinel rather than a reading. One
    # such sentinel put permanent daily means of -17 into long-term statistics.
    if value is None or not (4 <= value <= 30):
        return None
    return value


# -------------------------------------------------------------- the table --
SPECS: tuple[DerivedSpec, ...] = (
    # ---------------------------------------------------------------- energy --
    DerivedSpec(
        key="free_energy",
        value=lambda r: _free_kwh(r),
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
        # Free at a workplace PLUS the share of home charging the panels
        # covered. Counting only the workplace understates it: a home charge
        # running on surplus is equally free.
    ),
    DerivedSpec(
        key="free_share",
        value=lambda r: derive.share(_free_kwh(r), _total_kwh(r)),
        unit=PERCENTAGE,
        icon="mdi:gift-outline",
        precision=1,
    ),
    DerivedSpec(
        key="work_share",
        value=lambda r: derive.share(_work_kwh(r), _total_kwh(r)),
        unit=PERCENTAGE,
        icon="mdi:briefcase-outline",
        precision=1,
        requires=lambda r: bool(r.work_buckets),
    ),
    DerivedSpec(
        key="solar_share_home",
        value=lambda r: derive.share(_home_solar(r), _home_kwh(r)),
        unit=PERCENTAGE,
        icon="mdi:solar-power-variant",
        precision=1,
        requires=lambda r: r.meters.solar is not None,
    ),
    # ----------------------------------------------------------------- cost --
    DerivedSpec(
        key="cost_total",
        value=_cost_total,
        unit=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        precision=2,
        requires=lambda r: r.meters.cost is not None,
    ),
    DerivedSpec(
        key="cost_per_100km",
        value=lambda r: derive.per_100km(
            _cost_total(r), _distance(r), derive.MIN_KM_COST
        ),
        unit=f"{CURRENCY}/100km",
        icon="mdi:cash-multiple",
        precision=2,
        requires=lambda r: r.meters.cost is not None,
    ),
    DerivedSpec(
        key="home_charging_cost_rate",
        value=lambda r: float(r.meters.cost.current or 0),
        unit=f"{CURRENCY}/h",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:currency-usd",
        precision=3,
        requires=lambda r: r.meters.cost is not None,
        enabled=False,
        attributes=lambda r: {
            # What this would cost on our supply, priced continuously and
            # regardless of where the car actually is. The session keeps it or
            # discards it at the end.
            "provisional": True,
            "curtailed": r.meters._curtailed(),
        },
    ),
    DerivedSpec(
        key="free_charging_value",
        value=lambda r: derive.value_of_free(
            _work_kwh(r), _home_kwh(r), float(r.ledger.balance(BUCKET_HOME).cost)
        ),
        unit=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:gift",
        precision=2,
        requires=lambda r: bool(r.work_buckets) and r.meters.cost is not None,
        attributes=lambda r: {
            "home_rate_per_kwh": round(
                float(r.ledger.balance(BUCKET_HOME).cost) / max(_home_kwh(r), 1e-9), 4
            ),
            "work_kwh": round(_work_kwh(r), 2),
        },
    ),
    DerivedSpec(
        key="petrol_cost_avoided",
        value=lambda r: round(float(r.meters.petrol.total), 2),
        unit=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        # `total`, not `total_increasing`: Home Assistant rejects
        # total_increasing on a monetary device class outright. This only ever
        # rises, so total records exactly the same sums.
        state_class=SensorStateClass.TOTAL,
        icon="mdi:fuel",
        precision=2,
        requires=lambda r: r.meters.petrol.configured,
        attributes=lambda r: {
            "km_counted": round(float(r.meters.petrol.km_counted), 1),
            "held_price_per_litre": (
                None
                if r.meters.petrol.held_price is None
                else float(round(r.meters.petrol.held_price, 3))
            ),
        },
    ),
    DerivedSpec(
        key="net_saving",
        value=lambda r: derive.net_saving(
            round(float(r.meters.petrol.total), 2), _cost_total(r)
        ),
        unit=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:piggy-bank",
        precision=2,
        requires=lambda r: r.meters.petrol.configured and r.meters.cost is not None,
    ),
    # ---------------------------------------------------------- consumption --
    DerivedSpec(
        key="consumption_since_install",
        value=lambda r: derive.per_100km(
            _total_kwh(r), _distance(r), derive.MIN_KM_CONSUMPTION
        ),
        unit=KWH_PER_100KM,
        # kWh/100km validates only against this device class; without it the
        # entity is rejected outright and never reaches statistics.
        device_class=SensorDeviceClass.ENERGY_DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-export",
        precision=2,
        # What the driving actually costs in energy: everything that went into
        # the car over everything it drove. Includes charging losses, parked
        # drain and preconditioning, so it reads above the car's own figure -
        # that gap is real, not error.
    ),
    DerivedSpec(
        key="consumption_car",
        value=lambda r: (
            round(100 / e, 1)
            if (e := numeric_state(r.hass, r.config.opt(SECTION_CAR, CONF_EFFICIENCY)))
            else None
        ),
        unit=KWH_PER_100KM,
        device_class=SensorDeviceClass.ENERGY_DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-speed-limiter",
        precision=1,
        requires=lambda r: r.config.has(SECTION_CAR, CONF_EFFICIENCY),
    ),
    DerivedSpec(
        key="consumption_now",
        # Gated on actually driving, so a daily mean is weighted by time spent
        # moving rather than by the parked hours in between. A trip computer
        # parks at 0.0 between drives, and that zero does most of the damage to
        # a raw daily mean - so the guard has to exclude it, not just negatives.
        value=lambda r: _consumption_now(r),
        unit=KWH_PER_100KM,
        device_class=SensorDeviceClass.ENERGY_DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
        precision=1,
        requires=lambda r: r.config.has(SECTION_CAR, CONF_TRIP_CONSUMPTION)
        and r.config.has(SECTION_CAR, CONF_ENGINE_STATE),
        watch=lambda r: [
            r.config.opt(SECTION_CAR, CONF_TRIP_CONSUMPTION),
            r.config.opt(SECTION_CAR, CONF_ENGINE_STATE),
        ],
    ),
    # --------------------------------------------------------------- carbon --
    DerivedSpec(
        key="charge_co2",
        value=lambda r: float(round(r.meters.carbon_total, 3)),
        unit=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:molecule-co2",
        precision=3,
        requires=lambda r: r.meters.carbon is not None,
    ),
    DerivedSpec(
        key="co2_avoided",
        value=lambda r: derive.carbon_avoided(
            _distance(r),
            float(
                r.config.opt(
                    SECTION_THRESHOLDS,
                    CONF_PETROL_L_PER_100KM,
                    DEFAULT_PETROL_L_PER_100KM,
                )
            ),
            PETROL_KG_CO2_PER_L,
            None if r.meters.carbon_total is None else float(r.meters.carbon_total),
        ),
        unit=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:leaf",
        precision=1,
        requires=lambda r: r.meters.carbon is not None,
        attributes=lambda r: {
            "km_counted": _distance(r),
            "charging_emitted": (
                None
                if r.meters.carbon_total is None
                else float(round(r.meters.carbon_total, 3))
            ),
        },
    ),
    # ---------------------------------------------------------------- solar --
    DerivedSpec(
        key="home_solar_rate",
        value=lambda r: float(r.meters.solar.current or 0),
        unit=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        precision=3,
        requires=lambda r: r.meters.solar is not None,
        enabled=False,
    ),
    DerivedSpec(
        key="home_solar_energy",
        value=lambda r: float(round(r.meters.solar_total, 3)),
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:white-balance-sunny",
        precision=3,
        requires=lambda r: r.meters.solar is not None,
        enabled=False,
    ),
    # ---------------------------------------------------------------- tyres --
    DerivedSpec(
        key="tyre_lowest_cold",
        value=lowest_tyre,
        unit="psi",
        # NO pressure device class, deliberately. With one, Home Assistant
        # applies the unit system's preferred pressure unit and publishes kPa
        # where the arithmetic produced psi - while the ATTRIBUTES stay in psi,
        # because attributes are not unit-converted and states are. The numbers
        # on the same card then disagree with each other. This is a normalised
        # index rather than a measured pressure anyway.
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:car-tire-alert",
        precision=2,
        requires=lambda r: bool(r.config.opt(SECTION_CAR, CONF_TYRE_PRESSURE)),
        watch=lambda r: list(r.config.opt(SECTION_CAR, CONF_TYRE_PRESSURE) or []),
        attributes=lambda r: {
            "corners": _tyre_cold(r),
            "spread": (
                round(max(c.values()) - min(c.values()), 2)
                if (c := _tyre_cold(r))
                else None
            ),
            "lowest_corner": (
                min(c, key=c.get) if (c := _tyre_cold(r)) else None
            ),
            "reference_c": round(TYRE_REFERENCE_K - KELVIN_0C, 1),
        },
    ),
    DerivedSpec(
        key="tyre_drift",
        # Drift against the tyres' OWN history, never pass or fail. No placard
        # pressure is published anywhere in most cars' data and the reference
        # temperature is arbitrary, so the only honest comparison is with what
        # these tyres have been doing themselves.
        value=lambda r: r.tyre_baseline.drift(lowest_tyre(r)),
        unit="psi",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:trending-down",
        precision=2,
        requires=lambda r: bool(r.config.opt(SECTION_CAR, CONF_TYRE_PRESSURE)),
        watch=lambda r: list(r.config.opt(SECTION_CAR, CONF_TYRE_PRESSURE) or []),
        attributes=lambda r: {
            "baseline": r.tyre_baseline.median,
            "coverage": round(r.tyre_baseline.coverage, 3),
        },
    ),
    # ------------------------------------------------------------- the car --
    DerivedSpec(
        key="activity",
        # One value for a states timeline. Ordered deliberately: driving beats
        # everything because an engine running cannot be true at the same time
        # as the others, and charging beats merely being plugged in.
        value=lambda r: (
            "driving"
            if (
                (e := string_state(r.hass, r.config.opt(SECTION_CAR, CONF_ENGINE_STATE)))
                and e.strip().lower() in RUNNING_STATES
            )
            else "charging"
            if r.session.state.active
            else "plugged in"
            if r.is_plugged_in()
            else "parked"
        ),
        icon="mdi:car-clock",
        watch=lambda r: [r.config.opt(SECTION_CAR, CONF_ENGINE_STATE)],
    ),
    DerivedSpec(
        key="km_per_day",
        value=lambda r: derive.km_per_day(_distance(r), r.days_since_install),
        unit=f"{UnitOfLength.KILOMETERS}/d",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        precision=1,
    ),
    DerivedSpec(
        key="service_due_in",
        value=lambda r: (
            due[0]
            if (
                due := derive.service_due(
                    numeric_state(r.hass, r.config.opt(SECTION_CAR, CONF_DAYS_TO_SERVICE)),
                    numeric_state(
                        r.hass, r.config.opt(SECTION_CAR, CONF_DISTANCE_TO_SERVICE)
                    ),
                    derive.km_per_day(_distance(r), r.days_since_install),
                )
            )
            else None
        ),
        unit="d",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wrench-clock",
        requires=lambda r: r.config.has(SECTION_CAR, CONF_DAYS_TO_SERVICE),
        watch=lambda r: [
            r.config.opt(SECTION_CAR, CONF_DAYS_TO_SERVICE),
            r.config.opt(SECTION_CAR, CONF_DISTANCE_TO_SERVICE),
        ],
        attributes=lambda r: {
            "by_time": numeric_state(
                r.hass, r.config.opt(SECTION_CAR, CONF_DAYS_TO_SERVICE)
            ),
            "km_remaining": numeric_state(
                r.hass, r.config.opt(SECTION_CAR, CONF_DISTANCE_TO_SERVICE)
            ),
            # Which limit actually binds. A car reports both and says nothing
            # about which arrives first, which is the only part anybody wants.
            "binding": (
                due[1]
                if (
                    due := derive.service_due(
                        numeric_state(
                            r.hass, r.config.opt(SECTION_CAR, CONF_DAYS_TO_SERVICE)
                        ),
                        numeric_state(
                            r.hass, r.config.opt(SECTION_CAR, CONF_DISTANCE_TO_SERVICE)
                        ),
                        derive.km_per_day(_distance(r), r.days_since_install),
                    )
                )
                else None
            ),
        },
    ),
    DerivedSpec(
        key="secure",
        value=lambda r: "open" if _open_items(r) else "secure",
        icon="mdi:shield-car",
        requires=lambda r: bool(r.config.opt(SECTION_CAR, CONF_SECURITY_ENTITIES)),
        watch=lambda r: list(
            r.config.opt(SECTION_CAR, CONF_SECURITY_ENTITIES) or []
        ),
        attributes=lambda r: {"open_items": _open_items(r)},
    ),
    # --------------------------------------------------------------- public --
    DerivedSpec(
        key="public_dc_cost",
        value=lambda r: float(round(r.ledger.balance(BUCKET_PUBLIC_DC).cost, 2)),
        unit=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:ev-station",
        precision=2,
        attributes=lambda r: {
            "kwh": float(round(r.ledger.kwh(BUCKET_PUBLIC_DC), 3)),
            "per_kwh": (
                round(
                    float(r.ledger.balance(BUCKET_PUBLIC_DC).cost)
                    / float(r.ledger.kwh(BUCKET_PUBLIC_DC)),
                    3,
                )
                if r.ledger.kwh(BUCKET_PUBLIC_DC) > 0
                else None
            ),
        },
    ),
)
