"""Does it load, and does it degrade.

The two questions static checks cannot answer. A config flow that validates is
not a config flow that works, and a `requires` lambda that reads correctly is
not one that has ever been evaluated.

Every optional signal is absent in the minimal case on purpose. That is the
configuration most people will have on the first day, and it is the one where a
sensor publishing a confident zero would do the most damage - nobody has any
figures to compare it against yet.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_stats.const import (
    CONF_BATTERY,
    CONF_CHARGING_POWER,
    CONF_HOME_ZONE,
    CONF_HOUSE_LOAD,
    CONF_IMPORT_PRICE,
    CONF_ODOMETER,
    CONF_SOLAR_POWER,
    CONF_WORK_ZONES,
    DOMAIN,
    SECTION_HOUSE,
    SECTION_LOCATION,
    SECTION_PRICING,
)

POWER = "sensor.car_charging_power"
ODOMETER = "sensor.car_odometer"
BATTERY = "sensor.car_battery"
HOME_ZONE = "zone.home"
HOUSE = "sensor.house_load"
SOLAR = "sensor.solar_power"
PRICE = "sensor.import_price"
WORK_ZONE = "zone.workplace"

REQUIRED = {
    CONF_CHARGING_POWER: POWER,
    CONF_ODOMETER: ODOMETER,
    CONF_BATTERY: BATTERY,
    CONF_HOME_ZONE: HOME_ZONE,
}


def seed(hass: HomeAssistant, *, full: bool = False) -> None:
    """The entities the integration reads, as a plausible parked car."""
    hass.states.async_set(POWER, "0.0", {"unit_of_measurement": "kW"})
    hass.states.async_set(ODOMETER, "3400.0", {"unit_of_measurement": "km"})
    hass.states.async_set(BATTERY, "62", {"unit_of_measurement": "%"})
    hass.states.async_set(
        HOME_ZONE, "0", {"latitude": -33.4, "longitude": 151.3, "radius": 100}
    )
    if full:
        hass.states.async_set(HOUSE, "0.6", {"unit_of_measurement": "kW"})
        hass.states.async_set(SOLAR, "3.2", {"unit_of_measurement": "kW"})
        hass.states.async_set(PRICE, "0.31")
        hass.states.async_set(
            WORK_ZONE, "0", {"latitude": -33.5, "longitude": 151.4, "radius": 100}
        )


async def setup_entry(hass: HomeAssistant, options: dict | None = None):
    entry = MockConfigEntry(
        domain=DOMAIN, title="EX2", data=REQUIRED, options=options or {}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def keys_of(hass: HomeAssistant, entry_id: str) -> set[str]:
    """Entities by the unique-id suffix this integration assigned."""
    registry = er.async_get(hass)
    prefix = f"{entry_id}_"
    return {
        e.unique_id[len(prefix) :]
        for e in er.async_entries_for_config_entry(registry, entry_id)
        if e.unique_id.startswith(prefix)
    }


# ----------------------------------------------------------------- loading --
async def test_it_loads_with_only_the_four_required_entities(hass: HomeAssistant) -> None:
    seed(hass)
    entry = await setup_entry(hass)
    assert entry.state.recoverable is False or entry.state.name == "LOADED"
    keys = keys_of(hass, entry.entry_id)
    # The ledger exists on day one, whatever else does not.
    assert {"charge_energy", "energy_unknown", "energy_home", "balance_check"} <= keys


async def test_it_unloads_cleanly(hass: HomeAssistant) -> None:
    seed(hass)
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]


async def test_it_reloads(hass: HomeAssistant) -> None:
    """A reload is what every options change does, so it has to survive one."""
    seed(hass)
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id in hass.data[DOMAIN]


# ------------------------------------------------------------ degradation --
async def test_absent_signals_remove_their_sensors(hass: HomeAssistant) -> None:
    """Absent, not zero.

    A house-supply ratio with no house meter behind it would read 0.00 and mean
    "charged somewhere else", every single time.
    """
    seed(hass)
    entry = await setup_entry(hass)
    keys = keys_of(hass, entry.entry_id)
    for absent in (
        "house_supply_ratio",
        "house_baseline",
        "solar_share_home",
        "cost_total",
        "charge_co2",
        "trusted_location",
        "tyre_lowest_cold",
        "secure",
    ):
        assert absent not in keys, f"{absent} should not exist without its inputs"


async def test_configured_signals_add_their_sensors(hass: HomeAssistant) -> None:
    seed(hass, full=True)
    entry = await setup_entry(
        hass,
        {
            SECTION_HOUSE: {CONF_HOUSE_LOAD: HOUSE, CONF_SOLAR_POWER: SOLAR},
            SECTION_PRICING: {CONF_IMPORT_PRICE: PRICE},
            SECTION_LOCATION: {CONF_WORK_ZONES: [WORK_ZONE]},
        },
    )
    keys = keys_of(hass, entry.entry_id)
    assert {"house_supply_ratio", "solar_share_home", "cost_total"} <= keys
    # A work zone becomes a bucket named after the zone, never a typed slug.
    assert "energy_workplace" in keys
    assert "work_share" in keys


async def test_no_work_zone_means_no_work_bucket(hass: HomeAssistant) -> None:
    seed(hass)
    entry = await setup_entry(hass)
    keys = keys_of(hass, entry.entry_id)
    assert not any(k.startswith("energy_work") for k in keys)
    assert "work_share" not in keys


# -------------------------------------------------------------- the ledger --
async def test_the_services_are_registered(hass: HomeAssistant) -> None:
    seed(hass)
    await setup_entry(hass)
    for service in ("move_energy", "correct_session", "log_dc_session", "dashboard"):
        assert hass.services.has_service(DOMAIN, service), service


async def test_moving_energy_keeps_the_buckets_balanced(hass: HomeAssistant) -> None:
    """The invariant, exercised through the real service call."""
    seed(hass)
    entry = await setup_entry(hass)
    runtime = hass.data[DOMAIN][entry.entry_id]

    from decimal import Decimal

    await runtime.ledger.async_accrue(Decimal("10.0"))
    assert runtime.ledger.kwh("unknown") == Decimal("10.0")

    await hass.services.async_call(
        DOMAIN,
        "move_energy",
        {
            "config_entry_id": entry.entry_id,
            "source_bucket": "unknown",
            "target_bucket": "home",
            "kwh": 4.0,
        },
        blocking=True,
        return_response=True,
    )
    assert runtime.ledger.kwh("unknown") == Decimal("6.0")
    assert runtime.ledger.kwh("home") == Decimal("4.0")
    assert runtime.ledger.total_kwh() == Decimal("10.0")


async def test_moving_more_than_a_bucket_holds_is_refused(hass: HomeAssistant) -> None:
    """It raises rather than clamping.

    Silently moving less than asked is how a ledger ends up disagreeing with
    itself, and nobody goes looking for a correction that half-happened.
    """
    from homeassistant.exceptions import ServiceValidationError

    seed(hass)
    entry = await setup_entry(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "move_energy",
            {
                "config_entry_id": entry.entry_id,
                "source_bucket": "unknown",
                "target_bucket": "home",
                "kwh": 99.0,
            },
            blocking=True,
        )


# ------------------------------------------------------------- dashboards --
async def test_the_dashboard_service_returns_usable_yaml(hass: HomeAssistant) -> None:
    import yaml

    seed(hass)
    entry = await setup_entry(hass)
    result = await hass.services.async_call(
        DOMAIN,
        "dashboard",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    parsed = yaml.safe_load(result["yaml"])
    assert parsed["views"][0]["title"] == "EX2"
    # Every id in it must be one that actually exists on this installation.
    real = set(hass.states.async_entity_ids())
    for section in parsed["views"][0]["sections"]:
        for card in section["cards"]:
            if "entity" in card:
                assert card["entity"] in real, card["entity"]


async def test_the_panel_never_blocks_the_rest(hass: HomeAssistant) -> None:
    """The frontend is optional, and this harness has none.

    That is the point of the test. Declaring `panel_custom` a hard dependency
    is how this was written first, and it meant the whole integration - ledger,
    sensors, services - failed to set up wherever the frontend did. Here the
    frontend genuinely is unavailable, so a successful setup is the assertion.
    """
    seed(hass)
    entry = await setup_entry(hass)
    assert entry.entry_id in hass.data[DOMAIN]
    assert hass.services.has_service(DOMAIN, "move_energy")
    panels = hass.data.get("frontend_panels", {})
    if "ev-stats" in panels:
        assert panels["ev-stats"].component_name == "custom"
