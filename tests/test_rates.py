"""Cost, solar and carbon per hour.

All three split the car's draw between grid and panels the same way. If they
split it differently the kWh and the dollars disagree and nothing says which is
wrong, so most of these tests are really about that one split.

    python -m pytest tests/test_rates.py
"""

from __future__ import annotations

from decimal import Decimal

import pure

rates = pure.load("rates")

solar_rate = rates.solar_rate
cost_rate = rates.cost_rate
co2_rate = rates.co2_rate
petrol_price_per_litre = rates.petrol_price_per_litre

D = Decimal


# ------------------------------------------------------------------ solar --
def test_panels_covering_the_whole_car() -> None:
    """5 kW of PV, a 1.7 kW car, and 1.7 kW of house load: all of it is solar."""
    assert solar_rate(1.7, 5.0, 1.7) == D("1.7")


def test_panels_covering_part_of_it() -> None:
    """2 kW of PV against a 6 kW car with nothing else running."""
    assert solar_rate(6.0, 2.0, 6.0) == D(2)


def test_the_rest_of_the_house_gets_the_panels_first() -> None:
    """5 kW of PV, a 4 kW oven and a 2 kW car: the car gets the 1 kW left.

    The house is served first because it was already running. Only the surplus
    is the car's to claim.
    """
    assert solar_rate(2.0, 5.0, 6.0) == 1


def test_a_house_using_more_than_it_makes_leaves_the_car_nothing() -> None:
    assert solar_rate(2.0, 5.0, 9.0) == 0


def test_a_battery_soaking_up_surplus_is_not_the_car_using_grid() -> None:
    """The bug this rule replaced, from a recorded afternoon.

    Panels making 5.9 kW against a 1.7 kW car, but the house shows a large load
    because the home battery is storing the surplus. The old rule blamed the
    car for the resulting import and scored the session 79% solar. Subtracting
    only what the house consumes BESIDES the car gives the right answer.
    """
    # House load here is the car plus its own 1.0 kW; the battery is not a load
    # this sees, and the PV figure already exceeds both.
    assert solar_rate(1.7, 5.9, 2.7) == D("1.7")


def test_a_dc_charger_is_not_on_our_roof() -> None:
    assert solar_rate(50.0, 5.0, 1.0, is_dc=True) == 0


def test_no_panels_configured_is_unknown_not_zero() -> None:
    assert solar_rate(6.0, None, 2.0) is None
    assert solar_rate(6.0, 5.0, None) is None


def test_a_car_drawing_nothing_takes_no_solar() -> None:
    assert solar_rate(0.0, 5.0, 2.0) == 0


def test_an_unreadable_car_says_nothing() -> None:
    assert solar_rate(None, 5.0, 2.0) is None


# ------------------------------------------------------------------- cost --
def test_all_grid_costs_the_import_price() -> None:
    assert cost_rate(6.0, D(0), 0.30, 0.07) == D("1.80")


def test_all_solar_costs_the_feed_in_it_gave_up() -> None:
    """Not zero. A kWh into the car while exporting is a kWh not sold."""
    assert cost_rate(6.0, D(6), 0.30, 0.07) == D("0.42")


def test_a_mixture_is_split_at_the_same_boundary_as_the_solar_rate() -> None:
    assert cost_rate(6.0, D(2), 0.30, 0.10) == D("1.40")


def test_a_negative_feed_in_price_floors_at_zero() -> None:
    """Every observed negative feed-in here coincided with export curtailed.

    Nothing could have been sold, so the forgone feed-in really is zero - and a
    negative rate would make the cost total run backwards, so an accrual and a
    correction would disagree about which way money moves.
    """
    assert cost_rate(6.0, D(6), 0.30, -0.05) == 0


def test_curtailment_makes_the_solar_part_free() -> None:
    assert cost_rate(6.0, D(6), 0.30, 0.07, curtailed=True) == 0


def test_a_negative_import_price_is_not_income() -> None:
    """Allowed by some tariffs. This does not model charging as earning money."""
    assert cost_rate(6.0, D(0), -0.02, 0.07) == 0


def test_dc_charging_is_not_priced_here() -> None:
    """It is billed at the pump, and that number is typed in separately."""
    assert cost_rate(50.0, D(0), 0.30, 0.07, is_dc=True) == 0


def test_no_import_price_is_unknown_not_free() -> None:
    """Integrated as zero, a missing price records a free charge."""
    assert cost_rate(6.0, D(0), None, 0.07) is None


def test_a_missing_feed_in_price_treats_the_solar_part_as_free() -> None:
    """Unlike the import price: there is no forgone revenue to know about."""
    assert cost_rate(6.0, D(6), 0.30, None) == 0


# ----------------------------------------------------------------- carbon --
def test_grid_kilowatts_carry_grid_carbon() -> None:
    """6.76 kW at 345 g/kWh is 2.33 kg/h."""
    assert round(co2_rate(6.76, D(0), 345.0), 2) == D("2.33")


def test_solar_kilowatts_carry_none() -> None:
    assert co2_rate(6.0, D(6), 345.0) == 0


def test_the_solar_credit_must_be_zeroed_away_from_home() -> None:
    """The caller gates this, and getting it wrong was measurable.

    A 6.76 kW workplace charge read 0.51 kg/h where 345 g/kWh demands 2.33,
    because our own roof's output was being subtracted from a socket 30 km
    away. Passing zero is what "not at home" looks like here.
    """
    away = co2_rate(6.76, D(0), 345.0)
    at_home = co2_rate(6.76, D("5.3"), 345.0)
    assert round(away, 2) == D("2.33")
    # Roughly a fifth of the truth - which is what was actually being reported.
    assert round(at_home, 1) == D("0.5")


def test_no_carbon_intensity_is_unknown() -> None:
    assert co2_rate(6.0, D(0), None) is None


def test_the_three_rates_agree_on_the_same_split() -> None:
    """The property that matters: one boundary, used three times."""
    car, pv, load = 6.0, 4.0, 6.0
    solar = solar_rate(car, pv, load)
    assert solar == D(4)
    from_grid = D(str(car)) - solar
    assert cost_rate(car, solar, 0.30, 0.0) == from_grid * D("0.30")
    assert co2_rate(car, solar, 1000.0) == from_grid


# ----------------------------------------------------------------- petrol --
def test_cents_per_litre_are_converted() -> None:
    assert petrol_price_per_litre(230.9) == D("2.309")


def test_dollars_per_litre_are_left_alone() -> None:
    assert petrol_price_per_litre(2.309) == D("2.309")


def test_an_implausible_price_is_refused_rather_than_divided() -> None:
    """A feed that changes units must read unavailable.

    Divided blindly, a source switching from cents to dollars would inflate
    every saving a hundredfold and look entirely plausible doing it.
    """
    assert petrol_price_per_litre(0.5) is None
    assert petrol_price_per_litre(4000) is None
    assert petrol_price_per_litre(None) is None
