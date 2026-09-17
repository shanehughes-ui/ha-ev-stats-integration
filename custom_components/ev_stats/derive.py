"""The figures worked out from other figures.

Pure. Shares, rates per 100 km, tyre normalisation, service countdown, carbon
avoided - everything whose only input is other numbers this integration already
holds.

Two habits run through all of it.

**Absent is not zero.** Every function here returns `None` when it cannot
answer. "Saved nothing" and "not measured yet" are different claims, and a
dashboard showing $0.00 for the second one is lying confidently.

**Competence bounds.** A ratio computed over too little data is not a rough
answer, it is a wrong one that looks exactly like a right one. Consumption
waits for 200 km, cost per 100 km for 50, carbon for 100. Each bound is the
distance at which the endpoint error stops dominating.
"""

from __future__ import annotations

from decimal import Decimal

ZERO = Decimal(0)

# How far the car must have gone before each figure means anything.
MIN_KM_CONSUMPTION = 200.0
MIN_KM_COST = 50.0
MIN_KM_CARBON = 100.0
# Below this a share is being computed against noise.
MIN_KWH_SHARE = 1.0

# A share of exactly 100 must mean "the residue is zero", not "it rounded up".
SHARE_CEILING = 99.9
# Under this much left over, the residue really is nothing.
RESIDUE_EPSILON = 0.005


def share(part: float | None, whole: float | None) -> float | None:
    """`part` as a percentage of `whole`, refusing to round up to 100.

    The rounding matters more than it looks. Printed to no decimals, a true
    99.7687% showed as a flat 100 - and worse, the tile could not *move*: any
    future paid charge still rounded to 100 until it exceeded half a percent of
    lifetime energy. So the figure is capped just below, and a bare 100 is
    reserved for the case where there is genuinely nothing left over.
    """
    if part is None or whole is None or whole <= MIN_KWH_SHARE:
        return None
    if whole - part <= RESIDUE_EPSILON:
        return 100.0
    return round(min(part / whole * 100, SHARE_CEILING), 1)


def per_100km(total: float | None, km: float | None, minimum: float) -> float | None:
    """A total spread over distance, once there is enough distance."""
    if total is None or km is None or km < minimum or km <= 0:
        return None
    return round(total / km * 100, 2)


def km_per_day(km: float | None, days: float | None) -> float | None:
    """Average daily distance.

    Since installation rather than this calendar month, which grows more
    accurate with time. A monthly meter divided by the day of the month is
    violently noisy on the 1st and settles just in time to reset.
    """
    if km is None or days is None or days < 7:
        return None
    return round(km / days, 1)


def cold_pressure(
    pressure: float | None,
    temperature_c: float | None,
    reference_k: float,
    kelvin_0c: float,
) -> float | None:
    """Tyre pressure corrected to a common temperature.

    Raw TPMS numbers swing with tyre temperature - by about 6 psi across a 30
    degree range, which is what the gas law predicts, so the swing is thermal
    rather than a leak. Judged against a fixed threshold, a raw reading nags all
    afternoon and misses a real slow leak in the morning. Normalised, a leak
    shows up weeks earlier as drift against the tyre's own history.

    A missing temperature is fatal to the correction, so this declines rather
    than assuming a comfortable 20 degrees: assuming would turn a hot tyre into
    a high reading and hide the very drift being looked for.
    """
    if pressure is None or temperature_c is None or pressure <= 0:
        return None
    absolute = temperature_c + kelvin_0c
    if absolute <= 0:
        return None
    return round(pressure * reference_k / absolute, 2)


def service_due(
    days_left: float | None,
    km_left: float | None,
    km_a_day: float | None,
) -> tuple[float, str] | None:
    """Which service limit actually binds, time or distance.

    A car reports both and says nothing about which arrives first, which is the
    only part anybody wants to know.
    """
    if days_left is None:
        return None
    if km_left is None or km_a_day is None or km_a_day <= 5:
        # Too little driving to convert distance into days without the answer
        # being dominated by the estimate. Time is all that can be said.
        return (round(days_left), "time")
    by_distance = km_left / km_a_day
    if by_distance < days_left:
        return (round(by_distance), "distance")
    return (round(days_left), "time")


def carbon_avoided(
    km: float | None,
    litres_per_100km: float,
    kg_per_litre: float,
    charging_kg: float | None,
) -> float | None:
    """Petrol carbon avoided, net of the grid carbon charging actually incurred.

    Net, and it needs distance before it means anything. Charging happens
    *before* the driving it pays for, so a net figure starts negative by
    construction - measured at -0.3 kg on the day it was zeroed, being a
    quarter-kilo of charging carbon against no kilometres yet driven. That is
    arithmetically true and reads as "the EV is worse than petrol".

    `charging_kg` of None makes this unavailable rather than reporting the
    gross petrol figure as though it were net.
    """
    if km is None or charging_kg is None or km < MIN_KM_CARBON:
        return None
    petrol = km * litres_per_100km / 100 * kg_per_litre
    return round(petrol - charging_kg, 1)


def free_energy(work_kwh: float, home_solar_kwh: float) -> float:
    """Energy that genuinely cost nothing.

    Free at a workplace, plus the share of home charging our own panels
    covered. Counting only the workplace understates it - a home charge running
    on surplus is equally free.
    """
    return round(work_kwh + home_solar_kwh, 3)


def value_of_free(
    free_kwh: float | None,
    home_kwh: float | None,
    home_cost: float | None,
) -> float | None:
    """What free charging was worth, at what our own supply actually costs.

    The honest counterfactual is "what would this energy have cost on our
    supply", and the answer is usually small, because home charging here is
    largely surplus solar priced at the forgone feed-in. Valuing it at the grid
    import price instead is the other reasonable reading and belongs beside
    this as an alternative, not instead of it.

    This is not the same claim as "saved against petrol". That compares a
    different car; this compares the same car charged somewhere else.
    """
    if free_kwh is None or home_kwh is None or home_cost is None or home_kwh <= 1:
        return None
    return round(free_kwh * (home_cost / home_kwh), 2)


def net_saving(petrol_avoided: float | None, cost: float | None) -> float | None:
    """Both sides on the same basis, which is the whole trick.

    An earlier version of this subtracted a lifetime cost from a monthly petrol
    figure, and the headline saving was simply wrong.
    """
    if petrol_avoided is None or cost is None:
        return None
    return round(petrol_avoided - cost, 2)
