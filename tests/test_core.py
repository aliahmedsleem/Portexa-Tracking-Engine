import json
import sqlite3
from portexa_tracking.database import initialize, add_shipment, claim_due, list_shipments, store_result
from portexa_tracking.normalizer import normalize_key
from portexa_tracking.models import TrackingEvent, TrackingResult
from portexa_tracking.scheduler import next_delay_hours, run_once


def test_reference_normalization():
    assert normalize_key("CMA CGM", " demo987654 ", "bl") == ("CMA_CGM", "DEMO987654", "BL")


def test_invalid_reference():
    import pytest
    with pytest.raises(ValueError):
        normalize_key("MSC", "../bad", "BL")


def test_dedup_and_claim(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    initialize(conn)
    a = add_shipment(conn, "CMA_CGM", "DEMO987654", "BL")
    b = add_shipment(conn, "CMA CGM", "demo987654", "BL")
    assert a["id"] == b["id"]
    assert len(list_shipments(conn)) == 1
    assert claim_due(conn)["id"] == a["id"]
    assert claim_due(conn) is None
    conn.close()


def test_event_dedup(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    initialize(conn)
    row = add_shipment(conn, "MSC", "TEST1234", "BL")
    event = TrackingEvent("LOAD", "2030-03-01T12:00:00Z", "TEST PORT")
    result = TrackingResult("MSC", "TEST1234", "BL", "found",
                            source="synthetic_fixture", events=(event, event))
    store_result(conn, row["id"], result, "2030-03-02T00:00:00Z", 0)
    assert conn.execute("SELECT COUNT(*) FROM tracking_events").fetchone()[0] == 1
    conn.close()


def test_failure_does_not_erase_last_known_eta(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    initialize(conn)
    row = add_shipment(conn, "MSC", "TEST1234", "BL")
    store_result(conn, row["id"],
                 TrackingResult("MSC", "TEST1234", "BL", "found",
                                eta="2030-01-01T00:00:00Z", source="synthetic_fixture"),
                 "2030-01-01T00:00:00Z", 0)
    store_result(conn, row["id"], TrackingResult("MSC", "TEST1234", "BL",
                 "temporary_error", message="down"), "2030-01-02T00:00:00Z", 1)
    saved = conn.execute("SELECT * FROM shipments WHERE id=?", (row["id"],)).fetchone()
    assert saved["eta"] == "2030-01-01T00:00:00Z"
    assert saved["latest_status"] == "temporary_error"
    conn.close()


def test_backoff():
    assert next_delay_hours("temporary_error", 1) == 12
    assert next_delay_hours("found", 0) == 8


def test_pending_connector_is_transparently_unavailable(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    initialize(conn)
    add_shipment(conn, "CMA_CGM", "DEMO987654", "BL")
    result = run_once(conn)
    assert result["status"] == "unavailable"
    assert result["events_received"] == 0
    conn.close()
