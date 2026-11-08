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

## Additional implemented development components
- `src/portexa_tracking/operations_web.py`: separate loopback-only Flask web UI on port 8766 with login, role guards, CSRF, user management, assignment, task submission, supervisor approvals, notifications, pricing and payroll.
- `src/portexa_tracking/operations_cli.py`: initial admin bootstrap and manual idempotent task generation.
- `tests/test_operations_web.py`: access-control and CSRF tests.

## Not implemented yet — do not claim production readiness
- Automated systemd timer (not installed). A guarded dry-run scheduler is available in `operations_scheduler.py`; production source status values require verification.
- Automatic fair allocation, shift planning, deadlines, late policies, active-shipment eligibility checks.
- Detailed productivity analytics, audit browser, employee activation/deactivation and shift management UI.
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

## Local development preview (NOT production deployment)
```bash
cd ~/Portexa-Tracking-Engine
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q tests/test_operations.py tests/test_operations_web.py
python -m portexa_tracking.operations_cli bootstrap-admin
export PORTEXA_OPS_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')"
portexa-operations-ui
```
The secret must be persisted securely across restarts before real use. Access only through an SSH tunnel to loopback port 8766. **Do not port-forward this service publicly**. No scheduler is installed automatically; the operator task generation is currently a manual command. Do not create payable tasks before validating eligible shipments and assigning staff.

## Guarded scheduler (no timer installed)
The scheduler reads assigned source shipment IDs using the existing read-only PORTEXA bridge and excludes missing or known terminal shipments. Unrecognized status values are **not** automatically treated as terminal: review the production status vocabulary before enabling any automatic execution.

```bash
# Preview only; does not create tasks
python -m portexa_tracking.operations_scheduler
# After testing and verifying statuses, run once manually:
python -m portexa_tracking.operations_scheduler --apply
```

The legacy `operations_cli generate` command now also filters through the read-only source bridge. No cron/systemd timer is installed by this repository change. Tests have been added, but require running in the target environment before deployment.
