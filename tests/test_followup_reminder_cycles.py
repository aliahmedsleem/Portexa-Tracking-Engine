import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from portexa_tracking import operations_followup as f


class ReminderCycleTests(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

        self.conn.executescript("""
        CREATE TABLE ops_users(
            id INTEGER PRIMARY KEY,
            role TEXT NOT NULL,
            active INTEGER NOT NULL
        );
        INSERT INTO ops_users VALUES(1,'admin',1);
        INSERT INTO ops_users VALUES(2,'operator',1);
        """)

        f.init(self.conn)
        self.start = datetime(
            2026,11,1,tzinfo=timezone.utc
        )
        f.assign(self.conn,1,100,2,self.start)

    def tearDown(self):
        self.conn.close()

    def test_reminder_after_update(self):
        day3 = self.start + timedelta(days=3)

        due = f.due_reminders(self.conn,day3)
        self.assertEqual(len(due),1)

        self.assertTrue(
            f.record_reminder(self.conn,100,2,0,1,day3)
        )

        self.assertFalse(
            f.record_reminder(self.conn,100,2,0,1,day3)
        )

        f.record_update(self.conn,2,100,day3)

        day6 = self.start + timedelta(days=6)
        due = f.due_reminders(self.conn,day6)

        self.assertEqual(len(due),1)
        self.assertEqual(due[0]["update_count"],1)

        self.assertTrue(
            f.record_reminder(self.conn,100,2,1,1,day6)
        )

    def test_completed_tasks_have_no_reminders(self):
        f.complete(self.conn,2,100,self.start)

        day10 = self.start + timedelta(days=10)
        self.assertEqual(f.due_reminders(self.conn,day10),[])


if __name__ == "__main__":
    unittest.main()
