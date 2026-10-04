"""The geocoder's HTTP, caching and failure handling.

Here rather than in `tests/` because this is the half that needs Home
Assistant: a client session, a Store, and the event loop's clock. The decision
of which field to believe is pure and lives in `tests/test_suburb.py`.

No request in this file reaches the network - `aioclient_mock` answers them -
which is also the point of counting them. "Looked up once" is a claim about
request count, and nothing else can check it.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.ev_stats.geocode import URL, Geocoder

# Invented. A real coordinate in a test fixture is a published coordinate.
LAT, LON = -12.34567, 98.76543

ANSWER = {"address": {"suburb": "Northgate", "city": "Riverton"}}


def _ok(aioclient_mock, payload=ANSWER):
    aioclient_mock.get(URL, json=payload)


async def test_names_a_coordinate(hass: HomeAssistant, aioclient_mock) -> None:
    _ok(aioclient_mock)
    geo = Geocoder(hass, "entry")
    await geo.async_load()
    assert await geo.async_name(LAT, LON) == "Northgate"


async def test_a_place_is_looked_up_once(hass: HomeAssistant, aioclient_mock) -> None:
    """The cache is what keeps this off a rate limiter.

    A daily destination would otherwise be a request per visit, for ever.
    """
    _ok(aioclient_mock)
    geo = Geocoder(hass, "entry")
    await geo.async_load()
    assert await geo.async_name(LAT, LON) == "Northgate"
    assert await geo.async_name(LAT, LON) == "Northgate"
    assert aioclient_mock.call_count == 1


async def test_nearby_points_share_an_answer(hass: HomeAssistant, aioclient_mock) -> None:
    """Rounded to ~110 m: parking ten metres further along is the same suburb."""
    _ok(aioclient_mock)
    geo = Geocoder(hass, "entry")
    await geo.async_load()
    await geo.async_name(LAT, LON)
    await geo.async_name(LAT + 0.00004, LON - 0.00003)
    assert aioclient_mock.call_count == 1


async def test_a_place_with_no_name_is_remembered(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """"No name" is an answer and is cached.

    Open country genuinely has no suburb. Retrying would be one more request
    for the same nothing on every trip that ended there.
    """
    _ok(aioclient_mock, {"address": {"state": "Example"}})
    geo = Geocoder(hass, "entry")
    await geo.async_load()
    assert await geo.async_name(LAT, LON) is None
    assert await geo.async_name(LAT, LON) is None
    assert aioclient_mock.call_count == 1


async def test_a_failed_request_is_not_cached(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A timeout says nothing about the place.

    Caching it would make one bad minute permanent - the distinction the
    `_FAILED` sentinel exists for, since both paths return None to the caller.
    """
    aioclient_mock.get(URL, status=503)
    geo = Geocoder(hass, "entry")
    await geo.async_load()
    assert await geo.async_name(LAT, LON) is None

    aioclient_mock.clear_requests()
    _ok(aioclient_mock)
    assert await geo.async_name(LAT, LON) == "Northgate"


async def test_a_broken_answer_does_not_raise(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.get(URL, text="not json at all")
    geo = Geocoder(hass, "entry")
    await geo.async_load()
    assert await geo.async_name(LAT, LON) is None


async def test_the_cache_survives_a_restart(hass: HomeAssistant, aioclient_mock) -> None:
    _ok(aioclient_mock)
    geo = Geocoder(hass, "entry")
    await geo.async_load()
    await geo.async_name(LAT, LON)

    revived = Geocoder(hass, "entry")
    await revived.async_load()
    assert await revived.async_name(LAT, LON) == "Northgate"
    assert aioclient_mock.call_count == 1
