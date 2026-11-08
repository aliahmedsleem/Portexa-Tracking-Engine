"""Operator CLI for isolated engine development."""
import argparse
import json
from portexa_tracking.database import connect, initialize, add_shipment, list_shipments
from portexa_tracking.scheduler import run_once


def main(argv=None):
    parser = argparse.ArgumentParser(prog="portexa-tracking")
    parser.add_argument("--db", default=None, help="Local tracking SQLite file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize database")
    add = sub.add_parser("add", help="Add or deduplicate shipment")
    add.add_argument("--carrier", required=True)
    add.add_argument("--reference", required=True)
    add.add_argument("--type", default="BL", choices=["BL", "BOOKING", "CONTAINER"])
    sub.add_parser("list", help="List stored shipments")
    run = sub.add_parser("run-once", help="Process one due shipment")
    run.add_argument("--carrier", default=None)
    args = parser.parse_args(argv)
    with connect(args.db) as conn:
        initialize(conn)
        if args.command == "init":
            print("Tracking database ready.")
        elif args.command == "add":
            row = add_shipment(conn, args.carrier, args.reference, args.type)
            print(json.dumps(dict(row), ensure_ascii=False, indent=2))
        elif args.command == "list":
            print(json.dumps([dict(r) for r in list_shipments(conn)], ensure_ascii=False, indent=2))
        else:
            result = run_once(conn, args.carrier)
            print(json.dumps(result if result else {"status": "no_due_shipments"},
                             ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
