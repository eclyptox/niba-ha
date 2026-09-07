"""Config flow for Niba."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import (
    NibaApiClient,
    NibaApiError,
    NibaAuthError,
    decode_token,
    normalize_cups,
)
from .const import CONF_CUPS, CONF_TOKEN, DOMAIN, NIBA_CLIENT_URL


async def _discover_cups(session: Any, token: str) -> tuple[str, ...]:
    """Return the account's CUPS, or an empty tuple if they can't be read.

    Niba's frontend takes the CUPS from the contract rather than asking for
    it. Offering those values avoids the typos and stale codes that surface
    later as ``value_error.cups_not_found``.
    """

    try:
        contracts = await NibaApiClient(session, token).get_contracts()
    except NibaApiError:
        return ()

    active = [c.cups for c in contracts if c.is_active_electricity and c.cups]
    other = [c.cups for c in contracts if not c.is_active_electricity and c.cups]
    seen: dict[str, None] = {}
    for cups in [*active, *other]:
        seen.setdefault(normalize_cups(cups), None)
    return tuple(seen)


class NibaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Two-step config flow: token → CUPS."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow state shared between the token and CUPS steps."""

        self._token: str = ""
        self._user_email: str | None = None
        self._discovered: tuple[str, ...] = ()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: validate the JWT token against /users/me."""

        errors: dict[str, str] = {}
        if user_input is not None:
            raw = user_input[CONF_TOKEN]
            try:
                info = decode_token(raw)
                if info.is_expired:
                    errors[CONF_TOKEN] = "token_expired"
                else:
                    session = async_get_clientsession(self.hass)
                    client = NibaApiClient(session, raw)
                    await client.validate_token()
                    self._token = info.raw_token
                    self._user_email = info.email
                    self._discovered = await _discover_cups(
                        async_get_clientsession(self.hass), self._token
                    )
                    return await self.async_step_cups()
            except NibaAuthError:
                errors[CONF_TOKEN] = "invalid_auth"
            except NibaApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
            description_placeholders={"url": NIBA_CLIENT_URL},
        )

    async def async_step_cups(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: validate CUPS by fetching the current consumption period."""

        errors: dict[str, str] = {}
        if user_input is not None:
            cups = normalize_cups(user_input[CONF_CUPS])
            try:
                session = async_get_clientsession(self.hass)
                client = NibaApiClient(session, self._token)
                await client.get_consumption_period(cups)
            except NibaAuthError:
                errors["base"] = "invalid_auth"
            except NibaApiError:
                errors[CONF_CUPS] = "invalid_cups"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                # Include the CUPS so one Niba account can hold several
                # supply points; existing entries keep their email unique_id.
                unique_id = f"{self._user_email}:{cups}" if self._user_email else cups
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Niba {cups}",
                    data={CONF_TOKEN: self._token, CONF_CUPS: cups},
                )

        if self._discovered:
            schema = vol.Schema(
                {
                    vol.Required(CONF_CUPS, default=self._discovered[0]): vol.In(
                        self._discovered
                    )
                }
            )
        else:
            schema = vol.Schema({vol.Required(CONF_CUPS): str})

        return self.async_show_form(
            step_id="cups",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start the reauth flow when the stored token stops working."""

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-enter a new token when the current one has expired."""

        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            raw = user_input[CONF_TOKEN]
            try:
                info = decode_token(raw)
                if info.is_expired:
                    errors[CONF_TOKEN] = "token_expired"
                else:
                    session = async_get_clientsession(self.hass)
                    client = NibaApiClient(session, raw)
                    await client.validate_token()
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data_updates={CONF_TOKEN: info.raw_token},
                    )
            except NibaAuthError:
                errors[CONF_TOKEN] = "invalid_auth"
            except NibaApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
            description_placeholders={"url": NIBA_CLIENT_URL},
        )
