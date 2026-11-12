"""Shipment edit regression tests use temporary SQLite files only."""
import sqlite3
import pytest
from portexa_tracking import operations as ops
from portexa_tracking import operations_shipment_edit as edit


@pytest.fixture
def setup(monkeypatch, tmp_path):
    opsdb = sqlite3.connect(tmp_path / "operations.sqlite3")
    opsdb.row_factory = sqlite3.Row
    ops.init_operations(opsdb)
    admin = ops.create_user(opsdb,None,"Admin","07700000000","admin","strong-admin-pass","admin")
    worker = ops.create_user(opsdb,admin,"Worker","07700000001","worker","strong-worker-pass")
    other = ops.create_user(opsdb,admin,"Other","07700000002","other","strong-other-pass")
    opsdb.execute("INSERT INTO ops_assignments(shipment_id,user_id,assigned_at) VALUES(?,?,?)",(1,worker,ops.now_utc()))
    opsdb.execute("""INSERT INTO ops_tasks(shipment_id,cycle_at,user_id,price_iqd,created_at)
                    VALUES(?,?,?,?,?)""",(1,"2026-11-09T12:00:00+03:00",worker,250,ops.now_utc()))
    opsdb.commit()
    task = opsdb.execute("SELECT id FROM ops_tasks").fetchone()[0]
    source = tmp_path / "portexa-test.db"
    with sqlite3.connect(source) as conn:
        conn.execute("""CREATE TABLE shipment (
          id INTEGER PRIMARY KEY,owner_id INTEGER,invoice_no TEXT,bol_no VARCHAR(120),
          msc_tracking VARCHAR(300),ship_date DATE,arrival_date DATE,shipping_details TEXT,
          status TEXT,factory_id INTEGER,country_id INTEGER)""")
        conn.execute("INSERT INTO shipment(id,bol_no) VALUES(1,'TEST-001')")
    monkeypatch.setenv("PORTEXA_SOURCE_DB",str(source))
    monkeypatch.setenv("PORTEXA_SOURCE_WRITE_ENABLED","YES")
    yield opsdb,task,worker,other,source
    opsdb.close()


def payload(bol):
    return {"bol_no":bol,"msc_tracking":"","ship_date":"","arrival_date":"","shipping_details":""}


def expected():
    return {"bol_no":"TEST-001","msc_tracking":None,"ship_date":None,
            "arrival_date":None,"shipping_details":None}


def test_save_audit_and_no_payroll(setup):
    conn,task,worker,other,source=setup
    result=edit.save_shipment(conn,worker,task,payload("TEST-002"),expected())
    assert result["changed"]
    assert conn.execute("SELECT status FROM ops_shipment_change_log").fetchone()[0]=="applied"
    assert conn.execute("SELECT COUNT(*) FROM ops_audit WHERE action='shipment.updated'").fetchone()[0]==1
    assert conn.execute("SELECT status FROM ops_tasks").fetchone()[0]=="pending"
    assert conn.execute("SELECT COUNT(*) FROM ops_payments").fetchone()[0]==0
    assert edit.save_shipment(conn,worker,task,payload("TEST-002"),
        {**expected(),"bol_no":"TEST-002"})["changed"] is False


def test_reject_other_worker_and_reassignment(setup):
    conn,task,worker,other,source=setup
    with pytest.raises(PermissionError):
        edit.save_shipment(conn,other,task,payload("TEST-002"),expected())
    conn.execute("UPDATE ops_assignments SET user_id=? WHERE shipment_id=1",(other,))
    conn.commit()
    with pytest.raises(PermissionError):
        edit.save_shipment(conn,worker,task,payload("TEST-002"),expected())


def test_conflict_and_disabled_writes(setup,monkeypatch):
    conn,task,worker,other,source=setup
    with sqlite3.connect(source) as db:
        db.execute("UPDATE shipment SET bol_no='NEW' WHERE id=1")
    with pytest.raises(RuntimeError,match="تغيرت"):
        edit.save_shipment(conn,worker,task,payload("TEST-002"),expected())
    assert conn.execute("SELECT COUNT(*) FROM ops_shipment_change_log").fetchone()[0]==0
    with sqlite3.connect(source) as db:
        db.execute("UPDATE shipment SET bol_no='TEST-001' WHERE id=1")
    monkeypatch.delenv("PORTEXA_SOURCE_WRITE_ENABLED")
    with pytest.raises(RuntimeError,match="تعذر حفظ"):
        edit.save_shipment(conn,worker,task,payload("TEST-002"),expected())
    assert conn.execute("SELECT status FROM ops_shipment_change_log").fetchone()[0]=="failed"


def test_validation_before_log(setup):
    conn,task,worker,other,source=setup
    with pytest.raises(ValueError):
        edit.save_shipment(conn,worker,task,payload("x"*121),expected())
    assert conn.execute("SELECT COUNT(*) FROM ops_shipment_change_log").fetchone()[0]==0
