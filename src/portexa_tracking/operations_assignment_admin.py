"""Safe administrative assignment maintenance. Source Portexa is always read-only."""
from portexa_tracking import operations as ops
from portexa_tracking.portexa_bridge import source_connection, check_schema, valid_bill_of_lading

def inventory(conn):
    with source_connection() as source:
        check_schema(source)
        bills = {int(r["id"]): str(r["bol_no"]).strip() for r in source.execute(
            "SELECT id,bol_no FROM shipment") if valid_bill_of_lading(r["bol_no"])}
    assignments = {int(r["shipment_id"]): dict(r) for r in conn.execute(
        "SELECT a.shipment_id,a.user_id,a.active,u.full_name FROM ops_assignments a "
        "LEFT JOIN ops_users u ON u.id=a.user_id")}
    active = {sid for sid,a in assignments.items() if a["active"]}
    invalid = [{**a, "reason": "بوليصة غير صالحة أو شحنة غير موجودة"}
               for sid,a in sorted(assignments.items()) if a["active"] and sid not in bills]
    valid = [{"shipment_id":sid,"bol_no":bol, "assigned":bool(assignments.get(sid,{}).get("active")),
              "user_id":assignments.get(sid,{}).get("user_id"),
              "operator_name":assignments.get(sid,{}).get("full_name")}
             for sid,bol in sorted(bills.items())]
    return valid, invalid

def deactivate(conn, actor, shipment_id):
    ops._require(conn, actor, ("admin","supervisor"))
    sid = int(shipment_id)
    with conn:
        row = conn.execute("SELECT active FROM ops_assignments WHERE shipment_id=?", (sid,)).fetchone()
        if row is None or not row["active"]:
            raise LookupError("Active assignment not found")
        pending = conn.execute(
            "SELECT COUNT(*) FROM ops_tasks WHERE shipment_id=? AND status IN ('pending','submitted')",
            (sid,)).fetchone()[0]
        if pending:
            raise ValueError("Cannot remove assignment with pending or submitted tasks; resolve them first")
        conn.execute("UPDATE ops_assignments SET active=0 WHERE shipment_id=?", (sid,))
        ops.audit(conn, actor, "shipment.assignment.deactivated", "shipment", sid,
                  "assignment deactivated; review history retained")
