"""Validation of EKZ tariff slots for a target date."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Components used to compute the total price for validation
_PRICE_COMPONENTS: tuple[str, ...] = (
    "electricity",
    "grid",
    "regional_fees",
)

_FALLBACK_TOTAL_KEYS: tuple[str, ...] = ("integrated", "all_in")


@dataclass
class ValidationResult:
    """Result of slot validation."""

    valid: bool
    error: str | None = None  # "insufficient_slots" | "gap_detected" | "price_out_of_range"
    details: str | None = None
    slot_count: int = 0
    expected_slots: int = 0


def _slot_total_price(components: dict[str, float]) -> float | None:
    """Compute total price from a slot's components dict."""
    total = 0.0
    found = False
    for key in _PRICE_COMPONENTS:
        value = components.get(key)
        if isinstance(value, (int, float)):
            total += float(value)
            found = True
    if found and total > 0:
        return total
    for key in _FALLBACK_TOTAL_KEYS:
        value = components.get(key)
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
    return None


def _expected_slots_for_date(target_date: date, tz: tzinfo) -> int:
    """Calculate expected number of 15-min slots for a date, accounting for DST.

    Normal day: 96 (24h × 4)
    Spring DST (23h): 92
    Autumn DST (25h): 100
    """
    day_start = datetime.combine(target_date, time(0, 0), tzinfo=tz)
    day_end = datetime.combine(target_date + timedelta(days=1), time(0, 0), tzinfo=tz)
    # Convert to UTC to get real wall-clock difference (DST-aware)
    start_utc = dt_util.as_utc(day_start)
    end_utc = dt_util.as_utc(day_end)
    hours = (end_utc - start_utc).total_seconds() / 3600
    return int(hours * 4)


def _generate_expected_timestamps(target_date: date, tz: tzinfo) -> list[str]:
    """Generate all expected 15-min UTC ISO timestamps for a target date.

    Handles DST correctly by iterating in local time and converting to UTC.
    """
    timestamps: list[str] = []
    day_start = datetime.combine(target_date, time(0, 0), tzinfo=tz)
    day_end = datetime.combine(target_date + timedelta(days=1), time(0, 0), tzinfo=tz)

    current = day_start
    while current < day_end:
        utc_dt = dt_util.as_utc(current)
        timestamps.append(utc_dt.isoformat())
        current += timedelta(minutes=15)

    return timestamps


def validate_tomorrow_slots(
    stored_slots: dict[str, dict[str, float]],
    target_date: date,
    tz: tzinfo,
    min_slots: int = 96,
    min_price: float = 0.10,
    max_price: float = 0.99,
) -> ValidationResult:
    """Validate stored slots for a target date.

    Steps:
    1. Compute expected slot count (DST-aware)
    2. Filter slots for target date
    3. Check count >= expected
    4. Check for gaps (all expected 15-min timestamps present)
    5. Check every slot price within [min_price, max_price]
    """
    expected = _expected_slots_for_date(target_date, tz)
    # Use the lesser of configured min_slots and DST-adjusted expected
    required = min(min_slots, expected)

    # Filter slots for target date
    target_slots: dict[str, dict[str, float]] = {}
    for ts_key, components in stored_slots.items():
        parsed = dt_util.parse_datetime(ts_key)
        if parsed is None:
            continue
        local_dt = dt_util.as_local(parsed)
        if local_dt.date() == target_date:
            target_slots[ts_key] = components

    slot_count = len(target_slots)

    # 1. Count check
    if slot_count < required:
        return ValidationResult(
            valid=False,
            error="insufficient_slots",
            details=f"{slot_count} Slots vorhanden, {required} erwartet (DST-adjusted: {expected})",
            slot_count=slot_count,
            expected_slots=expected,
        )

    # 2. Gap detection
    expected_timestamps = _generate_expected_timestamps(target_date, tz)
    missing = [ts for ts in expected_timestamps if ts not in target_slots]
    if missing:
        missing_local = []
        for ts in missing[:5]:  # Show max 5 for readability
            parsed = dt_util.parse_datetime(ts)
            if parsed:
                missing_local.append(dt_util.as_local(parsed).strftime("%H:%M"))
        return ValidationResult(
            valid=False,
            error="gap_detected",
            details=f"{len(missing)} Lücken, z.B. {', '.join(missing_local)}",
            slot_count=slot_count,
            expected_slots=expected,
        )

    # 3. Price range check
    for ts_key, components in target_slots.items():
        price = _slot_total_price(components)
        if price is None:
            parsed = dt_util.parse_datetime(ts_key)
            local_str = dt_util.as_local(parsed).strftime("%H:%M") if parsed else ts_key
            return ValidationResult(
                valid=False,
                error="price_out_of_range",
                details=f"Slot {local_str}: kein gültiger Preis",
                slot_count=slot_count,
                expected_slots=expected,
            )
        if price < min_price or price > max_price:
            parsed = dt_util.parse_datetime(ts_key)
            local_str = dt_util.as_local(parsed).strftime("%H:%M") if parsed else ts_key
            return ValidationResult(
                valid=False,
                error="price_out_of_range",
                details=f"Slot {local_str}: {price:.4f} CHF/kWh ausserhalb [{min_price}, {max_price}]",
                slot_count=slot_count,
                expected_slots=expected,
            )

    return ValidationResult(
        valid=True,
        slot_count=slot_count,
        expected_slots=expected,
    )
