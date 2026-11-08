# PORTEXA source shipment bridge

This feature reads the existing PORTEXA SQLite database; it does not copy or
replace shipment rows in the Tracking Engine database.

## Read-only first (recommended)

On the Ubuntu server, locate the actual production SQLite file. Common path:
`/opt/portexa/app.db` (verify this path before use).

```bash
cd ~/Portexa-Tracking-Engine
source .venv/bin/activate
git pull --ff-only origin main
pip install -e '.[browser,dev]'
pytest -q
export PORTEXA_SOURCE_DB=/opt/portexa/app.db
portexa-tracking-ui
```

Open `http://127.0.0.1:8765/source` through the existing SSH tunnel.
Reading requires filesystem read permissions on the source DB and its directory.
**Do not change production database ownership or chmod it to world-readable.**
If permissions prevent read access, use a secure SQLite backup in a private
directory for the initial trial. That snapshot will not auto-refresh.

## Writes (only after explicit approval and backup)

The source bridge is read-only by default. The operator must independently
back up and verify the database before enabling writes. Never commit SQLite
files or secrets to GitHub. An SQLite backup can be made with:

```bash
sudo install -d -m 700 /var/backups/portexa
sudo sqlite3 /opt/portexa/app.db ".backup '/var/backups/portexa/app-before-tracking-bridge.db'"
```

Confirm actual paths and backup permissions. **Do not set the write flag
until the application is verified on a disposable copy of the database.**

To enable writes in a controlled maintenance session, set
`PORTEXA_SOURCE_WRITE_ENABLED=YES` when starting the dashboard. The service
must run as an OS user with write permission to the source database and
its SQLite journal directory; do not loosen permissions broadly.

Only `bol_no`, `msc_tracking`, `ship_date`, `arrival_date` and
`shipping_details` can be updated. The update checks original field values
to reject stale edits. All other fields are read-only.

## Security boundaries

The dashboard has no login, so it MUST remain bound to 127.0.0.1, accessed
only over a trusted SSH tunnel. Anyone with access to its tunnel can read
the source shipment data, and can edit it when the write flag is enabled.
For multi-user production use, implement authentication, authorization,
auditing and application-owned update endpoints instead of exposing this
maintenance tool. The standalone bridge is an operator-only maintenance tool.
