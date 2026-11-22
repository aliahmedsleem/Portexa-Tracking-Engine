"""Shipment write-lock contract tests using temporary SQLite databases."""

import sqlite3
import threading
from pathlib import Path

import pytest

from portexa_tracking import operations as ops
from portexa_tracking import operations_followup as followup
from portexa_tracking.operations_followup_lock import editable_transaction


@pytest.fixture
def setup_db(tmp_path):
    path = Path(tmp_path) / "operations.sqlite3"

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        ops.init_operations(conn)

        admin = ops.create_user(
            conn, None, "Admin", "07700000000",
            "admin", "strong-admin-pass", "admin"
        )

        worker = ops.create_user(
            conn, admin, "Worker", "07700000001",
            "worker", "strong-worker-pass"
        )

        conn.execute(
            """
            INSERT INTO ops_assignments
            (shipment_id,user_id,assigned_at)
            VALUES(?,?,?)
            """,
            (1, worker, ops.now_utc()),
        )

        conn.commit()

        followup.init(conn)
        followup.assign(conn, admin, 1, worker)

    return path, worker


def connect(path, timeout=1):
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn


def test_completion_cannot_pass_active_edit_lock(setup_db):
    path, worker = setup_db

    with connect(path) as writer:
        with editable_transaction(writer, worker, 1):

            result = []

            def attempt_completion():
                try:
                    with connect(path, timeout=0.2) as second:
                        second.execute("BEGIN IMMEDIATE")
                        result.append("lock_acquired")
                        second.rollback()
                except sqlite3.OperationalError:
                    result.append("blocked")

            thread = threading.Thread(target=attempt_completion)
            thread.start()
            thread.join(timeout=3)

            assert not thread.is_alive()
            assert result == ["blocked"]

        with connect(path) as second:
            second.execute("BEGIN IMMEDIATE")
            second.rollback()


def test_completed_followup_blocks_new_edit_lock(setup_db):
    path, worker = setup_db

    with connect(path) as conn:
        followup.complete(conn, worker, 1)

    with connect(path) as conn:
        with pytest.raises(PermissionError, match="Completed"):
            with editable_transaction(conn, worker, 1):
                pass

        assert not conn.in_transaction
