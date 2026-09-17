"""The dashboard, registered as a panel in the sidebar.

A panel rather than a page in `www/`, and the difference is authentication. A
file served from `/local/` is just a file: to read any data it has to be handed
a long-lived access token, which then sits in the page, in a bookmark, or in
whatever the browser has cached. The version of this that ran as a static page
needed exactly that.

A custom panel runs *inside* the Home Assistant frontend and is given the
`hass` object, already authenticated as whoever is looking at it. No token
exists to leak, it works on a phone through the app, and it goes away when the
integration is removed.

The page itself is one JavaScript file with no dependencies. It is served from
inside the component directory rather than copied into `www/`, so upgrading the
integration upgrades the dashboard and uninstalling it removes the dashboard
too - neither of which is true of a file the user was told to copy.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "ev-stats"
PANEL_TITLE = "EV Stats"
PANEL_ICON = "mdi:ev-station"
COMPONENT_NAME = "ev-stats-panel"

STATIC_URL = f"/{DOMAIN}_panel"
MODULE_FILE = "ev-stats-panel.js"


async def async_register(hass: HomeAssistant, version: str) -> None:
    """Serve the panel, once, however many cars are configured.

    One panel for the integration rather than one per car: the page itself
    lists whatever is configured and lets you switch between them, which is
    less cluttered than a sidebar entry per vehicle.
    """
    if hass.data.get(f"{DOMAIN}_panel_registered"):
        return

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL,
                str(Path(__file__).parent / "panel"),
                # Cached, with the integration's version on the URL below to
                # break it. Without the version an upgrade leaves the old
                # dashboard in place until the user clears their browser, and
                # they have no reason to suspect that is what happened.
                True,
            )
        ]
    )

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=COMPONENT_NAME,
        module_url=f"{STATIC_URL}/{MODULE_FILE}?v={version}",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        # Read-only, and it holds nothing a household member should not see.
        # Requiring admin would put it behind a login most people do not use on
        # the tablet this is meant to live on.
        require_admin=False,
        embed_iframe=False,
    )
    hass.data[f"{DOMAIN}_panel_registered"] = True
    _LOGGER.debug("EV Stats panel registered at /%s", PANEL_URL_PATH)


def async_remove(hass: HomeAssistant) -> None:
    """Take the panel away with the last car."""
    if not hass.data.pop(f"{DOMAIN}_panel_registered", False):
        return
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
