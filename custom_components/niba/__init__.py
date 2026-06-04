"""Home Assistant integration for Niba."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF_TOKEN, DOMAIN, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Niba from a config entry."""

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .api import NibaApiClient
    from .coordinator import NibaCoordinator

    session = async_get_clientsession(hass)
    client = NibaApiClient(session, entry.data[CONF_TOKEN])
    coordinator = NibaCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
