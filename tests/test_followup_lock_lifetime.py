import sqlite3
import tempfile
import unittest
from pathlib import Path

from portexa_tracking import operations_followup as followup
from portexa_tracking.operations_followup_lock import (
    editable_transaction,
    completion_transaction,
)


class FollowupLockLifetimeTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"

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

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=0.1)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=100")
        return conn

    def assert_write_blocked(self):
        with self.connect() as other:
            with self.assertRaises(sqlite3.OperationalError) as error:
                other.execute("BEGIN IMMEDIATE")

            self.assertIn("locked", str(error.exception).lower())

    def test_update_keeps_outer_lock(self):
        with self.connect() as conn:
            with editable_transaction(conn, 2, 100):
                followup.record_update(conn, 2, 100)

                self.assertTrue(
                    conn.in_transaction,
                    "record_update released the outer transaction",
                )

                self.assert_write_blocked()

    def test_completion_keeps_outer_lock(self):
        with self.connect() as conn:
            with completion_transaction(conn, 2, 100):
                followup.complete(conn, 2, 100)

                self.assertTrue(
                    conn.in_transaction,
                    "complete released the outer transaction",
                )

                self.assert_write_blocked()


if __name__ == "__main__":
    unittest.main()
