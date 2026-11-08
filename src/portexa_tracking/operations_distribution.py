"""Fair automatic assignment of eligible PORTEXA shipments.

Only reads the source database; never rewrites existing assignments or task history.
"""
from collections import Counter
from portexa_tracking.portexa_bridge import source_connection, check_schema
from portexa_tracking.operations import now_utc, audit

TERMINAL = {"delivered","completed","cancelled","canceled","archived","closed",
            "تم التسليم","مكتملة","ملغاة","مؤرشفة","مغلق"}


def source_eligible_ids():
    """Read all source shipments (not the UI's 500-row limited page)."""
    with source_connection() as source:
        check_schema(source)
        return [int(row["id"]) for row in source.execute("SELECT id,status FROM shipment ORDER BY id")
                if str(row["status"] or "").strip().lower() not in TERMINAL]


def distribution_preview(conn, source_ids=None):
    ids = source_eligible_ids() if source_ids is None else sorted(set(map(int, source_ids)))
    users = [dict(r) for r in conn.execute(
        "SELECT id,full_name,phone FROM ops_users WHERE role='operator' AND active=1 ORDER BY id")]
    assigned = {int(r["shipment_id"]):int(r["user_id"]) for r in conn.execute(
        "SELECT shipment_id,user_id FROM ops_assignments WHERE active=1")}
    counts = Counter(uid for sid,uid in assigned.items() if sid in ids)
    pending = [sid for sid in ids if sid not in assigned]
    plan = []
    if users:
        for sid in pending:
            user = min(users,key=lambda u:(counts[u["id"]],u["id"]))
            plan.append((sid,user["id"]))
            counts[user["id"]] += 1
    return {"users":[{**u,"load":counts[u["id"]]} for u in users],
            "eligible":len(ids),"already_assigned":sum(sid in assigned for sid in ids),
            "unassigned":len(pending),"plan":plan}


def distribute(conn, actor, source_ids=None):
    from portexa_tracking.operations import _require
    _require(conn,actor,("admin","supervisor"))
    # Source read occurs before any changes; failure leaves assignments unchanged.
    plan = distribution_preview(conn,source_ids)
    if plan["unassigned"] and not plan["users"]:
        raise ValueError("No active operators available")
    with conn:
        for sid,uid in plan["plan"]:
            conn.execute(
                "INSERT OR IGNORE INTO ops_assignments(shipment_id,user_id,active,assigned_at) VALUES(?,?,1,?)",
                (sid,uid,now_utc()))
            audit(conn,actor,"shipment.auto_assigned","shipment",sid,"user_id="+str(uid))
        if plan["plan"]:
            audit(conn,actor,"distribution.completed","assignment",None,
                  "count="+str(len(plan["plan"])))
    return plan


def assignment_history(conn, limit=500):
    return [dict(r) for r in conn.execute("""
        SELECT a.shipment_id,a.user_id,u.full_name AS operator_name,a.active,a.assigned_at,
          COUNT(t.id) AS reviews,
          SUM(CASE WHEN t.status='approved' THEN 1 ELSE 0 END) AS approved_reviews,
          MAX(t.submitted_at) AS last_submitted_at
        FROM ops_assignments a JOIN ops_users u ON u.id=a.user_id
        LEFT JOIN ops_tasks t ON t.shipment_id=a.shipment_id
        GROUP BY a.shipment_id,a.user_id,u.full_name,a.active,a.assigned_at
        ORDER BY a.shipment_id DESC LIMIT ?""",(min(max(int(limit),1),2000),))]


def review_history(conn,shipment_id):
    return [dict(r) for r in conn.execute("""
        SELECT t.id,t.cycle_at,t.status,t.review_note,t.evidence_source,
               t.route_changed,t.submitted_at,t.approved_at,
               u.full_name AS operator_name, a.full_name AS approver_name
        FROM ops_tasks t JOIN ops_users u ON u.id=t.user_id
        LEFT JOIN ops_users a ON a.id=t.approved_by
        WHERE t.shipment_id=? ORDER BY t.id DESC""",(int(shipment_id),))]
