"""Constants for EKZ Tariff."""
from __future__ import annotations

DOMAIN = "ekz_tariff"

CONF_PUBLISH_TIME = "publish_time"
CONF_EMS_INSTANCE_ID = "ems_instance_id"
CONF_REDIRECT_URI = "redirect_uri"

# Validation settings
CONF_MIN_PRICE_CHF_PER_KWH = "min_price_chf_per_kwh"
CONF_MAX_PRICE_CHF_PER_KWH = "max_price_chf_per_kwh"
CONF_MAX_RETRIES = "max_retries"
CONF_RETRY_INTERVAL_MINUTES = "retry_interval_minutes"
CONF_DEBUG_MODE = "debug_mode"

# Public-API-Fallback: Kunden-Preise aus öffentlichen Endpoints rekonstruieren,
# wenn customerTariffs nicht liefert (Auth-Fehler, 5xx, leere Antwort).
# customer.electricity == public electricity_dynamic (exakt),
# customer.grid == public <grid_tariff> + konstanter Offset (verifiziert 2026-08-23
# über 12 Stichproben an 4 Tagen: Offset 0.0276, regional_fees 0.0016 konstant).
CONF_PUBLIC_FALLBACK_ENABLED = "public_fallback_enabled"
CONF_PUBLIC_FALLBACK_GRID_TARIFF = "public_fallback_grid_tariff"
CONF_PUBLIC_FALLBACK_GRID_OFFSET = "public_fallback_grid_offset_chf_per_kwh"
CONF_PUBLIC_FALLBACK_REGIONAL_FEES = "public_fallback_regional_fees_chf_per_kwh"

DEFAULT_PUBLISH_TIME = "18:15"
DEFAULT_NAME = "EKZ Tariff"

DEFAULT_MIN_PRICE_CHF_PER_KWH = 0.10
DEFAULT_MAX_PRICE_CHF_PER_KWH = 0.99
DEFAULT_MAX_RETRIES = 4
DEFAULT_RETRY_INTERVAL_MINUTES = 15

DEFAULT_PUBLIC_FALLBACK_ENABLED = True
DEFAULT_PUBLIC_FALLBACK_GRID_TARIFF = "grid_400d"
DEFAULT_PUBLIC_FALLBACK_GRID_OFFSET = 0.0276
DEFAULT_PUBLIC_FALLBACK_REGIONAL_FEES = 0.0016

# Bus event name
EVENT_EKZ_NEW_DATA = "ekz_tariff_new_data"

PLATFORMS: list[str] = ["sensor", "binary_sensor"]
