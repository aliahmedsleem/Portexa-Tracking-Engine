"""Employee shipment editing. Source writes remain disabled unless explicitly enabled."""
import json
import sqlite3
from portexa_tracking import operations as ops
from portexa_tracking.portexa_bridge import EDITABLE, get_source_shipment, update_source_shipment

FIELDS = ("bol_no", "msc_tracking", "ship_date", "arrival_date", "shipping_details")


def authorized_task(conn, actor, task_id):
    ops._require(conn, actor, ("operator",))
    row = conn.execute("""
        SELECT t.id,t.shipment_id,t.user_id,t.status
        FROM ops_tasks t JOIN ops_assignments a ON a.shipment_id=t.shipment_id
        WHERE t.id=? AND t.user_id=? AND a.user_id=? AND a.active=1
          AND t.status IN ('pending','rejected')
    """, (task_id, actor, actor)).fetchone()
    if row is None:
        raise PermissionError("المهمة غير مسندة إليك أو غير قابلة للتعديل")
    return dict(row)


def editable_shipment(conn, actor, task_id):
    task = authorized_task(conn, actor, task_id)
    shipment = get_source_shipment(task["shipment_id"])
    if shipment is None:
        raise LookupError("الشحنة غير موجودة")
    return task, shipment


def _validate(values):
    from datetime import date
    import re
    if not isinstance(values, dict) or set(values) != set(FIELDS):
        raise ValueError("حقول الشحنة غير مكتملة")
    normalized = {}
    for key in FIELDS:
        value = values[key]
        if not isinstance(value, str):
            raise ValueError("قيمة غير صالحة")
        value = value.strip() if key != "shipping_details" else value.strip()
        limit = 120 if key == "bol_no" else 2000 if key == "shipping_details" else 300
        if len(value) > limit:
            raise ValueError("قيمة طويلة جداً: " + key)
        if key == "bol_no" and value and not re.fullmatch(r"[\w./ -]{1,120}", value, re.UNICODE):
            raise ValueError("رقم البوليصة غير صالح")
        if key in ("ship_date", "arrival_date") and value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError as exc:
                raise ValueError("صيغة التاريخ غير صالحة: " + key) from exc
        normalized[key] = value or None
    return normalized


def save_shipment(conn, actor, task_id, values, expected):
    """Serialize operations edits; record intent before writing to separate source DB."""
    values = _validate(values)
    if not isinstance(expected, dict) or set(expected) != set(FIELDS):
        raise ValueError("أعد فتح نموذج التعديل")
    # SQLite write lock prevents simultaneous reassignment by this operations service.
    conn.execute("BEGIN IMMEDIATE")
    try:
        task, current = editable_shipment(conn, actor, task_id)
        if any(current[k] != expected[k] for k in FIELDS):
            raise RuntimeError("تغيرت بيانات الشحنة؛ حدّث الصفحة قبل الحفظ")
        changed = {k: v for k, v in values.items() if v != current[k]}
        if not changed:
            conn.rollback()
            return {"changed": False, "shipment": current}
        old = {k: current[k] for k in changed}
        log_id = conn.execute("""
            INSERT INTO ops_shipment_change_log
              (task_id,shipment_id,user_id,original_values,updated_values,status,created_at)
            VALUES (?,?,?,?,?,'pending',?)
        """, (task_id, task["shipment_id"], actor,
              json.dumps(old,ensure_ascii=False), json.dumps(changed,ensure_ascii=False),
              ops.now_utc())).lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    try:
        result = update_source_shipment(task["shipment_id"], changed, old)
    except Exception as exc:
        # Read back: the source commit may have succeeded before a later failure.
        try:
            after = get_source_shipment(task["shipment_id"])
        except Exception:
            after = None
        if after is None:
            raise RuntimeError("نتيجة الحفظ غير مؤكدة؛ راجع سجل التحديثات قبل إعادة المحاولة") from exc
        if all(after[k] == v for k, v in changed.items()):
            status = "applied"
            result = after
        elif all(after[k] == v for k, v in old.items()):
            status = "failed"
        else:
            status = "conflict"
        with conn:
            conn.execute("""UPDATE ops_shipment_change_log SET status=?,error_message=?,applied_at=?
                            WHERE id=? AND status='pending'""",
                         (status,str(exc)[:500],ops.now_utc() if status=="applied" else None,log_id))
            ops.audit(conn, actor, "shipment.edit."+status, "shipment", task["shipment_id"], str(log_id))
        if status != "applied":
            raise RuntimeError("تعذر حفظ التعديل: " + str(exc)) from exc

    with conn:
        conn.execute("""UPDATE ops_shipment_change_log
                        SET status='applied',applied_at=?
                        WHERE id=? AND status='pending'""",(ops.now_utc(),log_id))
        ops.audit(conn,actor,"shipment.updated","shipment",task["shipment_id"],"log_id="+str(log_id))
    return {"changed": True, "shipment": result, "log_id": log_id}


def pending_changes(conn):
    return [dict(r) for r in conn.execute("""
        SELECT id,task_id,shipment_id,user_id,original_values,updated_values,created_at
        FROM ops_shipment_change_log WHERE status='pending' ORDER BY id
    """)]


def reconcile_pending(conn):
    """Conservative reconciliation: leave ambiguous or overwritten edits pending."""
    results = []
    for item in pending_changes(conn):
        try:
            current = get_source_shipment(item["shipment_id"])
            if current is None:
                results.append((item["id"],"unknown"))
                continue
            old = json.loads(item["original_values"])
            new = json.loads(item["updated_values"])
            if all(current[k] == v for k,v in new.items()):
                status = "applied"
            elif all(current[k] == v for k,v in old.items()):
                # May still have been applied then overwritten: do not infer failure.
                results.append((item["id"],"needs_review"))
                continue
            else:
                results.append((item["id"],"needs_review"))
                continue
            with conn:
                conn.execute("""UPDATE ops_shipment_change_log SET status=?,applied_at=?
                                WHERE id=? AND status='pending'""",
                             (status,ops.now_utc(),item["id"]))
                ops.audit(conn,item["user_id"],"shipment.reconciled","shipment",item["shipment_id"],
                          "log_id="+str(item["id"]))
            results.append((item["id"],status))
        except (OSError,ValueError,RuntimeError,sqlite3.Error):
            results.append((item["id"],"unknown"))
    return results
