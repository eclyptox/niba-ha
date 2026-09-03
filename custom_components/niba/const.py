"""Constants for the Niba integration."""

from __future__ import annotations

DOMAIN = "niba"
PLATFORMS: list[str] = ["sensor"]

NIBA_CLIENT_URL = "https://clientes.niba.es"

CONF_CUPS = "cups"
CONF_TOKEN = "token"

API_REFRESH_MINUTES = 60
BILLS_REFRESH_HOURS = 6
