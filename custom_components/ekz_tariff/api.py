"""EKZ tariff API client (myEKZ OAuth2)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

import aiohttp
from aiohttp import ClientError

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

_LOGGER = logging.getLogger(__name__)


class EkzTariffApiError(RuntimeError):
    """Raised when EKZ API calls fail."""


class EkzTariffAuthError(EkzTariffApiError):
    """Raised for OAuth/token problems."""


class EkzTariffApi:
    BASE_URL: Final[str] = "https://api.tariffs.ekz.ch/v1"
    CHF_PER_KWH_UNITS: Final[set[str]] = {"CHF_kWh", "CHF/kWh"}

    def __init__(self, session: aiohttp.ClientSession, oauth_session: OAuth2Session | None = None) -> None:
        self._session = session
        self._oauth_session = oauth_session

    # -- Token management --

    # Keycloak error strings that indicate the refresh token is genuinely invalid
    _AUTH_ERROR_KEYWORDS: frozenset = frozenset({
        "invalid_grant",
        "session_not_active",
        "token_expired",
        "token is not active",
    })

    async def _async_get_access_token(self) -> str:
        if not self._oauth_session:
            raise EkzTariffAuthError("No OAuth session available (myEKZ not configured)")
        try:
            await self._oauth_session.async_ensure_token_valid()
        except ConfigEntryAuthFailed:
            raise
        except aiohttp.ClientResponseError as err:
            if err.status in (400, 401):
                _LOGGER.warning("EKZ token refresh HTTP %s – reauthentication required", err.status)
                raise ConfigEntryAuthFailed(f"EKZ token refresh HTTP {err.status}") from err
            _LOGGER.warning("EKZ token refresh transient HTTP %s (will retry)", err.status)
            raise EkzTariffApiError(f"EKZ token refresh transient failure: {err}") from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("EKZ token refresh network error (will retry): %s", err)
            raise EkzTariffApiError(f"EKZ token refresh network error: {err}") from err
        except Exception as err:
            msg = str(err).lower()
            if any(kw in msg for kw in self._AUTH_ERROR_KEYWORDS):
                _LOGGER.warning("EKZ token genuinely invalid – reauthentication required: %s", err)
                raise ConfigEntryAuthFailed(f"EKZ token invalid: {err}") from err
            _LOGGER.warning("EKZ token refresh unexpected error (treating as transient): %s", err)
            raise EkzTariffApiError(f"EKZ token refresh failed: {err}") from err

        token = self._oauth_session.token or {}
        access_token = token.get("access_token")
        if not access_token:
            raise ConfigEntryAuthFailed("EKZ OAuth token missing access_token")
        _LOGGER.debug("EKZ access token OK, expires_in=%s", token.get("expires_in"))
        return access_token

    # -- API calls --

    async def fetch_ems_link_status(self, *, ems_instance_id: str, redirect_uri: str) -> dict[str, Any]:
        access_token = await self._async_get_access_token()
        url = f"{self.BASE_URL}/emsLinkStatus"
        params = {"ems_instance_id": ems_instance_id, "redirect_uri": redirect_uri}
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        try:
            async with self._session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                if resp.status == 401:
                    raise EkzTariffAuthError(f"401 Unauthorized: {text}")
                if resp.status >= 400:
                    raise EkzTariffApiError(f"EKZ API error {resp.status}: {text}")
                data: Any = await resp.json()
                _LOGGER.debug("emsLinkStatus payload=%r", data)
        except ClientError as err:
            raise EkzTariffApiError(f"HTTP error calling emsLinkStatus: {err}") from err

        if not isinstance(data, dict):
            raise EkzTariffApiError(f"Unexpected emsLinkStatus payload: {data!r}")
        return data

    async def fetch_customer_tariffs(
        self,
        *,
        ems_instance_id: str,
        tariff_type: str | None = None,
        start_timestamp: str | None = None,
        end_timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Fetch customer tariffs. Returns dict with 'prices' list and 'publication_timestamp'."""
        access_token = await self._async_get_access_token()
        url = f"{self.BASE_URL}/customerTariffs"
        params: dict[str, str] = {"ems_instance_id": ems_instance_id}
        if tariff_type:
            params["tariffType"] = tariff_type
        if start_timestamp:
            params["start_timestamp"] = start_timestamp
        if end_timestamp:
            params["end_timestamp"] = end_timestamp

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        try:
            async with self._session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                _LOGGER.debug("customerTariffs status=%s body=%s", resp.status, text[:1200])
                if resp.status == 401:
                    raise EkzTariffAuthError(f"401 Unauthorized: {text}")
                if resp.status >= 400:
                    raise EkzTariffApiError(f"EKZ API error {resp.status}: {text}")
                data: Any = await resp.json()
        except ClientError as err:
            raise EkzTariffApiError(f"HTTP error calling customerTariffs: {err}") from err

        # Normalize response shape
        if isinstance(data, list):
            return {"prices": [x for x in data if isinstance(x, dict)], "publication_timestamp": None}

        if isinstance(data, dict):
            prices = data.get("prices")
            if isinstance(prices, list):
                return {
                    "prices": [x for x in prices if isinstance(x, dict)],
                    "publication_timestamp": data.get("publication_timestamp"),
                }
            tariffs = data.get("tariffs")
            if isinstance(tariffs, list):
                return {
                    "prices": [x for x in tariffs if isinstance(x, dict)],
                    "publication_timestamp": data.get("publication_timestamp"),
                }

        raise EkzTariffApiError(f"Unexpected customerTariffs payload: {data!r}")

    async def fetch_public_tariffs(
        self,
        *,
        tariff_name: str,
        start_timestamp: str,
        end_timestamp: str,
    ) -> dict[str, Any]:
        """Fetch public tariffs (no auth). Returns dict with 'prices' list and 'publication_timestamp'."""
        url = f"{self.BASE_URL}/tariffs"
        params = {
            "tariff_name": tariff_name,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
        }
        headers = {"Accept": "application/json"}

        try:
            async with self._session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                _LOGGER.debug("public tariffs %s status=%s body=%s", tariff_name, resp.status, text[:600])
                if resp.status >= 400:
                    raise EkzTariffApiError(f"EKZ public API error {resp.status}: {text}")
                data: Any = await resp.json()
        except ClientError as err:
            raise EkzTariffApiError(f"HTTP error calling public tariffs: {err}") from err

        if isinstance(data, dict):
            prices = data.get("prices")
            if isinstance(prices, list):
                return {
                    "prices": [x for x in prices if isinstance(x, dict)],
                    "publication_timestamp": data.get("publication_timestamp"),
                }

        raise EkzTariffApiError(f"Unexpected public tariffs payload: {data!r}")

    # -- Helpers --

    @staticmethod
    def extract_chf_per_kwh(val: Any) -> float | None:
        """Extract CHF/kWh value from a component field (scalar, dict, or list)."""
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, list):
            for entry in val:
                if isinstance(entry, dict) and entry.get("unit") in ("CHF_kWh", "CHF/kWh"):
                    v = entry.get("value")
                    if isinstance(v, (int, float)):
                        return float(v)
        if isinstance(val, dict):
            if val.get("unit") in ("CHF_kWh", "CHF/kWh"):
                v = val.get("value")
                if isinstance(v, (int, float)):
                    return float(v)
        return None
