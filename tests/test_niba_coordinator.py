"""Tests for NibaCoordinator, running against a real Home Assistant instance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.niba.api import (
    Contract,
    NibaApiError,
    NibaAuthError,
    NibaData,
    parse_balance,
    parse_bills,
    parse_consumption_period,
    parse_user,
)
from custom_components.niba.const import CONF_CUPS, CONF_TOKEN, DOMAIN
from custom_components.niba.coordinator import EVENT_NEW_BILL, NibaCoordinator

CUPS = "ES0021000000000000AA"


class _FakeClient:
    """Client double that records calls and replays scripted payloads."""

    def __init__(
        self,
        bills: list[dict[str, Any]] | None = None,
        period_value: float | None = 10.0,
        error: Exception | None = None,
        cups_not_found: bool = False,
        discovers: str | None = None,
    ) -> None:
        self.bills = bills if bills is not None else []
        self.period_value = period_value
        self.error = error
        self.cups_not_found = cups_not_found
        self.discovers = discovers
        self.bills_calls = 0
        self.period_calls = 0
        self.discover_calls = 0

    async def get_contracts(self):
        return (
            Contract(
                cups=self.discovers,
                status="Active",
                contract_number="1",
                family="Electricity",
                town="DENIA",
                raw={},
            ),
        )

    async def discover_cups(self) -> str | None:
        self.discover_calls += 1
        return self.discovers

    def _maybe_raise(self) -> None:
        if self.error is not None:
            raise self.error
        if self.cups_not_found:
            raise NibaApiError(
                "Niba API error 404 on /cups/X/consumption-period: "
                '{"detail":[{"code":"value_error.cups_not_found"}]}'
            )

    async def get_user(self):
        self._maybe_raise()
        return parse_user({"email": "a@b.c"})

    async def get_bills(self, cups: str):
        self._maybe_raise()
        self.bills_calls += 1
        return parse_bills(self.bills)

    async def get_consumption_period(self, cups: str):
        self._maybe_raise()
        self.period_calls += 1
        return parse_consumption_period({"consumption_value": self.period_value})

    async def get_balance(self):
        self._maybe_raise()
        return parse_balance({"amount": 5})

    async def fetch_data(self, cups: str) -> NibaData:
        return NibaData(
            user=await self.get_user(),
            bills=await self.get_bills(cups),
            consumption_period=await self.get_consumption_period(cups),
            balance=await self.get_balance(),
        )


def _entry(cups: str = CUPS) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, data={CONF_TOKEN: "eyJtoken", CONF_CUPS: cups}
    )


def _coordinator(hass: HomeAssistant, client: _FakeClient) -> NibaCoordinator:
    entry = _entry()
    entry.add_to_hass(hass)
    return NibaCoordinator(hass, entry, client)


async def test_long_cups_from_an_existing_entry_is_normalized(
    hass: HomeAssistant,
) -> None:
    entry = _entry(f"{CUPS}0F")
    entry.add_to_hass(hass)
    client = _FakeClient()

    coordinator = NibaCoordinator(hass, entry, client)

    assert coordinator._cups == CUPS


async def test_bills_are_cached_between_refresh_windows(hass: HomeAssistant) -> None:
    client = _FakeClient(bills=[{"id": "1", "act_total_consumption": 100}])
    coordinator = _coordinator(hass, client)

    first = await coordinator._async_update_data()
    second = await coordinator._async_update_data()

    assert client.bills_calls == 1, "bills must not be refetched within 6 hours"
    assert client.period_calls == 2
    assert second.bills == first.bills


async def test_bills_are_refetched_after_the_cache_window(hass: HomeAssistant) -> None:
    client = _FakeClient(bills=[{"id": "1"}])
    coordinator = _coordinator(hass, client)

    await coordinator._async_update_data()
    coordinator._last_bills_fetch = datetime.now(UTC) - timedelta(hours=7)
    await coordinator._async_update_data()

    assert client.bills_calls == 2


async def test_first_load_records_the_bill_without_notifying(
    hass: HomeAssistant,
) -> None:
    events = []
    hass.bus.async_listen(EVENT_NEW_BILL, events.append)
    client = _FakeClient(bills=[{"id": "1", "billing_code": "F-1"}])
    coordinator = _coordinator(hass, client)

    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events == []
    assert coordinator._known_bill_id == "F-1"


async def test_a_new_bill_fires_the_bus_event(hass: HomeAssistant) -> None:
    events = []
    hass.bus.async_listen(EVENT_NEW_BILL, events.append)
    client = _FakeClient(
        bills=[{"id": "1", "billing_code": "F-1", "end_at": "2026-03-31"}]
    )
    coordinator = _coordinator(hass, client)
    await coordinator._async_update_data()

    client.bills = [
        {"id": "1", "billing_code": "F-1", "end_at": "2026-03-31"},
        {
            "id": "2",
            "billing_code": "F-2",
            "end_at": "2026-04-30",
            "total_amount": [17.34, "EUR"],
        },
    ]
    coordinator._last_bills_fetch = datetime.now(UTC) - timedelta(hours=7)
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["billing_code"] == "F-2"
    assert events[0].data["total_amount"] == 17.34


async def test_rejected_token_triggers_reauth(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, _FakeClient(error=NibaAuthError("nope")))

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_api_error_becomes_update_failed(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, _FakeClient(error=NibaApiError("boom")))

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_unexpected_error_becomes_update_failed(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, _FakeClient(error=RuntimeError("boom")))

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_accumulated_total_never_drops_when_a_period_closes(
    hass: HomeAssistant,
) -> None:
    """The dip between a closed period and its bill must not look like a reset."""

    client = _FakeClient(
        bills=[{"id": "1", "act_total_consumption": 100}], period_value=10.0
    )
    coordinator = _coordinator(hass, client)
    first = await coordinator._async_update_data()
    assert first.accumulated_consumption == 110.0

    # Niba closes the period: it restarts near zero and the bill is not out yet.
    client.period_value = 0.4
    second = await coordinator._async_update_data()

    assert second.raw_accumulated_consumption == 100.4
    assert second.accumulated_consumption == 110.0


async def test_accumulated_total_grows_again_once_the_bill_lands(
    hass: HomeAssistant,
) -> None:
    client = _FakeClient(
        bills=[{"id": "1", "act_total_consumption": 100}], period_value=10.0
    )
    coordinator = _coordinator(hass, client)
    await coordinator._async_update_data()

    client.bills = [
        {"id": "1", "act_total_consumption": 100},
        {"id": "2", "act_total_consumption": 12},
    ]
    client.period_value = 3.0
    coordinator._last_bills_fetch = datetime.now(UTC) - timedelta(hours=7)
    data = await coordinator._async_update_data()

    assert data.accumulated_consumption == 115.0


async def test_seed_accumulated_floor_keeps_the_highest_value(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, _FakeClient())

    coordinator.seed_accumulated_floor(None)
    assert coordinator._accumulated_floor is None

    coordinator.seed_accumulated_floor(120.0)
    coordinator.seed_accumulated_floor(80.0)

    assert coordinator._accumulated_floor == 120.0


REAL_CUPS = "ES0021000011349260ME"


async def test_stale_cups_is_replaced_with_the_one_on_the_contract(
    hass: HomeAssistant,
) -> None:
    """A 404 cups_not_found must recover from /contracts, not just fail."""

    client = _FakeClient(cups_not_found=True, discovers=REAL_CUPS)
    entry = _entry("ES0021999999999999ZZ")
    entry.add_to_hass(hass)
    coordinator = NibaCoordinator(hass, entry, client)

    with pytest.raises(UpdateFailed):
        # The double keeps failing, so the retry fails too; what matters is
        # that the CUPS was rediscovered and persisted.
        await coordinator._async_update_data()

    assert client.discover_calls == 1
    assert coordinator._cups == REAL_CUPS
    assert entry.data[CONF_CUPS] == REAL_CUPS


async def test_recovered_cups_makes_the_retry_succeed(hass: HomeAssistant) -> None:
    class _RecoveringClient(_FakeClient):
        async def get_consumption_period(self, cups: str):
            if cups != REAL_CUPS:
                raise NibaApiError(
                    "Niba API error 404 on /cups/X/consumption-period: "
                    '{"detail":[{"code":"value_error.cups_not_found"}]}'
                )
            return await super().get_consumption_period(cups)

    client = _RecoveringClient(discovers=REAL_CUPS)
    entry = _entry("ES0021999999999999ZZ")
    entry.add_to_hass(hass)
    coordinator = NibaCoordinator(hass, entry, client)

    data = await coordinator._async_update_data()

    assert coordinator._cups == REAL_CUPS
    assert data.consumption_period is not None


async def test_cups_recovery_is_attempted_only_once(hass: HomeAssistant) -> None:
    client = _FakeClient(cups_not_found=True, discovers=REAL_CUPS)
    entry = _entry("ES0021999999999999ZZ")
    entry.add_to_hass(hass)
    coordinator = NibaCoordinator(hass, entry, client)

    for _ in range(3):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    assert client.discover_calls == 1, "must not hammer /contracts every cycle"


async def test_other_api_errors_do_not_trigger_rediscovery(
    hass: HomeAssistant,
) -> None:
    client = _FakeClient(error=NibaApiError("boom"), discovers=REAL_CUPS)
    coordinator = _coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert client.discover_calls == 0


async def test_auth_errors_still_trigger_reauth_not_rediscovery(
    hass: HomeAssistant,
) -> None:
    client = _FakeClient(error=NibaAuthError("nope"), discovers=REAL_CUPS)
    coordinator = _coordinator(hass, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    assert client.discover_calls == 0


async def test_rediscovery_that_returns_the_same_cups_does_not_loop(
    hass: HomeAssistant,
) -> None:
    client = _FakeClient(cups_not_found=True, discovers=CUPS)
    coordinator = _coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator._cups == CUPS
