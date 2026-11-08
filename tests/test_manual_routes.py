from portexa_tracking.manual_routes import FIELDS, get_routes, save_route
import sqlite3
import pytest


def test_manual_routes_isolated(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE shipment (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO shipment VALUES (1)")
    monkeypatch.setenv("PORTEXA_SOURCE_DB", str(source))
    monkeypatch.setenv("PORTEXA_TRACKING_DB", str(tmp_path / "engine.db"))
    values = {k: None for k in FIELDS}
    values.update(loading_port="Shanghai", current_port="Jeddah", next_port="Aqaba", discharge_port="Umm Qasr")
    saved = save_route(1, values, None)
    assert saved["loading_port"] == "Shanghai"
    assert get_routes([1])[1]["next_port"] == "Aqaba"
    with pytest.raises(RuntimeError):
        save_route(1, values, None)
    assert save_route(1, values, saved["updated_at"])["source"] == "operator_manual"
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shipment").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='portexa_manual_routes'").fetchone() is None


def test_unknown_shipment_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE shipment (id INTEGER PRIMARY KEY)")
    monkeypatch.setenv("PORTEXA_SOURCE_DB", str(source))
    monkeypatch.setenv("PORTEXA_TRACKING_DB", str(tmp_path / "engine.db"))
    with pytest.raises(LookupError):
        save_route(5, {k: None for k in FIELDS}, None)
