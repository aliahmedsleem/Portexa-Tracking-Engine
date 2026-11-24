"""Administrative assignment maintenance; never writes to source Portexa."""
import json
from portexa_tracking import operations as ops
from portexa_tracking.portexa_bridge import source_connection, check_schema, valid_bill_of_lading

def source_bills():
    with source_connection() as source:
        check_schema(source)
        return {int(r["id"]): str(r["bol_no"]).strip() for r in source.execute(
            "SELECT id,bol_no FROM shipment") if valid_bill_of_lading(r["bol_no"])}

def inventory(conn):
    bills = source_bills()
    assignments = {int(r["shipment_id"]): dict(r) for r in conn.execute(
        "SELECT a.shipment_id,a.user_id,a.active,u.full_name FROM ops_assignments a "
        "LEFT JOIN ops_users u ON u.id=a.user_id")}
    invalid = []
    for sid,a in sorted(assignments.items()):
        if a["active"] and sid not in bills:
            counts = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status,COUNT(*) n FROM ops_tasks WHERE shipment_id=? GROUP BY status", (sid,))}
            invalid.append({**a, "reason": "بوليصة غير صالحة أو شحنة غير موجودة",
                            "pending_count": counts.get("pending",0),
                            "submitted_count": counts.get("submitted",0)})
    valid = [{"shipment_id":sid,"bol_no":bol, "assigned":bool(assignments.get(sid,{}).get("active")),
              "user_id":assignments.get(sid,{}).get("user_id"),
              "operator_name":assignments.get(sid,{}).get("full_name")}
             for sid,bol in sorted(bills.items())]
    return valid, invalid

def deactivate(conn, actor, shipment_id, confirm=False):
    ops._require(conn, actor, ("admin","supervisor"))
    sid = int(shipment_id)
    if not confirm:
        raise ValueError("Explicit confirmation required")
    # Check source eligibility again at execution time, not just when the page loaded.
    if sid in source_bills():
        raise ValueError("Valid source bill: use reassignment instead")
    conn.execute("SAVEPOINT assignment_deactivation")
    try:
        row = conn.execute("SELECT active FROM ops_assignments WHERE shipment_id=?", (sid,)).fetchone()
        if row is None or not row["active"]:
            raise LookupError("Active assignment not found")
        submitted = conn.execute(
            "SELECT COUNT(*) FROM ops_tasks WHERE shipment_id=? AND status='submitted'",
            (sid,)).fetchone()[0]
        if submitted:
            raise ValueError("Submitted reviews require a separate administrator decision")
        conn.execute("""CREATE TABLE IF NOT EXISTS ops_cancelled_task_archive(
            id INTEGER PRIMARY KEY,
            original_task_id INTEGER NOT NULL UNIQUE,
            shipment_id INTEGER NOT NULL,
            original_task_json TEXT NOT NULL,
            cancelled_by INTEGER NOT NULL,
            cancelled_at TEXT NOT NULL,
            reason TEXT NOT NULL
        )""")
        pending = [dict(r) for r in conn.execute(
            "SELECT * FROM ops_tasks WHERE shipment_id=? AND status='pending'", (sid,))]
        for task in pending:
            conn.execute("""INSERT INTO ops_cancelled_task_archive
                (original_task_id,shipment_id,original_task_json,cancelled_by,cancelled_at,reason)
                VALUES(?,?,?,?,?,?)""",
                (task["id"],sid,json.dumps(task,ensure_ascii=False,default=str),
                 actor,ops.now_utc(),"Invalid source bill; administrative deactivation"))
            # Retain original task and its review links; mark as rejected (schema-safe).
            conn.execute("UPDATE ops_tasks SET status='rejected',review_note=? WHERE id=?",
                         ("ملغاة إدارياً: إسناد غير صالح",task["id"]))
        conn.execute("UPDATE ops_assignments SET active=0 WHERE shipment_id=?", (sid,))
        ops.audit(conn, actor, "shipment.assignment.deactivated", "shipment", sid,
                  f"archived_pending={len(pending)}; submitted=0; review history retained")
        conn.execute("RELEASE SAVEPOINT assignment_deactivation")
        return len(pending)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT assignment_deactivation")
        conn.execute("RELEASE SAVEPOINT assignment_deactivation")
        raise
