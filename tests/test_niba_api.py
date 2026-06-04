"""Tests for the Niba API parsers."""

from __future__ import annotations

import base64
import json

import pytest

from niba.api import (
    NibaAuthError,
    NibaData,
    decode_token,
    parse_balance,
    parse_bills,
    parse_consumption_period,
    parse_user,
)


def _jwt(payload: dict[str, object]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    parts = []
    for item in (header, payload, b"signature"):
        if isinstance(item, bytes):
            encoded = base64.urlsafe_b64encode(item)
        else:
            encoded = base64.urlsafe_b64encode(json.dumps(item).encode())
        parts.append(encoded.rstrip(b"=").decode())
    return ".".join(parts)


def test_decode_token_accepts_authorization_header_value() -> None:
    token = _jwt({"email": "user@example.com", "exp": 1_799_000_000})

    info = decode_token(f"token {token}")

    assert info.raw_token == token
    assert info.email == "user@example.com"
    assert info.expires_at is not None


def test_decode_token_rejects_invalid_jwt() -> None:
    with pytest.raises(NibaAuthError):
        decode_token("not-a-jwt")


def test_parse_user() -> None:
    user = parse_user(
        {
            "name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "auth_provider": "GOOGLE",
            "member_code": "NIBA-1",
        }
    )

    assert user.email == "ada@example.com"
    assert user.auth_provider == "GOOGLE"


def test_parse_bills_keeps_solar_fields_as_first_class_values() -> None:
    bills = parse_bills(
        [
            {
                "id": 123,
                "billing_code": "F-1",
                "start_at": "2026-01-01",
                "end_at": "2026-01-31",
                "act_total_consumption": "100.5",
                "total_amount": 42.12,
                "status": "PAID",
                "self_consumption_surplus": "12.3",
                "self_consumption_discount": "4.56",
                "total_surplus": "98.7",
            }
        ]
    )

    assert bills[0].id == "123"
    assert bills[0].act_total_consumption == 100.5
    assert bills[0].self_consumption_surplus == 12.3
    assert bills[0].self_consumption_discount == 4.56
    assert bills[0].total_surplus == 98.7
    assert bills[0].period == "2026-01-01 - 2026-01-31"


def test_parse_consumption_period_and_balance() -> None:
    period = parse_consumption_period(
        {
            "cups_electricity": "ES0000000000000000AA0A",
            "consumption_value": "17.5",
            "consumption_amount": "6.2",
            "average_value": "1.1",
            "average_amount": "0.4",
            "previous_period_comparison": "0.92",
        }
    )
    balance = parse_balance(
        {
            "amount": 10,
            "pending_amount": "2.5",
            "solar_battery": "13.25",
        }
    )

    assert period.consumption_value == 17.5
    assert period.previous_period_comparison == 0.92
    assert balance.pending_amount == 2.5
    assert balance.solar_battery == 13.25


def test_monetary_fields_accept_amount_currency_tuple() -> None:
    """Niba API returns monetary values as [amount, "EUR"] instead of plain floats."""
    balance = parse_balance(
        {
            "amount": [9.51, "EUR"],
            "pending_amount": [0.0, "EUR"],
            "solar_battery": [9.51, "EUR"],
        }
    )
    period = parse_consumption_period(
        {
            "consumption_value": 231.0,
            "consumption_amount": [50.43, "EUR"],
            "estimated_consumption_amount": [50.43, "EUR"],
        }
    )
    bills = parse_bills(
        [{"total_amount": [8.67, "EUR"], "act_total_consumption": 101.0}]
    )

    assert balance.amount == 9.51
    assert balance.solar_battery == 9.51
    assert period.consumption_amount == 50.43
    assert period.estimated_consumption_amount == 50.43
    assert bills[0].total_amount == 8.67


def test_last_bill_returns_most_recent_by_end_at() -> None:
    bills = parse_bills(
        [
            {"id": "1", "end_at": "2026-03-31", "total_amount": [8.67, "EUR"]},
            {"id": "2", "end_at": "2026-04-30", "total_amount": [17.34, "EUR"]},
        ]
    )
    data = NibaData(user=parse_user({}), bills=bills, consumption_period=None, balance=None)

    assert data.last_bill is not None
    assert data.last_bill.id == "2"
    assert data.last_bill.total_amount == 17.34


def test_accumulated_consumption_adds_bills_and_current_period() -> None:
    bills = parse_bills(
        [
            {"act_total_consumption": 100},
            {"act_total_consumption": "50.5"},
        ]
    )
    period = parse_consumption_period({"consumption_value": "10.5"})
    user = parse_user({})

    data = NibaData(user=user, bills=bills, consumption_period=period, balance=None)

    assert data.accumulated_consumption == 161.0
