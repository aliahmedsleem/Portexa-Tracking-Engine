import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from portexa_tracking import operations as ops
from portexa_tracking import operations_followup as followup


class ReopenCycleTests(unittest.TestCase):
    def test_reopen_starts_new_reminder_cycle(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        try:
            ops.init_operations(conn)
            followup.init(conn)

            start = datetime(2026, 1, 1, tzinfo=timezone.utc)

            conn.execute("""
                INSERT INTO ops_users
                (id, full_name, phone, username, password_hash,
                 role, active, created_at)
                VALUES
                (1, 'Manager', '1', 'manager', 'x', 'admin', 1, ?),
                (2, 'Operator', '2', 'operator', 'x', 'operator', 1, ?)
            """, (start.isoformat(), start.isoformat()))
            conn.commit()

            followup.assign(conn, 1, 100, 2, when=start)

            first_due = start + timedelta(days=3)
            self.assertTrue(
                followup.record_reminder(
                    conn, 100, 2, 0, 1, when=first_due
                )
            )

            followup.complete(
                conn, 2, 100, when=start + timedelta(days=4)
            )

            reopened = start + timedelta(days=5)
            followup.reopen(conn, 1, 100, when=reopened)

            record = followup.get(conn, 100)
            self.assertEqual(record["status"], "active")
            self.assertEqual(record["update_count"], 1)

            self.assertEqual(
                followup.due_reminders(
                    conn, when=reopened + timedelta(days=2)
                ),
                []
            )

            due = followup.due_reminders(
                conn, when=reopened + timedelta(days=3)
            )

            self.assertEqual(len(due), 1)
            self.assertEqual(due[0]["shipment_id"], 100)
            self.assertEqual(due[0]["update_count"], 1)
            self.assertEqual(due[0]["reminder_number"], 1)

        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
