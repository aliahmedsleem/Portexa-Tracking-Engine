"""Safe bridge to PORTEXA's existing SQLite shipment table.

The production database is never modified unless explicitly enabled.
Read-only mode uses SQLite URI mode=ro. Writes use a single guarded transaction
with an optimistic concurrency check, and only an approved subset of fields.
"""
import os
import json
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


def normalize_shipment_date(value):
    """Accept ISO dates or day-first dates from the staff UI; store ISO only."""
    from datetime import datetime, date
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid date value")
    value = value.strip()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%d-%m-%Y").date().isoformat()
    except ValueError as exc:
        raise ValueError("Invalid shipment date (use YYYY-MM-DD or DD-MM-YYYY)") from exc


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
        if key in ("ship_date", "arrival_date"):
            value = normalize_shipment_date(value)
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


# Unified shipment and container movements are stored in the PORTEXA source DB.
# This path is explicitly gated and is never used by read-only tracking jobs.
SHIPMENT_STATUSES = ("NEW", "IN_TRANSIT", "RECEIVED")
MOVEMENT_FIELDS = ("loading_port", "current_port", "next_port", "discharge_port",
                   "transshipment_port", "vessel_name", "voyage_number", "etd", "eta",
                   "current_status", "operator_notes")


def read_container_movements(shipment_id):
    """Read container movements without creating or modifying production tables."""
    with source_connection() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops_container_movements'").fetchone()
        if not exists:
            return []
        return [dict(r) for r in conn.execute(
            "SELECT container_number," + ",".join(MOVEMENT_FIELDS) +
            " FROM ops_container_movements WHERE shipment_id=? ORDER BY id", (int(shipment_id),)
        )]


def read_container_history(shipment_id, limit=100):
    """Read movement events without changing source tables."""
    limit = min(max(int(limit), 1), 500)
    with source_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops_container_movement_history'"
        ).fetchone()
        if not exists:
            return []
        return [dict(row) for row in conn.execute(
            "SELECT * FROM ops_container_movement_history "
            "WHERE shipment_id=? ORDER BY id DESC LIMIT ?",
            (int(shipment_id), limit),
        )]


def save_shipment_bundle(shipment_id, values, expected, containers, expected_containers, actor_id=None):
    """One SQLite transaction for shipment fields and all container movements."""
    from datetime import datetime
    if not isinstance(values, dict) or set(values) != EDITABLE:
        raise ValueError("Incomplete shipment fields")
    if not isinstance(expected, dict) or set(expected) != EDITABLE:
        raise ValueError("Missing shipment version")
    if not isinstance(containers, list) or len(containers) > 100:
        raise ValueError("Invalid container count")
    if not isinstance(expected_containers, list):
        raise ValueError("Missing container version")
    validated = {}
    for key, value in values.items():
        if value is not None and not isinstance(value, str):
            raise ValueError("Invalid shipment field")
        value = value.strip() or None if value else None
        if value and len(value) > (2000 if key == "shipping_details" else 120 if key == "bol_no" else 300):
            raise ValueError("Field too long")
        if key == "bol_no" and value and not re.fullmatch(r"[\w./ -]{1,120}", value, re.UNICODE):
            raise ValueError("Invalid bill of lading")
        if key in ("ship_date", "arrival_date"):
            value = normalize_shipment_date(value)
        validated[key] = value
    cleaned = []
    seen = set()
    for item in containers:
        if not isinstance(item, dict) or set(item) != {"container_number", *MOVEMENT_FIELDS}:
            raise ValueError("Invalid container details")
        number = item["container_number"]
        if not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9-]{4,30}", number.strip()):
            raise ValueError("Invalid container number")
        number = number.strip().upper()
        if number in seen:
            raise ValueError("Duplicate container")
        seen.add(number)
        row = {"container_number": number}
        for key in MOVEMENT_FIELDS:
            value = item[key]
            if not isinstance(value, str):
                raise ValueError("Invalid movement value")
            value = value.strip()
            if len(value) > (1500 if key == "operator_notes" else 200):
                raise ValueError("Movement field too long")
            if key in ("etd", "eta") and value:
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    value = parsed.date().isoformat() if "T" not in value and " " not in value else parsed.isoformat()
                except ValueError:
                    value = normalize_shipment_date(value)
            row[key] = value or None
        cleaned.append(row)
    with source_connection(write=True) as conn:
        check_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT " + ",".join(EDITABLE) + " FROM shipment WHERE id=?",
                               (int(shipment_id),)).fetchone()
        if current is None:
            raise LookupError("Shipment not found")
        if any(current[k] != expected[k] for k in EDITABLE):
            raise RuntimeError("Shipment changed; reload before saving")
        has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops_container_movements'").fetchone()
        existing = [dict(r) for r in conn.execute(
            "SELECT container_number," + ",".join(MOVEMENT_FIELDS) +
            " FROM ops_container_movements WHERE shipment_id=? ORDER BY id", (int(shipment_id),)
        )] if has_table else []
        if existing != expected_containers:
            raise RuntimeError("Container movements changed; reload before saving")
        if existing and not cleaned:
            raise ValueError("Removing every container is blocked; retain at least one container")
        declared_count = conn.execute("SELECT containers_count FROM shipment WHERE id=?", (int(shipment_id),)).fetchone()[0]
        if not existing and declared_count and int(declared_count) > 0 and len(cleaned) < int(declared_count):
            raise ValueError("The shipment already declares more containers than provided; add all containers before saving")
        changed = any(validated[k] != current[k] for k in EDITABLE) or cleaned != existing
        if not changed:
            return False
        conn.execute("CREATE TABLE IF NOT EXISTS ops_container_movements ("
                     "id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL, "
                     "container_number TEXT NOT NULL, " +
                     ",".join(k + " TEXT" for k in MOVEMENT_FIELDS) +
                     ", UNIQUE(shipment_id,container_number))")
        cols = ("container_number",) + MOVEMENT_FIELDS
        # Keep the source shipment container count aligned with saved records.
        count_update = ",containers_count=?" if cleaned else ""
        params = [validated[k] for k in EDITABLE]
        if cleaned:
            params.append(len(cleaned))
        conn.execute(
            "UPDATE shipment SET " + ",".join(k + "=?" for k in EDITABLE)
            + count_update + " WHERE id=?",
            (*params, int(shipment_id)),
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ops_container_movement_history ("
            "id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL, "
            "container_number TEXT NOT NULL, action TEXT NOT NULL, "
            "previous_state TEXT, new_state TEXT, actor_id INTEGER, "
            "recorded_at TEXT NOT NULL)"
        )
        before = {row["container_number"]: row for row in existing}
        after = {row["container_number"]: row for row in cleaned}
        from datetime import timezone
        timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        for number in sorted(set(before) | set(after)):
            old, new = before.get(number), after.get(number)
            if old == new:
                continue
            action = "created" if old is None else "removed" if new is None else "updated"
            conn.execute(
                "INSERT INTO ops_container_movement_history("
                "shipment_id,container_number,action,previous_state,new_state,actor_id,recorded_at"
                ") VALUES(?,?,?,?,?,?,?)",
                (int(shipment_id), number, action,
                 json.dumps(old, ensure_ascii=False) if old is not None else None,
                 json.dumps(new, ensure_ascii=False) if new is not None else None,
                 int(actor_id) if actor_id is not None else None, timestamp),
            )
        conn.execute("DELETE FROM ops_container_movements WHERE shipment_id=?", (int(shipment_id),))
        for item in cleaned:
            conn.execute("INSERT INTO ops_container_movements(shipment_id," + ",".join(cols) +
                         ") VALUES(" + ",".join("?" for _ in range(len(cols) + 1)) + ")",
                         (int(shipment_id), *[item[k] for k in cols]))
        conn.commit()
        return True
