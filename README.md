# EKZ Tariff

<p align="center">
  <img src="banner.png" alt="EKZ Tariff banner" width="100%">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/HACS-Custom-blue.svg" alt="HACS">
  <img src="https://img.shields.io/badge/Home%20Assistant-Integration-blue" alt="Home Assistant">
  <img src="https://img.shields.io/badge/version-2.0.0-green" alt="Version">
  <img src="https://img.shields.io/badge/maintained-yes-brightgreen" alt="Maintained">
</p>

**EKZ Tariff** is a Home Assistant integration that provides **raw electricity tariff data from EKZ**.

The integration connects to the EKZ APIs and exposes tariff information as **verified raw data** inside Home Assistant.  
It is intentionally focused on **provider functionality only** and contains **no optimisation logic**.

---

## Features

- myEKZ dynamic tariffs (OAuth login)
- Automatic fallback to the public EKZ API when the customer endpoint fails
- 15-minute price slots (96 per day, DST-aware: 92 in spring, 100 in autumn)
- Electricity price components
  - electricity
  - grid
  - regional fees
  - integrated
- Automatic daily tariff refresh
- Retry logic if EKZ publishes tariffs late
- Validation before publishing: slot count, gaps, price bounds (all-or-nothing per day)
- All prices are **net** (excluding VAT)
- Clean Home Assistant entities
- Designed as a **provider for other integrations**

---

## Architecture

<p align="center">
  <img src="architecture-diagram.png" alt="Architecture diagram" width="850">
</p>

EKZ Tariff is the **raw data provider layer**: fetch, validate, hand over. Since **v2.0.0** it deliberately has
no `DataUpdateCoordinator` — a linear daily flow fires the bus event `ekz_tariff_new_data` with the validated
slots directly in the payload, so consumers never read a half-written store.

Higher-level logic such as cost calculation, cheapest windows, charging optimisation, and historical analysis belongs in **Tariff Saver**.

---

## Tariff Saver

The raw tariff data from this integration is intended to be used by **Tariff Saver**.

Tariff Saver adds:

- cheapest charging windows
- tariff optimisation
- energy cost calculation
- EV charging optimisation
- battery optimisation
- historical tariff analysis

Repository:

**https://github.com/cnc-lasercraft/tariff_saver**

---

## HACS Installation

1. Open **HACS**
2. Add this repository as **Custom Repository**
3. Category: **Integration**
4. Install **EKZ Tariff**
5. Restart Home Assistant

---

## Configuration

After installation:

`Settings → Devices & Services → Add Integration → EKZ Tariff`

Login with your **myEKZ account** to access your dynamic tariff.

---

## Entities

The integration exposes core tariff entities such as:

| Entity | Description |
|---|---|
| `binary_sensor.*_error` | On after all retries failed; attributes carry error type, message, retry count |
| `sensor.*_link_status` | State of the myEKZ / EMS link |
| `sensor.*_activity_log` | Recent fetch, validation and fallback events |
| `sensor.*_settings` | Current configuration, read by the settings card |

The integration is a data provider, not a display layer: the validated tariff slots are handed to consumers
through the `ekz_tariff_new_data` bus event and the storage, not through one entity per price component.

### Services

| Service | Purpose |
|---|---|
| `ekz_tariff.force_refresh` | Manual fetch, validation and signal for tomorrow |
| `ekz_tariff.fetch_date` | Fetch a specific date (recovery tool) |
| `ekz_tariff.test_fetch` | Same API call as the daily refresh, writes to a log file only |
| `ekz_tariff.update_setting` | Change a setting from the dashboard |
| `ekz_tariff.clear_activity_log` | Clear the activity log |

---

## Troubleshooting

The error binary sensor, the link status sensor and the activity log together show what happened on any given
evening: publication time, slot count, validation result and whether the public-API fallback stepped in.

Enabling the `debug_mode` setting additionally writes one JSON file per API call to `/config/ekz_tariff_debug/`.

Known quirks of the EKZ API are documented in [EKZ_API_ISSUES.md](EKZ_API_ISSUES.md).

---

## Disclaimer

This project is **not affiliated with EKZ**.

It simply uses the public and myEKZ APIs to retrieve tariff information.
