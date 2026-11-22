"""Recovery edge cases. All databases are temporary."""

import json
import sqlite3
import threading

from portexa_tracking import operations as ops
from portexa_tracking import operations_followup as followup
from portexa_tracking import operations_shipment_edit as edit
from test_operations_shipment_edit import setup as shipment_setup


def pending(conn, task, worker):
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
    return log_id


def apply_source(source, value="TEST-002"):
    with sqlite3.connect(source) as db:
        db.execute(
            "UPDATE shipment SET bol_no=? WHERE id=1",
            (value,),
        )


def state(conn):
    return dict(conn.execute("""
        SELECT user_id,status,update_count
        FROM ops_followups WHERE shipment_id=1
    """).fetchone())


def test_completed_followup_preserved_during_recovery(shipment_setup):
    conn, task, worker, other, source = shipment_setup
    log_id = pending(conn, task, worker)
    apply_source(source)

    followup.complete(conn, worker, 1)
    before = state(conn)

    result = edit.reconcile_pending(conn)

    assert (log_id, "needs_review") in result
    assert state(conn) == before
    assert conn.execute(
        "SELECT status FROM ops_shipment_change_log WHERE id=?",
        (log_id,),
    ).fetchone()[0] == "pending"


def test_reassigned_followup_not_credited_to_old_worker(shipment_setup):
    conn, task, worker, other, source = shipment_setup
    log_id = pending(conn, task, worker)
    apply_source(source)

    conn.execute(
        "UPDATE ops_followups SET user_id=? WHERE shipment_id=1",
        (other,),
    )
    conn.commit()

    before = state(conn)
    result = edit.reconcile_pending(conn)

    assert (log_id, "needs_review") in result
    assert state(conn) == before


def test_overwritten_source_remains_pending(shipment_setup):
    conn, task, worker, other, source = shipment_setup
    log_id = pending(conn, task, worker)

    apply_source(source, "TEST-003")

    result = edit.reconcile_pending(conn)

    assert (log_id, "needs_review") in result
    assert conn.execute(
        "SELECT status FROM ops_shipment_change_log WHERE id=?",
        (log_id,),
    ).fetchone()[0] == "pending"
    assert state(conn)["update_count"] == 0


def test_two_reconcilers_count_only_once(shipment_setup):
    conn, task, worker, other, source = shipment_setup
    log_id = pending(conn, task, worker)
    apply_source(source)

    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def run():
        connection = sqlite3.connect(db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            barrier.wait(timeout=5)
            results.append(edit.reconcile_pending(connection))
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(2)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=15)

    assert not any(thread.is_alive() for thread in threads)
    assert not errors, errors

    assert state(conn)["update_count"] == 1
    assert conn.execute(
        "SELECT status FROM ops_shipment_change_log WHERE id=?",
        (log_id,),
    ).fetchone()[0] == "applied"



def test_reconciliation_uses_locked_source_snapshot(shipment_setup):
    conn, task, worker, other, source = shipment_setup
    log_id = pending(conn, task, worker)
    apply_source(source, "TEST-003")

    result = edit.reconcile_pending(conn)

    assert (log_id, "needs_review") in result
    assert state(conn)["update_count"] == 0


def test_source_write_blocked_during_reconciliation(
    shipment_setup, monkeypatch
):
    conn, task, worker, other, source = shipment_setup
    log_id = pending(conn, task, worker)
    apply_source(source, "TEST-002")

    original_record_update = followup.record_update
    blocked = []

    def attempt_concurrent_write(
        connection, user_id, shipment_id, when=None
    ):
        competing = sqlite3.connect(source, timeout=0.1)

        try:
            competing.execute("BEGIN IMMEDIATE")
            competing.execute(
                "UPDATE shipment SET bol_no=? WHERE id=1",
                ("TEST-003",),
            )
            competing.commit()
            blocked.append(False)

        except sqlite3.OperationalError as exc:
            competing.rollback()
            assert "locked" in str(exc).lower()
            blocked.append(True)

        finally:
            competing.close()

        return original_record_update(
            connection, user_id, shipment_id, when=when
        )

    monkeypatch.setattr(
        followup,
        "record_update",
        attempt_concurrent_write,
    )

    result = edit.reconcile_pending(conn)

    assert blocked == [True]
    assert (log_id, "applied") in result
    assert state(conn)["update_count"] == 1

    with sqlite3.connect(source) as db:
        actual = db.execute(
            "SELECT bol_no FROM shipment WHERE id=1"
        ).fetchone()[0]

    assert actual == "TEST-002"
