"""Local-only administration and scheduler CLI.

Usage:
  python -m portexa_tracking.operations_cli bootstrap-admin
  python -m portexa_tracking.operations_cli generate
  python -m portexa_tracking.operations_cli summary
"""
import argparse
import getpass
from portexa_tracking.database import connect
from portexa_tracking.operations import create_user, generate_tasks, init_operations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["bootstrap-admin", "generate", "summary"])
    args = parser.parse_args()
    with connect() as conn:
        init_operations(conn)
        if args.command == "bootstrap-admin":
            if conn.execute("SELECT COUNT(*) FROM ops_users").fetchone()[0]:
                parser.error("Users already exist; bootstrap is disabled")
            name = input("Full name: ").strip()
            phone = input("Phone: ").strip()
            username = input("Username: ").strip()
            password = getpass.getpass("Password (12+ characters): ")
            create_user(conn, None, name, phone, username, password, "admin")
            print("Initial admin created.")
        elif args.command == "generate":
            print("New tasks:", generate_tasks(conn))
        else:
            for row in conn.execute("SELECT status,COUNT(*) n FROM ops_tasks GROUP BY status"):
                print(row["status"], row["n"])


if __name__ == "__main__":
    main()
