"""Discover new PORTEXA bills and notify operations staff without creating payable tasks.

First run establishes a silent baseline. Later runs notify admins/supervisors
and the assigned operator, if any. Source database is always read-only.
"""
import argparse
import sqlite3
from portexa_tracking.database import connect
from portexa_tracking.operations import init_operations, now_utc
from portexa_tracking.portexa_bridge import source_connection, check_schema, valid_bill_of_lading


def scan(conn, initialize_only=False):
    conn.execute("""CREATE TABLE IF NOT EXISTS ops_bill_discovery (
        shipment_id INTEGER PRIMARY KEY,
        bol_no TEXT NOT NULL,
        detected_at TEXT NOT NULL
    )""")
    with source_connection() as source:
        check_schema(source)
        shipments = [(int(r["id"]), str(r["bol_no"]).strip(), r["invoice_no"])
                     for r in source.execute("SELECT id,bol_no,invoice_no FROM shipment")
                     if valid_bill_of_lading(r["bol_no"])]
    baseline = conn.execute("SELECT COUNT(*) FROM ops_bill_discovery").fetchone()[0] == 0
    # A marker distinguishes an empty initialized source from a first run.
    initialized = conn.execute(
        "SELECT value FROM ops_settings WHERE key='bill_discovery_initialized'"
    ).fetchone()
    silent = initialize_only or initialized is None
    notifications = 0
    with conn:
        for shipment_id, bol_no, invoice_no in shipments:
            existing = conn.execute(
                "SELECT bol_no FROM ops_bill_discovery WHERE shipment_id=?", (shipment_id,)
            ).fetchone()
            if existing and existing["bol_no"] == bol_no:
                continue
            conn.execute("""INSERT INTO ops_bill_discovery(shipment_id,bol_no,detected_at)
                VALUES(?,?,?) ON CONFLICT(shipment_id) DO UPDATE SET
                bol_no=excluded.bol_no, detected_at=excluded.detected_at""",
                (shipment_id, bol_no, now_utc()))
            if silent:
                continue
            users = conn.execute("""SELECT id FROM ops_users
                WHERE active=1 AND role IN ('admin','supervisor')
                UNION
                SELECT u.id FROM ops_assignments a
                JOIN ops_users u ON u.id=a.user_id
                WHERE a.shipment_id=? AND a.active=1 AND u.active=1""",
                (shipment_id,)).fetchall()
            for user in users:
                conn.execute("""INSERT INTO ops_notifications
                    (user_id,title,body,created_at) VALUES(?,?,?,?)""",
                    (user["id"], "بوليصة شحن جديدة",
                     f"شحنة #{shipment_id} | البوليصة: {bol_no} | الفاتورة: {invoice_no or 'غير مسجلة'}",
                     now_utc()))
                notifications += 1
        conn.execute("""INSERT INTO ops_settings(key,value)
            VALUES('bill_discovery_initialized','1')
            ON CONFLICT(key) DO UPDATE SET value='1'""")
    return {"valid_shipments": len(shipments), "silent_baseline": silent,
            "notifications_created": notifications}


def main():
    parser = argparse.ArgumentParser(description="Read-only PORTEXA bill discovery")
    parser.add_argument("--baseline-only", action="store_true",
                        help="Register existing bills without notifications")
    args = parser.parse_args()
    with connect() as conn:
        init_operations(conn)
        print(scan(conn, initialize_only=args.baseline_only))


if __name__ == "__main__":
    main()
