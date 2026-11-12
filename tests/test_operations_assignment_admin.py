"""Regression tests for administrative deactivation; all databases are isolated."""
import sqlite3
import pytest
from portexa_tracking import operations as ops
from portexa_tracking import operations_assignment_admin as admin

@pytest.fixture
def setup(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ops.init_operations(db)
    manager = ops.create_user(db,None,"Admin","0770","admin","strong-admin-password","admin")
    worker = ops.create_user(db,manager,"Worker","0771","worker","strong-worker-password")
    bills = {4: "MEDU12345"}
    monkeypatch.setattr(admin,"source_bills",lambda: dict(bills))
    yield db,manager,worker,bills
    db.close()

def add_assignment(db,sid,worker,status=None):
    db.execute("INSERT INTO ops_assignments(shipment_id,user_id,assigned_at) VALUES(?,?,?)",
               (sid,worker,ops.now_utc()))
    if status:
        db.execute("""INSERT INTO ops_tasks(shipment_id,cycle_at,user_id,status,price_iqd,created_at)
                   VALUES(?,?,?,?,?,?)""",(sid,"2026-11-12T06:00:00+03:00",worker,status,250,ops.now_utc()))

def test_pending_archived_before_deactivation(setup):
    db,manager,worker,_ = setup
    add_assignment(db,5,worker,"pending")
    with pytest.raises(ValueError,match="confirmation"):
        admin.deactivate(db,manager,5)
    assert admin.deactivate(db,manager,5,True)==1
    assert db.execute("SELECT active FROM ops_assignments WHERE shipment_id=5").fetchone()[0]==0
    assert db.execute("SELECT status FROM ops_tasks WHERE shipment_id=5").fetchone()[0]=="rejected"
    archived=db.execute("SELECT original_task_json FROM ops_cancelled_task_archive WHERE shipment_id=5").fetchone()[0]
    assert '"status": "pending"' in archived
    assert db.execute("SELECT COUNT(*) FROM ops_audit WHERE action='shipment.assignment.deactivated'").fetchone()[0]==1

def test_valid_bill_cannot_be_deactivated(setup):
    db,manager,worker,_ = setup
    add_assignment(db,4,worker,"pending")
    with pytest.raises(ValueError,match="Valid source bill"):
        admin.deactivate(db,manager,4,True)
    assert db.execute("SELECT active FROM ops_assignments WHERE shipment_id=4").fetchone()[0]==1

def test_submitted_blocks_and_does_not_archive(setup):
    db,manager,worker,_ = setup
    add_assignment(db,6,worker,"submitted")
    with pytest.raises(ValueError,match="Submitted"):
        admin.deactivate(db,manager,6,True)
    assert db.execute("SELECT active FROM ops_assignments WHERE shipment_id=6").fetchone()[0]==1
    assert db.execute("SELECT status FROM ops_tasks WHERE shipment_id=6").fetchone()[0]=="submitted"

def test_approved_review_preserved(setup):
    db,manager,worker,_ = setup
    add_assignment(db,7,worker,"approved")
    assert admin.deactivate(db,manager,7,True)==0
    assert db.execute("SELECT status FROM ops_tasks WHERE shipment_id=7").fetchone()[0]=="approved"
    assert db.execute("SELECT active FROM ops_assignments WHERE shipment_id=7").fetchone()[0]==0

def test_missing_assignment_rejected(setup):
    db,manager,worker,_ = setup
    with pytest.raises(LookupError):
        admin.deactivate(db,manager,99,True)
