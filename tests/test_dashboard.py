"""Smoke tests for the localhost-only dashboard."""
from portexa_tracking.dashboard import HOST, HTML, overview
from portexa_tracking.database import connect, initialize, add_shipment


def test_dashboard_is_loopback_only():
    assert HOST == "127.0.0.1"


def test_dashboard_has_expected_controls():
    assert 'id="add"' in HTML
    assert 'id="run"' in HTML
    assert "synthetic_fixture" in HTML


def test_dashboard_lists_shipments(tmp_path):
    with connect(tmp_path / "dashboard.sqlite3") as conn:
        initialize(conn)
        add_shipment(conn, "MSC", "DEMO5678", "BL")
        data = overview(conn)
        assert len(data) == 1
        assert data[0]["reference"] == "DEMO5678"
        assert data[0]["events"] == []
