"""Regression tests for bill validation and new-bill notification deduplication."""
import sqlite3
from contextlib import contextmanager

from portexa_tracking import operations as ops
from portexa_tracking import operations_bill_discovery as discovery
from portexa_tracking.portexa_bridge import valid_bill_of_lading


def test_bill_validation():
    for value in (None, "", "  ", "NONE", " none ", "Null", " NULL "):
        assert not valid_bill_of_lading(value)
    for value in ("MEDU1234567", "  MSC-ABC123  "):
        assert valid_bill_of_lading(value)


def test_bill_discovery_baseline_and_new_bills(monkeypatch):
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    source.execute("""CREATE TABLE shipment (
        id INTEGER PRIMARY KEY, owner_id INTEGER, invoice_no TEXT, bol_no TEXT,
        msc_tracking TEXT, ship_date TEXT, arrival_date TEXT, shipping_details TEXT,
        status TEXT, factory_id INTEGER, country_id INTEGER
    )""")
    source.executemany("INSERT INTO shipment(id,invoice_no,bol_no) VALUES(?,?,?)",
                       [(1, "INV1", "NONE"), (2, "INV2", "MEDU001")])

    @contextmanager
    def source_factory():
        yield source

    monkeypatch.setattr(discovery, "source_connection", source_factory)
    local = sqlite3.connect(":memory:")
    local.row_factory = sqlite3.Row
    ops.init_operations(local)
    admin = ops.create_user(local, None, "Admin", "0770", "admin",
                            "very-strong-admin-password", "admin")
    operator = ops.create_user(local, admin, "Operator", "0771", "operator",
                               "very-strong-operator-password", "operator")
    local.execute("INSERT INTO ops_assignments(shipment_id,user_id,active,assigned_at) VALUES(?,?,1,'now')",
                  (1, operator))
    try:
        assert discovery.scan(local)["silent_baseline"]
        assert local.execute("SELECT COUNT(*) FROM ops_notifications").fetchone()[0] == 0
        source.execute("UPDATE shipment SET bol_no='MEDU002' WHERE id=1")
        source.commit()
        result = discovery.scan(local)
        assert result["notifications_created"] == 2
        assert discovery.scan(local)["notifications_created"] == 0
        assert local.execute("SELECT COUNT(*) FROM ops_notifications").fetchone()[0] == 2
        source.execute("UPDATE shipment SET bol_no='none' WHERE id=1")
        source.commit()
        assert discovery.scan(local)["notifications_created"] == 0
        source.execute("UPDATE shipment SET bol_no='MEDU003' WHERE id=1")
        source.commit()
        assert discovery.scan(local)["notifications_created"] == 2
    finally:
        local.close()
        source.close()
