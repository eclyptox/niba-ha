"""Tests for the Niba config flow."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.niba.api import NibaApiError, NibaAuthError, parse_user
from custom_components.niba.const import (
    CONF_CUPS,
    CONF_TOKEN,
    DOMAIN,
    NIBA_CLIENT_URL,
)

CUPS = "ES0021000000000000AA"
OTHER_CUPS = "ES0021000000000000BB"


@pytest.fixture(autouse=True)
def skip_integration_setup():
    """Keep the flow tests from actually setting up the integration."""

    with (
        patch("custom_components.niba.async_setup_entry", return_value=True),
        # Avoid creating a real aiohttp session: the client is mocked anyway.
        patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        yield


def _jwt(email: str = "a@b.c", *, expired: bool = False) -> str:
    delta = timedelta(days=-1 if expired else 30)
    exp = int((datetime.now(UTC) + delta).timestamp())
    parts = []
    for item in ({"alg": "HS256"}, {"email": email, "exp": exp}, b"sig"):
        raw = item if isinstance(item, bytes) else json.dumps(item).encode()
        parts.append(base64.urlsafe_b64encode(raw).rstrip(b"=").decode())
    return ".".join(parts)


def _patch_client(**overrides):
    client = AsyncMock()
    client.validate_token.return_value = parse_user({"email": "a@b.c"})
    client.get_consumption_period.return_value = None
    for key, value in overrides.items():
        setattr(client, key, AsyncMock(side_effect=value))
    return patch(
        "custom_components.niba.config_flow.NibaApiClient", return_value=client
    )


async def _run_flow(hass: HomeAssistant, token: str, cups: str = CUPS):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: token}
    )
    if result["type"] is not FlowResultType.FORM or result["step_id"] != "cups":
        return result
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CUPS: cups}
    )


async def test_full_flow_creates_the_entry(hass: HomeAssistant) -> None:
    with _patch_client():
        result = await _run_flow(hass, _jwt())

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CUPS] == CUPS
    assert result["title"] == f"Niba {CUPS}"


async def test_long_cups_is_normalized_before_being_stored(
    hass: HomeAssistant,
) -> None:
    with _patch_client():
        result = await _run_flow(hass, _jwt(), cups=f"  {CUPS.lower()}0f  ")

    assert result["data"][CONF_CUPS] == CUPS


async def test_expired_token_is_rejected_without_calling_niba(
    hass: HomeAssistant,
) -> None:
    with _patch_client():
        result = await _run_flow(hass, _jwt(expired=True))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TOKEN: "token_expired"}


async def test_malformed_token_is_rejected(hass: HomeAssistant) -> None:
    with _patch_client():
        result = await _run_flow(hass, "not-a-jwt")

    assert result["errors"] == {CONF_TOKEN: "invalid_auth"}


async def test_token_rejected_by_niba(hass: HomeAssistant) -> None:
    with _patch_client(validate_token=NibaAuthError("nope")):
        result = await _run_flow(hass, _jwt())

    assert result["errors"] == {CONF_TOKEN: "invalid_auth"}


async def test_unreachable_api_reports_cannot_connect(hass: HomeAssistant) -> None:
    with _patch_client(validate_token=NibaApiError("down")):
        result = await _run_flow(hass, _jwt())

    assert result["errors"] == {"base": "cannot_connect"}


async def test_unknown_cups_reports_invalid_cups(hass: HomeAssistant) -> None:
    with _patch_client(get_consumption_period=NibaApiError("cups_not_found")):
        result = await _run_flow(hass, _jwt())

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CUPS: "invalid_cups"}


async def test_same_cups_twice_is_aborted(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"a@b.c:{CUPS}",
        data={CONF_TOKEN: "eyJ", CONF_CUPS: CUPS},
    ).add_to_hass(hass)

    with _patch_client():
        result = await _run_flow(hass, _jwt())

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_second_cups_on_the_same_account_is_allowed(
    hass: HomeAssistant,
) -> None:
    """One Niba account can hold several supply points."""

    MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"a@b.c:{CUPS}",
        data={CONF_TOKEN: "eyJ", CONF_CUPS: CUPS},
    ).add_to_hass(hass)

    with _patch_client():
        result = await _run_flow(hass, _jwt(), cups=OTHER_CUPS)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CUPS] == OTHER_CUPS


async def test_reauth_updates_the_stored_token(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"a@b.c:{CUPS}",
        data={CONF_TOKEN: "old-token", CONF_CUPS: CUPS},
    )
    entry.add_to_hass(hass)
    new_token = _jwt()

    with _patch_client():
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: new_token}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == new_token


async def test_reauth_rejects_an_expired_token(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"a@b.c:{CUPS}",
        data={CONF_TOKEN: "old-token", CONF_CUPS: CUPS},
    )
    entry.add_to_hass(hass)

    with _patch_client():
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: _jwt(expired=True)}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TOKEN: "token_expired"}
    assert entry.data[CONF_TOKEN] == "old-token"


async def test_token_steps_expose_the_portal_url_placeholder(
    hass: HomeAssistant,
) -> None:
    """Without the placeholder the dialog would show a literal '{url}'."""

    with _patch_client():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["description_placeholders"] == {"url": NIBA_CLIENT_URL}

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"a@b.c:{CUPS}",
        data={CONF_TOKEN: "old-token", CONF_CUPS: CUPS},
    )
    entry.add_to_hass(hass)
    with _patch_client():
        result = await entry.start_reauth_flow(hass)

    # Home Assistant adds its own "name" placeholder to reauth flows.
    assert result["description_placeholders"]["url"] == NIBA_CLIENT_URL


async def test_every_placeholder_in_strings_is_provided(hass: HomeAssistant) -> None:
    """Each {placeholder} in strings.json must be filled by the flow."""

    import json
    import pathlib
    import re

    strings = json.loads(
        (
            pathlib.Path(__file__).parent.parent / "custom_components/niba/strings.json"
        ).read_text()
    )

    with _patch_client():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        provided = set(result["description_placeholders"] or {})
        needed = set(
            re.findall(r"\{(\w+)\}", strings["config"]["step"]["user"]["description"])
        )
        assert needed <= provided

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: _jwt()}
        )
        provided = set(result["description_placeholders"] or {})
        needed = set(
            re.findall(r"\{(\w+)\}", strings["config"]["step"]["cups"]["description"])
        )
        assert needed <= provided
