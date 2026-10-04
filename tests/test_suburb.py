"""Naming the place a trip ended, minus the network.

    python -m pytest tests/test_suburb.py
"""

from __future__ import annotations

import pytest

import pure

suburb = pure.load("suburb")
name_from = suburb.name_from
parse_position = suburb.parse_position
wanted = suburb.wanted
label = suburb.label

OUTSIDE = suburb.OUTSIDE_EVERY_ZONE


# ----------------------------------------------------------- the name field --
def test_prefers_the_most_specific_name() -> None:
    address = {"suburb": "Northgate", "city": "Riverton", "state": "Example"}
    assert name_from(address) == "Northgate"


def test_falls_back_through_the_levels() -> None:
    assert name_from({"town": "Riverton", "county": "Example"}) == "Riverton"
    assert name_from({"county": "Example"}) == "Example"


def test_a_state_is_not_a_place_a_car_parks() -> None:
    """Past county the answers stop naming anywhere recognisable."""
    assert name_from({"state": "Example", "country": "Exampleland"}) is None


@pytest.mark.parametrize("address", [None, {}, "Northgate", 7, {"suburb": "   "}])
def test_nothing_usable_is_no_name(address) -> None:
    assert name_from(address) is None


# ------------------------------------------------------------- the position --
# Every coordinate below is invented. The CI scrub rejects a real one on sight,
# and it has already caught a genuine one copied out of live trip data into this
# file - a test fixture is a published file like any other.
def test_reads_the_string_form() -> None:
    assert parse_position("-12.34567,98.76543") == (-12.34567, 98.76543)


def test_reads_the_list_form() -> None:
    """The shape a template-built record actually arrives in.

    The recording template renders `"lat,lon"`, and Home Assistant's
    native-type pass then literal_evals that into a tuple before it reaches the
    event. Treating it as a string yields `[-33.49071` and a geocoder that
    answers nothing - which is silent, because an unnamed trip looks exactly
    like a trip whose place has no name.
    """
    assert parse_position([-12.34567, 98.76543]) == (-12.34567, 98.76543)
    assert parse_position((-12.34567, 98.76543)) == (-12.34567, 98.76543)


def test_null_island_is_not_a_position() -> None:
    """(0, 0) is what several trackers report instead of "no fix".

    It is a real coordinate 600 km off Ghana, and a geocoder answers it
    confidently.
    """
    assert parse_position([0, 0]) is None
    assert parse_position("0.0,0.0") is None


@pytest.mark.parametrize(
    "value", [None, "", "not a position", [1], [91.0, 10.0], [10.0, 200.0], ["a", "b"]]
)
def test_unusable_positions_are_rejected(value) -> None:
    assert parse_position(value) is None


# ------------------------------------------------------ what to look up, if --
def test_only_ends_outside_every_zone() -> None:
    record = {
        "from_zone": "home",
        "from_position": [-12.34, 98.76],
        "to_zone": OUTSIDE,
        "to_position": [-12.40, 98.80],
    }
    assert set(wanted(record)) == {"to"}


def test_home_and_work_coordinates_are_never_looked_up() -> None:
    """The privacy boundary, asserted rather than left to a comment.

    Both ends already have names, so there is nothing to ask - and asking would
    send the coordinates of the house to a third party to be told something
    already known.
    """
    record = {
        "from_zone": "home",
        "from_position": [-12.34, 98.76],
        "to_zone": "workplace",
        "to_position": [-12.40, 98.80],
    }
    assert wanted(record) == {}


def test_a_trip_already_named_is_not_looked_up_again() -> None:
    record = {
        "to_zone": OUTSIDE,
        "to_position": [-12.40, 98.80],
        "to_suburb": "Northgate",
    }
    assert wanted(record) == {}


def test_no_position_means_nothing_to_ask() -> None:
    assert wanted({"to_zone": OUTSIDE}) == {}
    assert wanted({"to_zone": OUTSIDE, "to_position": None}) == {}


def test_both_ends_when_both_qualify() -> None:
    record = {
        "from_zone": OUTSIDE,
        "from_position": [-12.55, 98.95],
        "to_zone": OUTSIDE,
        "to_position": [-12.40, 98.80],
    }
    assert set(wanted(record)) == {"from", "to"}


# ------------------------------------------------------------------ labels --
NAMES = {"home": "Home", "workplace": "Workplace", OUTSIDE: "Elsewhere"}


def test_the_suburb_stands_in_for_elsewhere() -> None:
    assert label(OUTSIDE, "Northgate", NAMES) == "Northgate"


def test_a_known_zone_keeps_its_name() -> None:
    """A trip that ended at home reads "Home", never the suburb of the house -
    the more useful answer and the less identifying one."""
    assert label("home", "Northgate", NAMES) == "Home"


def test_elsewhere_without_a_suburb_is_still_elsewhere() -> None:
    assert label(OUTSIDE, None, NAMES) == "Elsewhere"
    assert label(OUTSIDE, "", NAMES) == "Elsewhere"


def test_an_unmapped_zone_shows_its_own_name() -> None:
    assert label("depot", None, NAMES) == "depot"
    assert label(None, None, NAMES) == "—"
