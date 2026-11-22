"""Read-only PORTEXA source synchronization for Operations.

Source: PORTEXA shipment table, opened read-only.
Target: a separate SQLite Operations database.

This module never modifies the source database.
"""

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

FIELDS = (
    "id",
    "owner_id",
    "invoice_no",
    "bol_no",
    "msc_tracking",
    "ship_date",
    "arrival_date",
    "shipping_details",
    "status",
    "factory_id",
    "country_id",
    "created_at",
)

STOP = False


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_source(path):
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Source database must be a file")

    conn = sqlite3.connect(
        resolved.as_uri() + "?mode=ro",
        uri=True,
        timeout=10,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def connect_target(path):
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def initialize(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ops_sync_shipments (
            shipment_id INTEGER PRIMARY KEY,
            owner_id INTEGER,
            invoice_no TEXT,
            bol_no TEXT,
            msc_tracking TEXT,
            ship_date TEXT,
            arrival_date TEXT,
            shipping_details TEXT,
            status TEXT,
            factory_id INTEGER,
            country_id INTEGER,
            created_at TEXT,
            fingerprint TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_changed_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            source_present INTEGER NOT NULL DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_ops_sync_bol
        ON ops_sync_shipments(bol_no);

        CREATE INDEX IF NOT EXISTS idx_ops_sync_status
        ON ops_sync_shipments(status);

        CREATE TABLE IF NOT EXISTS ops_sync_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            last_started_at TEXT,
            last_success_at TEXT,
            last_error TEXT,
            total_source INTEGER NOT NULL DEFAULT 0,
            inserted INTEGER NOT NULL DEFAULT 0,
            updated INTEGER NOT NULL DEFAULT 0,
            missing INTEGER NOT NULL DEFAULT 0
        );

        INSERT OR IGNORE INTO ops_sync_state(id)
        VALUES(1);
    """)


def fingerprint(row):
    payload = {field: row[field] for field in FIELDS}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_source(path):
    with connect_source(path) as conn:
        available = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(shipment)")
        }

        missing = set(FIELDS) - available
        if missing:
            raise RuntimeError(
                "Missing source columns: " + ", ".join(sorted(missing))
            )

        columns = ", ".join(FIELDS)

        # One consistent source snapshot per synchronization.
        conn.execute("BEGIN")
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT {columns} FROM shipment ORDER BY id"
            )
        ]
        conn.commit()
        return rows


def synchronize(source_path, target_path):
    started = utc_now()

    # Read the complete source before changing the target.
    rows = read_source(source_path)

    with connect_target(target_path) as conn:
        initialize(conn)

        inserted = 0
        updated = 0
        missing = 0

        # Serialize concurrent writers to the target.
        conn.execute("BEGIN IMMEDIATE")

        try:
            previous = {
                int(row["shipment_id"]): row
                for row in conn.execute(
                    "SELECT shipment_id, fingerprint, source_present "
                    "FROM ops_sync_shipments"
                )
            }

            seen = set()

            for row in rows:
                shipment_id = int(row["id"])
                seen.add(shipment_id)

                digest = fingerprint(row)
                old = previous.get(shipment_id)

                values = (
                    row["owner_id"],
                    row["invoice_no"],
                    row["bol_no"],
                    row["msc_tracking"],
                    row["ship_date"],
                    row["arrival_date"],
                    row["shipping_details"],
                    row["status"],
                    row["factory_id"],
                    row["country_id"],
                    row["created_at"],
                )

                if old is None:
                    conn.execute("""
                        INSERT INTO ops_sync_shipments (
                            shipment_id, owner_id, invoice_no, bol_no,
                            msc_tracking, ship_date, arrival_date,
                            shipping_details, status, factory_id,
                            country_id, created_at, fingerprint,
                            first_seen_at, last_changed_at,
                            last_seen_at, source_present
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, 1
                        )
                    """, (
                        shipment_id, *values,
                        digest, started, started, started,
                    ))
                    inserted += 1

                elif (
                    old["fingerprint"] != digest
                    or not old["source_present"]
                ):
                    conn.execute("""
                        UPDATE ops_sync_shipments
                        SET owner_id=?, invoice_no=?, bol_no=?,
                            msc_tracking=?, ship_date=?, arrival_date=?,
                            shipping_details=?, status=?, factory_id=?,
                            country_id=?, created_at=?, fingerprint=?,
                            last_changed_at=?, last_seen_at=?,
                            source_present=1
                        WHERE shipment_id=?
                    """, (
                        *values, digest, started, started, shipment_id,
                    ))
                    updated += 1

            # Keep history; do not delete local records or assignments.
            for shipment_id, old in previous.items():
                if shipment_id not in seen and old["source_present"]:
                    conn.execute("""
                        UPDATE ops_sync_shipments
                        SET source_present=0, last_changed_at=?
                        WHERE shipment_id=?
                    """, (started, shipment_id))
                    missing += 1

            conn.execute("""
                UPDATE ops_sync_state
                SET last_started_at=?,
                    last_success_at=?,
                    last_error=NULL,
                    total_source=?,
                    inserted=?,
                    updated=?,
                    missing=?
                WHERE id=1
            """, (
                started, utc_now(), len(rows),
                inserted, updated, missing,
            ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise

    return {
        "source_total": len(rows),
        "inserted": inserted,
        "updated": updated,
        "missing": missing,
        "timestamp": started,
    }


def stop_handler(signum, frame):
    global STOP
    STOP = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.interval < 1:
        parser.error("Interval must be at least 1 second")

    if Path(args.source).resolve() == Path(args.target).resolve():
        parser.error("Source and target must be different databases")

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    while not STOP:
        started = time.monotonic()

        try:
            result = synchronize(args.source, args.target)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(
                json.dumps({
                    "error": str(exc),
                    "timestamp": utc_now(),
                }),
                flush=True,
            )
            if args.once:
                raise

        if args.once:
            break

        remaining = max(0, args.interval - (time.monotonic() - started))
        if remaining:
            # Short waits allow graceful shutdown.
            end = time.monotonic() + remaining
            while not STOP and time.monotonic() < end:
                time.sleep(min(0.2, end - time.monotonic()))


if __name__ == "__main__":
    main()
