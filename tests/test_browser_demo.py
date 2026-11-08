from portexa_tracking.carriers.browser_demo import BrowserDemoAdapter
from portexa_tracking.carriers.registry import get_adapter, normalize_carrier
from portexa_tracking.database import add_shipment, connect, initialize
from portexa_tracking.scheduler import run_once


def test_browser_demo_registry():
    assert normalize_carrier("BROWSER_DEMO") == "BROWSER_DEMO"
    assert isinstance(get_adapter("BROWSER_DEMO"), BrowserDemoAdapter)


def test_browser_demo_not_found_without_browser():
    r = BrowserDemoAdapter().track("OTHER123", "BL")
    assert r.status == "not_found"


def test_browser_demo_end_to_end(tmp_path):
    with connect(tmp_path / "browser.sqlite3") as conn:
        initialize(conn)
        add_shipment(conn, "BROWSER_DEMO", "BROWSER123", "BL")
        result = run_once(conn, carrier="BROWSER_DEMO")
        assert result["status"] == "found"
        assert result["events_received"] == 3
        assert result["source"] == "synthetic_browser_demo"
        assert conn.execute("SELECT COUNT(*) FROM tracking_events").fetchone()[0] == 3
        assert conn.execute("SELECT eta FROM shipments").fetchone()[0] == "2030-06-21T18:00:00+03:00"
