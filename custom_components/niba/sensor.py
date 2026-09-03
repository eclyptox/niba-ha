"""Sensors for Niba."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NibaData
from .const import DOMAIN
from .coordinator import NibaCoordinator


@dataclass(frozen=True)
class NibaSensorDescription(SensorEntityDescription):
    """SensorEntityDescription plus a value extractor and optional extra attrs."""

    value_fn: Callable[[NibaData], Any] | None = None
    extra_attrs_fn: Callable[[NibaData], dict[str, Any]] | None = None
    restore_floor: bool = False
    """Seed the coordinator's monotonic clamp from the restored state."""


SENSORS: tuple[NibaSensorDescription, ...] = (
    # ── Período actual ────────────────────────────────────────────────────────
    NibaSensorDescription(
        key="consumption_value",
        name="Consumo período actual",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:lightning-bolt",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.consumption_period.consumption_value, 2)
            if d.consumption_period
            and d.consumption_period.consumption_value is not None
            else None
        ),
        extra_attrs_fn=lambda d: (
            {
                "inicio": d.consumption_period.start_at,
                "fin": d.consumption_period.end_at,
                "ultima_lectura": d.consumption_period.date_last_data,
                "dias_transcurridos": d.consumption_period.elapsed_days,
                "dias_restantes": d.consumption_period.remaining_days,
            }
            if d.consumption_period
            else {}
        ),
    ),
    NibaSensorDescription(
        key="consumption_amount",
        name="Importe período actual",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:currency-eur",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.consumption_period.consumption_amount, 2)
            if d.consumption_period
            and d.consumption_period.consumption_amount is not None
            else None
        ),
    ),
    NibaSensorDescription(
        key="estimated_consumption_amount",
        name="Importe estimado fin de período",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
        name="Comparación período anterior",
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
        name="Saldo monedero",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
        name="Batería solar",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
        name="Última factura",
        native_unit_of_measurement="€",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:receipt",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.last_bill.total_amount, 2)
            if d.last_bill and d.last_bill.total_amount is not None
            else None
        ),
        extra_attrs_fn=lambda d: (
            {
                "billing_code": d.last_bill.billing_code,
                "periodo": d.last_bill.period,
                "estado": d.last_bill.status,
                "consumo_kwh": d.last_bill.act_total_consumption,
                "base_imponible": d.last_bill.base_amount,
                "impuestos": d.last_bill.tax_amount,
                "termino_potencia": d.last_bill.term_power,
                "termino_energia": d.last_bill.nm_term_ener,
                "alquiler_contador": d.last_bill.nm_rental_amount,
                "excedente_autoconsumo": d.last_bill.self_consumption_surplus,
                "descuento_autoconsumo": d.last_bill.self_consumption_discount,
                "excedente_total": d.last_bill.total_surplus,
                "excedente_monedero": d.last_bill.wallet_surplus,
            }
            if d.last_bill
            else {}
        ),
    ),
    # ── Medias diarias ────────────────────────────────────────────────────────────
    NibaSensorDescription(
        key="daily_average_value",
        name="Consumo medio diario",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-line",
        suggested_display_precision=1,
        value_fn=lambda d: (
            round(d.consumption_period.daily_average_value, 2)
            if d.consumption_period
            and d.consumption_period.daily_average_value is not None
            else None
        ),
    ),
    NibaSensorDescription(
        key="daily_average_amount",
        name="Importe medio diario",
        native_unit_of_measurement="€",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cash-clock",
        suggested_display_precision=2,
        value_fn=lambda d: (
            round(d.consumption_period.daily_average_amount, 2)
            if d.consumption_period
            and d.consumption_period.daily_average_amount is not None
            else None
        ),
    ),
    # ── Diagnóstico ───────────────────────────────────────────────────────────────
    NibaSensorDescription(
        key="token_expires_at",
        name="Caducidad del token",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:key-alert-outline",
        value_fn=lambda d: d.token_expires_at,
    ),
    # ── Energy Dashboard ──────────────────────────────────────────────────────
    NibaSensorDescription(
        key="accumulated_consumption",
        restore_floor=True,
        name="Consumo acumulado",
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
    """Return one device per config entry, so several CUPS stay separate."""

    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "manufacturer": "Niba",
        "name": entry.title or "Niba",
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Niba sensors."""

    coordinator: NibaCoordinator = entry.runtime_data
    async_add_entities(
        (NibaRestoringSensor if description.restore_floor else NibaSensor)(
            coordinator, entry, description
        )
        for description in SENSORS
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
        """Bind one sensor description to the shared coordinator."""

        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_has_entity_name = True
        self._attr_name = description.name
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        """Return whether the coordinator holds usable data."""

        return self.coordinator.data is not None

    @property
    def native_value(self) -> Any:
        """Return the sensor value extracted from the latest Niba data."""

        if self.coordinator.data is None or self.entity_description.value_fn is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the description's extra attributes, if it declares any."""

        if (
            self.coordinator.data is None
            or self.entity_description.extra_attrs_fn is None
        ):
            return {}
        return self.entity_description.extra_attrs_fn(self.coordinator.data)


class NibaRestoringSensor(NibaSensor, RestoreSensor):
    """Accumulated sensor that seeds the coordinator clamp on restart."""

    async def async_added_to_hass(self) -> None:
        """Feed the last known total back into the coordinator."""

        await super().async_added_to_hass()
        last_data = await self.async_get_last_sensor_data()
        if last_data is None:
            return
        try:
            self.coordinator.seed_accumulated_floor(float(last_data.native_value))
        except (TypeError, ValueError):
            return
