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
No customer shipment references are committed to this public repository.

## Getting started
Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
portexa-tracking init
portexa-tracking add --carrier CMA_CGM --reference DEMO987654 --type BL
portexa-tracking list
portexa-tracking run-once
pytest
```

The built-in carrier adapters default to `unavailable` until configured with an authorized connector. To test the entire pipeline locally:

```bash
PORTEXA_FIXTURE_DIR=tests/fixtures portexa-tracking add --carrier FIXTURE --reference DEMO12345 --type BL
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

## Local operator dashboard (phase 2)

Run the dashboard on the Ubuntu server:

```bash
cd ~/Portexa-Tracking-Engine
source .venv/bin/activate
git pull --ff-only origin main
pip install -e ".[dev]"
portexa-tracking-ui
```

The UI listens on `127.0.0.1:8765` **only**. On a Windows computer with SSH access to the server, open a separate terminal:

```powershell
ssh -L 8765:127.0.0.1:8765 alialameeri@SERVER_LAN_IP
```

Then open `http://127.0.0.1:8765` in your Windows browser. Keep the SSH connection open.

**Do not forward port 8765 in your router, expose it via Nginx or run this local operator dashboard on a public interface.** This is a development console without user authentication, protected by its loopback-only binding and your SSH tunnel. The production-facing UI will require role-based authentication, HTTPS and CSRF protection.

The dashboard can add references, show normalized events and run one due check at a time. Production carriers will show `unavailable` until a permitted live integration is installed. To try synthetic data, launch the UI with `PORTEXA_FIXTURE_DIR=tests/fixtures portexa-tracking-ui`, then add carrier `FIXTURE` with reference `DEMO12345`.
