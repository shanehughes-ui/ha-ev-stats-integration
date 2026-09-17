"""The generated Lovelace view.

Worth testing because the whole point of generating it is that it adapts: an
installation without solar, or without an engine sensor, must get a dashboard
with those cards absent rather than a dashboard full of entities that read
`unavailable` for ever and make it look broken.

    python -m pytest tests/test_lovelace.py
"""

from __future__ import annotations

import yaml

import pure

lovelace = pure.load("lovelace")

FULL = {
    "free_share": "sensor.ex2_free_share",
    "net_saving": "sensor.ex2_net_saving",
    "consumption_since_install": "sensor.ex2_consumption",
    "cost_total": "sensor.ex2_charging_cost",
    "energy_unknown": "sensor.ex2_unknown",
    "energy_home": "sensor.ex2_home",
    "energy_workplace": "sensor.ex2_workplace",
    "session_classification": "sensor.ex2_session_classification",
    "session_energy": "sensor.ex2_session_energy",
    "house_supply_ratio": "sensor.ex2_house_supply_ratio",
    "solar_share_home": "sensor.ex2_home_solar_share",
    "charge_sessions": "sensor.ex2_charge_sessions",
    "trips": "sensor.ex2_trips",
    "distance_since_install": "sensor.ex2_distance_since_install",
    "km_per_day": "sensor.ex2_distance_per_day",
    "consumption_car": "sensor.ex2_consumption_car",
    "co2_avoided": "sensor.ex2_carbon_avoided",
    "balance_check": "sensor.ex2_balance_check",
    "pack_estimate": "sensor.ex2_usable_pack_size",
    "tyre_drift": "sensor.ex2_tyre_drift",
    "service_due_in": "sensor.ex2_service_due_in",
    "secure": "sensor.ex2_security",
    "route": "select.ex2_charging_attributed_to",
}
BUCKETS = ("unknown", "home", "workplace", "public_dc", "other")

MINIMAL = {
    "free_share": "sensor.van_free_share",
    "energy_unknown": "sensor.van_unknown",
    "balance_check": "sensor.van_balance_check",
}


def cards_of(view) -> list[dict]:
    return [card for section in view["sections"] for card in section["cards"]]


def entities_of(view) -> set[str]:
    found = set()
    for card in cards_of(view):
        if "entity" in card:
            found.add(card["entity"])
        found.update(card.get("entities", []))
    return found


# ----------------------------------------------------------------- shape --
def test_the_view_is_valid_yaml() -> None:
    view = lovelace.build_view("EX2", FULL, BUCKETS)
    parsed = yaml.safe_load(lovelace.to_yaml(view))
    assert parsed["views"][0]["title"] == "EX2"


def test_the_yaml_round_trips_exactly() -> None:
    """The markdown must survive being written out and read back.

    Not pedantry. A quoted YAML scalar folds single line breaks into spaces,
    and a markdown table that has lost its line breaks is a paragraph - which
    would look fine in the generated text and be broken on the dashboard.
    """
    view = lovelace.build_view("EX2", FULL, BUCKETS)
    assert yaml.safe_load(lovelace.to_yaml(view))["views"][0] == view


def test_multi_line_content_is_emitted_as_a_block() -> None:
    """So that whoever pastes it can see the table in it."""
    text = lovelace.to_yaml(lovelace.build_view("EX2", FULL, BUCKETS))
    assert "content: |-" in text


def test_every_id_in_the_view_came_from_the_map() -> None:
    """Nothing hard-coded. A typed-in id works on exactly one installation."""
    view = lovelace.build_view("EX2", FULL, BUCKETS)
    assert entities_of(view) <= set(FULL.values())


def test_the_car_is_named_after_the_entry() -> None:
    assert lovelace.build_view("the van", FULL, BUCKETS)["title"] == "the van"


# ---------------------------------------------------------- degradation --
def test_a_sparse_installation_gets_a_smaller_dashboard() -> None:
    """Absent signals remove their cards, they do not leave broken ones."""
    full = lovelace.build_view("EX2", FULL, BUCKETS)
    lean = lovelace.build_view("Van", MINIMAL, ("unknown",))
    assert len(cards_of(lean)) < len(cards_of(full))
    assert entities_of(lean) <= set(MINIMAL.values())


def test_no_card_references_an_entity_that_does_not_exist() -> None:
    view = lovelace.build_view("Van", MINIMAL, ("unknown",))
    for card in cards_of(view):
        if "entity" in card:
            assert card["entity"] in MINIMAL.values()


def test_an_installation_with_nothing_still_produces_a_view() -> None:
    """It should be sparse, not malformed."""
    view = lovelace.build_view("Van", {}, ())
    yaml.safe_load(lovelace.to_yaml(view))
    assert view["sections"] == [] or all(
        isinstance(s["cards"], list) for s in view["sections"]
    )


def test_work_buckets_appear_by_whatever_they_are_called() -> None:
    """No hard-coded workplace slug anywhere in the dashboard either."""
    view = lovelace.build_view("EX2", FULL, BUCKETS)
    assert "sensor.ex2_workplace" in entities_of(view)


# ------------------------------------------------------------- the table --
def test_the_sessions_table_reverses_through_a_list() -> None:
    """Jinja's `reverse` returns an iterator, and slicing one raises.

    It surfaced as an HTTP 400 with nothing useful in it, so the `| list` is
    load-bearing rather than stylistic.
    """
    view = lovelace.build_view("EX2", FULL, BUCKETS)
    markdown = [c for c in cards_of(view) if c["type"] == "markdown"]
    assert markdown
    for card in markdown:
        assert "| list | reverse | list" in card["content"]


def test_the_table_has_no_blank_line_after_the_separator() -> None:
    """A blank line anywhere after the separator ends a markdown table.

    The row count kept passing while the card was visibly broken, so this
    walks forward from the separator asserting the run is unbroken.
    """
    view = lovelace.build_view("EX2", FULL, BUCKETS)
    for card in [c for c in cards_of(view) if c["type"] == "markdown"]:
        lines = card["content"].split("\n")
        start = next(i for i, line in enumerate(lines) if line.startswith("|---"))
        for line in lines[start + 1 :]:
            if not line.strip():
                raise AssertionError("blank line inside the table")
            if line.strip().startswith("{% endfor"):
                break


def test_the_energy_chart_asks_for_change_not_state() -> None:
    """These are TOTAL statistics. Charted as state they are a staircase."""
    view = lovelace.build_view("EX2", FULL, BUCKETS)
    graph = next(c for c in cards_of(view) if c["type"] == "statistics-graph")
    assert graph["stat_types"] == ["change"]
