"""Serialize follow-up edits and completion using the operations SQLite DB."""

from contextlib import contextmanager

from portexa_tracking import operations_followup as followup


@contextmanager
def editable_transaction(conn, user_id, shipment_id):
    """Keep the operations write lock until the source update is finished."""
    if conn.in_transaction:
        raise RuntimeError(
            "Cannot start follow-up lock inside an existing transaction"
        )

    conn.execute("BEGIN IMMEDIATE")

    try:
        followup.require_editable(conn, user_id, shipment_id)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


@contextmanager
def completion_transaction(conn, user_id, shipment_id):
    """Serialize task completion against protected shipment writes."""
    if conn.in_transaction:
        raise RuntimeError(
            "Cannot complete follow-up inside an existing transaction"
        )

    conn.execute("BEGIN IMMEDIATE")

    try:
        followup.require_editable(conn, user_id, shipment_id)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
