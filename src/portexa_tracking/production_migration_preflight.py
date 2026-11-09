"""Read-only production preflight for legacy shipment container migration.

No write gate, no schema changes, no production writes. Explicitly checks
the existing verified backup files, route snapshot, and current source state.
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from portexa_tracking.legacy_container_split_preview import preview
from portexa_tracking.portexa_bridge import database_path, source_connection


def _hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(path, expected_digest):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix != ".sqlite3":
        raise ValueError("Backup must be an existing SQLite file")
    actual = _hash(path)
    if actual.lower() != expected_digest.lower():
        raise RuntimeError("Backup SHA-256 mismatch: " + path.name)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed: " + path.name)
    return {"path": str(path), "sha256_verified": True, "integrity_check": "ok"}


def preflight(shipment_id, source_backup, source_sha256, operations_backup, operations_sha256):
    report = preview(shipment_id)
    if not report["ready_for_manual_review"]:
        raise RuntimeError("Source/legacy records changed or require manual review")
    source = _verify(source_backup, source_sha256)
    operations = _verify(operations_backup, operations_sha256)
    if Path(source["path"]) == Path(operations["path"]):
        raise ValueError("Two distinct backups required")
    if Path(source["path"]) == database_path():
        raise ValueError("Backup must not be the live source database")
    with source_connection() as conn:
        shipment = conn.execute(
            "SELECT containers_count,status,bol_no FROM shipment WHERE id=?",
            (int(shipment_id),)
        ).fetchone()
        if shipment is None:
            raise LookupError("Shipment not found")
        state = dict(shipment)
        if state["containers_count"] != report["declared_containers"] or state["status"] != report["status"]:
            raise RuntimeError("Shipment changed during preflight")
    return {
        "shipment_id": int(shipment_id),
        "source_backup": source,
        "operations_backup": operations,
        "container_numbers": report["container_numbers"],
        "shipment_state": state,
        "legacy_route_updated_at": report["legacy_updated_at"],
        "safe_for_operator_review": True,
        "approved_to_write": False,
        "production_writes_performed": False,
        "next_step": "Explicit approval and a separately reviewed write procedure are required",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("shipment_id", type=int)
    parser.add_argument("--source-backup", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--operations-backup", required=True)
    parser.add_argument("--operations-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(preflight(args.shipment_id, args.source_backup, args.source_sha256,
                               args.operations_backup, args.operations_sha256),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
