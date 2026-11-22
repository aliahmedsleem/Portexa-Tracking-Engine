"""Verify source writes execute while the operations lock is held."""

import sqlite3

import pytest

from portexa_tracking import operations as ops
from portexa_tracking import operations_followup as followup
from portexa_tracking import portexa_bridge as bridge
from portexa_tracking.operations_followup_lock import editable_transaction


@pytest.fixture
def databases(tmp_path, monkeypatch):
    ops_path = tmp_path / "operations.sqlite3"
    source_path = tmp_path / "source.db"

    with sqlite3.connect(ops_path) as conn:
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
            "INSERT INTO ops_assignments "
            "(shipment_id,user_id,assigned_at) VALUES(?,?,?)",
            (1, worker, ops.now_utc()),
        )

        conn.commit()
        followup.init(conn)
        followup.assign(conn, admin, 1, worker)

    with sqlite3.connect(source_path) as conn:
        columns = ", ".join(
            field + " TEXT"
            for field in bridge.DISPLAY
            if field != "id"
        )
        conn.execute(
            "CREATE TABLE shipment ("
            "id INTEGER PRIMARY KEY, "
            + columns +
            ", containers_count INTEGER)"
        )
        conn.execute(
            "INSERT INTO shipment(id,bol_no,containers_count) "
            "VALUES(1,'BOL-001',0)"
        )

    monkeypatch.setenv("PORTEXA_SOURCE_DB", str(source_path))
    monkeypatch.setenv("PORTEXA_SOURCE_WRITE_ENABLED", "YES")

    return ops_path, source_path, worker


def connect(path):
    conn = sqlite3.connect(path, timeout=0.2)
    conn.row_factory = sqlite3.Row
    return conn


def test_source_write_inside_followup_lock(databases):
    ops_path, source_path, worker = databases

    with connect(ops_path) as conn:
        with editable_transaction(conn, worker, 1):

            result = bridge.update_source_shipment(
                1,
                {"bol_no": "BOL-002"},
                {"bol_no": "BOL-001"},
            )

            assert result["bol_no"] == "BOL-002"

            with pytest.raises(sqlite3.OperationalError):
                with connect(ops_path) as second:
                    second.execute("BEGIN IMMEDIATE")

            followup.record_update(conn, worker, 1)

    with connect(ops_path) as conn:
        record = followup.get(conn, 1)
        assert record["update_count"] == 1

    with sqlite3.connect(source_path) as conn:
        assert conn.execute(
            "SELECT bol_no FROM shipment WHERE id=1"
        ).fetchone()[0] == "BOL-002"


def test_source_failure_does_not_record_followup_update(databases, monkeypatch):
    ops_path, source_path, worker = databases

    monkeypatch.delenv("PORTEXA_SOURCE_WRITE_ENABLED")

    with connect(ops_path) as conn:
        with pytest.raises(PermissionError):
            with editable_transaction(conn, worker, 1):
                bridge.update_source_shipment(
                    1,
                    {"bol_no": "BOL-002"},
                    {"bol_no": "BOL-001"},
                )
                followup.record_update(conn, worker, 1)

    with connect(ops_path) as conn:
        assert followup.get(conn, 1)["update_count"] == 0

    with sqlite3.connect(source_path) as conn:
        assert conn.execute(
            "SELECT bol_no FROM shipment WHERE id=1"
        ).fetchone()[0] == "BOL-001"
