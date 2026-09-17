"""Reading the config entry.

Config-flow sections nest their data, so options come back as
``{"car": {...}, "house": {...}}`` rather than flat. Every read goes through
here so that shape is stated in one place, and so an absent option is always
``None`` rather than an empty string or a missing key depending on how the form
was submitted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State


class Config:
    """Typed-ish accessor over a config entry's data and options."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._data = dict(entry.data)
        self._options = dict(entry.options)

    def required(self, key: str) -> str:
        """A key from the config flow proper. Absent means a broken entry."""
        return self._data[key]

    def opt(self, sectn: str, key: str, default: Any = None) -> Any:
        """A key from an options section.

        An entity picker that was filled in and then cleared submits an empty
        string, which is not the same as "never set" but must behave the same.
        """
        value = (self._options.get(sectn) or {}).get(key, default)
        if value in ("", []):
            return default
        return value

    def has(self, sectn: str, key: str) -> bool:
        return self.opt(sectn, key) is not None


def numeric_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """A float from an entity, or None.

    `unknown` and `unavailable` are both None here on purpose. The original
    package used `| float(0)` in a dozen places and it cost it twice: a missing
    GPS fix became an odometer reading of zero, and a missing charging-carbon
    meter made a gross figure look like a net one. Absent is not zero.
    """
    if not entity_id:
        return None
    state: State | None = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def string_state(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    return state.state
