"""Turning a coordinate into the name of a place, minus the network.

Pure, like `classify.py` and `fingerprint.py`. The HTTP lives in
`geocode.py`; everything here is the part worth having a test for - which
field of a geocoder's answer to believe, and when there is nothing to ask
about in the first place.

`not_home` is an accurate and nearly useless label. It means the car was
outside every zone you have defined, which is most of the places it goes: a
trips table reading "Elsewhere -> Elsewhere" has told you nothing. A suburb
name is the smallest unit that is actually informative, and it is also about
as coarse as you would want to publish - which is why the lookup asks for
that zoom level rather than a street address.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

# Most specific first. A point in open country has no suburb, and `county` is
# the last level that still names somewhere a person would recognise; past
# that a geocoder starts answering with a state, which is not a place a car
# parks.
NAME_FIELDS: tuple[str, ...] = (
    "suburb",
    "neighbourhood",
    "village",
    "town",
    "city_district",
    "city",
    "municipality",
    "county",
)

# The zone name a tracker reports when the car is outside every defined zone.
# This is the ONLY state worth a lookup: home and work already have names, so
# asking about them would send their coordinates to a third party to be told
# something already known.
OUTSIDE_EVERY_ZONE = "not_home"

# Null Island. Several trackers report (0, 0) rather than "no position", and it
# is a real coordinate 600 km off Ghana that a geocoder answers confidently.
MIN_ABS_DEG = 1.0


def name_from(address: Mapping[str, Any] | None) -> str | None:
    """The most specific usable place name in a geocoder's address block."""
    if not isinstance(address, Mapping):
        return None
    for field in NAME_FIELDS:
        value = address.get(field)
        if value and str(value).strip():
            return str(value).strip()
    return None


def parse_position(value: Any) -> tuple[float, float] | None:
    """A stored position as (lat, lon), or None if it is not one.

    Accepts both shapes this has been stored in. The integration writes
    `"lat,lon"`, but a template-built record goes through Home Assistant's
    native-type pass, which `literal_eval`s that same string into a *tuple*
    before it ever reaches the event. Treating one as the other silently
    yields `[-33.49071` and a geocoder that answers nothing at all.
    """
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts: Sequence[Any] = list(value)[:2]
    else:
        parts = str(value).split(",")[:2]
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    if abs(lat) < MIN_ABS_DEG and abs(lon) < MIN_ABS_DEG:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return (lat, lon)


def wanted(record: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    """Which ends of a trip could be given a name, and where to look.

    Empty whenever there is nothing to ask - no position recorded, the car was
    somewhere already named, or the trip has been named before. That emptiness
    is the rate limiter: a commute between two known zones never generates a
    request at all.
    """
    out: dict[str, tuple[float, float]] = {}
    for end in ("from", "to"):
        if record.get(f"{end}_suburb"):
            continue
        if record.get(f"{end}_zone") != OUTSIDE_EVERY_ZONE:
            continue
        point = parse_position(record.get(f"{end}_position"))
        if point:
            out[end] = point
    return out


def label(zone: Any, suburb: Any, zone_names: Mapping[str, str] | None = None) -> str:
    """What to show for one end of a trip.

    The suburb stands in only for `not_home`. A trip that ended at home reads
    "Home", not the suburb the house is in: the zone name is both the more
    useful answer and the less identifying one.
    """
    zone_names = zone_names or {}
    if zone == OUTSIDE_EVERY_ZONE and suburb:
        return str(suburb)
    if zone is None:
        return "—"
    return zone_names.get(str(zone), str(zone))
