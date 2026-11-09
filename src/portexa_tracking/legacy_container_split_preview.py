"""Read-only preview of splitting comma-separated legacy container references.

Never writes to either SQLite database. Movement values are deliberately not
copied to the proposed per-container records without operator confirmation.
"""
import json
import re
from portexa_tracking.database import connect
from portexa_tracking.portexa_bridge import source_connection

SPLIT = re.compile(r"[,،;؛\n]+")


def preview(shipment_id):
    sid = int(shipment_id)
    with connect() as local, source_connection() as source:
        old = local.execute(
            "SELECT container_number,updated_at FROM portexa_manual_routes WHERE source_shipment_id=?",
            (sid,),
        ).fetchone()
        shipment = source.execute(
            "SELECT id,containers_count,status FROM shipment WHERE id=?", (sid,)
        ).fetchone()
        if not old or not shipment:
            raise LookupError("Shipment or legacy route not found")
        raw = old["container_number"] or ""
        numbers = [part.strip().upper() for part in SPLIT.split(raw) if part.strip()]
        valid = all(re.fullmatch(r"[A-Z0-9-]{4,30}", n) for n in numbers)
        unique = len(numbers) == len(set(numbers))
        exists = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ops_container_movements'"
        ).fetchone()
        newer = source.execute(
            "SELECT COUNT(*) FROM ops_container_movements WHERE shipment_id=?", (sid,)
        ).fetchone()[0] if exists else 0
        declared = shipment["containers_count"]
        return {
            "shipment_id": sid,
            "status": shipment["status"],
            "declared_containers": declared,
            "legacy_updated_at": old["updated_at"],
            "container_numbers": numbers,
            "existing_new_movements": newer,
            "numbers_valid": bool(valid and unique),
            "matches_declared_count": declared is not None and len(numbers) == int(declared),
            "ready_for_manual_review": bool(valid and unique and not newer and declared is not None and len(numbers) == int(declared)),
            "movement_data_policy": "Do not replicate legacy movement fields to each container without operator confirmation",
            "writes_performed": False,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("shipment_id", type=int)
    args = parser.parse_args()
    print(json.dumps(preview(args.shipment_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
