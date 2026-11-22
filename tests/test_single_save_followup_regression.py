"""Regression tests for single-shipment follow-up accounting."""

import sqlite3

from portexa_tracking import operations_shipment_edit as edit
from portexa_tracking import operations_followup as followup
from test_operations_shipment_edit import payload, expected, setup as shipment_setup


def followup_state(conn):
    return dict(conn.execute("""
        SELECT update_count,last_updated_at,status
        FROM ops_followups
        WHERE shipment_id=1
    """).fetchone())


def test_single_save_records_one_followup_update(shipment_setup):
    conn, task, worker, other, source = shipment_setup

    before = followup_state(conn)

    result = edit.save_shipment(
        conn, worker, task,
        payload("TEST-002"),
        expected(),
    )

    after = followup_state(conn)

    assert result["changed"] is True
    assert after["update_count"] == before["update_count"] + 1
    assert after["last_updated_at"] >= before["last_updated_at"]


def test_single_save_noop_does_not_update_followup(shipment_setup):
    conn, task, worker, other, source = shipment_setup

    before = followup_state(conn)

    result = edit.save_shipment(
        conn, worker, task,
        payload("TEST-001"),
        expected(),
    )

    after = followup_state(conn)

    assert result["changed"] is False
    assert after["update_count"] == before["update_count"]
    assert after["last_updated_at"] == before["last_updated_at"]
