import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portexa_tracking import manual_routes as routes
from portexa_tracking import operations_followup as followup
from portexa_tracking.operations_followup_lock import editable_transaction


class ManualRouteTransactionTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ops_db = self.root / "operations.sqlite3"
        self.source_db = self.root / "source.sqlite3"

        with sqlite3.connect(self.source_db) as conn:
            conn.execute(
                "CREATE TABLE shipment (id INTEGER PRIMARY KEY)"
            )
            conn.execute("INSERT INTO shipment(id) VALUES (100)")

        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE ops_users (
                    id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL
                );
                INSERT INTO ops_users VALUES
                    (1, 'admin', 1),
                    (2, 'operator', 1);
            """)
            followup.init(conn)
            followup.assign(conn, 1, 100, 2)
            routes.init_routes(conn)

        self.env = patch.dict(os.environ, {
            "PORTEXA_SOURCE_DB": str(self.source_db),
            "PORTEXA_TRACKING_DB": str(self.ops_db),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.ops_db, timeout=0.1)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=100")
        return conn

    def test_route_save_preserves_outer_lock(self):
        values = {key: "" for key in routes.FIELDS}

        with self.connect() as conn:
            with editable_transaction(conn, 2, 100):
                saved = routes.save_route(
                    100,
                    values,
                    None,
                    conn=conn,
                )

                self.assertEqual(
                    saved["source_shipment_id"], 100
                )

                self.assertTrue(
                    conn.in_transaction,
                    "Route save released the outer transaction",
                )

                with self.connect() as other:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute("BEGIN IMMEDIATE")

                followup.record_update(conn, 2, 100)

                self.assertTrue(conn.in_transaction)

        with self.connect() as conn:
            record = followup.get(conn, 100)
            self.assertEqual(record["update_count"], 1)

            route = conn.execute(
                "SELECT * FROM portexa_manual_routes "
                "WHERE source_shipment_id=100"
            ).fetchone()

            self.assertIsNotNone(route)

    def test_route_rollback_with_outer_transaction(self):
        values = {key: "" for key in routes.FIELDS}

        with self.assertRaisesRegex(RuntimeError, "rollback test"):
            with self.connect() as conn:
                with editable_transaction(conn, 2, 100):
                    routes.save_route(
                        100, values, None, conn=conn
                    )
                    followup.record_update(conn, 2, 100)
                    raise RuntimeError("rollback test")

        with self.connect() as conn:
            route = conn.execute(
                "SELECT 1 FROM portexa_manual_routes "
                "WHERE source_shipment_id=100"
            ).fetchone()

            self.assertIsNone(route)
            self.assertEqual(
                followup.get(conn, 100)["update_count"], 0
            )


if __name__ == "__main__":
    unittest.main()
