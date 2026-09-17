"""Fixtures for the loading tests.

These are the ones that need Home Assistant. Everything in `tests/` is
deliberately free of it - the arithmetic and the judgement should be checkable
without a harness - but "does this actually load" cannot be answered that way,
and six phases of green linting is not the same claim.

    docker run --rm -v "$PWD:/w" -w /w python:3.13-slim bash -c \\
      "pip install -q pytest-homeassistant-custom-component && \\
       python -m pytest tests_load -q"
"""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant see `custom_components/` at all.

    Without it every test here fails with "Integration ev_stats not found",
    which looks like a bug in the integration and is not one.
    """
    yield
