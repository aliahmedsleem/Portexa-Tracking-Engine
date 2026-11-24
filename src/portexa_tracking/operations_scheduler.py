"""Safe six-hour cycle scheduler, read-only source eligibility check.

Default is dry-run. Use --apply only after source status mapping is validated.
Run at 00,06,12,18 Asia/Baghdad via a dedicated scheduler service/timer.
"""
import argparse
import os
from portexa_tracking.portexa_bridge import source_connection, check_schema, valid_bill_of_lading
from datetime import datetime, timezone
from portexa_tracking.database import connect
from portexa_tracking import operations_followup as followup
from portexa_tracking.operations import init_operations, generate_tasks, cycle_start
from portexa_tracking.operations_distribution import source_eligible_ids, source_status_summary, distribute

# Exact known terminal statuses; site-specific status values must be reviewed before enabling.
TERMINAL = {"delivered", "completed", "cancelled", "canceled", "archived", "closed",
            "تم التسليم", "مكتملة", "ملغاة", "مؤرشفة", "مغلق"}


def eligible_assignments(conn):
    """Only explicitly allowed source statuses; fail closed on missing configuration."""
    active_source = set(source_eligible_ids())
    assigned = [int(row[0]) for row in conn.execute(
        """SELECT a.shipment_id FROM ops_assignments a
           JOIN ops_users u ON u.id=a.user_id
           WHERE a.active=1 AND u.active=1 AND u.role='operator'""")]
    return sorted(set(assigned) & active_source), sorted(set(assigned) - active_source)


def sync_followups(conn, eligible_ids):
    """Backfill missing follow-ups without reopening or replacing existing records."""
    followup.init(conn)
    eligible = set(eligible_ids)
    now = followup.stamp()
    created = 0
    with conn:
        for row in conn.execute("""SELECT a.shipment_id,a.user_id
            FROM ops_assignments a JOIN ops_users u ON u.id=a.user_id
            WHERE a.active=1 AND u.active=1 AND u.role='operator'""").fetchall():
            if row["shipment_id"] not in eligible:
                continue
            cur = conn.execute("""INSERT OR IGNORE INTO ops_followups
                (shipment_id,user_id,status,assigned_at,last_updated_at)
                VALUES (?,?,'active',?,?)""",
                (row["shipment_id"],row["user_id"],now,now))
            created += cur.rowcount
    return created


def synchronize_source(conn):
    """Discover bills and close only explicitly confirmed terminal source statuses."""
    terminal = {"received", "delivered", "تم الاستلام", "تم التسليم"}
    terminal.update(v.strip().casefold() for v in os.environ.get(
        "PORTEXA_OPS_TERMINAL_STATUSES", "").split(",") if v.strip())
    with source_connection() as source:
        check_schema(source)
        rows = [dict(r) for r in source.execute("SELECT id,bol_no,status FROM shipment")]
    followup.init(conn)
    now = followup.stamp()
    discovered = closed = 0
    with conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS ops_discovered_bills(
            shipment_id INTEGER NOT NULL, bol_no TEXT NOT NULL,
            discovered_at TEXT NOT NULL, PRIMARY KEY(shipment_id,bol_no))""")
        for row in rows:
            sid = row["id"]
            if valid_bill_of_lading(row["bol_no"]):
                bol = str(row["bol_no"]).strip()
                cur = conn.execute("INSERT OR IGNORE INTO ops_discovered_bills VALUES(?,?,?)",
                                   (sid,bol,now))
                if cur.rowcount:
                    discovered += 1
                    conn.execute("""INSERT INTO ops_notifications(user_id,title,body,created_at)
                        SELECT id,?,?,? FROM ops_users WHERE active=1 AND role IN ('admin','supervisor')""",
                        ("بوليصة مكتشفة", bol, now))
            if str(row["status"] or "").strip().casefold() in terminal:
                record = followup.get(conn,sid)
                if record and record["status"] == "active":
                    cur = conn.execute("""UPDATE ops_followups SET status='completed',
                        completed_at=?,completed_by=NULL WHERE shipment_id=? AND status='active'""",
                        (now,sid))
                    if cur.rowcount:
                        closed += 1
                        followup.event(conn,sid,0,"source_completed",str(row["status"]))
                        conn.execute("""INSERT INTO ops_notifications(user_id,title,body,created_at)
                            SELECT id,?,?,? FROM ops_users WHERE active=1 AND
                            (role IN ('admin','supervisor') OR id=?)""",
                            ("انتهاء المتابعة", str(row["bol_no"] or sid), now,record["user_id"]))
    return discovered, closed


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
    p.add_argument("--auto-assign", action="store_true", help="assign eligible new shipments before creating tasks")
    p.add_argument("--actor-id", type=int, help="authorized admin or supervisor ID required with --auto-assign")
    p.add_argument("--statuses", action="store_true", help="print source status counts; no changes")
    args=p.parse_args()
    if args.statuses:
        for row in source_status_summary():
            print(repr(row["status"]), row["count"])
        return
    if args.auto_assign and (not args.apply or not args.actor_id):
        p.error("--auto-assign requires --apply and --actor-id")
    with connect() as conn:
        init_operations(conn)
        if args.apply:
            print('Discovered / closed:', synchronize_source(conn))
        if args.auto_assign:
            plan = distribute(conn,args.actor_id)
            print("Auto-assigned:",len(plan["plan"]))
        info=preview(conn)
        print("Cycle:",info["cycle_at"])
        print("Eligible:",info["eligible"],"Skipped:",info["skipped"],"New:",info["new"])
        if args.apply:
            # Recheck source before commit; never generate from unverified IDs.
            active,_=eligible_assignments(conn)
            print("Follow-ups created:", sync_followups(conn, active))
            active = [sid for sid in active if (followup.get(conn, sid) or {}).get('status') == 'active']
            created=generate_tasks(conn,eligible_ids=active)
            print("Created:",created)
        else:
            print("DRY RUN: no tasks created")


if __name__=="__main__":
    main()
