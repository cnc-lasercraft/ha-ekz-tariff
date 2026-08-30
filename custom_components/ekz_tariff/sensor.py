"""Sensor platform for EKZ Tariff."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEBUG_MODE,
    CONF_MAX_RETRIES,
    CONF_MIN_PRICE_CHF_PER_KWH,
    CONF_MAX_PRICE_CHF_PER_KWH,
    CONF_PUBLIC_FALLBACK_ENABLED,
    CONF_PUBLIC_FALLBACK_GRID_OFFSET,
    CONF_PUBLIC_FALLBACK_GRID_TARIFF,
    CONF_PUBLIC_FALLBACK_REGIONAL_FEES,
    CONF_PUBLISH_TIME,
    CONF_RETRY_INTERVAL_MINUTES,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MIN_PRICE_CHF_PER_KWH,
    DEFAULT_MAX_PRICE_CHF_PER_KWH,
    DEFAULT_PUBLIC_FALLBACK_ENABLED,
    DEFAULT_PUBLIC_FALLBACK_GRID_OFFSET,
    DEFAULT_PUBLIC_FALLBACK_GRID_TARIFF,
    DEFAULT_PUBLIC_FALLBACK_REGIONAL_FEES,
    DEFAULT_PUBLISH_TIME,
    DEFAULT_RETRY_INTERVAL_MINUTES,
    DOMAIN,
)

_SETTINGS_KEYS = (
    CONF_PUBLISH_TIME,
    CONF_MIN_PRICE_CHF_PER_KWH,
    CONF_MAX_PRICE_CHF_PER_KWH,
    CONF_MAX_RETRIES,
    CONF_RETRY_INTERVAL_MINUTES,
    CONF_DEBUG_MODE,
    CONF_PUBLIC_FALLBACK_ENABLED,
    CONF_PUBLIC_FALLBACK_GRID_TARIFF,
    CONF_PUBLIC_FALLBACK_GRID_OFFSET,
    CONF_PUBLIC_FALLBACK_REGIONAL_FEES,
)

_SETTINGS_DEFAULTS = {
    CONF_PUBLISH_TIME: DEFAULT_PUBLISH_TIME,
    CONF_MIN_PRICE_CHF_PER_KWH: DEFAULT_MIN_PRICE_CHF_PER_KWH,
    CONF_MAX_PRICE_CHF_PER_KWH: DEFAULT_MAX_PRICE_CHF_PER_KWH,
    CONF_MAX_RETRIES: DEFAULT_MAX_RETRIES,
    CONF_RETRY_INTERVAL_MINUTES: DEFAULT_RETRY_INTERVAL_MINUTES,
    CONF_DEBUG_MODE: False,
    CONF_PUBLIC_FALLBACK_ENABLED: DEFAULT_PUBLIC_FALLBACK_ENABLED,
    CONF_PUBLIC_FALLBACK_GRID_TARIFF: DEFAULT_PUBLIC_FALLBACK_GRID_TARIFF,
    CONF_PUBLIC_FALLBACK_GRID_OFFSET: DEFAULT_PUBLIC_FALLBACK_GRID_OFFSET,
    CONF_PUBLIC_FALLBACK_REGIONAL_FEES: DEFAULT_PUBLIC_FALLBACK_REGIONAL_FEES,
}


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title,
        "manufacturer": "EKZ",
        "model": "Tariff API",
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    activity_log_sensor = EkzActivityLogSensor(entry)
    settings_sensor = EkzSettingsSensor(entry)
    link_status_sensor = EkzLinkStatusSensor(entry)

    # Register so __init__.py can update state
    domain_data = hass.data[DOMAIN]
    domain_data[f"{entry.entry_id}_activity_log_sensor"] = activity_log_sensor
    domain_data[f"{entry.entry_id}_link_status_sensor"] = link_status_sensor
    domain_data[f"{entry.entry_id}_settings_sensor"] = settings_sensor

    async_add_entities(
        [activity_log_sensor, settings_sensor, link_status_sensor],
        update_before_add=False,
    )


class EkzActivityLogSensor(SensorEntity):
    """Activity log showing recent operations."""

    _attr_has_entity_name = True
    _attr_name = "Activity Log"
    _attr_icon = "mdi:history"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ekz_activity_log"
        self._attr_device_info = _device_info(entry)
        self._log_entries: list[dict[str, str]] = []

    @callback
    def set_log(self, entries: list[dict[str, str]]) -> None:
        """Update log entries. Called from __init__.py."""
        self._log_entries = entries
        if self.hass:
            self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        if not self._log_entries:
            return "Keine Aktivität"
        latest = self._log_entries[0]
        return f"{latest.get('icon', '')} {latest.get('msg', '')}"[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "entries": self._log_entries,
            "count": len(self._log_entries),
        }


class EkzSettingsSensor(SensorEntity):
    """Exposes current settings for the dashboard card."""

    _attr_has_entity_name = True
    _attr_name = "Settings"
    _attr_icon = "mdi:cog"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_settings"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str:
        return "configured"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        opts = dict(self._entry.data)
        opts.update(dict(self._entry.options))
        settings: dict[str, Any] = {}
        for key in _SETTINGS_KEYS:
            settings[key] = opts.get(key, _SETTINGS_DEFAULTS.get(key))
        return {"settings": settings, "entry_id": self._entry.entry_id}


class EkzLinkStatusSensor(SensorEntity):
    """EMS link status."""

    _attr_has_entity_name = True
    _attr_name = "Link status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:link"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_link_status"
        self._attr_device_info = _device_info(entry)
        self._link_status: str | None = None

    @callback
    def set_link_status(self, status: str | None) -> None:
        """Update link status. Called from __init__.py."""
        self._link_status = status
        if self.hass:
            self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        return self._link_status
