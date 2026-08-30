"""Herold integration helper — optional dependency.

Unabhängig von Tariff Saver (eigene Copy, kein Cross-CC-Import).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

HEROLD_DOMAIN = "herold"
QUELLE = "custom_components.ekz_tariff"

TOPICS: dict[str, dict[str, Any]] = {
    "ekz_tariff/fetch_error": {
        "name": "EKZ Fetch-Fehler",
        "beschreibung": "API-Call zu EKZ ist fehlgeschlagen (Netzwerk, OAuth, HTTP-Error).",
        "default_severity": "warnung",
    },
    "ekz_tariff/validation_failed": {
        "name": "EKZ Daten-Validierung fehlgeschlagen",
        "beschreibung": "EKZ-Antwort hatte unplausible Slots (Slot-Count, Lücken, Preisgrenzen) — Tag verworfen.",
        "default_severity": "warnung",
    },
    "ekz_tariff/retries_exhausted": {
        "name": "EKZ Retries ausgeschöpft",
        "beschreibung": "Alle Retry-Versuche fehlgeschlagen. Binary-Sensor ekz_tariff_error ist ON.",
        "default_severity": "kritisch",
    },
    "ekz_tariff/public_fallback": {
        "name": "EKZ Public-API-Fallback aktiv",
        "beschreibung": "Kunden-API lieferte nicht — Preise wurden aus der öffentlichen EKZ-API rekonstruiert.",
        "default_severity": "warnung",
    },
    "ekz_tariff/recovered": {
        "name": "EKZ wieder OK",
        "beschreibung": "Nach vorherigem Fehler wieder erfolgreich Daten geladen.",
        "default_severity": "info",
    },
}


def available(hass: HomeAssistant) -> bool:
    return HEROLD_DOMAIN in hass.data


async def register_topics(hass: HomeAssistant) -> None:
    if not available(hass):
        return
    for topic_id, meta in TOPICS.items():
        try:
            await hass.services.async_call(
                HEROLD_DOMAIN,
                "topic_registrieren",
                {"topic": topic_id, "quelle": QUELLE, **meta},
                blocking=False,
            )
        except Exception as e:
            _LOGGER.warning("Herold topic_registrieren failed for %s: %s", topic_id, e)


async def senden(
    hass: HomeAssistant,
    *,
    topic: str,
    titel: str,
    message: str,
    severity: str = "info",
    actions: list[dict[str, Any]] | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    if not available(hass):
        _LOGGER.debug("Herold nicht verfügbar, skip senden: %s", topic)
        return
    data: dict[str, Any] = {
        "topic": topic,
        "titel": titel,
        "message": message,
        "severity": severity,
    }
    if actions:
        data["actions"] = actions
    if payload:
        data["payload"] = payload
    try:
        await hass.services.async_call(HEROLD_DOMAIN, "senden", data, blocking=True)
    except Exception as e:
        _LOGGER.warning("Herold senden failed for %s: %s", topic, e)
