"""Small, durable SQLite job and event store. One job per carrier+reference+kind."""
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from portexa_tracking.models import TrackingResult, utc_now
from portexa_tracking.normalizer import normalize_key


def connect(path=None):
    path = Path(path or os.environ.get("PORTEXA_TRACKING_DB", "data/tracking.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def initialize(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS shipments (
      id INTEGER PRIMARY KEY,
      carrier TEXT NOT NULL,
      reference TEXT NOT NULL,
      reference_type TEXT NOT NULL,
      latest_status TEXT NOT NULL DEFAULT 'pending',
      eta TEXT,
      source TEXT,
      last_checked_at TEXT,
      checked_error TEXT,
      next_check_at TEXT NOT NULL,
      lease_until TEXT,
      failures INTEGER NOT NULL DEFAULT 0,
      stopped INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(carrier, reference, reference_type)
    );
    CREATE INDEX IF NOT EXISTS idx_shipments_due ON shipments(stopped,next_check_at,lease_until);
    CREATE TABLE IF NOT EXISTS tracking_events (
      id INTEGER PRIMARY KEY,
      shipment_id INTEGER NOT NULL REFERENCES shipments(id),
      event_key TEXT NOT NULL,
      code TEXT NOT NULL,
      occurred_at TEXT NOT NULL,
      location TEXT NOT NULL,
      description TEXT NOT NULL,
      container TEXT NOT NULL,
      first_seen_at TEXT NOT NULL,
      UNIQUE(shipment_id, event_key)
    );
    """)


def add_shipment(conn, carrier, reference, kind):
    carrier, reference, kind = normalize_key(carrier, reference, kind)
    now = utc_now()
    with conn:
        conn.execute("""
            INSERT INTO shipments(carrier,reference,reference_type,next_check_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(carrier,reference,reference_type) DO NOTHING
        """, (carrier, reference, kind, now, now, now))
    return conn.execute("""
        SELECT * FROM shipments WHERE carrier=? AND reference=? AND reference_type=?
    """, (carrier, reference, kind)).fetchone()


def list_shipments(conn):
    return conn.execute("SELECT * FROM shipments ORDER BY id DESC").fetchall()


def claim_due(conn, lease_minutes=5, carrier=None):
    """Claim a single due task atomically; never hold transaction during network access."""
    now = utc_now()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=lease_minutes)).isoformat(timespec="seconds")
    # A write transaction serializes claimers on one SQLite database.
    conn.execute("BEGIN IMMEDIATE")
    try:
        conditions = "stopped=0 AND next_check_at<=? AND (lease_until IS NULL OR lease_until<=?)"
        params = [now, now]
        if carrier:
            conditions += " AND carrier=?"
            params.append(carrier)
        row = conn.execute(
            "SELECT * FROM shipments WHERE " + conditions +
            " ORDER BY next_check_at,id LIMIT 1", params).fetchone()
        if row:
            conn.execute("UPDATE shipments SET lease_until=? WHERE id=?", (expires, row["id"]))
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise


def store_result(conn, shipment_id, result: TrackingResult, next_check_at, failures):
    import hashlib
    now = utc_now()
    with conn:
        # Only verified, complete found results are treated as new tracking information.
        if result.status == "found":
            for event in result.events:
                identity = json.dumps(event.identity(), ensure_ascii=False, separators=(",", ":"))
                key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                conn.execute("""
                    INSERT OR IGNORE INTO tracking_events(
                      shipment_id,event_key,code,occurred_at,location,description,container,first_seen_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                """, (shipment_id, key, event.code, event.occurred_at, event.location,
                      event.description, event.container, now))
            conn.execute("""
                UPDATE shipments SET latest_status=?,eta=?,source=?,last_checked_at=?,
                    checked_error=NULL,next_check_at=?,failures=?,lease_until=NULL,updated_at=?
                WHERE id=?
            """, (result.status, result.eta, result.source, now, next_check_at, failures, now, shipment_id))
        else:
            # Preserve last confirmed ETA and source on failure, but disclose latest lookup failure.
            conn.execute("""
                UPDATE shipments SET latest_status=?,last_checked_at=?,checked_error=?,
                    next_check_at=?,failures=?,lease_until=NULL,updated_at=? WHERE id=?
            """, (result.status, now, result.message[:300], next_check_at, failures, now, shipment_id))


def release_lease(conn, shipment_id):
    with conn:
        conn.execute("UPDATE shipments SET lease_until=NULL WHERE id=?", (shipment_id,))
