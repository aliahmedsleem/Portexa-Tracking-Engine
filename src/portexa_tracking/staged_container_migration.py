"""Safely rehearse legacy container registration on an isolated SQLite copy.

This command NEVER writes to the PORTEXA production source or the legacy route DB.
It creates a temporary source DB via SQLite backup, validates the legacy split,
and uses the production bundle writer on the disposable copy only.
"""
import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from portexa_tracking.legacy_container_split_preview import preview
from portexa_tracking.portexa_bridge import (
    EDITABLE, MOVEMENT_FIELDS, database_path, read_container_history,
    read_container_movements, save_shipment_bundle, source_connection,
)


def rehearse(shipment_id):
    sid = int(shipment_id)
    report = preview(sid)
    if not report["ready_for_manual_review"]:
        raise RuntimeError("Legacy route requires manual review; no staging performed")
    if not report["container_numbers"]:
        raise ValueError("No container numbers to migrate")
    original_path = database_path()
    with source_connection() as original:
        shipment = original.execute(
            "SELECT " + ",".join(sorted(EDITABLE)) +
            " FROM shipment WHERE id=?", (sid,)
        ).fetchone()
        if shipment is None:
            raise LookupError("Shipment not found")
        original_values = dict(shipment)
    # Staging lives outside the repository and is removed on exit.
    with tempfile.TemporaryDirectory(prefix="portexa-migration-stage-") as directory:
        staged_path = Path(directory) / "staged.sqlite"
        with source_connection() as original, sqlite3.connect(staged_path) as staged:
            original.backup(staged)
        previous_path = os.environ.get("PORTEXA_SOURCE_DB")
        previous_gate = os.environ.get("PORTEXA_SOURCE_WRITE_ENABLED")
        try:
            os.environ["PORTEXA_SOURCE_DB"] = str(staged_path)
            os.environ["PORTEXA_SOURCE_WRITE_ENABLED"] = "YES"
            # Recheck the staged snapshot before any write.
            with source_connection() as staged:
                row = staged.execute(
                    "SELECT " + ",".join(sorted(EDITABLE)) +
                    " FROM shipment WHERE id=?", (sid,)
                ).fetchone()
                if dict(row) != original_values:
                    raise RuntimeError("Source snapshot changed during staging")
            containers = [
                {"container_number": number, **{key: "" for key in MOVEMENT_FIELDS}}
                for number in report["container_numbers"]
            ]
            values = {key: (original_values[key] or "") for key in EDITABLE}
            applied = save_shipment_bundle(
                sid, values, original_values, containers, [], actor_id=None
            )
            staged_movements = read_container_movements(sid)
            staged_history = read_container_history(sid)
            if not applied or len(staged_movements) != len(containers):
                raise AssertionError("Staged migration failed verification")
            if len(staged_history) != len(containers):
                raise AssertionError("Staged history incomplete")
            if any(row[key] is not None for row in staged_movements for key in MOVEMENT_FIELDS):
                raise AssertionError("Legacy movement was copied unexpectedly")
        finally:
            if previous_path is None:
                os.environ.pop("PORTEXA_SOURCE_DB", None)
            else:
                os.environ["PORTEXA_SOURCE_DB"] = previous_path
            if previous_gate is None:
                os.environ.pop("PORTEXA_SOURCE_WRITE_ENABLED", None)
            else:
                os.environ["PORTEXA_SOURCE_WRITE_ENABLED"] = previous_gate
    if database_path() != original_path:
        raise AssertionError("Source DB environment not restored")
    return {
        "shipment_id": sid,
        "container_numbers": report["container_numbers"],
        "staged_containers": len(staged_movements),
        "staged_history_events": len(staged_history),
        "legacy_movement_copied": False,
        "production_writes_performed": False,
        "legacy_route_preserved": True,
        "staging_database_removed": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("shipment_id", type=int)
    args = parser.parse_args()
    print(json.dumps(rehearse(args.shipment_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
