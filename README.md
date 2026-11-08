# PORTEXA Tracking Engine

Independent multi-carrier tracking service for PORTEXA, designed around verifiable shipment events and authorized integrations.

## What this project does
- Receives a carrier and an ocean shipment reference (B/L, booking or container).
- Deduplicates references across customers, stores tracking events and schedules refreshes.
- Supports separate adapters for CMA CGM, MSC, Maersk, Hapag-Lloyd and COSCO.
- Provides a local JSON-fixture connector for deterministic end-to-end testing.
- Never labels synthetic events as live carrier data.
- Does not scrape carrier websites until automation is expressly permitted by their terms.

## First test reference
CMA CGM: `NAM8787725` (unverified; no live data claimed).

## Getting started
Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
portexa-tracking init
portexa-tracking add --carrier CMA_CGM --reference NAM8787725 --type BL
portexa-tracking list
portexa-tracking run-once
pytest
```

The built-in carrier adapters default to `unavailable` until configured with an authorized connector. To test the entire pipeline locally:

```bash
PORTEXA_FIXTURE_DIR=tests/fixtures portexa-tracking run-once --carrier FIXTURE
```

By default, state is stored in `./data/tracking.sqlite3` (override with `PORTEXA_TRACKING_DB`). No production PORTEXA database, ports or services are touched.

## Architecture
```text
CLI / future authenticated API
    -> SQLite registry (carrier + normalized reference)
    -> due-job scheduler with lease
    -> carrier adapter (authorized source)
    -> normalizer + event identity
    -> tracking snapshots and event history
    -> future PORTEXA integration / notifications
```

## Security and operations
- Do not store credentials in Git; use environment variables.
- No CAPTCHA bypass, credential sharing, stealth automation or evasion of rate limits.
- Only authorized APIs, feeds and website automation explicitly allowed by the provider.
- Errors use exponential backoff and low concurrency.
- Shipment information can be commercially sensitive: restrict access to authorized customers and redact logs.
- The public GitHub repository contains code and synthetic test data only.

## Status
Foundation ready; carrier integrations are **not** connected to live data. Next milestone: obtain permission or an approved data-access method for one carrier and validate a real response against the official tracker.
