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

    # Enforce the new follow-up lifecycle on every legacy task edit.
    from portexa_tracking import operations_followup as followup
    followup.require_editable(conn, actor, row["shipment_id"])

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
    """Durable intent, protected source write, and follow-up accounting."""
    from portexa_tracking import operations_followup as followup
    from portexa_tracking.operations_followup_lock import editable_transaction

    values = _validate(values)

    if not isinstance(expected, dict) or set(expected) != set(FIELDS):
        raise ValueError("أعد فتح نموذج التعديل")

    # Phase 1: persist the intent before touching the source database.
    conn.execute("BEGIN IMMEDIATE")
    try:
        task, current = editable_shipment(conn, actor, task_id)

        if any(current[k] != expected[k] for k in FIELDS):
            raise RuntimeError("تغيرت بيانات الشحنة؛ حدّث الصفحة قبل الحفظ")

        changed = {
            key: value
            for key, value in values.items()
            if value != current[key]
        }

        if not changed:
            conn.rollback()
            return {"changed": False, "shipment": current}

        old = {key: current[key] for key in changed}

        log_id = conn.execute("""
            INSERT INTO ops_shipment_change_log
              (task_id,shipment_id,user_id,original_values,
               updated_values,status,created_at)
            VALUES (?,?,?,?,?,'pending',?)
        """, (
            task_id,
            task["shipment_id"],
            actor,
            json.dumps(old, ensure_ascii=False),
            json.dumps(changed, ensure_ascii=False),
            ops.now_utc(),
        )).lastrowid

        conn.commit()
    except BaseException:
        conn.rollback()
        raise

    shipment_id = task["shipment_id"]

    # Phase 2: lock follow-up state while writing the source database.
    try:
        with editable_transaction(conn, actor, shipment_id):
            authorized_task(conn, actor, task_id)

            latest = get_source_shipment(shipment_id)
            if latest is None:
                raise LookupError("الشحنة غير موجودة")

            if any(latest[key] != expected[key] for key in FIELDS):
                raise RuntimeError(
                    "تغيرت بيانات الشحنة؛ حدّث الصفحة قبل الحفظ"
                )

            result = update_source_shipment(shipment_id, changed, old)

            followup.record_update(conn, actor, shipment_id)

            conn.execute("""
                UPDATE ops_shipment_change_log
                SET status='applied',applied_at=?
                WHERE id=? AND status='pending'
            """, (ops.now_utc(), log_id))

            ops.audit(
                conn, actor, "shipment.updated",
                "shipment", shipment_id,
                "log_id=" + str(log_id),
            )

        return {
            "changed": True,
            "shipment": result,
            "log_id": log_id,
        }

    except Exception as exc:
        # Do not blindly retry: the source commit might have succeeded.
        try:
            after = get_source_shipment(shipment_id)
        except Exception:
            after = None

        if after is None:
            raise RuntimeError(
                "نتيجة الحفظ غير مؤكدة؛ راجع سجل التحديثات"
            ) from exc

        if all(after[key] == value for key, value in changed.items()):
            # Source may have committed. Leave pending for reconciliation.
            raise RuntimeError(
                "تم رصد التعديل في المصدر لكن تثبيت المتابعة غير مؤكد؛ "
                "تحتاج العملية إلى مصالحة"
            ) from exc

        if all(after[key] == value for key, value in old.items()):
            status = "failed"
        else:
            status = "conflict"

        with conn:
            conn.execute("""
                UPDATE ops_shipment_change_log
                SET status=?,error_message=?
                WHERE id=? AND status='pending'
            """, (status, str(exc)[:500], log_id))

            ops.audit(
                conn, actor, "shipment.edit." + status,
                "shipment", shipment_id,
                "log_id=" + str(log_id),
            )

        raise RuntimeError("تعذر حفظ التعديل: " + str(exc)) from exc


def pending_changes(conn):
    return [dict(r) for r in conn.execute("""
        SELECT id,task_id,shipment_id,user_id,original_values,updated_values,created_at
        FROM ops_shipment_change_log WHERE status='pending' ORDER BY id
    """)]


def reconcile_pending(conn):
    """Recover confirmed source updates without racing source writers."""
    from portexa_tracking import operations_followup as followup
    from portexa_tracking.portexa_bridge import (
        source_connection,
        check_schema,
        DISPLAY,
    )

    results = []

    for item in pending_changes(conn):
        if conn.in_transaction:
            raise RuntimeError(
                "Reconciliation requires a connection without an active transaction"
            )

        try:
            old = json.loads(item["original_values"])
            new = json.loads(item["updated_values"])

            if (
                not isinstance(old, dict)
                or not isinstance(new, dict)
                or not new
                or set(old) != set(new)
                or set(new) - set(FIELDS)
            ):
                results.append((item["id"], "needs_review"))
                continue

            conn.execute("BEGIN IMMEDIATE")

            try:
                row = conn.execute("""
                    SELECT status
                    FROM ops_shipment_change_log
                    WHERE id=?
                """, (item["id"],)).fetchone()

                if row is None or row["status"] != "pending":
                    conn.rollback()
                    continue

                owner = followup.get(conn, item["shipment_id"])

                if (
                    owner is None
                    or owner["user_id"] != item["user_id"]
                    or owner["status"] != "active"
                ):
                    conn.rollback()
                    results.append((item["id"], "needs_review"))
                    continue

                # Acquire the source write lock before reading.
                with source_connection(write=True) as source:
                    check_schema(source)
                    source.execute("BEGIN IMMEDIATE")

                    current_row = source.execute(
                        "SELECT " + ", ".join(DISPLAY) +
                        " FROM shipment WHERE id=?",
                        (int(item["shipment_id"]),),
                    ).fetchone()

                    if current_row is None:
                        source.rollback()
                        conn.rollback()
                        results.append((item["id"], "unknown"))
                        continue

                    current = dict(current_row)

                    if not all(
                        current[key] == value
                        for key, value in new.items()
                    ):
                        source.rollback()
                        conn.rollback()
                        results.append((item["id"], "needs_review"))
                        continue

                    # Source stays locked through operations commit.
                    followup.record_update(
                        conn,
                        item["user_id"],
                        item["shipment_id"],
                    )

                    changed = conn.execute("""
                        UPDATE ops_shipment_change_log
                        SET status='applied', applied_at=?
                        WHERE id=? AND status='pending'
                    """, (ops.now_utc(), item["id"]))

                    if changed.rowcount != 1:
                        raise RuntimeError(
                            "Pending change was modified"
                        )

                    ops.audit(
                        conn,
                        item["user_id"],
                        "shipment.reconciled",
                        "shipment",
                        item["shipment_id"],
                        "log_id=" + str(item["id"]),
                    )

                    conn.commit()
                    source.rollback()

                    results.append((item["id"], "applied"))

            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise

        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            if conn.in_transaction:
                conn.rollback()
            results.append((item["id"], "unknown"))

    return results
