"""Sensors for Niba."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NibaCoordinator
from .api import NibaData


@dataclass(frozen=True)
class NibaSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a value extractor and optional extra attrs."""

    value_fn: Callable[[NibaData], Any] | None = None
    extra_attrs_fn: Callable[[NibaData], dict[str, Any]] | None = None


SENSORS: tuple[NibaSensorDescription, ...] = (
    # ── Período actual ────────────────────────────────────────────────────────
    NibaSensorDescription(
        key="consumption_value",
        name="Consumo período actual (Niba)",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:lightning-bolt",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.consumption_period.consumption_value, 2)
            if d.consumption_period and d.consumption_period.consumption_value is not None
            else None
        ),
        extra_attrs_fn=lambda d: (
            {
                "inicio": d.consumption_period.start_at,
                "fin": d.consumption_period.end_at,
                "ultima_lectura": d.consumption_period.date_last_data,
            }
            if d.consumption_period
            else {}
        ),
    ),
    NibaSensorDescription(
        key="consumption_amount",
        name="Importe período actual (Niba)",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:currency-eur",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.consumption_period.consumption_amount, 2)
            if d.consumption_period and d.consumption_period.consumption_amount is not None
            else None
        ),
    ),
    NibaSensorDescription(
        key="estimated_consumption_amount",
        name="Importe estimado fin de período (Niba)",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cash-clock",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.consumption_period.estimated_consumption_amount, 2)
            if d.consumption_period
            and d.consumption_period.estimated_consumption_amount is not None
            else None
        ),
    ),
    NibaSensorDescription(
        key="previous_period_comparison",
        name="Comparación período anterior (Niba)",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent",
        suggested_display_precision=1,
        value_fn=lambda d: (
            round(d.consumption_period.previous_period_comparison * 100, 1)
            if d.consumption_period
            and d.consumption_period.previous_period_comparison is not None
            else None
        ),
    ),
    # ── Saldo ─────────────────────────────────────────────────────────────────
    NibaSensorDescription(
        key="balance_amount",
        name="Saldo monedero (Niba)",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wallet",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.balance.amount, 2)
            if d.balance and d.balance.amount is not None
            else None
        ),
        extra_attrs_fn=lambda d: (
            {
                "pendiente": d.balance.pending_amount,
                "total_cargado": d.balance.total_loaded,
                "total_gastado": d.balance.total_spent,
            }
            if d.balance
            else {}
        ),
    ),
    NibaSensorDescription(
        key="solar_battery",
        name="Batería solar (Niba)",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.balance.solar_battery, 2)
            if d.balance and d.balance.solar_battery is not None
            else None
        ),
    ),
    # ── Última factura ────────────────────────────────────────────────────────
    NibaSensorDescription(
        key="last_bill_amount",
        name="Última factura (Niba)",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:receipt",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.last_bill.total_amount, 2)
            if d.last_bill and d.last_bill.total_amount is not None
            else None
        ),
        extra_attrs_fn=lambda d: (
            {
                "periodo": d.last_bill.period,
                "estado": d.last_bill.status,
                "consumo_kwh": d.last_bill.act_total_consumption,
                "excedente_autoconsumo": d.last_bill.self_consumption_surplus,
                "descuento_autoconsumo": d.last_bill.self_consumption_discount,
            }
            if d.last_bill
            else {}
        ),
    ),
    # ── Energy Dashboard ──────────────────────────────────────────────────────
    NibaSensorDescription(
        key="accumulated_consumption",
        name="Consumo acumulado (Niba)",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt-circle",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.accumulated_consumption, 2)
            if d.accumulated_consumption is not None
            else None
        ),
    ),
)


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "manufacturer": "Niba",
        "name": "Niba",
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Niba sensors."""

    coordinator: NibaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NibaSensor(coordinator, entry, description) for description in SENSORS
    )


class NibaSensor(CoordinatorEntity[NibaCoordinator], SensorEntity):
    """A Niba sensor backed by the coordinator."""

    entity_description: NibaSensorDescription

    def __init__(
        self,
        coordinator: NibaCoordinator,
        entry: ConfigEntry,
        description: NibaSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_has_entity_name = True
        self._attr_name = description.name
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None or self.entity_description.value_fn is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None or self.entity_description.extra_attrs_fn is None:
            return {}
        return self.entity_description.extra_attrs_fn(self.coordinator.data)
