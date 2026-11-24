# Employee shipment editing (staged deployment)

This branch adds employee editing of five PORTEXA source shipment fields:
`bol_no`, `msc_tracking`, `ship_date`, `arrival_date`, `shipping_details`.

The invoice, status, ownership and other source fields remain read-only.
Employees can edit only an active assigned shipment with a task in
`pending` or `rejected` status. Review submission, supervisor approval
and 250-IQD payment calculation remain independent.

## Safeguards

- Flask login, operator role and existing CSRF validation.
- Signed form snapshot (expires after 1 hour), plus optimistic source DB comparison.
- Server-side validation and active assignment/task verification.
- Durable `ops_shipment_change_log` migration via `init_operations`.
- Source writes disabled unless `PORTEXA_SOURCE_WRITE_ENABLED=YES`.
- Failed and conflicting writes recorded; ambiguous outcomes remain pending.
- Pending log reconciliation is available programmatically via
  `operations_shipment_edit.reconcile_pending(conn)`. Ambiguous records
  must be manually reviewed, not automatically retried.

## Test before enabling production

```bash
cd /home/alialameeri/Portexa-Tracking-Engine
.venv/bin/python -m pytest -q tests/test_operations.py tests/test_operations_shipment_edit.py
```

The tests use temporary SQLite databases and do not access PORTEXA production.

## Production rollout

1. Back up `/opt/portexa/app.db` using SQLite backup API and the
   operations DB. Keep the source writes flag disabled.
2. Deploy the code and restart `portexa-operations.service`. Confirm
   login, operator display and the form while writes are disabled.
3. Test the end-to-end UI against a *copy* of the source DB in a staging
   instance (never a production shipment).
4. Review service-user SQLite file and directory permissions, source
   backup, and pending log records.
5. Only after explicit approval, enable source writes in the service
   environment and restart the service. Verify a controlled change and
   audit log, then inspect the main PORTEXA UI.

### Server has local changes

The server currently has an uncommitted `operations_web.py` change
(displaying shipment details and a configurable bind host). Do **not**
use `git reset --hard`. Stash tracked and untracked changes before
switching branches, and retain the stash until the new branch has been
validated. A safe procedure is:

```bash
cd /home/alialameeri/Portexa-Tracking-Engine
git stash push -u -m "pre-employee-edit-local-changes"
git fetch origin
git switch --track origin/feature/employee-shipment-edit
.venv/bin/python -m pytest -q tests/test_operations.py tests/test_operations_shipment_edit.py
```

The branch already includes the local read-only shipment display and
configurable `PORTEXA_OPS_HOST` behavior. Inspect the stash before
dropping it; never discard unreviewed local work.
