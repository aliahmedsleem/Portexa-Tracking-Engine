"""Integration checks for one-shot independent container migration on isolated DBs."""
import sqlite3

import pytest
from portexa_tracking import execute_container_migration as migration


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    tracking = tmp_path / "tracking.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE shipment (id INTEGER PRIMARY KEY,bol_no TEXT,status TEXT,containers_count INTEGER)"
        )
        conn.execute(
            "INSERT INTO shipment VALUES(?,?,?,?)",
            (8, migration.BOL, migration.STATUS, 2)
        )
    with sqlite3.connect(tracking) as conn:
        conn.execute(
            "CREATE TABLE portexa_manual_routes (source_shipment_id INTEGER PRIMARY KEY,"
            "container_number TEXT,updated_at TEXT,loading_port TEXT)"
        )
        conn.execute(
            "INSERT INTO portexa_manual_routes VALUES(?,?,?,?)",
            (8, "PILU8009484 , FFAU7669829", migration.LEGACY_UPDATED, "Basra")
        )
    monkeypatch.setenv("PORTEXA_SOURCE_DB", str(source))
    monkeypatch.setenv("PORTEXA_TRACKING_DB", str(tracking))
    monkeypatch.setenv("PORTEXA_SOURCE_WRITE_ENABLED", "YES")
    return source, tracking, tmp_path / "backups"


def test_migration_requires_exact_confirmation(isolated):
    source, tracking, backups = isolated
    with pytest.raises(PermissionError):
        migration.execute(backups, "wrong")
    with sqlite3.connect(source) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='ops_container_movements'"
        ).fetchone()[0] == 0


def test_migration_preserves_legacy_and_rejects_repeat(isolated):
    source, tracking, backups = isolated
    result = migration.execute(backups, migration.CONFIRM)
    assert result["production_write_committed"] is True
    assert len(result["backups"]) == 2
    with sqlite3.connect(source) as conn:
        rows = conn.execute(
            "SELECT container_number,current_port FROM ops_container_movements ORDER BY container_number"
        ).fetchall()
        assert rows == [("FFAU7669829", None), ("PILU8009484", None)]
        assert conn.execute("SELECT COUNT(*) FROM ops_container_movement_history").fetchone()[0] == 2
        assert conn.execute("SELECT bol_no,status,containers_count FROM shipment").fetchone() == (
            migration.BOL, migration.STATUS, 2
        )
    with sqlite3.connect(tracking) as conn:
        assert conn.execute(
            "SELECT container_number,loading_port FROM portexa_manual_routes"
        ).fetchone() == ("PILU8009484 , FFAU7669829", "Basra")
    with pytest.raises(RuntimeError, match="preflight|already exist"):
        migration.execute(backups, migration.CONFIRM)


def test_migration_blocks_changed_shipment(isolated):
    source, tracking, backups = isolated
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE shipment SET bol_no='DIFFERENT' WHERE id=8")
    # The initial preview does not check BOL; the locked transaction must.
    with pytest.raises(RuntimeError, match="Shipment fields changed"):
        migration.execute(backups, migration.CONFIRM)
    with sqlite3.connect(source) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='ops_container_movements'"
        ).fetchone()[0] == 0
