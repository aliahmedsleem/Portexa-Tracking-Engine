"""Regression test for reconciliation after a partial source commit."""

import sqlite3
import json

from portexa_tracking import operations as ops
from portexa_tracking import operations_shipment_edit as edit
from test_operations_shipment_edit import setup as shipment_setup


def test_reconcile_applied_change_updates_followup_once(shipment_setup):
    conn, task, worker, other, source = shipment_setup

    before = conn.execute(
        "SELECT update_count FROM ops_followups WHERE shipment_id=1"
    ).fetchone()[0]

    # Simulate a durable pending intent whose source write succeeded.
    log_id = conn.execute("""
        INSERT INTO ops_shipment_change_log
          (task_id,shipment_id,user_id,original_values,
           updated_values,status,created_at)
        VALUES (?,?,?,?,?,'pending',?)
    """, (
        task, 1, worker,
        json.dumps({"bol_no": "TEST-001"}),
        json.dumps({"bol_no": "TEST-002"}),
        ops.now_utc(),
    )).lastrowid
    conn.commit()

    with sqlite3.connect(source) as db:
        db.execute(
            "UPDATE shipment SET bol_no='TEST-002' WHERE id=1"
        )

    first = edit.reconcile_pending(conn)

    assert (log_id, "applied") in first

    after_first = conn.execute(
        "SELECT update_count FROM ops_followups WHERE shipment_id=1"
    ).fetchone()[0]

    assert after_first == before + 1

    # Reconciliation must be idempotent.
    second = edit.reconcile_pending(conn)

    after_second = conn.execute(
        "SELECT update_count FROM ops_followups WHERE shipment_id=1"
    ).fetchone()[0]

    assert second == []
    assert after_second == after_first
