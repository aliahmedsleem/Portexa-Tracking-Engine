import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from portexa_tracking import operations_followup as followup


class FollowupTests(unittest.TestCase):
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
        INSERT INTO ops_users VALUES(3,'operator',1);
        INSERT INTO ops_users VALUES(4,'supervisor',1);
        """)

        followup.init(self.conn)
        self.start = datetime(
            2026,11,1,tzinfo=timezone.utc
        )

    def tearDown(self):
        self.conn.close()

    def test_assignment_and_ownership(self):
        followup.assign(self.conn,1,100,2,self.start)

        self.assertEqual(len(followup.my_active(self.conn,2)),1)
        self.assertEqual(len(followup.my_active(self.conn,3)),0)

        with self.assertRaises(PermissionError):
            followup.require_editable(self.conn,3,100)

        with self.assertRaises(ValueError):
            followup.assign(self.conn,1,100,3,self.start)

    def test_update_resets_reminder_clock(self):
        followup.assign(self.conn,1,100,2,self.start)

        day4 = self.start + timedelta(days=3,minutes=1)
        self.assertEqual(len(followup.due_reminders(self.conn,day4)),1)

        followup.record_update(self.conn,2,100,day4)

        self.assertEqual(len(followup.due_reminders(self.conn,day4)),0)

    def test_completion_locks_and_hides(self):
        followup.assign(self.conn,1,100,2,self.start)
        followup.complete(self.conn,2,100,self.start)

        self.assertEqual(len(followup.my_active(self.conn,2)),0)
        self.assertEqual(followup.statistics(self.conn)["completed"],1)

        with self.assertRaises(PermissionError):
            followup.require_editable(self.conn,2,100)

        with self.assertRaises(PermissionError):
            followup.complete(self.conn,2,100)

        self.assertEqual(
            followup.due_reminders(
                self.conn,self.start+timedelta(days=10)
            ),[]
        )

    def test_only_admin_reopens(self):
        followup.assign(self.conn,1,100,2,self.start)
        followup.complete(self.conn,2,100,self.start)

        with self.assertRaises(PermissionError):
            followup.reopen(self.conn,4,100,self.start)

        followup.reopen(self.conn,1,100,self.start)

        self.assertEqual(len(followup.my_active(self.conn,2)),1)

    def test_reminders_every_three_days(self):
        followup.assign(self.conn,1,100,2,self.start)

        self.assertEqual(
            followup.due_reminders(
                self.conn,self.start+timedelta(days=2)
            ),[]
        )

        due = followup.due_reminders(
            self.conn,self.start+timedelta(days=3)
        )

        self.assertEqual(due[0]["reminder_number"],1)

        self.conn.execute("""
            INSERT INTO ops_followup_reminders
            (shipment_id,user_id,reminder_number,created_at)
            VALUES(100,2,1,?)
        """, (followup.stamp(self.start),))
        self.conn.commit()

        self.assertEqual(
            followup.due_reminders(
                self.conn,self.start+timedelta(days=4)
            ),[]
        )

        due = followup.due_reminders(
            self.conn,self.start+timedelta(days=6)
        )
        self.assertEqual(due[0]["reminder_number"],2)


if __name__ == "__main__":
    unittest.main()
