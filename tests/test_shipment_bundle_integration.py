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


def test_history_records_only_actual_container_changes(source_db):
    items = [container("ABCD1234567"), container("EFGH1234567")]
    assert bridge.read_container_history(1) == []
    bridge.save_shipment_bundle(1, payload(), original(), items, [], actor_id=42)
    first = bridge.read_container_history(1)
    assert len(first) == 2
    assert {event["action"] for event in first} == {"created"}
    assert {event["actor_id"] for event in first} == {42}
    current = {key: ("BOL2" if key == "bol_no" else None) for key in bridge.EDITABLE}
    snapshot = bridge.read_container_movements(1)
    updated = [dict(item) for item in snapshot]
    updated[0]["current_port"] = "Basra"
    bridge.save_shipment_bundle(1, payload(), current, [
        {k: (v or "") for k, v in item.items()} for item in updated
    ], snapshot, actor_id=42)
    history = bridge.read_container_history(1)
    assert len(history) == 3
    assert history[0]["action"] == "updated"
    assert history[0]["container_number"] == "ABCD1234567"
    import json
    assert json.loads(history[0]["new_state"])["current_port"] == "Basra"
    assert json.loads(history[0]["previous_state"])["current_port"] is None
