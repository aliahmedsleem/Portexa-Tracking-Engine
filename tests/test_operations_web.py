"""Basic web access-control tests."""
import sqlite3
import pytest
from portexa_tracking import operations as ops
from portexa_tracking import operations_web as web


@pytest.fixture
def client(monkeypatch):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    ops.init_operations(db)
    admin = ops.create_user(db, None, "Admin", "0770", "admin", "strong-password-admin", "admin")
    operator = ops.create_user(db, admin, "Operator", "0771", "operator", "strong-password-operator")
    # Admin dashboard reads source bills; supply an isolated fixture DB.
    source = sqlite3.connect(":memory:", check_same_thread=False)
    source.row_factory = sqlite3.Row
    source.execute("""CREATE TABLE shipment (
        id INTEGER PRIMARY KEY, bol_no TEXT
    )""")
    source.executemany("INSERT INTO shipment(id,bol_no) VALUES(?,?)",
                       [(4,"MEDU123456"),(5,None),(8,"NULL")])
    monkeypatch.setattr(web, "db", lambda: _NonClosing(db))
    monkeypatch.setattr("portexa_tracking.portexa_bridge.source_connection",
                        lambda: _NonClosing(source))
    monkeypatch.setattr("portexa_tracking.portexa_bridge.check_schema",
                        lambda source_conn: None)
    web.app.config.update(TESTING=True, SECRET_KEY="test-only-secret")
    yield web.app.test_client()
    db.close()
    source.close()


class _NonClosing:
    def __init__(self, conn): self.conn = conn
    def __enter__(self): return self.conn
    def __exit__(self, *args): return False
    def __getattr__(self, name): return getattr(self.conn, name)


def token(client):
    with client.session_transaction() as s:
        return s["csrf"]


def test_login_and_role_permissions(client):
    assert client.get("/").status_code == 302
    assert client.get("/login").status_code == 200
    assert client.post("/login", data={"username":"operator","password":"strong-password-operator","csrf":token(client)}).status_code == 302
    assert client.get("/").status_code == 200
    assert client.post("/price",data={"csrf":token(client),"amount":"1"}).status_code == 403
    assert client.post("/logout",data={}).status_code == 403
    assert client.post("/logout",data={"csrf":token(client)}).status_code == 302


def test_admin_dashboard(client):
    client.get("/login")
    assert client.post("/login", data={"username":"admin","password":"strong-password-admin","csrf":token(client)}).status_code == 302
    assert client.get("/").status_code == 200
    assert client.post("/price",data={"csrf":token(client),"amount":"300"}).status_code == 302


def test_distribution_preview_role_guard(client, monkeypatch):
    client.get("/login")
    client.post("/login", data={"username":"operator","password":"strong-password-operator","csrf":token(client)})
    assert client.get("/distribution/preview").status_code == 403
    client.post("/logout",data={"csrf":token(client)})
    client.get("/login")
    client.post("/login",data={"username":"admin","password":"strong-password-admin","csrf":token(client)})
    monkeypatch.setattr(web.dist,"distribution_preview",lambda conn:{
        "eligible":2,"already_assigned":0,"unassigned":2,
        "plan":[(100,2),(101,2)],
        "users":[{"id":2,"full_name":"Operator","phone":"0771","load":2}]
    })
    monkeypatch.setattr(web,"cycle_preview",lambda conn:{"new":0})
    result=client.get("/distribution/preview")
    assert result.status_code==200
    assert "100".encode() in result.data


def test_notification_inbox_read_and_user_isolation(client):
    client.get("/login")
    client.post("/login",data={"username":"admin","password":"strong-password-admin","csrf":token(client)})
    with web.db() as conn:
        ops.notify(conn,1,2,"Task assigned","Please review shipment")
        ops.notify(conn,1,1,"Admin only","Private")
    assert client.get("/notifications/count").json["unread"]==1
    assert b"Admin only" in client.get("/notifications").data
    assert b"Task assigned" not in client.get("/notifications").data
    with web.db() as conn:
        other_id=conn.execute("SELECT id FROM ops_notifications WHERE user_id=2").fetchone()[0]
    assert client.post(f"/notifications/{other_id}/read",data={"csrf":token(client)}).status_code==302
    with web.db() as conn:
        assert conn.execute("SELECT read_at FROM ops_notifications WHERE id=?",(other_id,)).fetchone()[0] is None
    assert client.post("/notifications/read-all",data={"csrf":token(client)}).status_code==302
    assert client.get("/notifications/count").json["unread"]==0


def test_notification_send_and_recipient_view(client):
    client.get("/login")
    client.post("/login",data={"username":"admin","password":"strong-password-admin","csrf":token(client)})
    assert client.post("/notifications/send",data={"csrf":token(client),"user_id":"2","title":"Hello","body":"New task"}).status_code==302
    client.post("/logout",data={"csrf":token(client)})
    client.get("/login")
    client.post("/login",data={"username":"operator","password":"strong-password-operator","csrf":token(client)})
    assert client.get("/notifications/count").json["unread"]==1
    assert b"New task" in client.get("/notifications").data
    assert client.post("/notifications/read-all",data={}).status_code==403
