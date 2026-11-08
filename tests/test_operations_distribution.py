"""Balanced automatic distribution regression tests."""
import sqlite3
from portexa_tracking import operations as ops
from portexa_tracking import operations_distribution as dist


def setup():
    c=sqlite3.connect(":memory:")
    c.row_factory=sqlite3.Row
    ops.init_operations(c)
    admin=ops.create_user(c,None,"Admin","0770","admin","very-strong-admin-password","admin")
    a=ops.create_user(c,admin,"Operator A","0771","op-a","very-strong-operator-password")
    b=ops.create_user(c,admin,"Operator B","0772","op-b","very-strong-operator-password")
    return c,admin,a,b


def test_balanced_and_idempotent_distribution():
    c,admin,a,b=setup()
    try:
        plan=dist.distribute(c,admin,source_ids=[10,11,12,13,14])
        assert len(plan["plan"])==5
        counts=[r[0] for r in c.execute("SELECT COUNT(*) FROM ops_assignments GROUP BY user_id ORDER BY user_id")]
        assert counts==[3,2]
        before=list(c.execute("SELECT shipment_id,user_id FROM ops_assignments ORDER BY shipment_id"))
        assert dist.distribute(c,admin,source_ids=[10,11,12,13,14])["plan"]==[]
        after=list(c.execute("SELECT shipment_id,user_id FROM ops_assignments ORDER BY shipment_id"))
        assert before==after
        history=dist.assignment_history(c)
        assert len(history)==5
        assert all(row["reviews"]==0 for row in history)
    finally:
        c.close()


def test_no_active_operators_does_not_assign():
    c,admin,a,b=setup()
    try:
        c.execute("UPDATE ops_users SET active=0 WHERE role='operator'")
        try:
            dist.distribute(c,admin,source_ids=[1,2])
            assert False, "Expected no-operators error"
        except ValueError:
            pass
        assert c.execute("SELECT COUNT(*) FROM ops_assignments").fetchone()[0]==0
    finally:
        c.close()
