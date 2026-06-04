"""Data coordinator for Niba."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NibaApiClient, NibaApiError, NibaAuthError, NibaData
from .const import API_REFRESH_MINUTES, BILLS_REFRESH_HOURS, CONF_CUPS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class NibaCoordinator(DataUpdateCoordinator[NibaData]):
    """Fetch Niba data every hour; refresh bills only every 6 hours."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: NibaApiClient,
    ) -> None:
        self.client = client
        self._cups: str = entry.data[CONF_CUPS]
        self._cached_bills: tuple = ()
        self._last_bills_fetch: datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=API_REFRESH_MINUTES),
        )

    async def _async_update_data(self) -> NibaData:
        try:
            if self._should_refresh_bills():
                data = await self.client.fetch_data(self._cups)
                self._cached_bills = data.bills
                self._last_bills_fetch = datetime.now(UTC)
                _LOGGER.debug("balance raw: %s", data.balance.raw if data.balance else None)
                _LOGGER.debug(
                    "consumption_period raw: %s",
                    data.consumption_period.raw if data.consumption_period else None,
                )
                _LOGGER.debug(
                    "last_bill raw: %s",
                    data.last_bill.raw if data.last_bill else None,
                )
                return data

            user, consumption_period, balance = await asyncio.gather(
                self.client.get_user(),
                self.client.get_consumption_period(self._cups),
                self.client.get_balance(),
            )
            return NibaData(
                user=user,
                bills=self._cached_bills,
                consumption_period=consumption_period,
                balance=balance,
            )
        except NibaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NibaApiError as err:
            raise UpdateFailed(str(err)) from err
        except (ConfigEntryAuthFailed, UpdateFailed):
            raise
        except Exception as err:
            raise UpdateFailed(f"Error inesperado actualizando Niba: {err}") from err

    def _should_refresh_bills(self) -> bool:
        if self._last_bills_fetch is None:
            return True
        return datetime.now(UTC) - self._last_bills_fetch >= timedelta(
            hours=BILLS_REFRESH_HOURS
        )
