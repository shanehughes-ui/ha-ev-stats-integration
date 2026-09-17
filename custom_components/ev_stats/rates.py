"""What a charging car costs, emits, and takes from the panels, per hour.

Pure. Three rates that all split the car's draw the same way, which is the
point: if they split it differently, the kWh and the dollars can disagree and
there is no way to tell which is wrong.

The split is *marginal*. While the house is exporting, a kWh into the car is a
kWh not sold, so it costs the feed-in it gave up - not nothing. While the house
is importing, it costs the import price.

`None` means "cannot say", never zero. A missing import price integrated as
zero does not record an unknown cost, it records a free charge.
"""

from __future__ import annotations

from decimal import Decimal

ZERO = Decimal(0)
# Grid carbon is published in grams per kWh; everything here is kilograms.
G_PER_KG = Decimal(1000)


def _dec(value: float | Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def solar_rate(
    car_kw: float | None,
    pv_kw: float | None,
    house_load_kw: float | None,
    is_dc: bool = False,
) -> Decimal | None:
    """How much of the car's draw the panels are actually covering, in kW.

    PV output minus everything else the house is doing, capped at what the car
    is drawing. The subtraction is what makes this survive a battery.

    The rule it replaced blamed the car for any grid import at the time, and
    with a home battery that is simply wrong: on one recorded afternoon the
    panels made 3.7-5.9 kW all day against a 1.7 kW car, yet the session scored
    79% solar because the battery was soaking up the surplus and the resulting
    import was charged to the car. A battery choosing to store surplus is not
    the car using grid. Under this rule the same session scores 99%, which is
    what the panels plainly did.
    """
    car = _dec(car_kw)
    if car is None:
        return None
    # A DC charger is not on our roof by definition.
    if is_dc or car <= ZERO:
        return ZERO
    pv, load = _dec(pv_kw), _dec(house_load_kw)
    if pv is None or load is None:
        return None
    everything_else = load - car
    return min(car, max(pv - everything_else, ZERO))


def cost_rate(
    car_kw: float | None,
    solar_kw: Decimal | None,
    import_price: float | None,
    export_price: float | None,
    curtailed: bool = False,
    is_dc: bool = False,
) -> Decimal | None:
    """What charging costs per hour, if it is on our supply. AUD/h.

    Deliberately not gated on location. The classifier cannot know where the
    car is until a session has run the better part of an hour, and gating here
    would price the first part of every home charge at zero. The session
    records this provisionally and decides at the end whether to keep it.

    Never negative, and that is not only defensive. A feed-in tariff can go
    negative, and a negative forgone price would make this rate negative:

      * the cost total would run backwards, so a correction and an accrual
        would disagree about which direction money moves;
      * zero is also the physically correct answer. Every observed negative
        feed-in price here coincided with export being curtailed outright - so
        nothing could have been sold, and the forgone feed-in really is zero.

    Curtailment is therefore tested explicitly rather than inferred from the
    sign, and the final floor covers a negative *import* price too. This does
    not model charging as income.
    """
    car = _dec(car_kw)
    if car is None:
        return None
    if is_dc or car <= ZERO:
        return ZERO

    imported = _dec(import_price)
    if imported is None or solar_kw is None:
        return None

    forgone = ZERO if curtailed else max(_dec(export_price) or ZERO, ZERO)
    from_grid = max(car - solar_kw, ZERO)
    return max(from_grid * imported + solar_kw * forgone, ZERO)


def co2_rate(
    car_kw: float | None,
    solar_kw: Decimal | None,
    grid_g_per_kwh: float | None,
) -> Decimal | None:
    """Grid carbon the charging is actually incurring, kg/h.

    Uses the same grid/solar split as the cost, so a solar-covered kWh carries
    no grid carbon and the two figures cannot contradict each other.

    `solar_kw` must already be zero when the car is not at home. Our roof does
    not cover a socket on somebody else's building, and the caller is the only
    one that knows. Getting that wrong was measurable: the rate read 0.51 kg/h
    during a 6.76 kW charge at a workplace where 345 g/kWh demands 2.33, having
    subtracted 5.3 kW of our own PV from a charger 30 km away.
    """
    car = _dec(car_kw)
    grid = _dec(grid_g_per_kwh)
    if car is None or grid is None:
        return None
    if car <= ZERO or grid <= ZERO:
        return ZERO
    from_grid = max(car - (solar_kw or ZERO), ZERO)
    return from_grid * grid / G_PER_KG


def petrol_price_per_litre(raw: float | None) -> Decimal | None:
    """Normalise a pump price that might be quoted in cents.

    Fuel feeds commonly quote cents per litre (230.9) while everything here
    works in dollars. Converted by range rather than by a bare division, so a
    feed that changes units reads as unavailable instead of silently inflating
    every saving a hundredfold.
    """
    value = _dec(raw)
    if value is None:
        return None
    if Decimal(100) <= value <= Decimal(400):
        return value / Decimal(100)
    if Decimal(1) <= value <= Decimal(4):
        return value
    return None
