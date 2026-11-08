"""Authenticated staff operations UI, isolated from the legacy local dashboard.

Run only on loopback behind SSH tunnel. Uses Flask sessions, CSRF, and role checks.
"""
import os
import secrets
from functools import wraps
from flask import Flask, abort, flash, redirect, render_template_string, request, session, url_for
from markupsafe import escape
from portexa_tracking.database import connect
from portexa_tracking import operations as ops

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("PORTEXA_OPS_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.environ.get("PORTEXA_OPS_HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=3600,
    MAX_CONTENT_LENGTH=16384,
)
CSS = """
<style>
:root{font-family:system-ui,Tahoma,sans-serif;color-scheme:dark;background:#0a192b;color:#e7edf6}
*{box-sizing:border-box}body{margin:0}header{background:#11283f;padding:16px 4%;border-bottom:1px solid #345}
main{max-width:1200px;margin:auto;padding:20px 14px}h1{color:#e7bf76;font-size:24px}
nav{display:flex;gap:12px;flex-wrap:wrap}a{color:#e7bf76}
.panel{background:#142b42;border:1px solid #34536b;border-radius:14px;padding:18px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:12px}
label{display:block;margin:10px 0}input,select,textarea,button{font:inherit;padding:10px;border-radius:8px;max-width:100%;width:100%}
input,select,textarea{background:#071a2b;color:white;border:1px solid #48627a}
button{background:#e7bf76;color:#12243a;border:0;font-weight:bold;cursor:pointer}
table{border-collapse:collapse;width:100%;min-width:580px}td,th{padding:10px;border-bottom:1px solid #38506a;text-align:right}
.scroll{overflow-x:auto}.error{color:#ffc6a9}.muted{color:#aabaca}
@media(max-width:600px){main{padding:12px}.panel{padding:12px}h1{font-size:20px}}
</style>
"""
BASE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PORTEXA Operations</title>""" + CSS + """</head><body>
<header><h1>PORTEXA · إدارة مهام التتبع</h1><nav>
{% if user %}<a href="{{ url_for('home') }}">الرئيسية</a><a href="{{ url_for('notifications') }}">الإشعارات</a>
<form method="post" action="{{ url_for('logout') }}" style="display:inline"><input type="hidden" name="csrf" value="{{ csrf }}"><button>خروج</button></form>
{% endif %}</nav></header><main>
{% for msg in get_flashed_messages() %}<p class="panel">{{ msg }}</p>{% endfor %}
{{ body|safe }}</main></body></html>"""


def db():
    conn = connect()
    ops.init_operations(conn)
    return conn


def current_user():
    uid = session.get("uid")
    if not isinstance(uid, int):
        return None
    with db() as conn:
        row = conn.execute("SELECT id,full_name,role,active FROM ops_users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row and row["active"] else None


def token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not secrets.compare_digest(str(request.form.get("csrf", "")), str(session.get("csrf", ""))):
            abort(403)


def page(body, **kwargs):
    from flask import render_template_string
    user = current_user()
    rendered = render_template_string(body, user=user, csrf=token(), **kwargs)
    return render_template_string(BASE, body=rendered, user=user, csrf=token())


def require(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            u = current_user()
            if not u:
                return redirect(url_for("login"))
            if roles and u["role"] not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return deco


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        with db() as conn:
            u = ops.authenticate(conn, request.form.get("username", ""), request.form.get("password", ""))
        if u:
            session.clear()
            session["uid"] = u["id"]
            session.permanent = True
            token()
            return redirect(url_for("home"))
        flash("بيانات الدخول غير صحيحة")
    return page("""<section class="panel"><h2>تسجيل الدخول</h2><form method="post">
<input type="hidden" name="csrf" value="{{ csrf }}">
<label>اسم المستخدم<input name="username" required autocomplete="username"></label>
<label>كلمة المرور<input type="password" name="password" required autocomplete="current-password"></label>
<button>دخول</button></form></section>""")


@app.post("/logout")
@require()
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@require()
def home():
    u = current_user()
    with db() as conn:
        if u["role"] == "operator":
            tasks = ops.my_tasks(conn, u["id"])
            return page("""<section class="panel"><h2>مهامي</h2><p>عدد المهام: {{ tasks|length }}</p></section>
{% for t in tasks %}<section class="panel"><h3>شحنة #{{ t.shipment_id }}</h3>
<p>الدورة: {{ t.cycle_at }} · الحالة: {{ t.status }} · الأجر: {{ t.price_iqd }} د.ع</p>
{% if t.status in ('pending','rejected') %}<form method="post" action="{{ url_for('submit',task_id=t.id) }}">
<input type="hidden" name="csrf" value="{{ csrf }}">
<label>مصدر المراجعة<input name="source" required maxlength="500"></label>
<label>ملاحظات المراجعة<textarea name="note" required maxlength="2000"></textarea></label>
<label>نتيجة المراجعة<select name="changed"><option value="0">تم التحقق — لا يوجد تغيير</option><option value="1">تم تحديث البيانات</option></select></label>
<button>إرسال للمراجعة</button></form>{% endif %}</section>
{% else %}<section class="panel">لا توجد مهام حالياً</section>{% endfor %}""", tasks=tasks)
        stats = ops.dashboard_stats(conn, u["id"])
        users = [dict(r) for r in conn.execute("SELECT id,full_name,phone,username,role,active FROM ops_users ORDER BY id")]
        pending = [dict(r) for r in conn.execute("SELECT t.id,t.shipment_id,t.user_id,t.review_note,t.evidence_source,t.route_changed FROM ops_tasks t WHERE t.status='submitted' ORDER BY t.id LIMIT 100")]
        return page("""<div class="grid">
<section class="panel"><h3>المستخدمون</h3><strong>{{ stats.users }}</strong></section>
<section class="panel"><h3>البوالص المسندة</h3><strong>{{ stats.assignments }}</strong></section>
<section class="panel"><h3>نسبة الاعتماد</h3><strong>{{ stats.approval_rate }}%</strong></section>
<section class="panel"><h3>بانتظار المراجعة</h3><strong>{{ stats.tasks.get('submitted',0) }}</strong></section></div>
<section class="panel"><h2>المستخدمون</h2><div class="scroll"><table><tr><th>الاسم</th><th>الهاتف</th><th>الدور</th><th>الحالة</th><th>المستحقات</th></tr>
{% for x in users %}<tr><td>{{ x.full_name }}</td><td>{{ x.phone }}</td><td>{{ x.role }}</td><td>{{ 'نشط' if x.active else 'موقوف' }}</td><td><a href="{{ url_for('user_payroll',user_id=x.id) }}">كشف حساب</a></td></tr>{% endfor %}</table></div></section>
{% if user.role=='admin' %}<section class="panel"><h2>إضافة موظف</h2><form method="post" action="{{ url_for('add_user') }}">
<input type="hidden" name="csrf" value="{{ csrf }}"><div class="grid">
<label>الاسم الكامل<input name="name" required></label><label>رقم الهاتف<input name="phone" required></label>
<label>اسم المستخدم<input name="username" required></label><label>كلمة المرور<input type="password" name="password" minlength="12" required></label>
<label>الصلاحية<select name="role"><option value="operator">موظف</option><option value="supervisor">مشرف</option></select></label></div><button>إضافة</button></form></section>{% endif %}
<section class="panel"><h2>إسناد بوليصة</h2><form method="post" action="{{ url_for('assign_task') }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<div class="grid"><label>رقم الشحنة في PORTEXA<input name="shipment_id" type="number" min="1" required></label>
<label>الموظف<select name="user_id">{% for x in users if x.role=='operator' and x.active %}<option value="{{ x.id }}">{{ x.full_name }}</option>{% endfor %}</select></label></div><button>إسناد</button></form></section>
<section class="panel"><h2>تحديثات تنتظر الاعتماد</h2>
{% for t in pending %}<div class="panel"><strong>شحنة #{{ t.shipment_id }}</strong>
<p>المصدر: {{ t.evidence_source }} · {{ t.review_note }}</p><p>تغيير بيانات: {{ 'نعم' if t.route_changed else 'لا' }}</p>
<form method="post" action="{{ url_for('decision',task_id=t.id) }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<label>سبب الرفض (إن وجد)<input name="reason"></label>
<div class="grid"><button name="decision" value="approve">اعتماد</button><button name="decision" value="reject">رفض</button></div></form></div>
{% else %}<p class="muted">لا توجد تحديثات تنتظر الاعتماد.</p>{% endfor %}</section>
{% if user.role=='admin' %}<section class="panel"><h2>سعر المراجعة المعتمدة</h2><form method="post" action="{{ url_for('price') }}"><input type="hidden" name="csrf" value="{{ csrf }}"><label>د.ع لكل مراجعة<input type="number" min="0" max="10000000" name="amount" required></label><button>حفظ السعر للدورات القادمة</button></form></section>{% endif %}
<section class="panel"><h2>إرسال إشعار</h2><form method="post" action="{{ url_for('send_notice') }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<label>المستخدم<select name="user_id">{% for x in users if x.active %}<option value="{{ x.id }}">{{ x.full_name }}</option>{% endfor %}</select></label>
<label>العنوان<input name="title" required></label><label>الرسالة<textarea name="body" required></textarea></label><button>إرسال</button></form></section>""", stats=stats, users=users, pending=pending)


def action(callback):
    try:
        with db() as conn:
            callback(conn, current_user()["id"])
        flash("تمت العملية بنجاح")
    except (ValueError, LookupError, PermissionError, OSError) as exc:
        flash("تعذر تنفيذ العملية: " + str(exc))
    return redirect(url_for("home"))


@app.post("/users")
@require("admin")
def add_user():
    return action(lambda c,a: ops.create_user(c,a,request.form["name"],request.form["phone"],request.form["username"],request.form["password"],request.form.get("role","operator")))


@app.post("/assign")
@require("admin","supervisor")
def assign_task():
    return action(lambda c,a: ops.assign(c,a,int(request.form["shipment_id"]),int(request.form["user_id"])))


@app.post("/tasks/<int:task_id>/submit")
@require("operator")
def submit(task_id):
    return action(lambda c,a: ops.submit_task(c,a,task_id,request.form["source"],request.form["note"],request.form["changed"]=="1"))


@app.post("/tasks/<int:task_id>/decision")
@require("admin","supervisor")
def decision(task_id):
    return action(lambda c,a: ops.decide_task(c,a,task_id,request.form["decision"]=="approve",request.form.get("reason","")))


@app.post("/price")
@require("admin")
def price():
    return action(lambda c,a: ops.set_price(c,a,int(request.form["amount"])))


@app.post("/notifications/send")
@require("admin","supervisor")
def send_notice():
    return action(lambda c,a: ops.notify(c,a,int(request.form["user_id"]),request.form["title"],request.form["body"]))


@app.get("/notifications")
@require()
def notifications():
    u = current_user()
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM ops_notifications WHERE user_id=? ORDER BY id DESC LIMIT 100",(u["id"],))]
    return page("""<section class="panel"><h2>الإشعارات</h2>{% for n in rows %}<div class="panel"><strong>{{ n.title }}</strong><p>{{ n.body }}</p><small>{{ n.created_at }}</small></div>{% else %}<p>لا توجد إشعارات</p>{% endfor %}</section>""",rows=rows)


@app.get("/payroll/<int:user_id>")
@require("admin","supervisor")
def user_payroll(user_id):
    with db() as conn:
        amounts=ops.payroll(conn,current_user()["id"],user_id)
        row=conn.execute("SELECT full_name FROM ops_users WHERE id=?",(user_id,)).fetchone()
    if row is None: abort(404)
    return page("""<section class="panel"><h2>كشف حساب: {{ name }}</h2><p>المكتسب: {{ x.earned_iqd }} د.ع</p>
<p>المدفوع: {{ x.paid_iqd }} د.ع</p><p>المتبقي: {{ x.balance_iqd }} د.ع</p>
{% if user.role=='admin' %}<form method="post" action="{{ url_for('pay',user_id=user_id) }}">
<input type="hidden" name="csrf" value="{{ csrf }}"><label>المبلغ المدفوع<input type="number" name="amount" min="1" max="{{ x.balance_iqd }}" required></label>
<label>ملاحظات<input name="note"></label><button>تسجيل الدفعة</button></form>{% endif %}</section>""",name=row["full_name"],x=amounts,user_id=user_id)


@app.post("/payroll/<int:user_id>/pay")
@require("admin")
def pay(user_id):
    return action(lambda c,a: ops.record_payment(c,a,user_id,int(request.form["amount"]),request.form.get("note","")))


def main():
    if not os.environ.get("PORTEXA_OPS_SECRET_KEY"):
        raise SystemExit("Set PORTEXA_OPS_SECRET_KEY to a persistent random secret before starting")
    with db():
        pass
    app.run(host="127.0.0.1",port=int(os.environ.get("PORTEXA_OPS_PORT","8766")),debug=False)


if __name__=="__main__":
    main()
