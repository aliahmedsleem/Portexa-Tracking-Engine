# Operations and payroll module — implementation status

This module is the **first backend foundation**, not a finished staff web application. The existing local tracking dashboard remains unchanged and unauthenticated; do **not** expose it publicly or give operators SSH access to it.

## Policy
- Every correctly reviewed and supervisor-approved shipment task is payable, including an evidence-backed "no change" review.
- Four slots per day, at 00:00, 06:00, 12:00 and 18:00 Asia/Baghdad.
- Each active shipment assignment generates at most one task per slot (unique shipment + cycle).
- Tasks are not automatically approved or paid.
- A submitted task requires a non-empty evidence source and review note, plus explicit change/no-change flag.
- Rejection requires a reason; rejected tasks can be resubmitted.
- Prices are stored per task at creation. Future price changes do not rewrite earnings.
- Payments are manually recorded and cannot exceed approved, unpaid earnings.
- All operations tables are in the Tracking Engine SQLite database, **not** PORTEXA's production database.

## Implemented
`src/portexa_tracking/operations.py`: schema, password hashing, user roles, assignments, task generation, submission, approval, payroll, notifications, audit.
`tests/test_operations.py`: core regression coverage.

## Not implemented yet — do not claim production readiness
- Authenticated web login, sessions, CSRF and role-specific operator/admin pages.
- Scheduling service/systemd timer to invoke `generate_tasks` every six hours.
- Automatic fair allocation, shift planning, deadlines, late policies, active-shipment eligibility checks.
- Supervisor review screen, dashboard, notifications inbox, finance/payment UI.
- Public deployment, email/push notifications and end-to-end acceptance tests.

## Developer check
```bash
cd ~/Portexa-Tracking-Engine
git pull --ff-only origin main
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q tests/test_operations.py
```

**Do not initialize or run operations against production until the authenticated web interface and safe deployment procedure are complete.**
