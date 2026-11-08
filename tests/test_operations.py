"""Operations foundation regression tests; no production database access."""
import sqlite3
from datetime import datetime, timezone
import pytest
from portexa_tracking import operations as op


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    op.init_operations(conn)
    yield conn
    conn.close()


def setup_users(db):
    admin = op.create_user(db, None, "Admin", "07700000000", "admin", "admin-pass-strong", "admin")
    operator = op.create_user(db, admin, "Operator", "07700000001", "operator", "operator-pass-strong")
    return admin, operator


def test_passwords_and_roles(db):
    admin, operator = setup_users(db)
    assert op.authenticate(db, "operator", "operator-pass-strong")["id"] == operator
    assert op.authenticate(db, "operator", "wrong") is None
    with pytest.raises(PermissionError):
        op.create_user(db, operator, "X", "1", "x", "password-long-123")


def test_cycles_baghdad():
    assert op.cycle_start(datetime(2026, 11, 8, 3, 15, tzinfo=timezone.utc)).startswith("2026-11-08T06:00")
    assert op.cycle_start(datetime(2026, 11, 7, 22, 15, tzinfo=timezone.utc)).startswith("2026-11-08T00:00")


def test_review_payroll_no_change(db):
    admin, operator = setup_users(db)
    op.set_price(db, admin, 300)
    db.execute("INSERT INTO ops_assignments(shipment_id,user_id,assigned_at) VALUES(?,?,?)", (123, operator, op.now_utc()))
    assert op.generate_tasks(db, datetime(2026, 11, 8, 9, tzinfo=timezone.utc)) == 1
    assert op.generate_tasks(db, datetime(2026, 11, 8, 9, tzinfo=timezone.utc)) == 0
    task_id = db.execute("SELECT id FROM ops_tasks").fetchone()[0]
    op.submit_task(db, operator, task_id, "Carrier portal checked", "Verified; no change", False)
    assert op.payroll(db, admin, operator)["earned_iqd"] == 0
    op.decide_task(db, admin, task_id, True)
    assert op.payroll(db, admin, operator)["earned_iqd"] == 300
    op.set_price(db, admin, 500)
    assert op.payroll(db, admin, operator)["earned_iqd"] == 300
    op.record_payment(db, admin, operator, 300)
    assert op.payroll(db, admin, operator)["balance_iqd"] == 0
    with pytest.raises(ValueError):
        op.record_payment(db, admin, operator, 1)
    with pytest.raises(ValueError):
        op.decide_task(db, admin, task_id, True)


def test_no_pay_before_approval_and_rejected_task(db):
    admin, operator = setup_users(db)
    db.execute("INSERT INTO ops_tasks(shipment_id,cycle_at,user_id,price_iqd,created_at) VALUES(?,?,?,?,?)",
               (11, "2026-11-08T12:00:00+03:00", operator, 250, op.now_utc()))
    task_id = db.execute("SELECT id FROM ops_tasks").fetchone()[0]
    with pytest.raises(ValueError):
        op.submit_task(db, operator, task_id, "", "done", False)
    op.submit_task(db, operator, task_id, "Carrier website", "Verified no change", False)
    op.decide_task(db, admin, task_id, False, "Insufficient evidence")
    assert op.payroll(db, admin, operator)["earned_iqd"] == 0
    op.submit_task(db, operator, task_id, "Carrier portal and screenshot", "Verified no change", False)
    op.decide_task(db, admin, task_id, True)
    assert op.payroll(db, admin, operator)["earned_iqd"] == 250


def test_generate_only_verified_eligible_ids(db):
    admin, operator = setup_users(db)
    for sid in (101, 102):
        db.execute("INSERT INTO ops_assignments(shipment_id,user_id,assigned_at) VALUES(?,?,?)",
                   (sid,operator,op.now_utc()))
    dt=datetime(2026,11,8,9,tzinfo=timezone.utc)
    assert op.generate_tasks(db,dt,eligible_ids=[]) == 0
    assert op.generate_tasks(db,dt,eligible_ids=[101]) == 1
    assert op.generate_tasks(db,dt,eligible_ids=[101]) == 0
    assert [r[0] for r in db.execute("SELECT shipment_id FROM ops_tasks")] == [101]
