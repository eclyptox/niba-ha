"""End-to-end tests: set up the integration and inspect the entities."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.niba.api import (
    NibaData,
    parse_balance,
    parse_bills,
    parse_consumption_period,
    parse_user,
)
from custom_components.niba.const import CONF_CUPS, CONF_TOKEN, DOMAIN

CUPS = "ES0021000000000000AA"


def _niba_data(consumption: float = 10.0) -> NibaData:
    return NibaData(
        user=parse_user({"email": "a@b.c"}),
        bills=parse_bills(
            [
                {
                    "id": "1",
                    "billing_code": "F-1",
                    "start_at": "2026-03-01",
                    "end_at": "2026-03-31",
                    "act_total_consumption": 100,
                    "total_amount": [17.34, "EUR"],
                    "status": "PAID",
                }
            ]
        ),
        consumption_period=parse_consumption_period(
            {
                "consumption_value": consumption,
                "consumption_amount": [12.5, "EUR"],
                "estimated_consumption_amount": [30.0, "EUR"],
                "previous_period_comparison": 0.125,
                "start_at": "2026-04-01",
                "end_at": "2026-04-30",
            }
        ),
        balance=parse_balance({"amount": [9.51, "EUR"], "solar_battery": [3.2, "EUR"]}),
    )


async def _setup(hass: HomeAssistant, data: Any = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"a@b.c:{CUPS}",
        data={CONF_TOKEN: "eyJtoken", CONF_CUPS: CUPS},
        title=f"Niba {CUPS}",
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.fetch_data.return_value = data if data is not None else _niba_data()
    with (
        patch("custom_components.niba.api.NibaApiClient", return_value=client),
        # Avoid creating a real aiohttp session: the client is mocked anyway.
        patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_setup_creates_every_declared_sensor(hass: HomeAssistant) -> None:
    from custom_components.niba.sensor import SENSORS

    await _setup(hass)

    states = [s for s in hass.states.async_all("sensor") if "niba" in s.entity_id]
    assert len(states) == len(SENSORS)


async def test_sensor_values_come_from_the_coordinator(hass: HomeAssistant) -> None:
    await _setup(hass)

    assert (
        hass.states.get("sensor.niba_es0021000000000000aa_consumo_periodo_actual").state
        == "10.0"
    )
    assert (
        hass.states.get("sensor.niba_es0021000000000000aa_saldo_monedero").state
        == "9.51"
    )
    assert (
        hass.states.get("sensor.niba_es0021000000000000aa_ultima_factura").state
        == "17.34"
    )
    assert (
        hass.states.get("sensor.niba_es0021000000000000aa_consumo_acumulado").state
        == "110.0"
    )


async def test_percentage_sensor_is_scaled(hass: HomeAssistant) -> None:
    await _setup(hass)

    state = hass.states.get(
        "sensor.niba_es0021000000000000aa_comparacion_periodo_anterior"
    )
    assert state.state == "12.5"


async def test_last_bill_exposes_its_period_as_attributes(hass: HomeAssistant) -> None:
    await _setup(hass)

    state = hass.states.get("sensor.niba_es0021000000000000aa_ultima_factura")
    assert state.attributes["billing_code"] == "F-1"
    assert state.attributes["periodo"] == "2026-03-01 - 2026-03-31"
    assert state.attributes["estado"] == "PAID"


async def test_accumulated_sensor_feeds_the_energy_dashboard(
    hass: HomeAssistant,
) -> None:
    await _setup(hass)

    state = hass.states.get("sensor.niba_es0021000000000000aa_consumo_acumulado")
    assert state.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING
    assert state.attributes["unit_of_measurement"] == "kWh"


async def test_missing_payload_sections_do_not_break_the_sensors(
    hass: HomeAssistant,
) -> None:
    """Every value_fn must tolerate a payload without period or balance."""

    empty = NibaData(
        user=parse_user({}), bills=(), consumption_period=None, balance=None
    )
    await _setup(hass, data=empty)

    states = [s for s in hass.states.async_all("sensor") if "niba" in s.entity_id]
    assert states, "sensors should still be created"
    assert all(s.state != STATE_UNAVAILABLE for s in states)


async def test_unload_makes_the_entities_unavailable(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    states = [s for s in hass.states.async_all("sensor") if "niba" in s.entity_id]
    assert states
    assert all(s.state == STATE_UNAVAILABLE for s in states)


async def test_every_sensor_declares_a_state_class(hass: HomeAssistant) -> None:
    """Without a state_class Home Assistant keeps no long-term statistics."""

    from custom_components.niba.sensor import SENSORS

    missing = [d.key for d in SENSORS if d.state_class is None]
    assert not missing


async def test_state_classes_are_valid_for_their_device_class(
    hass: HomeAssistant,
) -> None:
    """Check each description against Home Assistant's own compatibility map."""

    from homeassistant.components.sensor.const import DEVICE_CLASS_STATE_CLASSES

    from custom_components.niba.sensor import SENSORS

    invalid = [
        (d.key, d.device_class, d.state_class)
        for d in SENSORS
        if d.device_class is not None
        and d.state_class not in DEVICE_CLASS_STATE_CLASSES.get(d.device_class, set())
    ]
    assert not invalid


async def test_monetary_sensors_report_statistics(hass: HomeAssistant) -> None:
    await _setup(hass)

    for entity_id in (
        "sensor.niba_es0021000000000000aa_saldo_monedero",
        "sensor.niba_es0021000000000000aa_bateria_solar",
        "sensor.niba_es0021000000000000aa_ultima_factura",
        "sensor.niba_es0021000000000000aa_importe_estimado_fin_de_periodo",
    ):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.attributes["state_class"] == SensorStateClass.TOTAL, entity_id
