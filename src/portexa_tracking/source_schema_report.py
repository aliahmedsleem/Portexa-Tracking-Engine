"""Read-only diagnostics for production PORTEXA schema compatibility.

Usage: python -m portexa_tracking.source_schema_report
Does not print shipment/customer data and does not modify any database.
"""
import json
from portexa_tracking.portexa_bridge import source_connection

INTERESTING = ("shipment", "container", "containers", "shipment_container",
               "shipment_containers", "ops_container_movements")
STATUS_HINTS = ("status", "state", "container", "bol", "tracking", "ship_date",
                "arrival_date", "vessel", "port", "route")


def inspect_schema():
    with source_connection() as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        selected = [name for name in tables if any(h in name.lower() for h in INTERESTING)]
        report = {"tables": {}, "shipment_status_values": []}
        for name in selected:
            cols = [dict(r) for r in conn.execute("PRAGMA table_info(" + '"' + name.replace('"', '""') + '"' + ")")]
            report["tables"][name] = {
                "columns": [{"name": c["name"], "type": c["type"], "not_null": bool(c["notnull"])}
                            for c in cols],
                "potential_movement_fields": [c["name"] for c in cols
                    if any(h in c["name"].lower() for h in STATUS_HINTS)]
            }
        if "shipment" in tables:
            columns = {c["name"] for c in conn.execute("PRAGMA table_info(shipment)")}
            if "status" in columns:
                report["shipment_status_values"] = [
                    {"status": r["status"], "count": r["n"]}
                    for r in conn.execute(
                        "SELECT status,COUNT(*) AS n FROM shipment GROUP BY status ORDER BY n DESC LIMIT 100"
                    )
                ]
        return report


def main():
    print(json.dumps(inspect_schema(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
