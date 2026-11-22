"""HTTP integration tests for shipment bundle follow-up lifecycle."""

import sqlite3

import pytest

from portexa_tracking import operations as ops
from portexa_tracking import operations_followup as followup
from portexa_tracking import operations_web as web
from portexa_tracking import portexa_bridge as bridge


@pytest.fixture
def setup_http(tmp_path, monkeypatch):
    ops_path = tmp_path / "operations.sqlite3"
    source_path = tmp_path / "source.db"

    monkeypatch.setenv("PORTEXA_TRACKING_DB", str(ops_path))
    monkeypatch.setenv("PORTEXA_SOURCE_DB", str(source_path))
    monkeypatch.setenv("PORTEXA_SOURCE_WRITE_ENABLED", "YES")

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

        conn.execute(
            """
            INSERT INTO ops_tasks
            (shipment_id,cycle_at,user_id,price_iqd,created_at)
            VALUES(?,?,?,?,?)
            """,
            (1, ops.now_utc(), worker, 0, ops.now_utc()),
        )

        conn.commit()
        followup.init(conn)
        followup.assign(conn, admin, 1, worker)

        task_id = conn.execute(
            "SELECT id FROM ops_tasks"
        ).fetchone()[0]

    with sqlite3.connect(source_path) as conn:
        cols = ", ".join(
            key + " TEXT"
            for key in bridge.DISPLAY
            if key != "id"
        )

        conn.execute(
            "CREATE TABLE shipment "
            "(id INTEGER PRIMARY KEY, "
            + cols +
            ", containers_count INTEGER)"
        )

        conn.execute(
            "INSERT INTO shipment "
            "(id,bol_no,containers_count) "
            "VALUES(1,'BOL-001',0)"
        )

    monkeypatch.setattr(web, "db", lambda: bridge_ops_connection(ops_path))

    web.app.config.update(
        TESTING=True,
        SECRET_KEY="bundle-http-test-only",
    )

    client = web.app.test_client()
    client.get("/login")

    with client.session_transaction() as session:
        csrf = session["csrf"]

    response = client.post(
        "/login",
        data={
            "username": "worker",
            "password": "strong-worker-pass",
            "csrf": csrf,
        },
    )

    assert response.status_code == 302

    return client, ops_path, source_path, task_id, worker


def bridge_ops_connection(path):
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def snapshot(task_id):
    shipment = bridge.get_source_shipment(1)

    return web.snapshot_signer().dumps({
        "task_id": task_id,
        "values": {
            key: shipment[key]
            for key in bridge.EDITABLE
        },
        "containers": bridge.read_container_movements(1),
    })


def post_bundle(client, task_id, bol):
    with client.session_transaction() as session:
        csrf = session["csrf"]

    data = {
        "csrf": csrf,
        "snapshot": snapshot(task_id),
        "bol_no": bol,
        "msc_tracking": "",
        "ship_date": "",
        "arrival_date": "",
        "shipping_details": "",
    }

    return client.post(
        f"/tasks/{task_id}/bundle/save",
        data=data,
    )


def get_followup(path):
    with bridge_ops_connection(path) as conn:
        return dict(followup.get(conn, 1))


def test_bundle_http_updates_followup_once(setup_http):
    client, ops_path, source_path, task_id, worker = setup_http

    response = post_bundle(client, task_id, "BOL-002")
    assert response.status_code == 302

    assert bridge.get_source_shipment(1)["bol_no"] == "BOL-002"
    assert get_followup(ops_path)["update_count"] == 1


def test_bundle_http_no_change_does_not_reset_reminder(setup_http):
    client, ops_path, source_path, task_id, worker = setup_http

    before = get_followup(ops_path)

    response = post_bundle(client, task_id, "BOL-001")
    assert response.status_code == 302

    after = get_followup(ops_path)

    assert after["update_count"] == before["update_count"]
    assert after["last_updated_at"] == before["last_updated_at"]


def test_bundle_http_rejects_completed_followup(setup_http):
    client, ops_path, source_path, task_id, worker = setup_http

    with bridge_ops_connection(ops_path) as conn:
        followup.complete(conn, worker, 1)

    response = post_bundle(client, task_id, "BOL-999")
    assert response.status_code == 302

    assert bridge.get_source_shipment(1)["bol_no"] == "BOL-001"
    assert get_followup(ops_path)["update_count"] == 0
