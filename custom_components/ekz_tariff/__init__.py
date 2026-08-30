"""EKZ Tariff integration — slim data provider.

Fetches tomorrow's dynamic electricity prices from the EKZ API,
validates them, and fires a bus event with the slots for Tariff Saver.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import EkzTariffApi, EkzTariffAuthError
from .const import (
    CONF_DEBUG_MODE,
    CONF_EMS_INSTANCE_ID,
    CONF_MAX_PRICE_CHF_PER_KWH,
    CONF_MAX_RETRIES,
    CONF_MIN_PRICE_CHF_PER_KWH,
    CONF_PUBLIC_FALLBACK_ENABLED,
    CONF_PUBLIC_FALLBACK_GRID_OFFSET,
    CONF_PUBLIC_FALLBACK_GRID_TARIFF,
    CONF_PUBLIC_FALLBACK_REGIONAL_FEES,
    CONF_PUBLISH_TIME,
    CONF_REDIRECT_URI,
    CONF_RETRY_INTERVAL_MINUTES,
    DEFAULT_MAX_PRICE_CHF_PER_KWH,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MIN_PRICE_CHF_PER_KWH,
    DEFAULT_PUBLIC_FALLBACK_ENABLED,
    DEFAULT_PUBLIC_FALLBACK_GRID_OFFSET,
    DEFAULT_PUBLIC_FALLBACK_GRID_TARIFF,
    DEFAULT_PUBLIC_FALLBACK_REGIONAL_FEES,
    DEFAULT_PUBLISH_TIME,
    DEFAULT_RETRY_INTERVAL_MINUTES,
    DOMAIN,
    EVENT_EKZ_NEW_DATA,
    PLATFORMS,
)
from .validator import validate_tomorrow_slots

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1  # Keep at 1 to avoid migration issues with old storage files
STORAGE_KEY = "ekz_tariff"


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.strip().split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return 18, 15


def _extract_link_status(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("link_status", "linkStatus", "status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    link = payload.get("link")
    if isinstance(link, dict):
        for key in ("status", "link_status", "linkStatus"):
            value = link.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _parse_customer_slots(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Parse customerTariffs response into {utc_iso: {component: chf_per_kwh}} dict."""
    raw_prices = payload.get("prices", [])
    if not isinstance(raw_prices, list):
        return {}

    result: dict[str, dict[str, float]] = {}
    for item in raw_prices:
        if not isinstance(item, dict):
            continue
        start_ts = item.get("start_timestamp")
        if not isinstance(start_ts, str):
            continue
        dt_start = dt_util.parse_datetime(start_ts)
        if dt_start is None:
            continue

        components: dict[str, float] = {}
        for key in ("electricity", "grid", "regional_fees", "metering", "integrated", "feed_in"):
            value = EkzTariffApi.extract_chf_per_kwh(item.get(key))
            if value is not None:
                components[key] = value

        if components:
            utc_key = dt_util.as_utc(dt_start).isoformat()
            result[utc_key] = components

    return result


def _slots_to_event_payload(slots: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    """Convert internal slot dict to JSON-serializable list for bus event."""
    result = []
    for utc_key in sorted(slots):
        entry = {"start": utc_key}
        entry.update(slots[utc_key])
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
#  Setup
# ---------------------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    # Herold-Topics registrieren (optional, no-op wenn Herold fehlt)
    from . import herold as _herold
    hass.async_create_task(_herold.register_topics(hass))

    config = dict(entry.data)
    config.update(dict(entry.options))

    # OAuth session + API client
    session = async_get_clientsession(hass)
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    api = EkzTariffApi(session, oauth_session=oauth_session)

    ems_instance_id: str | None = config.get(CONF_EMS_INSTANCE_ID)
    redirect_uri: str | None = config.get(CONF_REDIRECT_URI)

    # Storage (minimal)
    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")
    stored = await store.async_load()
    if not isinstance(stored, dict) or stored.get("_version", 0) < 2:
        # Old format or empty — start fresh
        stored = {}

    # State
    activity_log: list[dict[str, str]] = stored.get("activity_log", [])
    error_state: dict[str, Any] | None = stored.get("error_state")
    last_api_success_utc: str | None = stored.get("last_api_success_utc")
    link_status: str | None = stored.get("link_status")
    tomorrow_date: str | None = stored.get("tomorrow_date")
    tomorrow_slots: list[dict[str, Any]] | None = stored.get("slots")

    retry_state: dict[str, Any] = {"count": 0, "pending_cancel": None}

    # ---------------------------------------------------------------------------
    #  Activity Log
    # ---------------------------------------------------------------------------

    def log_activity(icon: str, msg: str) -> None:
        activity_log.insert(0, {"time": dt_util.now().isoformat(), "icon": icon, "msg": msg})
        # Trim: max 50 entries, max 7 days old
        cutoff = (dt_util.now() - timedelta(days=7)).isoformat()
        while len(activity_log) > 50:
            activity_log.pop()
        activity_log[:] = [e for e in activity_log if e.get("time", "") >= cutoff]
        _update_activity_log_sensor()

    def _update_activity_log_sensor() -> None:
        sensor = hass.data[DOMAIN].get(f"{entry.entry_id}_activity_log_sensor")
        if sensor and hasattr(sensor, "set_log"):
            sensor.set_log(activity_log)

    def _update_error_sensor(state: dict[str, Any] | None) -> None:
        nonlocal error_state
        error_state = state
        sensor = hass.data[DOMAIN].get(f"{entry.entry_id}_error_sensor")
        if sensor and hasattr(sensor, "set_error"):
            sensor.set_error(state)

    def _update_link_status_sensor(status: str | None) -> None:
        nonlocal link_status
        link_status = status
        sensor = hass.data[DOMAIN].get(f"{entry.entry_id}_link_status_sensor")
        if sensor and hasattr(sensor, "set_link_status"):
            sensor.set_link_status(status)

    # ---------------------------------------------------------------------------
    #  Storage save
    # ---------------------------------------------------------------------------

    async def _save_storage() -> None:
        await store.async_save({
            "_version": 2,
            "tomorrow_date": tomorrow_date,
            "slots": tomorrow_slots,
            "validated_at": dt_util.utcnow().isoformat() if tomorrow_slots else None,
            "last_api_success_utc": last_api_success_utc,
            "link_status": link_status,
            "activity_log": activity_log[:50],
            "error_state": error_state,
        })

    # ---------------------------------------------------------------------------
    #  Debug dump
    # ---------------------------------------------------------------------------

    def _is_debug_mode() -> bool:
        opts = dict(entry.data)
        opts.update(dict(entry.options))
        return bool(opts.get(CONF_DEBUG_MODE, False))

    async def _write_debug_dump(label: str, request_params: dict, response_raw: Any, parsed_count: int) -> None:
        if not _is_debug_mode():
            return
        debug_dir = hass.config.path("ekz_tariff_debug")
        timestamp = dt_util.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"{timestamp}_{label}.json"
        filepath = os.path.join(debug_dir, filename)
        data = {
            "timestamp": dt_util.now().isoformat(),
            "type": label,
            "request": request_params,
            "response_raw": response_raw,
            "parsed_slot_count": parsed_count,
        }

        def _write():
            os.makedirs(debug_dir, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str, ensure_ascii=False)

        try:
            await hass.async_add_executor_job(_write)
            _LOGGER.debug("EKZ debug: wrote %s", filepath)
        except Exception as err:
            _LOGGER.warning("EKZ debug write failed: %s", err)

    # ---------------------------------------------------------------------------
    #  Retry helpers
    # ---------------------------------------------------------------------------

    def _cancel_pending_retry() -> None:
        cancel = retry_state.get("pending_cancel")
        if callable(cancel):
            cancel()
            retry_state["pending_cancel"] = None

    def _schedule_retry() -> None:
        opts = dict(entry.data)
        opts.update(dict(entry.options))
        interval_sec = int(opts.get(CONF_RETRY_INTERVAL_MINUTES, DEFAULT_RETRY_INTERVAL_MINUTES)) * 60
        retry_state["pending_cancel"] = async_call_later(hass, interval_sec, _retry_callback)

    async def _retry_callback(_now) -> None:
        retry_state["pending_cancel"] = None
        log_activity("🔄", f"Retry #{retry_state['count'] + 1}")
        await _do_fetch_and_process()

    def _get_max_retries() -> int:
        opts = dict(entry.data)
        opts.update(dict(entry.options))
        return int(opts.get(CONF_MAX_RETRIES, DEFAULT_MAX_RETRIES))

    # ---------------------------------------------------------------------------
    #  Public-API-Fallback
    # ---------------------------------------------------------------------------

    def _is_public_fallback_enabled() -> bool:
        opts = dict(entry.data)
        opts.update(dict(entry.options))
        return bool(opts.get(CONF_PUBLIC_FALLBACK_ENABLED, DEFAULT_PUBLIC_FALLBACK_ENABLED))

    async def _fetch_public_fallback_slots(target_date) -> dict[str, dict[str, float]]:
        """Rekonstruiert Kunden-Slots aus den öffentlichen Endpoints (ohne Auth).

        electricity == public electricity_dynamic (exakt identisch),
        grid == public <grid_tariff> + konstanter Offset, regional_fees konstant.
        Wirft EkzTariffApiError bei HTTP-Fehlern; leeres Dict wenn ein
        Komponenten-Feed keine Slots liefert.
        """
        opts = dict(entry.data)
        opts.update(dict(entry.options))
        grid_tariff = str(opts.get(CONF_PUBLIC_FALLBACK_GRID_TARIFF, DEFAULT_PUBLIC_FALLBACK_GRID_TARIFF)).strip() or DEFAULT_PUBLIC_FALLBACK_GRID_TARIFF
        grid_offset = float(opts.get(CONF_PUBLIC_FALLBACK_GRID_OFFSET, DEFAULT_PUBLIC_FALLBACK_GRID_OFFSET))
        regional_fees = float(opts.get(CONF_PUBLIC_FALLBACK_REGIONAL_FEES, DEFAULT_PUBLIC_FALLBACK_REGIONAL_FEES))

        now_local = dt_util.now()
        day_start = dt_util.as_utc(datetime.combine(target_date, time(0, 0), tzinfo=now_local.tzinfo))
        day_end = day_start + timedelta(days=1)

        elec_payload = await api.fetch_public_tariffs(
            tariff_name="electricity_dynamic",
            start_timestamp=day_start.isoformat(),
            end_timestamp=day_end.isoformat(),
        )
        grid_payload = await api.fetch_public_tariffs(
            tariff_name=grid_tariff,
            start_timestamp=day_start.isoformat(),
            end_timestamp=day_end.isoformat(),
        )

        elec_slots = _parse_customer_slots(elec_payload)  # {utc: {"electricity": v}}
        grid_slots = _parse_customer_slots(grid_payload)  # {utc: {"grid": v}}

        result: dict[str, dict[str, float]] = {}
        for utc_key, comp in elec_slots.items():
            elec = comp.get("electricity")
            grid_raw = grid_slots.get(utc_key, {}).get("grid")
            if elec is None or grid_raw is None:
                continue
            grid = round(grid_raw + grid_offset, 5)
            result[utc_key] = {
                "electricity": elec,
                "grid": grid,
                "regional_fees": regional_fees,
                # integrated = electricity + grid (EKZ-Konvention, ohne regional_fees)
                "integrated": round(elec + grid, 5),
            }
        return result

    # ---------------------------------------------------------------------------
    #  Core: fetch and process
    # ---------------------------------------------------------------------------

    async def _do_fetch_and_process() -> None:
        nonlocal last_api_success_utc, tomorrow_date, tomorrow_slots

        if not ems_instance_id:
            log_activity("❌", "ems_instance_id nicht konfiguriert")
            return

        # 1. Check EMS link status (rein informativ — darf den Fetch nie abbrechen.
        # Vor dem Fallback-Umbau beendete ein 401 hier den ganzen Abend ohne Retry.)
        try:
            status_payload = await api.fetch_ems_link_status(
                ems_instance_id=ems_instance_id, redirect_uri=redirect_uri or "",
            )
            _update_link_status_sensor(_extract_link_status(status_payload))
        except EkzTariffAuthError as err:
            log_activity("🔑", f"Link-Status Auth-Fehler: {err}")
            _LOGGER.warning("EKZ link status auth failed (continuing to fetch): %s", err)
        except Exception as err:
            _LOGGER.warning("EKZ link status check failed: %s", err)

        # 2. Compute time window for tomorrow
        now_local = dt_util.now()
        tomorrow = (now_local + timedelta(days=1)).date()
        tom_start = dt_util.as_utc(datetime.combine(tomorrow, time(0, 0), tzinfo=now_local.tzinfo))
        tom_end = tom_start + timedelta(days=1)

        request_params = {
            "ems_instance_id": ems_instance_id,
            "tariff_type": "electricity_dynamic",
            "start_timestamp": tom_start.isoformat(),
            "end_timestamp": tom_end.isoformat(),
        }

        # 3. Fetch customerTariffs (Fehler führen NICHT mehr zum Abbruch —
        # der Public-API-Fallback bekommt in 4b immer seine Chance).
        # Auth-Fehler werden seit dem Fallback-Umbau wie API-Fehler behandelt
        # (retried); das alte raise ConfigEntryAuthFailed beendete den Abend
        # ohne Retry (Vorfall 19.–23.08.: 4 Tage keine Daten wegen 401).
        payload = None
        primary_err: Exception | None = None
        auth_failed = False
        try:
            payload = await api.fetch_customer_tariffs(
                ems_instance_id=ems_instance_id,
                tariff_type="electricity_dynamic",
                start_timestamp=tom_start.isoformat(),
                end_timestamp=tom_end.isoformat(),
            )
        except EkzTariffAuthError as err:
            primary_err = err
            auth_failed = True
            log_activity("🔑", f"Auth-Fehler: {err}")
            _LOGGER.error("EKZ auth failed: %s", err)
        except Exception as err:
            primary_err = err
            log_activity("❌", f"API-Fehler: {err}")
            _LOGGER.error("EKZ fetch failed: %s", err)

        # 4. Parse
        parsed_slots: dict[str, dict[str, float]] = {}
        used_fallback = False
        if payload is not None:
            parsed_slots = _parse_customer_slots(payload)
            last_api_success_utc = dt_util.utcnow().isoformat()
            # Debug dump (async file write)
            await _write_debug_dump("fetch", request_params, payload, len(parsed_slots))
            if not parsed_slots:
                log_activity("⚠️", f"Keine Slots für {tomorrow}")

        # 4b. Public-API-Fallback (Kunden-API down oder leer)
        if not parsed_slots and _is_public_fallback_enabled():
            try:
                fb_slots = await _fetch_public_fallback_slots(tomorrow)
            except Exception as fb_err:
                log_activity("🌐", f"Public-Fallback fehlgeschlagen: {fb_err}")
                _LOGGER.warning("EKZ public fallback failed: %s", fb_err)
            else:
                if fb_slots:
                    parsed_slots = fb_slots
                    used_fallback = True
                    log_activity("🌐", f"Public-API-Fallback: {len(fb_slots)} Slots für {tomorrow} rekonstruiert")
                else:
                    log_activity("🌐", f"Public-Fallback: keine Slots für {tomorrow}")

        if not parsed_slots:
            retry_state["count"] += 1
            error_type = "auth_error" if auth_failed else ("api_error" if primary_err else "no_data")
            message = str(primary_err) if primary_err else f"Keine Daten für {tomorrow}"
            if retry_state["count"] >= _get_max_retries():
                _update_error_sensor({
                    "error_type": error_type,
                    "message": message,
                    "retry_count": retry_state["count"],
                    "last_attempt": dt_util.now().isoformat(),
                })
                log_activity("❌", f"Keine Daten nach {retry_state['count']} Versuchen: {message}")
                from . import herold as _herold
                await _herold.senden(
                    hass,
                    topic="ekz_tariff/retries_exhausted",
                    titel="EKZ: Retries ausgeschöpft",
                    message=f"Kunden-API und Public-Fallback nach {retry_state['count']} Versuchen ohne Daten: {message}",
                    severity="kritisch",
                )
            else:
                from . import herold as _herold
                await _herold.senden(
                    hass,
                    topic="ekz_tariff/fetch_error",
                    titel="EKZ: Fetch-Fehler",
                    message=f"Kein Datenabruf möglich (Versuch {retry_state['count']}): {message}. Retry geplant.",
                    severity="warnung",
                )
                _schedule_retry()
            await _save_storage()
            return

        # 5. Validate
        opts = dict(entry.data)
        opts.update(dict(entry.options))
        min_price = float(opts.get(CONF_MIN_PRICE_CHF_PER_KWH, DEFAULT_MIN_PRICE_CHF_PER_KWH))
        max_price = float(opts.get(CONF_MAX_PRICE_CHF_PER_KWH, DEFAULT_MAX_PRICE_CHF_PER_KWH))
        tz = dt_util.get_time_zone(hass.config.time_zone)

        result = validate_tomorrow_slots(
            stored_slots=parsed_slots,
            target_date=tomorrow,
            tz=tz,
            min_slots=96,
            min_price=min_price,
            max_price=max_price,
        )

        if not result.valid:
            log_activity("⚠️", f"Ungültig: {result.error} — {result.details}")
            retry_state["count"] += 1
            if retry_state["count"] >= _get_max_retries():
                _update_error_sensor({
                    "error_type": "validation_failed",
                    "message": f"{result.error}: {result.details}",
                    "retry_count": retry_state["count"],
                    "last_attempt": dt_util.now().isoformat(),
                    "details": result.details,
                })
                log_activity("❌", f"Ungültige Daten nach {retry_state['count']} Versuchen: {result.details}")
                from . import herold as _herold
                await _herold.senden(
                    hass,
                    topic="ekz_tariff/retries_exhausted",
                    titel="EKZ: Retries ausgeschöpft",
                    message=f"Validierung fehlgeschlagen nach {retry_state['count']} Versuchen: {result.error} — {result.details}",
                    severity="kritisch",
                )
            else:
                _schedule_retry()
            await _save_storage()
            return

        # 6. SUCCESS — store, fire event, log
        had_error_before = error_state is not None
        _update_error_sensor(None)
        if used_fallback:
            from . import herold as _herold
            await _herold.senden(
                hass,
                topic="ekz_tariff/public_fallback",
                titel="EKZ: Public-API-Fallback aktiv",
                message=(
                    f"Kunden-API lieferte nicht ({primary_err if primary_err else 'keine Slots'}) — "
                    f"Preise für {tomorrow.strftime('%d.%m.%Y')} aus Public-API rekonstruiert."
                ),
                severity="warnung",
            )
        elif had_error_before:
            from . import herold as _herold
            await _herold.senden(
                hass,
                topic="ekz_tariff/recovered",
                titel="EKZ: wieder OK",
                message=f"Nach vorherigem Fehler wieder erfolgreich Daten für {tomorrow.strftime('%d.%m.%Y')} geladen.",
                severity="info",
            )

        tomorrow_date = str(tomorrow)
        tomorrow_slots = _slots_to_event_payload(parsed_slots)

        await _save_storage()

        date_str = tomorrow.strftime("%d.%m.%Y")
        source_note = " (Public-Fallback)" if used_fallback else ""
        log_activity("✅", f"{result.slot_count} Slots für {date_str} validiert{source_note}")

        # Fire bus event with slots directly in payload
        event_data = {
            "date": str(tomorrow),
            "entry_id": entry.entry_id,
            "slots": tomorrow_slots,
        }
        _LOGGER.info("EKZ: Firing %s: date=%s, %d slots", EVENT_EKZ_NEW_DATA, tomorrow, len(tomorrow_slots))
        hass.bus.async_fire(EVENT_EKZ_NEW_DATA, event_data)

        log_activity("📤", f"Signal an Tariff Saver gesendet ({date_str})")
        await _save_storage()

    # ---------------------------------------------------------------------------
    #  Daily fetch timer
    # ---------------------------------------------------------------------------

    async def _daily_fetch(_now) -> None:
        _cancel_pending_retry()
        retry_state["count"] = 0
        publish_time = config.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)
        log_activity("⏰", f"Täglicher Fetch um {publish_time}")
        try:
            await _do_fetch_and_process()
        except ConfigEntryAuthFailed:
            _LOGGER.error("EKZ daily fetch: auth failed")
        except Exception as err:
            log_activity("💥", f"Unerwarteter Fehler: {err}")
            _LOGGER.error("EKZ daily fetch failed: %s", err, exc_info=True)

    # ---------------------------------------------------------------------------
    #  Proactive token refresh
    # ---------------------------------------------------------------------------

    async def _proactive_token_refresh(_now) -> None:
        try:
            await oauth_session.async_ensure_token_valid()
            _LOGGER.debug("EKZ proactive token refresh OK")
        except Exception as err:
            _LOGGER.warning("EKZ proactive token refresh failed: %s", err)

    # ---------------------------------------------------------------------------
    #  Schedule timers
    # ---------------------------------------------------------------------------

    publish_time = config.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)
    hour, minute = _parse_hhmm(publish_time)

    hass.data[DOMAIN][f"{entry.entry_id}_unsub_daily"] = async_track_time_change(
        hass, _daily_fetch, hour=hour, minute=minute, second=0,
    )
    hass.data[DOMAIN][f"{entry.entry_id}_unsub_token"] = async_track_time_interval(
        hass, _proactive_token_refresh, timedelta(hours=20),
    )

    # ---------------------------------------------------------------------------
    #  Services
    # ---------------------------------------------------------------------------

    async def _force_refresh_service(call) -> None:
        log_activity("🔄", "Manueller Refresh gestartet")
        _cancel_pending_retry()
        retry_state["count"] = 0
        try:
            await _do_fetch_and_process()
        except ConfigEntryAuthFailed:
            pass
        except Exception as err:
            log_activity("💥", f"Refresh Fehler: {err}")

    async def _test_fetch_service(call) -> None:
        """Same API call as daily, writes to log file only — no system changes."""
        now_local = dt_util.now()
        tomorrow = (now_local + timedelta(days=1)).date()
        tom_start = dt_util.as_utc(datetime.combine(tomorrow, time(0, 0), tzinfo=now_local.tzinfo))
        tom_end = tom_start + timedelta(days=1)

        request_params = {
            "ems_instance_id": ems_instance_id,
            "tariff_type": "electricity_dynamic",
            "start_timestamp": tom_start.isoformat(),
            "end_timestamp": tom_end.isoformat(),
        }
        try:
            payload = await api.fetch_customer_tariffs(**request_params)
            parsed = _parse_customer_slots(payload)
            await _write_debug_dump("test_fetch", request_params, payload, len(parsed))
            log_activity("🧪", f"Test-Fetch: {len(parsed)} Slots für {tomorrow}")
        except Exception as err:
            log_activity("🧪", f"Test-Fetch FEHLER: {err}")
        # Public-Fallback mittesten (nur Log, kein Systemeingriff)
        try:
            fb = await _fetch_public_fallback_slots(tomorrow)
            log_activity("🧪", f"Test Public-Fallback: {len(fb)} Slots für {tomorrow}")
        except Exception as fb_err:
            log_activity("🧪", f"Test Public-Fallback FEHLER: {fb_err}")

    _FLOAT_KEYS = {
        CONF_MIN_PRICE_CHF_PER_KWH,
        CONF_MAX_PRICE_CHF_PER_KWH,
        CONF_PUBLIC_FALLBACK_GRID_OFFSET,
        CONF_PUBLIC_FALLBACK_REGIONAL_FEES,
    }
    _INT_KEYS = {CONF_MAX_RETRIES, CONF_RETRY_INTERVAL_MINUTES}
    _BOOL_KEYS = {CONF_DEBUG_MODE, CONF_PUBLIC_FALLBACK_ENABLED}

    async def _update_setting_service(call) -> None:
        key = str(call.data.get("key", ""))
        value = call.data.get("value")
        if not key:
            return
        if key in _BOOL_KEYS:
            value = bool(value)
        elif key in _FLOAT_KEYS:
            value = float(value or 0)
        elif key in _INT_KEYS:
            value = int(value or 0)
        else:
            value = str(value or "")
        new_options = dict(entry.options)
        new_options[key] = value
        hass.config_entries.async_update_entry(entry, options=new_options)
        # Update settings sensor
        settings_sensor = hass.data[DOMAIN].get(f"{entry.entry_id}_settings_sensor")
        if settings_sensor and hasattr(settings_sensor, "async_write_ha_state"):
            settings_sensor.async_write_ha_state()

    async def _clear_activity_log_service(call) -> None:
        activity_log.clear()
        _update_activity_log_sensor()
        await _save_storage()

    async def _fetch_date_service(call) -> None:
        """Fetch + process slots for a specific date (recovery tool)."""
        nonlocal last_api_success_utc, tomorrow_date, tomorrow_slots
        date_str = str(call.data.get("date", "")).strip()
        if not date_str:
            log_activity("❌", "fetch_date: kein Datum angegeben")
            return
        try:
            from datetime import date as date_cls
            target = date_cls.fromisoformat(date_str)
        except ValueError:
            log_activity("❌", f"fetch_date: ungültiges Datum '{date_str}'")
            return

        now_local = dt_util.now()
        target_start = dt_util.as_utc(datetime.combine(target, time(0, 0), tzinfo=now_local.tzinfo))
        target_end = target_start + timedelta(days=1)

        log_activity("🔄", f"fetch_date für {target}")
        payload = None
        try:
            payload = await api.fetch_customer_tariffs(
                ems_instance_id=ems_instance_id,
                tariff_type="electricity_dynamic",
                start_timestamp=target_start.isoformat(),
                end_timestamp=target_end.isoformat(),
            )
        except EkzTariffAuthError as err:
            log_activity("🔑", f"fetch_date Auth-Fehler: {err}")
        except Exception as err:
            log_activity("❌", f"fetch_date API-Fehler: {err}")

        parsed_slots: dict[str, dict[str, float]] = {}
        used_fallback = False
        if payload is not None:
            request_params = {
                "ems_instance_id": ems_instance_id,
                "tariff_type": "electricity_dynamic",
                "start_timestamp": target_start.isoformat(),
                "end_timestamp": target_end.isoformat(),
            }
            parsed_slots = _parse_customer_slots(payload)
            last_api_success_utc = dt_util.utcnow().isoformat()
            await _write_debug_dump("fetch_date", request_params, payload, len(parsed_slots))

        if not parsed_slots and _is_public_fallback_enabled():
            try:
                parsed_slots = await _fetch_public_fallback_slots(target)
            except Exception as fb_err:
                log_activity("🌐", f"fetch_date Public-Fallback fehlgeschlagen: {fb_err}")
            else:
                if parsed_slots:
                    used_fallback = True
                    log_activity("🌐", f"fetch_date Public-Fallback: {len(parsed_slots)} Slots rekonstruiert")

        if not parsed_slots:
            log_activity("⚠️", f"fetch_date: Keine Slots für {target}")
            await _save_storage()
            return

        date_fmt = target.strftime("%d.%m.%Y")
        source_note = " (Public-Fallback)" if used_fallback else ""
        log_activity("✅", f"fetch_date: {len(parsed_slots)} Slots für {date_fmt}{source_note}")

        tomorrow_date = str(target)
        tomorrow_slots = _slots_to_event_payload(parsed_slots)
        await _save_storage()

        event_data = {
            "date": str(target),
            "entry_id": entry.entry_id,
            "slots": tomorrow_slots,
        }
        _LOGGER.info("EKZ fetch_date: Firing %s: date=%s, %d slots", EVENT_EKZ_NEW_DATA, target, len(tomorrow_slots))
        hass.bus.async_fire(EVENT_EKZ_NEW_DATA, event_data)
        log_activity("📤", f"Signal an Tariff Saver gesendet ({date_fmt})")
        _update_error_sensor(None)

    hass.services.async_register(DOMAIN, "force_refresh", _force_refresh_service)
    hass.services.async_register(DOMAIN, "test_fetch", _test_fetch_service)
    hass.services.async_register(DOMAIN, "fetch_date", _fetch_date_service)
    hass.services.async_register(DOMAIN, "update_setting", _update_setting_service)
    hass.services.async_register(DOMAIN, "clear_activity_log", _clear_activity_log_service)

    # ---------------------------------------------------------------------------
    #  Setup platforms (sensors + binary_sensor)
    # ---------------------------------------------------------------------------

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Restore sensor states after platform setup
    _update_activity_log_sensor()
    if error_state:
        _update_error_sensor(error_state)
    if link_status:
        _update_link_status_sensor(link_status)

    # Cleanup orphaned entities from v1
    _cleanup_orphaned_entities(hass, entry)

    # Cleanup old option keys from v1
    _OBSOLETE_OPTION_KEYS = {"max_retries_no_data", "max_retries_invalid_data", "min_slots_per_day"}
    old_opts = dict(entry.options)
    cleaned = {k: v for k, v in old_opts.items() if k not in _OBSOLETE_OPTION_KEYS}
    if len(cleaned) != len(old_opts):
        hass.config_entries.async_update_entry(entry, options=cleaned)
        _LOGGER.info("EKZ: Removed obsolete option keys: %s", _OBSOLETE_OPTION_KEYS & set(old_opts))

    log_activity("💾", "EKZ Tariff v2 gestartet")
    await _save_storage()

    return True


# ---------------------------------------------------------------------------
#  Entity cleanup
# ---------------------------------------------------------------------------

def _cleanup_orphaned_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove orphaned entity registry entries from v1."""
    eid = entry.entry_id
    valid_suffixes = {
        f"{eid}_error",
        f"{eid}_ekz_activity_log",
        f"{eid}_settings",
        f"{eid}_link_status",
    }
    registry = er.async_get(hass)
    to_remove = [
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, eid)
        if entity.unique_id not in valid_suffixes
    ]
    for entity_id in to_remove:
        _LOGGER.info("EKZ: Removing orphaned entity %s", entity_id)
        registry.async_remove(entity_id)


# ---------------------------------------------------------------------------
#  Unload
# ---------------------------------------------------------------------------

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    domain = hass.data.get(DOMAIN, {})
    for suffix in ("_unsub_daily", "_unsub_token"):
        unsub = domain.pop(f"{entry.entry_id}{suffix}", None)
        if callable(unsub):
            unsub()
    # Remove services
    for svc in ("force_refresh", "test_fetch", "fetch_date", "update_setting", "clear_activity_log"):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)
    if unload_ok:
        domain.pop(entry.entry_id, None)
        # Clean up sensor references
        for suffix in ("_error_sensor", "_activity_log_sensor", "_link_status_sensor", "_settings_sensor"):
            domain.pop(f"{entry.entry_id}{suffix}", None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
