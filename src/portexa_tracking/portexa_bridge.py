"""Safe bridge to PORTEXA's existing SQLite shipment table.

The production database is never modified unless explicitly enabled.
Read-only mode uses SQLite URI mode=ro. Writes use a single guarded transaction
with an optimistic concurrency check, and only an approved subset of fields.
"""
import os
import re
import sqlite3
from pathlib import Path

EDITABLE = {"bol_no", "msc_tracking", "ship_date", "arrival_date", "shipping_details"}
DISPLAY = ("id", "owner_id", "invoice_no", "bol_no", "msc_tracking",
           "ship_date", "arrival_date", "shipping_details", "status", "factory_id", "country_id")


def database_path():
    raw = os.environ.get("PORTEXA_SOURCE_DB", "").strip()
    if not raw:
        raise RuntimeError("PORTEXA_SOURCE_DB is not configured")
    path = Path(raw).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in (".db", ".sqlite", ".sqlite3"):
        raise ValueError("Expected an existing SQLite database file")
    return path


def source_connection(write=False):
    path = database_path()
    if write and os.environ.get("PORTEXA_SOURCE_WRITE_ENABLED") != "YES":
        raise PermissionError("Source writes disabled. Set PORTEXA_SOURCE_WRITE_ENABLED=YES explicitly")
    if write:
        conn = sqlite3.connect(str(path), timeout=10)
    else:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def check_schema(conn):
    names = {row["name"] for row in conn.execute("PRAGMA table_info(shipment)")}
    missing = set(DISPLAY) - names
    if missing:
        raise RuntimeError("PORTEXA shipment schema mismatch: " + ", ".join(sorted(missing)))


def list_source_shipments(limit=200, missing_only=False):
    limit = min(max(int(limit), 1), 500)
    with source_connection() as conn:
        check_schema(conn)
        columns = ", ".join(DISPLAY)
        where = "WHERE bol_no IS NULL OR trim(bol_no) = ''" if missing_only else ""
        rows = conn.execute(
            f"SELECT {columns} FROM shipment {where} ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_source_shipment(shipment_id):
    with source_connection() as conn:
        check_schema(conn)
        row = conn.execute(
            "SELECT " + ", ".join(DISPLAY) + " FROM shipment WHERE id=?",
            (int(shipment_id),),
        ).fetchone()
        return dict(row) if row else None


def update_source_shipment(shipment_id, values, expected):
    if not isinstance(values, dict) or not values or set(values) - EDITABLE:
        raise ValueError("Only approved shipment fields may be updated")
    if not isinstance(expected, dict) or set(expected) != set(values):
        raise ValueError("Expected original values required for every edited field")
    updates = {}
    for key, value in values.items():
        if value is not None and not isinstance(value, str):
            raise ValueError("Field values must be strings or null")
        if value is not None and len(value) > (2000 if key == "shipping_details" else 300):
            raise ValueError("Field value too long: " + key)
        if key in ("ship_date", "arrival_date") and value:
            from datetime import date
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Invalid ISO date for " + key) from exc
        if key == "bol_no" and value and not re.fullmatch(r"[\w./ -]{1,120}", value, re.UNICODE):
            raise ValueError("Invalid bill of lading reference")
        updates[key] = value or None
    with source_connection(write=True) as conn:
        check_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT " + ", ".join(DISPLAY) + " FROM shipment WHERE id=?", (int(shipment_id),)
        ).fetchone()
        if row is None:
            raise LookupError("Shipment not found")
        for key in updates:
            if row[key] != expected[key]:
                raise RuntimeError("Shipment changed since form was opened; reload before saving")
        sql = "UPDATE shipment SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?"
        conn.execute(sql, (*updates.values(), int(shipment_id)))
        conn.commit()
        return get_source_shipment(shipment_id)
