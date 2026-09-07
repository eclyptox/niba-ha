"""Tests for NibaApiClient using a fake HTTP session.

These tests exercise the client without Home Assistant or aiohttp: the client
only depends on the ``HttpSession`` / ``HttpResponse`` protocols declared in
``niba.api``.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.niba.api import (
    API_BASE_URL,
    NibaApiClient,
    NibaApiError,
    NibaAuthError,
    NibaPayloadError,
)


class _FakeResponse:
    """Minimal stand-in for an aiohttp response."""

    def __init__(
        self,
        status: int = 200,
        payload: Any = None,
        text: str = "",
        raise_on_json: Exception | None = None,
    ) -> None:
        self.status = status
        self._payload = payload
        self._text = text
        self._raise_on_json = raise_on_json

    async def json(self) -> Any:
        if self._raise_on_json is not None:
            raise self._raise_on_json
        return self._payload

    async def text(self) -> str:
        return self._text


class _FakeRequest:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeSession:
    """Records requests and replays queued responses by path suffix."""

    def __init__(self, responses: dict[str, _FakeResponse | Exception]) -> None:
        self._responses = responses
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeRequest:
        self.requests.append((url, kwargs.get("headers", {})))
        for suffix, response in self._responses.items():
            if url.endswith(suffix):
                return _FakeRequest(response)
        raise AssertionError(f"Unexpected request to {url}")

    @property
    def paths(self) -> list[str]:
        return [url.removeprefix(API_BASE_URL) for url, _ in self.requests]


CUPS = "ES0021000000000000AA"
CUPS_LONG = "ES0021000000000000AA0F"


def _client(
    responses: dict[str, _FakeResponse | Exception],
) -> tuple[NibaApiClient, _FakeSession]:
    session = _FakeSession(responses)
    return NibaApiClient(session, "eyJtoken"), session


async def test_get_user_sends_token_authorization_header() -> None:
    client, session = _client({"/users/me": _FakeResponse(payload={"email": "a@b.c"})})

    user = await client.get_user()

    assert user.email == "a@b.c"
    _, headers = session.requests[0]
    assert headers["authorization"] == "token eyJtoken"
    assert headers["accept"] == "application/json"


async def test_requests_target_the_documented_endpoints() -> None:
    client, session = _client(
        {
            "/users/me": _FakeResponse(payload={"email": "a@b.c"}),
            "/bills": _FakeResponse(payload=[]),
            "/consumption-period": _FakeResponse(payload={"consumption_value": 1}),
            "/balances": _FakeResponse(payload={"amount": 2}),
        }
    )

    await client.fetch_data(CUPS)

    assert sorted(session.paths) == sorted(
        [
            "/users/me",
            f"/cups/{CUPS}/bills",
            f"/cups/{CUPS}/consumption-period",
            "/balances",
        ]
    )


async def test_cups_is_normalized_in_request_urls() -> None:
    client, session = _client(
        {
            "/bills": _FakeResponse(payload=[]),
            "/consumption-period": _FakeResponse(payload={}),
        }
    )

    await client.get_bills(CUPS_LONG)
    await client.get_consumption_period(f"  {CUPS_LONG.lower()}  ")

    assert session.paths == [
        f"/cups/{CUPS}/bills",
        f"/cups/{CUPS}/consumption-period",
    ]


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_token_raises_auth_error(status: int) -> None:
    client, _ = _client({"/users/me": _FakeResponse(status=status)})

    with pytest.raises(NibaAuthError):
        await client.get_user()


async def test_server_error_includes_status_and_body() -> None:
    client, _ = _client({"/users/me": _FakeResponse(status=500, text="boom")})

    with pytest.raises(NibaApiError) as err:
        await client.get_user()

    assert "500" in str(err.value)
    assert "boom" in str(err.value)
    assert "/users/me" in str(err.value), "the failing endpoint must be named"


async def test_auth_error_is_not_masked_as_generic_api_error() -> None:
    """NibaAuthError must survive the broad except in _get for reauth to work."""

    client, _ = _client({"/users/me": _FakeResponse(status=401)})

    with pytest.raises(NibaAuthError):
        await client.get_user()


async def test_transport_failure_becomes_api_error() -> None:
    client, _ = _client({"/users/me": OSError("connection reset")})

    with pytest.raises(NibaApiError) as err:
        await client.get_user()

    assert isinstance(err.value.__cause__, OSError)


async def test_invalid_json_becomes_api_error() -> None:
    client, _ = _client(
        {"/users/me": _FakeResponse(raise_on_json=ValueError("not json"))}
    )

    with pytest.raises(NibaApiError):
        await client.get_user()


async def test_non_object_consumption_period_raises_payload_error() -> None:
    client, _ = _client({"/consumption-period": _FakeResponse(payload=[1, 2])})

    with pytest.raises(NibaPayloadError):
        await client.get_consumption_period(CUPS)


async def test_non_object_balance_raises_payload_error() -> None:
    client, _ = _client({"/balances": _FakeResponse(payload="nope")})

    with pytest.raises(NibaPayloadError):
        await client.get_balance()


async def test_cups_errors_name_the_failing_endpoint() -> None:
    """A 404 on /cups/... is the CUPS being wrong; the path has to be visible."""

    client, _ = _client(
        {
            "/consumption-period": _FakeResponse(
                status=404, text='{"detail":[{"code":"value_error.cups_not_found"}]}'
            )
        }
    )

    with pytest.raises(NibaApiError) as err:
        await client.get_consumption_period(CUPS)

    message = str(err.value)
    assert f"/cups/{CUPS}/consumption-period" in message
    assert "cups_not_found" in message


async def test_non_list_bills_raises_payload_error() -> None:
    client, _ = _client({"/bills": _FakeResponse(payload={"not": "a list"})})

    with pytest.raises(NibaPayloadError):
        await client.get_bills(CUPS)


async def test_fetch_data_propagates_auth_error_from_any_endpoint() -> None:
    client, _ = _client(
        {
            "/users/me": _FakeResponse(payload={"email": "a@b.c"}),
            "/bills": _FakeResponse(payload=[]),
            "/consumption-period": _FakeResponse(payload={}),
            "/balances": _FakeResponse(status=401),
        }
    )

    with pytest.raises(NibaAuthError):
        await client.fetch_data(CUPS)
