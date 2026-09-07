"""Shared pytest configuration for the Niba integration tests."""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request: pytest.FixtureRequest) -> None:
    """Let Home Assistant load custom_components/niba during tests.

    ``recorder_mock`` has to be resolved before ``hass`` exists, and
    ``enable_custom_integrations`` pulls ``hass`` in. Resolving both here, in
    that order, keeps this autouse fixture from creating ``hass`` too early
    for the tests that ask for the recorder.
    """

    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")
