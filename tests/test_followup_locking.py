import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from portexa_tracking import operations_followup as followup
from portexa_tracking.operations_followup_lock import (
    editable_transaction,
    completion_transaction,
)


class FollowupLockingTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "locking.sqlite3"

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
        conn = sqlite3.connect(
            self.db_path,
            timeout=5,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def test_completion_waits_for_edit(self):
        edit_started = threading.Event()
        release_edit = threading.Event()
        results = []
        errors = []

        def editor():
            try:
                with self.connect() as conn:
                    with editable_transaction(conn, 2, 100):
                        edit_started.set()

                        if not release_edit.wait(timeout=3):
                            raise RuntimeError("Edit release timed out")

                        followup.record_update(conn, 2, 100)

                results.append("edit")
            except Exception as exc:
                errors.append(exc)

        def closer():
            try:
                if not edit_started.wait(timeout=3):
                    raise RuntimeError("Edit never started")

                with self.connect() as conn:
                    with completion_transaction(conn, 2, 100):
                        followup.complete(conn, 2, 100)

                results.append("complete")
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=editor)
        t2 = threading.Thread(target=closer)

        t1.start()
        t2.start()

        self.assertTrue(edit_started.wait(timeout=3))

        # Completion must not finish while edit holds the write lock.
        time.sleep(0.15)
        self.assertNotIn("complete", results)

        release_edit.set()

        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, ["edit", "complete"])

        with self.connect() as conn:
            record = followup.get(conn, 100)

            self.assertEqual(record["status"], "completed")
            self.assertEqual(record["update_count"], 1)

    def test_edit_rejected_after_completion(self):
        with self.connect() as conn:
            with completion_transaction(conn, 2, 100):
                followup.complete(conn, 2, 100)

        with self.connect() as conn:
            with self.assertRaises(PermissionError):
                with editable_transaction(conn, 2, 100):
                    pass

        with self.connect() as conn:
            self.assertEqual(
                followup.get(conn, 100)["status"],
                "completed",
            )


if __name__ == "__main__":
    unittest.main()
