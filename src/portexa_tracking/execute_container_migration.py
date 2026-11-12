"""One-shot, guarded production registration of shipment 8's independent containers.

Requires explicit --execute and exact confirmation string. Takes fresh verified
online backups first; never copies legacy movement fields or deletes legacy routes.
No persistent environment changes or service restart.
"""
import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from portexa_tracking.legacy_container_split_preview import SPLIT, preview
from portexa_tracking.portexa_bridge import MOVEMENT_FIELDS, source_connection
from portexa_tracking.verified_db_backup import backup_pair

SHIPMENT_ID = 8
BOL = "PGU600133600"
STATUS = "IN_TRANSIT"
NUMBERS = ("PILU8009484", "FFAU7669829")
LEGACY_UPDATED = "2026-11-08T11:42:30.959721+00:00"
CONFIRM = "MIGRATE-SHIPMENT-8-INDEPENDENT"


def execute(destination, confirmation):
    if confirmation != CONFIRM:
        raise PermissionError("Exact migration confirmation is required")
    if os.environ.get("PORTEXA_SOURCE_WRITE_ENABLED") != "YES":
        raise PermissionError("Production write gate is disabled")
    # Read-only check first, before producing fresh backups.
    report = preview(SHIPMENT_ID)
    if not report["ready_for_manual_review"] or tuple(report["container_numbers"]) != NUMBERS:
        raise RuntimeError("Container preflight mismatch")
    if report["legacy_updated_at"] != LEGACY_UPDATED or report["status"] != STATUS:
        raise RuntimeError("Legacy route or shipment state changed")
    backups = backup_pair(destination)
    if len(backups["backups"]) != 2 or any(
        entry["integrity_check"] != "ok" for entry in backups["backups"]
    ):
        raise RuntimeError("Verified backups required")
    # Pin the route snapshot by checking it again immediately before the source write.
    # Legacy route is never updated or removed by this procedure.
    tracking_path = Path(os.environ.get("PORTEXA_TRACKING_DB", "data/tracking.sqlite3")).expanduser().resolve(strict=True)
    with sqlite3.connect(tracking_path.as_uri() + "?mode=ro", uri=True) as local:
        row = local.execute(
            "SELECT container_number,updated_at FROM portexa_manual_routes WHERE source_shipment_id=?",
            (SHIPMENT_ID,)
        ).fetchone()
        if not row:
            raise RuntimeError("Legacy route missing")
        raw, updated = row
        numbers = tuple(part.strip().upper() for part in SPLIT.split(raw or "") if part.strip())
        if updated != LEGACY_UPDATED or numbers != NUMBERS:
            raise RuntimeError("Legacy route changed after backup")
        with source_connection(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            shipment = conn.execute(
                "SELECT bol_no,status,containers_count FROM shipment WHERE id=?",
                (SHIPMENT_ID,)
            ).fetchone()
            if not shipment or (shipment["bol_no"], shipment["status"], shipment["containers_count"]) != (BOL, STATUS, 2):
                raise RuntimeError("Shipment fields changed; refusing migration")
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops_container_movements'"
            ).fetchone()
            if exists and conn.execute(
                "SELECT 1 FROM ops_container_movements WHERE shipment_id=? LIMIT 1", (SHIPMENT_ID,)
            ).fetchone():
                raise RuntimeError("Container records already exist; refusing migration")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ops_container_movements ("
                "id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL, "
                "container_number TEXT NOT NULL, " +
                ",".join(k + " TEXT" for k in MOVEMENT_FIELDS) +
                ", UNIQUE(shipment_id,container_number))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ops_container_movement_history ("
                "id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL, "
                "container_number TEXT NOT NULL, action TEXT NOT NULL, "
                "previous_state TEXT, new_state TEXT, actor_id INTEGER, "
                "recorded_at TEXT NOT NULL)"
            )
            timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            for number in NUMBERS:
                conn.execute(
                    "INSERT INTO ops_container_movements(shipment_id,container_number) VALUES(?,?)",
                    (SHIPMENT_ID, number)
                )
                snapshot = {"container_number": number, **{key: None for key in MOVEMENT_FIELDS}}
                conn.execute(
                    "INSERT INTO ops_container_movement_history("
                    "shipment_id,container_number,action,previous_state,new_state,actor_id,recorded_at"
                    ") VALUES(?,?,?,?,?,?,?)",
                    (SHIPMENT_ID, number, "created", None,
                     json.dumps(snapshot, ensure_ascii=False), None, timestamp)
                )
            saved = conn.execute(
                "SELECT container_number FROM ops_container_movements WHERE shipment_id=? ORDER BY container_number",
                (SHIPMENT_ID,)
            ).fetchall()
            if [r[0] for r in saved] != sorted(NUMBERS):
                raise AssertionError("Post-insert validation failed")
            conn.commit()
    return {
        "shipment_id": SHIPMENT_ID,
        "registered_containers": list(NUMBERS),
        "movement_fields_copied": False,
        "legacy_route_modified": False,
        "backups": backups["backups"],
        "production_write_committed": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--backup-directory", required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.backup_directory, args.confirm), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
