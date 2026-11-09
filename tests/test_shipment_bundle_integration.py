"""SQLite integration tests for the unified source shipment write gate and rollback."""
import sqlite3
import pytest
from portexa_tracking import portexa_bridge as bridge


@pytest.fixture
def source_db(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    conn = sqlite3.connect(path)
    cols = ", ".join(k + " TEXT" for k in bridge.DISPLAY if k != "id")
    conn.execute("CREATE TABLE shipment (id INTEGER PRIMARY KEY, " + cols +
                 ", containers_count INTEGER)")
    conn.execute("INSERT INTO shipment (id,bol_no,containers_count,status) VALUES (1,'BOL1',2,'NEW')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("PORTEXA_SOURCE_DB", str(path))
    monkeypatch.setenv("PORTEXA_SOURCE_WRITE_ENABLED", "YES")
    return path


def payload():
    return {key: ("BOL2" if key == "bol_no" else "") for key in bridge.EDITABLE}


def original():
    return {key: ("BOL1" if key == "bol_no" else None) for key in bridge.EDITABLE}


def container(number):
    return {"container_number": number, **{k: "" for k in bridge.MOVEMENT_FIELDS}}


def test_reject_incomplete_container_registration_without_modifying_shipment(source_db):
    with pytest.raises(ValueError, match="more containers"):
        bridge.save_shipment_bundle(1, payload(), original(), [container("ABCD1234567")], [])
    with sqlite3.connect(source_db) as conn:
        assert conn.execute("SELECT bol_no,containers_count FROM shipment").fetchone() == ("BOL1", 2)
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='ops_container_movements'").fetchone()[0] == 0


def test_save_multiple_containers_and_reject_stale_snapshot(source_db):
    items = [container("ABCD1234567"), container("EFGH1234567")]
    assert bridge.save_shipment_bundle(1, payload(), original(), items, []) is True
    with sqlite3.connect(source_db) as conn:
        assert conn.execute("SELECT bol_no,containers_count FROM shipment").fetchone() == ("BOL2", 2)
    assert len(bridge.read_container_movements(1)) == 2
    with pytest.raises(RuntimeError, match="changed"):
        bridge.save_shipment_bundle(1, payload(), original(), items, [])


def test_write_gate_blocks_all_changes(source_db, monkeypatch):
    monkeypatch.delenv("PORTEXA_SOURCE_WRITE_ENABLED")
    with pytest.raises(PermissionError):
        bridge.save_shipment_bundle(1, payload(), original(),
                                    [container("ABCD1234567"), container("EFGH1234567")], [])
