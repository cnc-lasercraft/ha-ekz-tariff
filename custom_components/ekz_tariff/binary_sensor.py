"""Binary sensor for EKZ Tariff error state."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


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
    sensor = EkzTariffErrorSensor(entry)
    # Register so __init__.py can call set_error()
    hass.data[DOMAIN][f"{entry.entry_id}_error_sensor"] = sensor
    async_add_entities([sensor], update_before_add=False)


class EkzTariffErrorSensor(BinarySensorEntity):
    """ON when EKZ Tariff failed to fetch valid data after max retries."""

    _attr_has_entity_name = True
    _attr_name = "Error"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:alert-circle"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_error"
        self._attr_device_info = _device_info(entry)
        self._error_state: dict[str, Any] | None = None

    @callback
    def set_error(self, error_state: dict[str, Any] | None) -> None:
        """Update error state and write to HA. Called from __init__.py."""
        self._error_state = error_state
        if self.hass:
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._error_state is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._error_state is None:
            return {"error_type": None, "message": None, "retry_count": 0}
        return {
            "error_type": self._error_state.get("error_type"),
            "message": self._error_state.get("message"),
            "retry_count": self._error_state.get("retry_count", 0),
            "last_attempt": self._error_state.get("last_attempt"),
            "details": self._error_state.get("details"),
        }
