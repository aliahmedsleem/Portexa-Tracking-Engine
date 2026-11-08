"""Safe six-hour cycle scheduler, read-only source eligibility check.

Default is dry-run. Use --apply only after source status mapping is validated.
Run at 00,06,12,18 Asia/Baghdad via a dedicated scheduler service/timer.
"""
import argparse
from datetime import datetime, timezone
from portexa_tracking.database import connect
from portexa_tracking.operations import init_operations, generate_tasks, cycle_start
from portexa_tracking.portexa_bridge import source_connection, check_schema

# Exact known terminal statuses; site-specific status values must be reviewed before enabling.
TERMINAL = {"delivered", "completed", "cancelled", "canceled", "archived", "closed",
            "تم التسليم", "مكتملة", "ملغاة", "مؤرشفة", "مغلق"}


def eligible_assignments(conn):
    """Fail closed if PORTEXA source cannot be read or its schema differs."""
    assigned = [int(row[0]) for row in conn.execute(
        """SELECT a.shipment_id FROM ops_assignments a
           JOIN ops_users u ON u.id=a.user_id
           WHERE a.active=1 AND u.active=1 AND u.role='operator'""")]
    if not assigned:
        return [], []
    with source_connection() as src:
        check_schema(src)
        # SQLite's bind limit varies by version; use small batches.
        rows = []
        for start in range(0, len(assigned), 400):
            chunk = assigned[start:start+400]
            q = ",".join("?" for _ in chunk)
            rows.extend(src.execute("SELECT id,status FROM shipment WHERE id IN ("+q+")", chunk).fetchall())
    active = [int(r["id"]) for r in rows if str(r["status"] or "").strip().lower() not in TERMINAL]
    inactive = sorted(set(assigned) - set(active))
    return active, inactive


def preview(conn, when=None):
    active, inactive = eligible_assignments(conn)
    slot = cycle_start(when or datetime.now(timezone.utc))
    if not active:
        return {"cycle_at":slot,"eligible":0,"skipped":len(inactive),"new":0,"ids":[]}
    existing = set()
    for start in range(0, len(active), 400):
        chunk = active[start:start+400]
        q = ",".join("?" for _ in chunk)
        existing.update(int(r[0]) for r in conn.execute(
            "SELECT shipment_id FROM ops_tasks WHERE cycle_at=? AND shipment_id IN ("+q+")",
            [slot,*chunk]))
    new = sorted(set(active)-existing)
    return {"cycle_at":slot,"eligible":len(active),"skipped":len(inactive),"new":len(new),"ids":new}


def main():
    p=argparse.ArgumentParser(description="PORTEXA operations six-hour cycle")
    p.add_argument("--apply", action="store_true", help="create tasks (default is dry-run)")
    args=p.parse_args()
    with connect() as conn:
        init_operations(conn)
        info=preview(conn)
        print("Cycle:",info["cycle_at"])
        print("Eligible:",info["eligible"],"Skipped:",info["skipped"],"New:",info["new"])
        if args.apply:
            # Recheck source before commit; never generate from unverified IDs.
            active,_=eligible_assignments(conn)
            created=generate_tasks(conn,eligible_ids=active)
            print("Created:",created)
        else:
            print("DRY RUN: no tasks created")


if __name__=="__main__":
    main()
