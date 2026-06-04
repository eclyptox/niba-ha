"""API client and parsers for Niba.

The Niba private API uses an authorization header in the form
``authorization: token <JWT>``. The JWT is copied by the user from the browser
and is decoded locally only to read non-sensitive metadata such as ``exp`` and
``email``; its signature is validated by Niba when calling ``/users/me``.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any, Protocol

API_BASE_URL = "https://api.clientes.niba.es/api"


class NibaApiError(Exception):
    """Base exception for Niba API failures."""


class NibaAuthError(NibaApiError):
    """Raised when the Niba token is missing, invalid, or rejected."""


class NibaPayloadError(NibaApiError):
    """Raised when Niba returns data in an unexpected shape."""


class HttpResponse(Protocol):
    """Small protocol matching aiohttp-like responses used by the client."""

    status: int

    async def json(self) -> Any:
        """Return decoded JSON."""

    async def text(self) -> str:
        """Return response text."""

    def raise_for_status(self) -> None:
        """Raise an HTTP exception."""


class HttpSession(Protocol):
    """Small protocol matching aiohttp.ClientSession enough for tests."""

    def get(self, url: str, **kwargs: Any) -> Any:
        """Return an async context manager yielding an HTTP response."""


@dataclass(frozen=True)
class TokenInfo:
    """Decoded metadata from the JWT payload."""

    raw_token: str
    expires_at: datetime | None
    email: str | None
    payload: Mapping[str, Any]

    @property
    def is_expired(self) -> bool:
        """Return whether the token is already expired."""

        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)


@dataclass(frozen=True)
class User:
    """Niba account user."""

    name: str | None
    last_name: str | None
    email: str | None
    auth_provider: str | None
    member_code: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class Bill:
    """Niba bill."""

    id: str | None
    billing_code: str | None
    start_at: str | None
    end_at: str | None
    act_total_consumption: float | None
    base_amount: float | None
    tax_amount: float | None
    total_amount: float | None
    status: str | None
    self_consumption_surplus: float | None
    self_consumption_discount: float | None
    total_surplus: float | None
    wallet_surplus: float | None
    term_power: float | None
    nm_term_ener: float | None
    nm_rental_amount: float | None
    raw: Mapping[str, Any]

    @property
    def period(self) -> str | None:
        """Return a human-readable billing period."""

        if not self.start_at and not self.end_at:
            return None
        if not self.start_at:
            return self.end_at
        if not self.end_at:
            return self.start_at
        return f"{self.start_at} - {self.end_at}"


@dataclass(frozen=True)
class ConsumptionPeriod:
    """Current billing period consumption."""

    cups_electricity: str | None
    start_at: str | None
    end_at: str | None
    date_last_data: str | None
    consumption_value: float | None
    consumption_amount: float | None
    average_value: float | None
    average_amount: float | None
    estimated_consumption_amount: float | None
    previous_period_comparison: float | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class Balance:
    """Niba wallet and solar battery balances."""

    amount: float | None
    pending_amount: float | None
    total_loaded: float | None
    total_spent: float | None
    solar_battery: float | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class NibaData:
    """Aggregated Niba data used by the coordinator."""

    user: User
    bills: tuple[Bill, ...]
    consumption_period: ConsumptionPeriod | None
    balance: Balance | None

    @property
    def last_bill(self) -> Bill | None:
        """Return the most recent bill, assuming the API returns newest first."""

        return self.bills[0] if self.bills else None

    @property
    def accumulated_consumption(self) -> float | None:
        """Return historical bills plus current-period kWh for Energy Dashboard."""

        values = [
            bill.act_total_consumption
            for bill in self.bills
            if bill.act_total_consumption is not None
        ]
        if (
            self.consumption_period is not None
            and self.consumption_period.consumption_value is not None
        ):
            values.append(self.consumption_period.consumption_value)
        return sum(values) if values else None


def decode_token(token: str) -> TokenInfo:
    """Decode JWT payload metadata without verifying the signature."""

    raw_token = normalize_token(token)
    parts = raw_token.split(".")
    if len(parts) < 2:
        raise NibaAuthError("Invalid JWT format")

    try:
        payload_bytes = _base64url_decode(parts[1])
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        raise NibaAuthError("Invalid JWT payload") from err

    if not isinstance(payload, Mapping):
        raise NibaAuthError("Invalid JWT payload shape")

    exp = payload.get("exp")
    expires_at = None
    if isinstance(exp, int | float):
        expires_at = datetime.fromtimestamp(exp, UTC)

    email = payload.get("email")
    return TokenInfo(
        raw_token=raw_token,
        expires_at=expires_at,
        email=email if isinstance(email, str) else None,
        payload=payload,
    )


def normalize_token(token: str) -> str:
    """Strip optional copied authorization prefix from the JWT."""

    token = token.strip()
    prefix = "token "
    if token.lower().startswith(prefix):
        token = token[len(prefix) :].strip()
    if not token:
        raise NibaAuthError("Empty token")
    return token


def parse_user(payload: Mapping[str, Any]) -> User:
    """Parse ``/users/me``."""

    return User(
        name=_str_or_none(payload.get("name")),
        last_name=_str_or_none(payload.get("last_name")),
        email=_str_or_none(payload.get("email")),
        auth_provider=_str_or_none(payload.get("auth_provider")),
        member_code=_str_or_none(payload.get("member_code")),
        raw=payload,
    )


def parse_bills(payload: Any) -> tuple[Bill, ...]:
    """Parse ``/cups/{cups}/bills``."""

    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        raise NibaPayloadError("Bills response must be a list")

    bills: list[Bill] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise NibaPayloadError("Bill item must be an object")
        bills.append(
            Bill(
                id=_str_or_none(item.get("id")),
                billing_code=_str_or_none(item.get("billing_code")),
                start_at=_str_or_none(item.get("start_at")),
                end_at=_str_or_none(item.get("end_at")),
                act_total_consumption=_float_or_none(item.get("act_total_consumption")),
                base_amount=_float_or_none(item.get("base_amount")),
                tax_amount=_float_or_none(item.get("tax_amount")),
                total_amount=_float_or_none(item.get("total_amount")),
                status=_str_or_none(item.get("status")),
                self_consumption_surplus=_float_or_none(
                    item.get("self_consumption_surplus")
                ),
                self_consumption_discount=_float_or_none(
                    item.get("self_consumption_discount")
                ),
                total_surplus=_float_or_none(item.get("total_surplus")),
                wallet_surplus=_float_or_none(item.get("wallet_surplus")),
                term_power=_float_or_none(item.get("term_power")),
                nm_term_ener=_float_or_none(item.get("nm_term_ener")),
                nm_rental_amount=_float_or_none(item.get("nm_rental_amount")),
                raw=item,
            )
        )
    return tuple(bills)


def parse_consumption_period(payload: Mapping[str, Any]) -> ConsumptionPeriod:
    """Parse ``/cups/{cups}/consumption-period``."""

    return ConsumptionPeriod(
        cups_electricity=_str_or_none(payload.get("cups_electricity")),
        start_at=_str_or_none(payload.get("start_at")),
        end_at=_str_or_none(payload.get("end_at")),
        date_last_data=_str_or_none(payload.get("date_last_data")),
        consumption_value=_float_or_none(payload.get("consumption_value")),
        consumption_amount=_float_or_none(payload.get("consumption_amount")),
        average_value=_float_or_none(payload.get("average_value")),
        average_amount=_float_or_none(payload.get("average_amount")),
        estimated_consumption_amount=_float_or_none(
            payload.get("estimated_consumption_amount")
        ),
        previous_period_comparison=_float_or_none(
            payload.get("previous_period_comparison")
        ),
        raw=payload,
    )


def parse_balance(payload: Mapping[str, Any]) -> Balance:
    """Parse ``/balances``."""

    return Balance(
        amount=_float_or_none(payload.get("amount")),
        pending_amount=_float_or_none(payload.get("pending_amount")),
        total_loaded=_float_or_none(payload.get("total_loaded")),
        total_spent=_float_or_none(payload.get("total_spent")),
        solar_battery=_float_or_none(payload.get("solar_battery")),
        raw=payload,
    )


class NibaApiClient:
    """Async client for confirmed Niba API endpoints."""

    def __init__(
        self,
        session: HttpSession,
        token: str,
        *,
        base_url: str = API_BASE_URL,
    ) -> None:
        self._session = session
        self._token = normalize_token(token)
        self._base_url = base_url.rstrip("/")

    @property
    def token_info(self) -> TokenInfo:
        """Return decoded JWT metadata."""

        return decode_token(self._token)

    async def get_user(self) -> User:
        """Fetch and parse the current user."""

        payload = await self._get("/users/me")
        if not isinstance(payload, Mapping):
            raise NibaPayloadError("User response must be an object")
        return parse_user(payload)

    async def get_bills(self, cups: str) -> tuple[Bill, ...]:
        """Fetch and parse historical bills for a CUPS."""

        return parse_bills(await self._get(f"/cups/{cups}/bills"))

    async def get_consumption_period(self, cups: str) -> ConsumptionPeriod:
        """Fetch and parse current billing-period consumption for a CUPS."""

        payload = await self._get(f"/cups/{cups}/consumption-period")
        if not isinstance(payload, Mapping):
            raise NibaPayloadError("Consumption period response must be an object")
        return parse_consumption_period(payload)

    async def get_balance(self) -> Balance:
        """Fetch and parse wallet and solar battery balances."""

        payload = await self._get("/balances")
        if not isinstance(payload, Mapping):
            raise NibaPayloadError("Balance response must be an object")
        return parse_balance(payload)

    async def fetch_data(self, cups: str) -> NibaData:
        """Fetch all confirmed Niba resources used by the integration."""

        user, bills, consumption_period, balance = await asyncio.gather(
            self.get_user(),
            self.get_bills(cups),
            self.get_consumption_period(cups),
            self.get_balance(),
        )
        return NibaData(
            user=user,
            bills=bills,
            consumption_period=consumption_period,
            balance=balance,
        )

    async def validate_token(self) -> User:
        """Validate credentials by calling Niba's user endpoint."""

        return await self.get_user()

    async def _get(self, path: str) -> Any:
        headers = {
            "authorization": f"token {self._token}",
            "accept": "application/json",
        }
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status in (401, 403):
                    raise NibaAuthError("Niba rejected the token")
                if response.status >= 400:
                    text = await response.text()
                    raise NibaApiError(f"Niba API error {response.status}: {text}")
                return await response.json()
        except NibaApiError:
            raise
        except Exception as err:  # noqa: BLE001 - keep client independent of aiohttp.
            raise NibaApiError("Error communicating with Niba API") from err


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
