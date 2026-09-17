"""The Lovelace view, built with this installation's own entity ids.

A dashboard shipped as a YAML file has to contain entity ids, and entity ids
here come from the device name - a car called "EX2" produces
`sensor.ex2_free_share` and one called "the van" produces something else. So a
file would work on exactly one installation, and everybody else would be told
to find and replace.

This generates it instead, from the entity registry, and hands back the YAML to
paste into a dashboard's raw configuration editor. Core cards only: no custom
card has to be installed first, which matters because the point of the exercise
is that this works the moment it is set up.

The panel in `panel.py` is the better-looking half of this. The Lovelace view
exists because people want these figures beside the rest of their house.
"""

from __future__ import annotations

from typing import Any

import yaml

from .const import BUCKET_UNKNOWN


def _tile(entity: str | None, name: str | None = None) -> dict[str, Any] | None:
    if not entity:
        return None
    card: dict[str, Any] = {"type": "tile", "entity": entity}
    if name:
        card["name"] = name
    return card


def _present(cards: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    """Drop the cards whose entity does not exist on this installation.

    The same rule the sensors follow: an absent signal removes the thing that
    would have shown it, rather than leaving a card that reads "unavailable"
    for ever and makes the dashboard look broken.
    """
    return [card for card in cards if card]


def _sessions_table(entity: str) -> dict[str, Any]:
    """The recent sessions, as markdown.

    Two details here are scars. The rows are reversed through `| list` because
    Jinja's `reverse` returns an iterator and slicing one raises - which
    surfaced as an HTTP 400 with no useful message. And the whole table is one
    unbroken run of lines: a blank line anywhere after the separator ends the
    table, so a multi-line comment in the middle silently turns it back into
    prose.
    """
    return {
        "type": "markdown",
        "content": (
            "{% set rows = (state_attr('" + entity + "', 'sessions') or []) "
            "| list | reverse | list %}\n"
            "| When | Where | kWh | Peak |\n"
            "|---|---|--:|--:|\n"
            "{% for s in rows[:5] %}"
            "| {{ as_timestamp(s.end, 0) | timestamp_custom('%-d %b %H:%M', true, '—') }} "
            "| {{ s.bucket | replace('_', ' ') | title }}"
            "{{ ' ·' if s.corrected else '' }} "
            "| {{ '%.2f' | format(s.kwh | float(0)) }} "
            "| {{ '%.1f' | format(s.peak_kw | float(0)) }} |\n"
            "{% endfor %}"
        ),
    }


def _trips_table(entity: str) -> dict[str, Any]:
    return {
        "type": "markdown",
        "content": (
            "{% set rows = (state_attr('" + entity + "', 'trips') or []) "
            "| list | reverse | list %}\n"
            "| When | km | min | Finished |\n"
            "|---|--:|--:|---|\n"
            "{% for t in rows[:5] %}"
            "| {{ as_timestamp(t.end, 0) | timestamp_custom('%-d %b %H:%M', true, '—') }} "
            "| {{ '%.1f' | format(t.km | float(0)) }} "
            "| {{ '%.0f' | format(t.minutes | float(0)) }} "
            "| {{ (t.ended_at or '—') | replace('_', ' ') | title }} |\n"
            "{% endfor %}"
        ),
    }


def build_view(
    title: str,
    entities: dict[str, str],
    buckets: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """One view, holding only the cards this installation can fill."""

    def e(key: str) -> str | None:
        return entities.get(key)

    bucket_entities = [
        entities[f"energy_{b}"] for b in buckets if f"energy_{b}" in entities
    ]

    headline = _present(
        [
            _tile(e("free_share"), "Free"),
            _tile(e("net_saving"), "Saved"),
            _tile(e("consumption_since_install"), "Consumption"),
            _tile(e("cost_total"), "Cost"),
        ]
    )

    attribution: list[dict[str, Any]] = []
    if bucket_entities:
        attribution.append(
            {
                "type": "statistics-graph",
                "title": "Where the energy came from",
                "entities": bucket_entities,
                # `change` over `state`: these are TOTAL statistics, so the
                # change per period is what a bar chart of "energy by place"
                # actually means. A sum sensor charted as state is a staircase.
                "stat_types": ["change"],
                "chart_type": "bar",
                "period": "day",
                "days_to_show": 30,
            }
        )

    charging = _present(
        [
            _tile(e("session_classification"), "This session"),
            _tile(e("session_energy"), "Energy"),
            _tile(e("house_supply_ratio"), "House supplied"),
            _tile(e("solar_share_home"), "Solar share"),
        ]
    )
    if e("charge_sessions"):
        charging.append(_sessions_table(e("charge_sessions")))

    driving = _present(
        [
            _tile(e("distance_since_install"), "Distance"),
            _tile(e("km_per_day"), "Per day"),
            _tile(e("consumption_car"), "Car's figure"),
            _tile(e("co2_avoided"), "Carbon avoided"),
        ]
    )
    if e("trips"):
        driving.append(_trips_table(e("trips")))

    detail = _present(
        [
            _tile(e("balance_check"), "Balance check"),
            _tile(e(f"energy_{BUCKET_UNKNOWN}"), "Unattributed"),
            _tile(e("pack_estimate"), "Usable pack"),
            _tile(e("tyre_drift"), "Tyre drift"),
            _tile(e("service_due_in"), "Service due"),
            _tile(e("secure"), "Security"),
            _tile(e("route"), "Attribute to"),
        ]
    )

    sections = []
    for heading, cards in (
        (None, headline),
        ("Attribution", attribution),
        ("Charging", charging),
        ("Driving", driving),
        ("The detail", detail),
    ):
        if not cards:
            continue
        block = list(cards)
        if heading:
            block.insert(0, {"type": "heading", "heading": heading})
        sections.append({"type": "grid", "cards": block})

    return {
        "title": title,
        "path": "ev-stats",
        "icon": "mdi:ev-station",
        # Sections, so the tiles reflow rather than stretching. The earlier
        # masonry version wasted most of the width on a wide screen, which was
        # the complaint that started the tidy-up.
        "type": "sections",
        "max_columns": 3,
        "sections": sections,
    }


class _Dumper(yaml.SafeDumper):
    """Emits multi-line strings as block literals.

    Purely so the result is readable by whoever pastes it. Folded into a
    quoted scalar, a markdown table arrives as one wrapped line and nobody can
    see the table in it - which matters, because the point of returning YAML
    rather than writing it somewhere is that a person reads it first.
    """


def _block_strings(dumper: yaml.Dumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _block_strings)


def to_yaml(view: dict[str, Any]) -> str:
    return yaml.dump(
        {"views": [view]},
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    )
