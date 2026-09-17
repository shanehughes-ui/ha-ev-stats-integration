"""The figures worked out from other figures.

Two things are being guarded here, and they are the same two habits that most
of this project's history of bugs came down to: a figure that says zero when it
means "no idea", and a figure computed over too little data to mean anything.

    python -m pytest tests/test_derive.py
"""

from __future__ import annotations

import pure

derive = pure.load("derive")

share = derive.share
per_100km = derive.per_100km
km_per_day = derive.km_per_day
cold_pressure = derive.cold_pressure
service_due = derive.service_due
carbon_avoided = derive.carbon_avoided
free_energy = derive.free_energy
value_of_free = derive.value_of_free
net_saving = derive.net_saving

KELVIN_0C = 273.15
REFERENCE_K = 293.15  # 20 degrees


# ------------------------------------------------------------------ shares --
def test_a_plain_share() -> None:
    assert share(25.0, 100.0) == 25.0


def test_a_share_will_not_round_up_to_a_hundred() -> None:
    """The bug: 99.7687% printed as a flat 100 and then could not move.

    Worse than cosmetic. Any future paid charge still rounded to 100 until it
    exceeded half a percent of lifetime energy, so the tile was frozen on a
    claim that was not true.
    """
    assert share(99.7687, 100.0) == 99.8
    # 0.01 kWh left over is small but real, so this must not print as 100.
    assert share(99.99, 100.0) == 99.9


def test_a_hundred_means_the_residue_is_actually_zero() -> None:
    """The threshold is absolute, not proportional - five watt-hours of energy.

    So a residue of one watt-hour really does round to a whole 100, and one of
    ten does not. The claim being made is about the energy left over, not about
    the number of decimal places in the percentage.
    """
    assert share(100.0, 100.0) == 100.0
    assert share(99.999, 100.0) == 100.0
    assert share(99.99, 100.0) != 100.0


def test_a_share_of_almost_nothing_is_not_a_share() -> None:
    """Below a kWh this is being computed against noise."""
    assert share(0.4, 0.5) is None


def test_a_missing_part_is_not_a_zero_share() -> None:
    assert share(None, 100.0) is None


# -------------------------------------------------------------- per 100 km --
def test_consumption_waits_for_enough_distance() -> None:
    assert per_100km(25.0, 150.0, derive.MIN_KM_CONSUMPTION) is None
    assert per_100km(25.0, 200.0, derive.MIN_KM_CONSUMPTION) == 12.5


def test_a_real_consumption_figure() -> None:
    """141 kWh over 1,098 km is 12.84 kWh/100 km, measured at the wall."""
    assert per_100km(141.0, 1098.0, derive.MIN_KM_CONSUMPTION) == 12.84


def test_cost_per_100km_has_a_lower_bar_than_consumption() -> None:
    assert per_100km(5.0, 60.0, derive.MIN_KM_COST) == 8.33


# ------------------------------------------------------------- km per day --
def test_a_week_is_the_minimum() -> None:
    """Fewer days than that and one long drive sets the average."""
    assert km_per_day(300.0, 3.0) is None
    assert km_per_day(300.0, 10.0) == 30.0


# ------------------------------------------------------------------ tyres --
def test_a_tyre_at_the_reference_temperature_is_unchanged() -> None:
    assert cold_pressure(36.0, 20.0, REFERENCE_K, KELVIN_0C) == 36.0


def test_a_hot_tyre_normalises_downwards() -> None:
    """The whole point: the swing is thermal, not a leak.

    A raw threshold nags in the afternoon and misses a real slow leak in the
    morning. Corrected, the same tyre reads the same all day.
    """
    hot = cold_pressure(40.0, 50.0, REFERENCE_K, KELVIN_0C)
    assert hot < 40.0
    assert round(hot, 1) == 36.3


def test_a_cold_tyre_normalises_upwards() -> None:
    assert cold_pressure(34.0, 5.0, REFERENCE_K, KELVIN_0C) > 34.0


def test_a_missing_temperature_declines_rather_than_assuming_twenty() -> None:
    """Assuming would turn a hot tyre into a high reading and hide the drift."""
    assert cold_pressure(36.0, None, REFERENCE_K, KELVIN_0C) is None


def test_absolute_zero_does_not_divide() -> None:
    assert cold_pressure(36.0, -300.0, REFERENCE_K, KELVIN_0C) is None


# ---------------------------------------------------------------- service --
def test_distance_binds_for_a_high_mileage_driver() -> None:
    assert service_due(300.0, 5000.0, 50.0) == (100, "distance")


def test_time_binds_for_a_low_mileage_driver() -> None:
    assert service_due(300.0, 5000.0, 10.0) == (300, "time")


def test_too_little_driving_to_convert_distance_into_days() -> None:
    result = service_due(300.0, 5000.0, 2.0)
    assert result == (300, "time")


def test_without_a_service_date_there_is_nothing_to_say() -> None:
    assert service_due(None, 5000.0, 50.0) is None


# ----------------------------------------------------------------- carbon --
def test_carbon_avoided_is_net_of_the_charging_that_paid_for_it() -> None:
    """1,000 km at 7.5 L/100 km and 2.31 kg/L is 173.25 kg, less 40 charged."""
    assert carbon_avoided(1000.0, 7.5, 2.31, 40.0) == 133.2


def test_carbon_waits_for_distance_because_it_starts_negative() -> None:
    """Charging happens before the driving it pays for.

    Zeroed on the day, a net figure really did read -0.3 kg: a quarter-kilo of
    charging carbon against no kilometres yet driven. Arithmetically true, and
    it reads as "the EV is worse than petrol".
    """
    assert carbon_avoided(20.0, 7.5, 2.31, 0.3) is None


def test_no_charging_carbon_means_unavailable_not_gross() -> None:
    """Zero here would report the petrol figure as though it were net."""
    assert carbon_avoided(1000.0, 7.5, 2.31, None) is None


# ------------------------------------------------------------------- free --
def test_free_energy_counts_home_solar_as_well_as_work() -> None:
    """A home charge running on surplus is as free as one at a workplace."""
    assert free_energy(127.0, 12.5) == 139.5


def test_what_free_charging_was_worth_at_our_own_rate() -> None:
    """20 kWh of home charging that cost $2 is 10c/kWh, so 100 free kWh is $10."""
    assert value_of_free(100.0, 20.0, 2.0) == 10.0


def test_free_charging_is_worth_nothing_measurable_without_a_home_rate() -> None:
    assert value_of_free(100.0, 0.5, 2.0) is None
    assert value_of_free(100.0, 20.0, None) is None


def test_net_saving_needs_both_sides() -> None:
    assert net_saving(200.0, 35.0) == 165.0
    assert net_saving(None, 35.0) is None
    assert net_saving(200.0, None) is None
