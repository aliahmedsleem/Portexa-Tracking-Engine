"""Persistent shipment follow-up workflow.

Separate from the legacy six-hour review/payroll system.
No automatic writes to the PORTEXA source database.
"""

import sqlite3
from datetime import datetime, timedelta, timezone


from contextlib import contextmanager
from itertools import count

_followup_savepoint_counter = count(1)


@contextmanager
def followup_transaction(conn):
    """Preserve an existing SQLite transaction using a SAVEPOINT."""
    if conn.in_transaction:
        name = f"followup_sp_{next(_followup_savepoint_counter)}"

        conn.execute(f"SAVEPOINT {name}")

        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            conn.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            conn.execute(f"RELEASE SAVEPOINT {name}")

    else:
        conn.execute("BEGIN IMMEDIATE")

        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()


INTERVAL = timedelta(days=3)


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or utcnow()).astimezone(timezone.utc).isoformat(
        timespec="seconds"
    )


def parse(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def init(conn):
    """Create independent workflow tables; safe to call repeatedly."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ops_followups (
        shipment_id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active','completed')),
        assigned_at TEXT NOT NULL,
        last_updated_at TEXT NOT NULL,
        completed_at TEXT,
        completed_by INTEGER,
        reopened_at TEXT,
        reopened_by INTEGER,
        update_count INTEGER NOT NULL DEFAULT 0
            CHECK(update_count >= 0)
    );

    CREATE INDEX IF NOT EXISTS idx_followups_user_status
        ON ops_followups(user_id,status);

    CREATE TABLE IF NOT EXISTS ops_followup_reminders (
        id INTEGER PRIMARY KEY,
        shipment_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        reminder_number INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(shipment_id,reminder_number)
    );

    CREATE TABLE IF NOT EXISTS ops_followup_events (
        id INTEGER PRIMARY KEY,
        shipment_id INTEGER NOT NULL,
        actor_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL
    );
    """)
    init_reminders(conn)


def init_reminders(conn):
    """Create reminder history scoped to each update cycle."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_followup_reminder_deliveries (
            id INTEGER PRIMARY KEY,
            shipment_id INTEGER NOT NULL,
            update_count INTEGER NOT NULL,
            reminder_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(shipment_id,update_count,reminder_number)
        )
    """)


def record_reminder(conn, shipment_id, user_id, update_count,
                    reminder_number, when=None):
    """Reserve a due reminder once per update cycle."""
    when = when or utcnow()
    with followup_transaction(conn):
        record = get(conn, shipment_id)

        if not record or record["status"] != "active":
            return False

        if record["user_id"] != user_id:
            return False

        if record["update_count"] != update_count:
            return False

        due_at = parse(record["last_updated_at"]) + (
            INTERVAL * reminder_number
        )

        if when < due_at:
            return False

        if update_count == 0:
            legacy = conn.execute("""
                SELECT 1 FROM ops_followup_reminders
                WHERE shipment_id=? AND reminder_number=?
            """, (shipment_id,reminder_number)).fetchone()

            if legacy:
                return False

        result = conn.execute("""
            INSERT OR IGNORE INTO ops_followup_reminder_deliveries
            (shipment_id,update_count,reminder_number,user_id,created_at)
            VALUES(?,?,?,?,?)
        """, (
            shipment_id,update_count,reminder_number,
            user_id,stamp(when)
        ))

        return result.rowcount == 1


def actor(conn, user_id, roles):
    row = conn.execute(
        "SELECT id,role,active FROM ops_users WHERE id=?",
        (user_id,),
    ).fetchone()

    if not row or not row["active"] or row["role"] not in roles:
        raise PermissionError("User is not authorized")

    return row


def event(conn, shipment_id, actor_id, action, details="", when=None):
    conn.execute("""
        INSERT INTO ops_followup_events
        (shipment_id,actor_id,action,details,created_at)
        VALUES(?,?,?,?,?)
    """, (shipment_id,actor_id,action,details,stamp(when)))


def get(conn, shipment_id):
    row = conn.execute(
        "SELECT * FROM ops_followups WHERE shipment_id=?",
        (shipment_id,),
    ).fetchone()
    return dict(row) if row else None


def assign(conn, manager_id, shipment_id, user_id, when=None):
    """Assign an unassigned shipment or reactivate an inactive legacy assignment."""
    actor(conn, manager_id, ("admin","supervisor"))
    actor(conn, user_id, ("operator",))

    when = when or utcnow()
    shipment_id = int(shipment_id)

    with followup_transaction(conn):
        existing = get(conn, shipment_id)
        if existing:
            raise ValueError(
                "Shipment already has a follow-up record; "
                "use the explicit reassignment/reopen workflow"
            )

        conn.execute("""
            INSERT INTO ops_followups
            (shipment_id,user_id,status,assigned_at,last_updated_at)
            VALUES(?,?,'active',?,?)
        """, (shipment_id,user_id,stamp(when),stamp(when)))

        event(conn,shipment_id,manager_id,"assigned",
              f"user_id={user_id}",when)


def require_editable(conn, user_id, shipment_id):
    """Call before every shipment/route/container write endpoint."""
    actor(conn,user_id,("operator",))
    record = get(conn,shipment_id)

    if not record:
        raise LookupError("Follow-up not found")

    if record["user_id"] != user_id:
        raise PermissionError("Shipment belongs to another operator")

    if record["status"] != "active":
        raise PermissionError("Completed shipment is locked")

    return record


def record_update(conn, user_id, shipment_id, when=None):
    """Call only after a successful actual shipment update."""
    when = when or utcnow()

    with followup_transaction(conn):
        require_editable(conn,user_id,shipment_id)

        conn.execute("""
            UPDATE ops_followups
            SET last_updated_at=?,
                update_count=update_count+1
            WHERE shipment_id=? AND status='active'
        """, (stamp(when),shipment_id))

        event(conn,shipment_id,user_id,"updated","",when)


def complete(conn, user_id, shipment_id, when=None):
    """Close once. Completed follow-ups cannot be edited."""
    when = when or utcnow()

    with followup_transaction(conn):
        require_editable(conn,user_id,shipment_id)

        changed = conn.execute("""
            UPDATE ops_followups
            SET status='completed',
                completed_at=?,
                completed_by=?
            WHERE shipment_id=? AND user_id=? AND status='active'
        """, (stamp(when),user_id,shipment_id,user_id))

        if changed.rowcount != 1:
            raise ValueError("Follow-up is already completed")

        event(conn,shipment_id,user_id,"completed","",when)


def reopen(conn, manager_id, shipment_id, when=None):
    """Admin-only reopening, with audit trail."""
    actor(conn,manager_id,("admin",))
    when = when or utcnow()

    with followup_transaction(conn):
        changed = conn.execute("""
            UPDATE ops_followups
            SET status='active',
                last_updated_at=?,
                update_count=update_count+1,
                completed_at=NULL,
                completed_by=NULL,
                reopened_at=?,
                reopened_by=?
            WHERE shipment_id=? AND status='completed'
        """, (
            stamp(when),stamp(when),manager_id,shipment_id
        ))

        if changed.rowcount != 1:
            raise ValueError("Follow-up is not completed")

        event(conn,shipment_id,manager_id,"reopened","",when)


def my_active(conn, user_id):
    actor(conn,user_id,("operator",))

    return [
        dict(row) for row in conn.execute("""
            SELECT * FROM ops_followups
            WHERE user_id=? AND status='active'
            ORDER BY last_updated_at ASC,shipment_id
        """, (user_id,))
    ]


def statistics(conn):
    row = conn.execute("""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END)
              AS active,
          SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END)
              AS completed
        FROM ops_followups
    """).fetchone()

    return {key: row[key] or 0 for key in row.keys()}



def due_reminders(conn, when=None):
    """Find due reminders without modifying delivery history."""
    when = when or utcnow()
    results = []

    rows = conn.execute("""
        SELECT shipment_id,user_id,last_updated_at,update_count
        FROM ops_followups
        WHERE status='active'
    """).fetchall()

    for row in rows:
        elapsed = when - parse(row["last_updated_at"])
        periods = int(
            elapsed.total_seconds() // INTERVAL.total_seconds()
        )

        if periods < 1:
            continue

        delivered = {
            int(item[0])
            for item in conn.execute("""
                SELECT reminder_number
                FROM ops_followup_reminder_deliveries
                WHERE shipment_id=? AND update_count=?
            """, (
                row["shipment_id"],
                row["update_count"]
            ))
        }

        # Legacy reminders belong only to the original update cycle.
        # Later cycles use the new cycle-scoped delivery table.
        if row["update_count"] == 0:
            delivered.update(
                int(item[0])
                for item in conn.execute("""
                    SELECT reminder_number
                    FROM ops_followup_reminders
                    WHERE shipment_id=?
                """, (row["shipment_id"],))
            )

        for number in range(1, periods + 1):
            if number not in delivered:
                results.append({
                    "shipment_id": row["shipment_id"],
                    "user_id": row["user_id"],
                    "update_count": row["update_count"],
                    "reminder_number": number,
                    "days_since_update": elapsed.days,
                })
                break

    return results


def reminder_status(conn, user_id, shipment_id, when=None):
    """Read-only status for dashboard badges."""
    record = require_editable(conn,user_id,shipment_id)
    when = when or utcnow()
    next_due = parse(record["last_updated_at"]) + INTERVAL

    return {
        "due":when >= next_due,
        "next_due_at":stamp(next_due),
        "days_since_update":max(
            0,(when-parse(record["last_updated_at"])).days
        ),
    }
