"""Config flow for EKZ Tariff."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow

from .const import (
    CONF_DEBUG_MODE,
    CONF_MAX_PRICE_CHF_PER_KWH,
    CONF_MAX_RETRIES,
    CONF_MIN_PRICE_CHF_PER_KWH,
    CONF_PUBLISH_TIME,
    CONF_REDIRECT_URI,
    CONF_RETRY_INTERVAL_MINUTES,
    DEFAULT_MAX_PRICE_CHF_PER_KWH,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MIN_PRICE_CHF_PER_KWH,
    DEFAULT_PUBLISH_TIME,
    DEFAULT_RETRY_INTERVAL_MINUTES,
    DOMAIN,
    DEFAULT_NAME,
)

_LOGGER = logging.getLogger(__name__)


def _generate_ems_instance_id() -> str:
    return f"ha-{uuid.uuid4().hex}"


def _get_auth_impl_id(flow: config_entry_oauth2_flow.AbstractOAuth2FlowHandler) -> str | None:
    impl = getattr(flow, "flow_impl", None)
    if impl is None:
        return None
    for attr in ("implementation_id", "id", "domain"):
        value = getattr(impl, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_token(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        if "token" in data and isinstance(data["token"], dict):
            return data["token"]
        if "access_token" in data or "refresh_token" in data:
            return data
    return None


class ConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle a config flow for EKZ Tariff."""

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        return {"scope": "openid offline_access"}

    def __init__(self) -> None:
        super().__init__()
        self._name: str = DEFAULT_NAME
        self._redirect_uri: str | None = None
        self._publish_time: str = DEFAULT_PUBLISH_TIME
        self._ems_instance_id: str | None = None
        self._ekz_reauth_entry_id: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._name = str(user_input[CONF_NAME]).strip() or DEFAULT_NAME
            self._redirect_uri = str(user_input[CONF_REDIRECT_URI]).strip()
            self._publish_time = str(user_input.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)).strip()
            self._ems_instance_id = _generate_ems_instance_id()
            await self.async_set_unique_id(f"ekz_tariff::{self._name.lower()}")
            self._abort_if_unique_id_configured()
            return await self.async_step_pick_implementation()

        default_redirect = (self.hass.config.external_url or "").rstrip("/") + "/"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                    vol.Required(CONF_REDIRECT_URI, default=default_redirect or "https://"): str,
                    vol.Optional(CONF_PUBLISH_TIME, default=DEFAULT_PUBLISH_TIME): str,
                }
            ),
        )

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None):
        entry_id = (
            self.context.get("entry_id")
            or self.context.get("source_entry_id")
            or self.context.get("reauth_entry_id")
        )
        self._ekz_reauth_entry_id = entry_id
        if entry_id:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry:
                self._name = entry.data.get(CONF_NAME, entry.title)
                self._redirect_uri = entry.data.get(CONF_REDIRECT_URI)
                self._publish_time = entry.data.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)
                self._ems_instance_id = entry.data.get("ems_instance_id")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return await self.async_step_pick_implementation()
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({}))

    async def async_oauth_create_entry(self, data: dict[str, Any]):
        return await self.async_step_auth_create_entry(data)

    async def async_step_auth_create_entry(self, data: dict[str, Any]):
        token = _extract_token(data)
        auth_impl = _get_auth_impl_id(self)
        entry_id = (
            self._ekz_reauth_entry_id
            or self.context.get("entry_id")
            or self.context.get("source_entry_id")
            or self.context.get("reauth_entry_id")
        )

        if entry_id:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry:
                updates = {
                    CONF_NAME: self._name or entry.data.get(CONF_NAME) or DEFAULT_NAME,
                    CONF_REDIRECT_URI: self._redirect_uri or entry.data.get(CONF_REDIRECT_URI),
                    CONF_PUBLISH_TIME: self._publish_time or entry.data.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME),
                    "ems_instance_id": self._ems_instance_id or entry.data.get("ems_instance_id"),
                    "auth_implementation": auth_impl or entry.data.get("auth_implementation") or DOMAIN,
                }
                if token is not None:
                    updates["token"] = token
                return self.async_update_reload_and_abort(entry, data_updates=updates)

        entry_data = {
            CONF_NAME: self._name or DEFAULT_NAME,
            CONF_REDIRECT_URI: self._redirect_uri,
            CONF_PUBLISH_TIME: self._publish_time,
            "ems_instance_id": self._ems_instance_id,
            "auth_implementation": auth_impl or DOMAIN,
        }
        if token is not None:
            entry_data["token"] = token
        return self.async_create_entry(title=self._name or DEFAULT_NAME, data=entry_data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EkzTariffOptionsFlow(config_entry)


class EkzTariffOptionsFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler):
    """Handle EKZ Tariff options."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PUBLISH_TIME,
                        default=opts.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME),
                    ): str,
                    vol.Optional(
                        CONF_MIN_PRICE_CHF_PER_KWH,
                        default=opts.get(CONF_MIN_PRICE_CHF_PER_KWH, DEFAULT_MIN_PRICE_CHF_PER_KWH),
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_MAX_PRICE_CHF_PER_KWH,
                        default=opts.get(CONF_MAX_PRICE_CHF_PER_KWH, DEFAULT_MAX_PRICE_CHF_PER_KWH),
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_MAX_RETRIES,
                        default=opts.get(CONF_MAX_RETRIES, DEFAULT_MAX_RETRIES),
                    ): vol.Coerce(int),
                    vol.Optional(
                        CONF_RETRY_INTERVAL_MINUTES,
                        default=opts.get(CONF_RETRY_INTERVAL_MINUTES, DEFAULT_RETRY_INTERVAL_MINUTES),
                    ): vol.Coerce(int),
                    vol.Optional(
                        CONF_DEBUG_MODE,
                        default=opts.get(CONF_DEBUG_MODE, False),
                    ): bool,
                }
            ),
        )
