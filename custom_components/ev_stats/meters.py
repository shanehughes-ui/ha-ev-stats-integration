"""Cost, solar and carbon, as live rates and as running totals.

Each is a rate in something-per-hour integrated over hours, so the time unit
cancels and the total comes out in dollars, kWh and kilograms. They share one
split of the car's draw between grid and panels - see `rates.py` - so the
figures cannot contradict each other.

Nothing here touches the ledger. These meters run continuously and
*provisionally*: they price every charge as though it were on our supply,
because the classifier cannot know where the car is until a session has run the
better part of an hour, and waiting would price the first part of every home
charge at zero. The session decides at the end whether its provisional figures
are kept, and a later correction restates them. That is the same path, and
there is only the one.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .classify import SUPPLY_DC
from .const import (
    BUCKET_HOME,
    CONF_FUEL_PRICE,
    CONF_ODOMETER,
    CONF_PETROL_L_PER_100KM,
    DEFAULT_PETROL_L_PER_100KM,
    PETROL_MAX_KM_STEP,
    SECTION_THRESHOLDS,
    CONF_CO2_INTENSITY,
    CONF_EXPORT_PRICE,
    CONF_HOUSE_LOAD,
    CONF_IMPORT_PRICE,
    CONF_SOLAR_CURTAILMENT,
    CONF_SOLAR_POWER,
    DOMAIN,
    SECTION_HOUSE,
    SECTION_PRICING,
)
from .helpers import numeric_state, string_state
from .integrator import ValueIntegrator
from .rates import co2_rate, cost_rate, petrol_price_per_litre, solar_rate

_LOGGER = logging.getLogger(__name__)

# A minute. The inputs are prices and PV output, which move on that scale, and
# a charge that emits no state changes for an hour still has to be priced.
RATE_SUB_INTERVAL = 60.0
# Grid carbon moves slowly and the source usually publishes every half hour.
CO2_SUB_INTERVAL = 300.0

# States a curtailment sensor uses for "export is blocked right now".
CURTAILED_STATES = frozenset({"active", "on", "true", "curtailed", "curtailing"})

STORE_VERSION = 1
# Written at most this often. The totals move continuously and losing the
# last few seconds of a rate to a hard restart is worth less than the disk.
SAVE_DELAY_S = 60


class RateMeters:
    """The three provisional meters, and whichever of them can exist."""

    def __init__(self, hass: HomeAssistant, entry_id: str, runtime) -> None:
        self.hass = hass
        self.runtime = runtime
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.meters")
        config = runtime.config

        self._pv = config.opt(SECTION_HOUSE, CONF_SOLAR_POWER)
        self._house = config.opt(SECTION_HOUSE, CONF_HOUSE_LOAD)
        self._curtailment = config.opt(SECTION_HOUSE, CONF_SOLAR_CURTAILMENT)
        self._import = config.opt(SECTION_PRICING, CONF_IMPORT_PRICE)
        self._export = config.opt(SECTION_PRICING, CONF_EXPORT_PRICE)
        self._co2 = config.opt(SECTION_PRICING, CONF_CO2_INTENSITY)
        car = runtime.charging_power_entity

        # Each meter exists only if its inputs do. Solar needs panels and a
        # house meter to subtract the rest of the house from; cost needs a
        # price. Without them the sensors are absent rather than reading zero,
        # which would say this charging was free.
        self.solar: ValueIntegrator | None = None
        if self._pv and self._house:
            self.solar = ValueIntegrator(
                hass,
                [car, self._pv, self._house],
                self._solar_now,
                RATE_SUB_INTERVAL,
                self._changed,
            )

        self.cost: ValueIntegrator | None = None
        if self._import:
            self.cost = ValueIntegrator(
                hass,
                [car, self._pv, self._house, self._import, self._export,
                 self._curtailment],
                self._cost_now,
                RATE_SUB_INTERVAL,
                self._changed,
            )

        self.petrol = PetrolMeter(hass, runtime)

        self.carbon: ValueIntegrator | None = None
        if self._co2:
            self.carbon = ValueIntegrator(
                hass,
                [car, self._pv, self._house, self._co2],
                self._co2_now,
                CO2_SUB_INTERVAL,
                self._changed,
            )

    # ------------------------------------------------------------ lifecycle --
    async def async_load(self) -> None:
        """Restore the totals.

        Without this every restart resets the cost meter to zero, and the next
        session prices itself from there - so the provisional figures a
        correction reaches for would describe only the time since the last
        reboot.
        """
        data = await self._store.async_load() or {}
        self.restore(
            Decimal(str(data.get("cost", "0"))),
            Decimal(str(data.get("solar", "0"))),
            Decimal(str(data.get("carbon", "0"))),
        )
        held = data.get("petrol_price")
        self.petrol.restore(
            Decimal(str(data.get("petrol", "0"))),
            Decimal(str(data.get("petrol_km", "0"))),
            None if held is None else Decimal(str(held)),
        )

    def async_start(self) -> None:
        for meter in (self.solar, self.cost, self.carbon):
            if meter:
                meter.async_start()
        if self.petrol.configured:
            self.petrol.async_start(self._changed)

    def async_stop(self) -> None:
        for meter in (self.solar, self.cost, self.carbon):
            if meter:
                meter.async_stop()
        self.petrol.async_stop()

    def _changed(self, _total: Decimal) -> None:
        # Debounced by Store rather than written on every tick. The callback
        # runs off the event loop when the write finally happens, so it reads
        # only plain numbers already held here and never touches hass.
        self._store.async_delay_save(self._snapshot, SAVE_DELAY_S)

    def _snapshot(self) -> dict[str, str]:
        return {
            "cost": str(self.cost_total or 0),
            "solar": str(self.solar_total or 0),
            "carbon": str(self.carbon_total or 0),
            "petrol": str(self.petrol.total),
            "petrol_km": str(self.petrol.km_counted),
            "petrol_price": (
                None if self.petrol.held_price is None else str(self.petrol.held_price)
            ),
        }

    # --------------------------------------------------------------- totals --
    @property
    def cost_total(self) -> Decimal | None:
        return self.cost.total if self.cost else None

    @property
    def solar_total(self) -> Decimal | None:
        return self.solar.total if self.solar else None

    @property
    def carbon_total(self) -> Decimal | None:
        return self.carbon.total if self.carbon else None

    def restore(self, cost: Decimal, solar: Decimal, carbon: Decimal) -> None:
        if self.cost:
            self.cost.restore(cost)
        if self.solar:
            self.solar.restore(solar)
        if self.carbon:
            self.carbon.restore(carbon)

    # ---------------------------------------------------------------- rates --
    def _car_kw(self) -> float | None:
        return numeric_state(self.hass, self.runtime.charging_power_entity)

    def _is_dc(self) -> bool:
        return self.runtime.session.state.supply == SUPPLY_DC

    def _curtailed(self) -> bool:
        value = string_state(self.hass, self._curtailment)
        return value is not None and value.strip().lower() in CURTAILED_STATES

    def _solar_now(self) -> Decimal | None:
        return solar_rate(
            self._car_kw(),
            numeric_state(self.hass, self._pv),
            numeric_state(self.hass, self._house),
            is_dc=self._is_dc(),
        )

    def _cost_now(self) -> Decimal | None:
        # Solar with no panels configured is zero rather than unknown: a house
        # with no PV really does cover none of the charge from its roof.
        solar = self._solar_now() if self.solar else Decimal(0)
        return cost_rate(
            self._car_kw(),
            solar,
            numeric_state(self.hass, self._import),
            numeric_state(self.hass, self._export),
            curtailed=self._curtailed(),
            is_dc=self._is_dc(),
        )

    def _co2_now(self) -> Decimal | None:
        """Carbon, with the solar credit gated on the car being at home.

        The one place location gates a rate, and it has to. The solar rate is
        provisional by design - it says what our roof was doing regardless of
        where the car is, because a session end can discard it. A plain
        integral has no session end to correct it, so without this gate a car
        charging 30 km away is credited with our house's panels.

        Left ungated, this was measurably wrong: 0.51 kg/h during a 6.76 kW
        workplace charge where 345 g/kWh demands 2.33.
        """
        at_home = self.runtime.charging_at() == BUCKET_HOME
        solar = self._solar_now() if (self.solar and at_home) else Decimal(0)
        return co2_rate(
            self._car_kw(),
            solar,
            numeric_state(self.hass, self._co2),
        )


class PetrolMeter:
    """What the same driving would have cost in petrol.

    Priced as the kilometres are driven, at the pump price prevailing then, so
    the past never re-prices itself. Multiplying a running distance total by
    today's price - which is the obvious way to do this - rewrites last month's
    saving every time the pump moves.

    The price is *held*. A feed that drops out freezes the last good figure
    rather than writing `unknown` into a running total, and a quoted price is
    range-checked before it is believed, so a feed that switches from cents to
    dollars reads as unavailable instead of inflating every saving a
    hundredfold.
    """

    def __init__(self, hass: HomeAssistant, runtime) -> None:
        self.hass = hass
        self.runtime = runtime
        self._odometer = runtime.config.required(CONF_ODOMETER)
        self._source = runtime.config.opt(SECTION_PRICING, CONF_FUEL_PRICE)
        self.total: Decimal = Decimal(0)
        self.held_price: Decimal | None = None
        self.km_counted: Decimal = Decimal(0)
        self._last_odometer: float | None = None
        self._unsub = None
        self._on_change = None

    @property
    def configured(self) -> bool:
        return self._source is not None

    def restore(self, total: Decimal, km: Decimal, price: Decimal | None) -> None:
        self.total = total
        self.km_counted = km
        self.held_price = price

    def async_start(self, on_change) -> None:
        self._on_change = on_change
        self._last_odometer = numeric_state(self.hass, self._odometer)
        self._refresh_price()
        watch = [self._odometer] + ([self._source] if self._source else [])
        self._unsub = async_track_state_change_event(
            self.hass, watch, self._handle_change
        )

    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _refresh_price(self) -> None:
        price = petrol_price_per_litre(numeric_state(self.hass, self._source))
        if price is not None:
            self.held_price = price

    @callback
    def _handle_change(self, _event: Event[EventStateChangedData]) -> None:
        self._refresh_price()
        odo = numeric_state(self.hass, self._odometer)
        if odo is None:
            return
        if self._last_odometer is None:
            self._last_odometer = odo
            return

        step = Decimal(str(odo - self._last_odometer))
        self._last_odometer = odo
        # Negative, zero, or absurd: a glitched reading rather than a drive.
        if not (Decimal(0) < step < Decimal(str(PETROL_MAX_KM_STEP))):
            return
        if self.held_price is None:
            # No price has ever been believed, so these kilometres cannot be
            # priced. They are deliberately not counted rather than counted at
            # zero, which would permanently understate the comparison.
            return

        litres_per_100 = Decimal(
            str(
                self.runtime.config.opt(
                    SECTION_THRESHOLDS,
                    CONF_PETROL_L_PER_100KM,
                    DEFAULT_PETROL_L_PER_100KM,
                )
            )
        )
        self.total += step * litres_per_100 / Decimal(100) * self.held_price
        self.km_counted += step
        if self._on_change:
            self._on_change(self.total)
