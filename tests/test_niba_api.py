"""Tests for the Niba API parsers."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import json

import pytest

from custom_components.niba.api import (
    NibaAuthError,
    NibaData,
    decode_token,
    normalize_cups,
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


def test_normalize_cups_uses_first_20_characters() -> None:
    assert normalize_cups(" es0021000000000000aa0f ") == "ES0021000000000000AA"
    assert normalize_cups("ES0021000000000000AA") == "ES0021000000000000AA"


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
    data = NibaData(
        user=parse_user({}), bills=bills, consumption_period=None, balance=None
    )

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


def _data(
    bills_payload: list[dict], period_value: float | None, floor: float | None = None
) -> NibaData:
    return NibaData(
        user=parse_user({}),
        bills=parse_bills(bills_payload),
        consumption_period=(
            None
            if period_value is None
            else parse_consumption_period({"consumption_value": period_value})
        ),
        balance=None,
        accumulated_floor=floor,
    )


def test_accumulated_consumption_without_floor_matches_raw_sum() -> None:
    data = _data([{"act_total_consumption": 100}], 10)

    assert data.raw_accumulated_consumption == 110
    assert data.accumulated_consumption == 110


def test_accumulated_consumption_clamps_the_dip_when_a_period_closes() -> None:
    """Period restarts at ~0 before its bill lands: the total must not drop."""

    data = _data([{"act_total_consumption": 100}], 0.4, floor=110.0)

    assert data.raw_accumulated_consumption == 100.4
    assert data.accumulated_consumption == 110.0


def test_accumulated_consumption_resumes_growing_once_the_bill_arrives() -> None:
    data = _data(
        [{"act_total_consumption": 100}, {"act_total_consumption": 12}],
        3.0,
        floor=110.0,
    )

    assert data.accumulated_consumption == 115.0


def test_accumulated_consumption_falls_back_to_floor_without_data() -> None:
    data = _data([], None, floor=110.0)

    assert data.raw_accumulated_consumption is None
    assert data.accumulated_consumption == 110.0


def test_accumulated_consumption_is_none_without_data_or_floor() -> None:
    assert _data([], None).accumulated_consumption is None


def test_last_bill_falls_back_to_first_when_no_bill_has_an_end_date() -> None:
    bills = parse_bills([{"id": "1"}, {"id": "2"}])
    data = NibaData(
        user=parse_user({}), bills=bills, consumption_period=None, balance=None
    )

    assert data.last_bill is not None
    assert data.last_bill.id == "1"


def test_last_bill_is_none_without_bills() -> None:
    data = NibaData(
        user=parse_user({}), bills=(), consumption_period=None, balance=None
    )

    assert data.last_bill is None


def _period(**payload: object):
    return parse_consumption_period(
        {"start_at": "2026-08-01", "end_at": "2026-09-02", **payload}
    )


def test_elapsed_days_matches_nibas_own_day_count() -> None:
    """Niba shows 32 days for 1 Aug - 2 Sep, so the diff must not be inclusive."""

    period = _period(date_last_data="2026-09-02")

    assert period.elapsed_days == 32


def test_elapsed_days_tolerates_a_full_iso_timestamp() -> None:
    period = _period(date_last_data="2026-09-02T10:30:00Z")

    assert period.elapsed_days == 32


def test_elapsed_days_is_none_without_a_start_date() -> None:
    period = parse_consumption_period({"date_last_data": "2026-09-02"})

    assert period.elapsed_days is None
    assert period.daily_average_value is None


def test_daily_averages_prefer_the_values_niba_sends() -> None:
    period = _period(
        date_last_data="2026-09-02",
        consumption_value=406.04,
        consumption_amount=72.90,
        average_value=99.0,
        average_amount=88.0,
    )

    assert period.daily_average_value == 99.0
    assert period.daily_average_amount == 88.0


def test_daily_averages_are_computed_when_niba_omits_them() -> None:
    """Reproduces the 12.7 kWh / 2.28 EUR the web dashboard shows."""

    period = _period(
        date_last_data="2026-09-02",
        consumption_value=406.04,
        consumption_amount=72.90,
    )

    assert round(period.daily_average_value, 1) == 12.7
    assert round(period.daily_average_amount, 2) == 2.28


def test_remaining_days_is_never_negative() -> None:
    period = parse_consumption_period({"end_at": "2020-01-01"})

    assert period.remaining_days == 0


def test_remaining_days_is_none_without_an_end_date() -> None:
    assert parse_consumption_period({}).remaining_days is None


def test_period_without_a_closed_day_yields_no_average() -> None:
    """A period that started today has no elapsed day to divide by."""

    today = datetime.now(UTC).date().isoformat()
    period = parse_consumption_period(
        {"start_at": today, "date_last_data": today, "consumption_value": 5}
    )

    assert period.elapsed_days is None
    assert period.daily_average_value is None
