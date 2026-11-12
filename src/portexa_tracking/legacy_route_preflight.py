"""Read-only preflight for migrating legacy manual routes to source container movements.

No migration or database writes occur. Output contains counts and shipment IDs only.
"""
import json
from portexa_tracking.database import connect
from portexa_tracking.portexa_bridge import source_connection


def inspect():
    with connect() as operations, source_connection() as source:
        old_table = operations.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='portexa_manual_routes'"
        ).fetchone()
        new_table = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops_container_movements'"
        ).fetchone()
        if not old_table:
            return {"legacy_routes": 0, "safe_candidates": [], "conflicts": [],
                    "missing_shipments": [], "missing_container_numbers": []}
        legacy = operations.execute(
            "SELECT source_shipment_id,container_number FROM portexa_manual_routes ORDER BY source_shipment_id"
        ).fetchall()
        safe, conflicts, missing, unnamed = [], [], [], []
        for row in legacy:
            sid = int(row["source_shipment_id"])
            shipment = source.execute("SELECT id,containers_count FROM shipment WHERE id=?", (sid,)).fetchone()
            if shipment is None:
                missing.append(sid)
                continue
            if not row["container_number"] or not str(row["container_number"]).strip():
                unnamed.append(sid)
                continue
            exists = source.execute(
                "SELECT 1 FROM ops_container_movements WHERE shipment_id=? LIMIT 1", (sid,)
            ).fetchone() if new_table else None
            if exists or (shipment["containers_count"] is not None and int(shipment["containers_count"]) > 1):
                conflicts.append(sid)
            else:
                safe.append(sid)
        return {
            "legacy_routes": len(legacy),
            "safe_candidates": safe,
            "conflicts": conflicts,
            "missing_shipments": missing,
            "missing_container_numbers": unnamed,
            "note": "Safe candidates are for review only; no migration has been performed.",
        }


def main():
    print(json.dumps(inspect(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
