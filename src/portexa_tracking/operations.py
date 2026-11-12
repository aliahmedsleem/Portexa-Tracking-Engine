"""Task/payroll foundation for PORTEXA Tracking Engine.

Independent SQLite tables; no production shipment writes. All money in IQD integers.
A task is payable only after explicit supervisor approval, even if route is unchanged.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from portexa_tracking.database import connect

SLOTS = (0, 6, 12, 18)
ROLES = ("admin", "supervisor", "operator")
STATUSES = ("pending", "submitted", "approved", "rejected")
PBKDF2_ITERATIONS = 260000


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_operations(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ops_users(
      id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, phone TEXT NOT NULL,
      username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('admin','supervisor','operator')),
      active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ops_assignments(
      shipment_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES ops_users(id),
      active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), assigned_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ops_tasks(
      id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL,
      cycle_at TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES ops_users(id),
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','submitted','approved','rejected')),
      review_note TEXT, evidence_source TEXT, route_changed INTEGER,
      submitted_at TEXT, approved_at TEXT, approved_by INTEGER REFERENCES ops_users(id),
      price_iqd INTEGER NOT NULL CHECK(price_iqd >= 0),
      created_at TEXT NOT NULL, UNIQUE(shipment_id,cycle_at)
    );
    CREATE INDEX IF NOT EXISTS ops_tasks_user_cycle ON ops_tasks(user_id,cycle_at,status);
    CREATE TABLE IF NOT EXISTS ops_notifications(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES ops_users(id),
      title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
      read_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ops_settings(
      key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    INSERT OR IGNORE INTO ops_settings(key,value) VALUES('price_per_approved_review_iqd','250');
    CREATE TABLE IF NOT EXISTS ops_payments(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES ops_users(id),
      amount_iqd INTEGER NOT NULL CHECK(amount_iqd > 0),
      note TEXT, paid_at TEXT NOT NULL, recorded_by INTEGER NOT NULL REFERENCES ops_users(id)
    );
    CREATE TABLE IF NOT EXISTS ops_shipment_change_log(
      id INTEGER PRIMARY KEY,
      task_id INTEGER NOT NULL REFERENCES ops_tasks(id),
      shipment_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL REFERENCES ops_users(id),
      original_values TEXT NOT NULL,
      updated_values TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('pending','applied','failed','conflict')),
      error_message TEXT,
      created_at TEXT NOT NULL,
      applied_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_ops_change_log_shipment
      ON ops_shipment_change_log(shipment_id,created_at);
    CREATE TABLE IF NOT EXISTS ops_audit(
      id INTEGER PRIMARY KEY, actor_id INTEGER, action TEXT NOT NULL,
      target_type TEXT NOT NULL, target_id INTEGER, details TEXT, created_at TEXT NOT NULL
    );
    """)


def hash_password(password):
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (PBKDF2_ITERATIONS, salt.hex(), key.hex())


def verify_password(password, encoded):
    try:
        scheme, rounds, salt, digest = encoded.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (TypeError, ValueError, AttributeError):
        return False


def _require(conn, actor, roles):
    row = conn.execute("SELECT * FROM ops_users WHERE id=? AND active=1", (actor,)).fetchone()
    if not row or row["role"] not in roles:
        raise PermissionError("Insufficient privileges")
    return row


def audit(conn, actor, action, kind, target, details=""):
    conn.execute("INSERT INTO ops_audit(actor_id,action,target_type,target_id,details,created_at) VALUES(?,?,?,?,?,?)",
                 (actor, action, kind, target, details, now_utc()))


def create_user(conn, actor, full_name, phone, username, password, role="operator"):
    if role not in ROLES or not full_name.strip() or not phone.strip() or not username.strip():
        raise ValueError("Invalid user details")
    with conn:
        existing = conn.execute("SELECT COUNT(*) FROM ops_users").fetchone()[0]
        if existing:
            _require(conn, actor, ("admin",))
        elif role != "admin" or actor is not None:
            raise PermissionError("First account must be an admin")
        cur = conn.execute("INSERT INTO ops_users(full_name,phone,username,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
                           (full_name.strip(), phone.strip(), username.strip(), hash_password(password), role, now_utc()))
        audit(conn, actor, "user.created", "user", cur.lastrowid, role)
        return cur.lastrowid


def update_user(conn, actor, user_id, full_name, phone, username, role, active):
    """Admin-only account edit; protect the last active admin."""
    _require(conn, actor, ("admin",))
    if (not all(isinstance(x, str) for x in (full_name, phone, username))
            or not all((full_name.strip(), phone.strip(), username.strip()))
            or any(len(x.strip()) > n for x, n in ((full_name, 120), (phone, 40), (username, 80)))
            or role not in ROLES or type(active) is not bool):
        raise ValueError("Invalid user details")
    with conn:
        target = conn.execute("SELECT id,role,active FROM ops_users WHERE id=?", (user_id,)).fetchone()
        if target is None:
            raise LookupError("User not found")
        if target["role"] == "admin" and target["active"] and (role != "admin" or not active):
            remaining = conn.execute(
                "SELECT COUNT(*) FROM ops_users WHERE role='admin' AND active=1"
            ).fetchone()[0]
            if remaining <= 1:
                raise ValueError("Cannot deactivate or demote the last active admin")
        if user_id == actor and not active:
            raise ValueError("Cannot deactivate your own account")
        try:
            conn.execute(
                "UPDATE ops_users SET full_name=?,phone=?,username=?,role=?,active=? WHERE id=?",
                (full_name.strip(), phone.strip(), username.strip(), role, int(active), user_id)
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Username already exists") from exc
        audit(conn, actor, "user.updated", "user", user_id,
              "role=" + role + ";active=" + str(int(active)))


def reset_user_password(conn, actor, user_id, new_password):
    """Replace password hash; never retrieve or audit plaintext passwords."""
    _require(conn, actor, ("admin",))
    password_hash = hash_password(new_password)
    with conn:
        if conn.execute("SELECT id FROM ops_users WHERE id=?", (user_id,)).fetchone() is None:
            raise LookupError("User not found")
        conn.execute("UPDATE ops_users SET password_hash=? WHERE id=?", (password_hash, user_id))
        audit(conn, actor, "user.password.reset", "user", user_id)


def authenticate(conn, username, password):
    row = conn.execute("SELECT * FROM ops_users WHERE username=? AND active=1", (username,)).fetchone()
    return dict(row) if row and verify_password(password, row["password_hash"]) else None


def assign(conn, actor, shipment_id, user_id):
    from portexa_tracking.portexa_bridge import get_source_shipment, valid_bill_of_lading
    _require(conn, actor, ("admin", "supervisor"))
    target = _require(conn, user_id, ("operator",))
    shipment = get_source_shipment(shipment_id)
    if shipment is None:
        raise LookupError("Shipment not found")
    if not valid_bill_of_lading(shipment.get("bol_no")):
        raise ValueError("Shipment has no valid bill of lading")
    existing = conn.execute(
        "SELECT user_id FROM ops_assignments WHERE shipment_id=? AND active=1",
        (int(shipment_id),)).fetchone()
    if existing and int(existing["user_id"]) != int(user_id):
        pending = conn.execute(
            "SELECT COUNT(*) FROM ops_tasks WHERE shipment_id=? AND status IN ('pending','submitted')",
            (int(shipment_id),)).fetchone()[0]
        if pending:
            raise ValueError("Cannot reassign shipment with pending or submitted tasks")
    with conn:
        conn.execute("""INSERT INTO ops_assignments(shipment_id,user_id,assigned_at) VALUES(?,?,?)
          ON CONFLICT(shipment_id) DO UPDATE SET user_id=excluded.user_id,active=1,assigned_at=excluded.assigned_at""",
          (int(shipment_id), target["id"], now_utc()))
        audit(conn, actor, "shipment.assigned", "shipment", shipment_id, "user_id="+str(user_id))


def cycle_start(when):
    """Six-hour slots at 00/06/12/18 Asia/Baghdad (UTC+03)."""
    from datetime import timedelta
    baghdad = timezone(timedelta(hours=3))
    dt = when.astimezone(baghdad).replace(minute=0, second=0, microsecond=0)
    return dt.replace(hour=(dt.hour // 6) * 6).isoformat(timespec="seconds")


def generate_tasks(conn, when=None, eligible_ids=None):
    """Idempotent; optional verified source-ID filter for production scheduling."""
    slot = cycle_start(when or datetime.now(timezone.utc))
    with conn:
        price = int(conn.execute("SELECT value FROM ops_settings WHERE key='price_per_approved_review_iqd'").fetchone()[0])
        if price < 0:
            raise ValueError("Invalid price")
        ids = None if eligible_ids is None else sorted({int(i) for i in eligible_ids if int(i) > 0})
        if ids is not None and not ids:
            return 0
        before = conn.total_changes
        query = """INSERT OR IGNORE INTO ops_tasks(shipment_id,cycle_at,user_id,price_iqd,created_at)
          SELECT a.shipment_id, ?, a.user_id, ?, ?
          FROM ops_assignments a JOIN ops_users u ON u.id=a.user_id
          WHERE a.active=1 AND u.active=1"""
        params = [slot, price, now_utc()]
        if ids is not None:
            query += " AND a.shipment_id IN (" + ",".join("?" for _ in ids) + ")"
            params.extend(ids)
        conn.execute(query, params)
        return conn.total_changes - before


def submit_task(conn, actor, task_id, evidence_source, review_note, route_changed):
    if not evidence_source or not evidence_source.strip() or len(evidence_source) > 500:
        raise ValueError("Review evidence/source required")
    if not review_note or not review_note.strip() or len(review_note) > 2000:
        raise ValueError("Review note required, including when unchanged")
    if type(route_changed) is not bool:
        raise ValueError("route_changed must be boolean")
    _require(conn, actor, ("operator",))
    with conn:
        cur = conn.execute("""UPDATE ops_tasks SET status='submitted',evidence_source=?,review_note=?,
          route_changed=?,submitted_at=? WHERE id=? AND user_id=? AND status IN ('pending','rejected')""",
          (evidence_source.strip(), review_note.strip(), int(route_changed), now_utc(), task_id, actor))
        if cur.rowcount != 1:
            raise ValueError("Task not assigned or already submitted")
        audit(conn, actor, "task.submitted", "task", task_id)


def decide_task(conn, actor, task_id, approve, reason=""):
    _require(conn, actor, ("admin", "supervisor"))
    if type(approve) is not bool or (not approve and not reason.strip()):
        raise ValueError("Rejection requires a reason")
    with conn:
        row = conn.execute("SELECT user_id FROM ops_tasks WHERE id=? AND status='submitted'", (task_id,)).fetchone()
        if row is None:
            raise ValueError("Task is not awaiting review")
        status = "approved" if approve else "rejected"
        conn.execute("UPDATE ops_tasks SET status=?,approved_at=?,approved_by=? WHERE id=?",
                     (status, now_utc() if approve else None, actor if approve else None, task_id))
        conn.execute("INSERT INTO ops_notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",
                     (row["user_id"], "نتيجة مراجعة المهمة", "تم اعتماد المهمة" if approve else "تم رفض المهمة: "+reason[:500], now_utc()))
        audit(conn, actor, "task."+status, "task", task_id, reason[:500])


def set_price(conn, actor, amount):
    _require(conn, actor, ("admin",))
    if type(amount) is not int or not 0 <= amount <= 10000000:
        raise ValueError("Invalid price")
    with conn:
        conn.execute("UPDATE ops_settings SET value=? WHERE key='price_per_approved_review_iqd'", (str(amount),))
        audit(conn, actor, "price.changed", "settings", None, str(amount))


def payroll(conn, actor, user_id):
    _require(conn, actor, ("admin", "supervisor"))
    earned = conn.execute("SELECT COALESCE(SUM(price_iqd),0) FROM ops_tasks WHERE user_id=? AND status='approved'", (user_id,)).fetchone()[0]
    paid = conn.execute("SELECT COALESCE(SUM(amount_iqd),0) FROM ops_payments WHERE user_id=?", (user_id,)).fetchone()[0]
    return {"earned_iqd": earned, "paid_iqd": paid, "balance_iqd": earned-paid}


def record_payment(conn, actor, user_id, amount, note=""):
    _require(conn, actor, ("admin",))
    if type(amount) is not int or amount <= 0:
        raise ValueError("Invalid payment")
    with conn:
        balance = payroll(conn, actor, user_id)["balance_iqd"]
        if amount > balance:
            raise ValueError("Payment exceeds approved unpaid earnings")
        cur = conn.execute("INSERT INTO ops_payments(user_id,amount_iqd,note,paid_at,recorded_by) VALUES(?,?,?,?,?)",
                           (user_id, amount, note[:500], now_utc(), actor))
        audit(conn, actor, "payment.recorded", "payment", cur.lastrowid, str(amount))
        return cur.lastrowid


def dashboard_stats(conn, actor):
    _require(conn, actor, ("admin", "supervisor"))
    counts = {row["status"]: row["n"] for row in conn.execute("SELECT status,COUNT(*) n FROM ops_tasks GROUP BY status")}
    total = sum(counts.values())
    return {"users": conn.execute("SELECT COUNT(*) FROM ops_users WHERE active=1").fetchone()[0],
            "assignments": conn.execute("SELECT COUNT(*) FROM ops_assignments WHERE active=1").fetchone()[0],
            "tasks": counts, "approval_rate": round(100*counts.get("approved",0)/total,2) if total else 0}


def my_tasks(conn, actor, limit=100):
    _require(conn, actor, ("operator",))
    return [dict(r) for r in conn.execute("SELECT * FROM ops_tasks WHERE user_id=? ORDER BY cycle_at DESC,id DESC LIMIT ?",
                                         (actor, min(max(int(limit),1),500)))]


def notify(conn, actor, user_id, title, body):
    _require(conn, actor, ("admin", "supervisor"))
    if not title.strip() or not body.strip():
        raise ValueError("Notification must have content")
    with conn:
        conn.execute("INSERT INTO ops_notifications(user_id,title,body,created_at) VALUES(?,?,?,?)",
                     (user_id, title[:160], body[:2000], now_utc()))
        audit(conn, actor, "notification.sent", "user", user_id)


def bootstrap_database():
    with connect() as conn:
        init_operations(conn)
