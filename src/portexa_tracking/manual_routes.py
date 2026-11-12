"""Operator-entered route details stored separately from production PORTEXA.

No SeaRates calls. Never assume manual entries are live AIS or carrier data.
"""
import sqlite3
from datetime import datetime, timezone
from portexa_tracking.database import connect

FIELDS = (
    "loading_port", "current_port", "next_port", "discharge_port",
    "transshipment_port", "vessel_name", "voyage_number",
    "container_number", "etd", "eta", "current_status", "operator_notes"
)
DATE_FIELDS = {"etd", "eta"}


def init_routes(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS portexa_manual_routes (
      source_shipment_id INTEGER PRIMARY KEY,
      loading_port TEXT, current_port TEXT, next_port TEXT,
      discharge_port TEXT, transshipment_port TEXT, vessel_name TEXT,
      voyage_number TEXT, container_number TEXT, etd TEXT, eta TEXT,
      current_status TEXT, operator_notes TEXT,
      updated_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'operator_manual'
    )""")
    conn.commit()


def get_routes(ids):
    if not ids:
        return {}
    with connect() as conn:
        init_routes(conn)
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT * FROM portexa_manual_routes WHERE source_shipment_id IN (" + placeholders + ")",
            [int(i) for i in ids]
        ).fetchall()
        return {row["source_shipment_id"]: dict(row) for row in rows}


def save_route(shipment_id, values, expected_updated_at):
    from portexa_tracking.portexa_bridge import source_connection
    with source_connection() as source:
        exists = source.execute("SELECT 1 FROM shipment WHERE id=?", (int(shipment_id),)).fetchone()
    if exists is None:
        raise LookupError("PORTEXA shipment not found")
    if not isinstance(values, dict) or set(values) != set(FIELDS):
        raise ValueError("All manual route fields are required in request")
    if not (expected_updated_at is None or isinstance(expected_updated_at, str)):
        raise ValueError("Invalid version")
    cleaned = {}
    for key in FIELDS:
        value = values[key]
        if value is not None and not isinstance(value, str):
            raise ValueError("Invalid field: " + key)
        value = value.strip() if value else None
        if value and len(value) > (1500 if key == "operator_notes" else 200):
            raise ValueError("Field too long: " + key)
        if key in DATE_FIELDS and value:
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Invalid date: " + key) from exc
        cleaned[key] = value
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with connect() as conn:
        init_routes(conn)
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT updated_at FROM portexa_manual_routes WHERE source_shipment_id=?", (int(shipment_id),)).fetchone()
        if (old["updated_at"] if old else None) != expected_updated_at:
            raise RuntimeError("Route was changed; reload before saving")
        conn.execute(
            "INSERT INTO portexa_manual_routes(source_shipment_id," + ",".join(FIELDS) + ",updated_at) VALUES (" +
            ",".join("?" for _ in range(len(FIELDS) + 2)) + ") ON CONFLICT(source_shipment_id) DO UPDATE SET " +
            ",".join(f"{k}=excluded.{k}" for k in FIELDS) + ",updated_at=excluded.updated_at",
            (int(shipment_id), *(cleaned[k] for k in FIELDS), now)
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM portexa_manual_routes WHERE source_shipment_id=?", (int(shipment_id),)).fetchone())
