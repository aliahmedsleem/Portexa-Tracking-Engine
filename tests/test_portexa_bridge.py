import sqlite3
import pytest
from portexa_tracking.portexa_bridge import (
    list_source_shipments, update_source_shipment, get_source_shipment
)


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / "portexa.db"
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE shipment (
            id INTEGER PRIMARY KEY, owner_id INTEGER, invoice_no TEXT,
            bol_no TEXT, msc_tracking TEXT, ship_date TEXT, arrival_date TEXT,
            shipping_details TEXT, status TEXT, factory_id INTEGER, country_id INTEGER
        )""")
        c.execute("""INSERT INTO shipment VALUES
            (1, 7, 'INV-1', NULL, NULL, NULL, NULL, NULL, 'NEW', 2, 3),
            (2, 8, 'INV-2', 'BL100', NULL, NULL, NULL, NULL, 'NEW', 2, 3)""")
    monkeypatch.setenv("PORTEXA_SOURCE_DB", str(path))
    monkeypatch.delenv("PORTEXA_SOURCE_WRITE_ENABLED", raising=False)
    return path


def test_read_only_and_missing(source):
    rows = list_source_shipments(missing_only=True)
    assert len(rows) == 1
    assert rows[0]["id"] == 1


def test_writes_off_by_default(source):
    with pytest.raises(PermissionError):
        update_source_shipment(1, {"bol_no": "BL123"}, {"bol_no": None})
    assert get_source_shipment(1)["bol_no"] is None


def test_guarded_write_and_stale_form(source, monkeypatch):
    monkeypatch.setenv("PORTEXA_SOURCE_WRITE_ENABLED", "YES")
    result = update_source_shipment(1, {"bol_no": "BL123"}, {"bol_no": None})
    assert result["bol_no"] == "BL123"
    with pytest.raises(RuntimeError, match="changed"):
        update_source_shipment(1, {"bol_no": "BL999"}, {"bol_no": None})


def test_reject_unapproved_fields(source, monkeypatch):
    monkeypatch.setenv("PORTEXA_SOURCE_WRITE_ENABLED", "YES")
    with pytest.raises(ValueError):
        update_source_shipment(1, {"owner_id": "99"}, {"owner_id": 7})
