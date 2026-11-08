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
from portexa_tracking import operations_distribution as dist
from portexa_tracking.operations_scheduler import preview as cycle_preview

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
:root{font-family:system-ui,Tahoma,Arial,sans-serif;color-scheme:dark;background:#081827;color:#eaf1f8}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 85% 0%,#193750 0%,#081827 42%)}
header{background:#10283fdf;padding:18px clamp(16px,4vw,56px);border-bottom:1px solid #31485d;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
header h1{font-size:clamp(19px,2vw,25px);margin:0;color:#efc77c;letter-spacing:.2px}
main{max-width:1260px;margin:0 auto;padding:clamp(16px,3vw,34px)}
h2{font-size:20px;color:#f2d39b;margin:0 0 18px}h3{font-size:16px;margin:0 0 10px}
nav{display:flex;align-items:center;gap:14px;flex-wrap:wrap}a{color:#efc77c;text-decoration:none}a:hover{text-decoration:underline}
.panel{background:#142a40;border:1px solid #2f4c65;border-radius:16px;padding:clamp(16px,2vw,24px);margin:0 0 18px;box-shadow:0 10px 32px #0002}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,245px),1fr));gap:16px}
label{display:block;margin:0 0 16px;font-size:14px;color:#c8d7e6}
input,select,textarea{display:block;width:100%;min-height:44px;margin-top:7px;padding:11px 13px;border-radius:9px;border:1px solid #49657e;background:#091c2e;color:#fff;font:inherit}
input:focus,select:focus,textarea:focus{outline:2px solid #d9b16d;outline-offset:1px}
button{width:auto;min-height:42px;padding:10px 20px;background:#e7bf76;color:#10243b;border:0;border-radius:9px;font:inherit;font-weight:700;cursor:pointer}
button:hover{background:#f8d89a}form{max-width:100%}
.scroll{overflow-x:auto;border:1px solid #29465d;border-radius:10px}
table{width:100%;border-collapse:collapse;min-width:620px}td,th{text-align:right;padding:13px;border-bottom:1px solid #29465d;font-size:14px}th{color:#e8c688;background:#1c3951}
.muted{color:#aabaca}.error{color:#ffc6a9}strong{font-size:19px}
body:has(form[action$="/login"]) main{min-height:calc(100vh - 95px);display:grid;place-items:center}
body:has(form[action$="/login"]) main>.panel{width:min(100%,420px);padding:clamp(24px,4vw,38px)}
body:has(form[action$="/login"]) main>.panel form button{width:100%;margin-top:6px}
body:has(form[action$="/login"]) main>.panel h2{text-align:center;margin-bottom:24px}
nav form{margin:0}nav button{min-height:34px;padding:6px 14px}
@media(max-width:600px){header{padding:15px}main{padding:14px}.panel{margin-bottom:12px}.grid{gap:10px}table{min-width:550px}}
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
        assignments = dist.assignment_history(conn)
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
<section class="panel"><h2>التوزيع التلقائي العادل</h2>
<p class="muted">تُسند البوالص الجديدة تلقائياً إلى الموظفين الأقل حملاً، وتبقى الإسنادات السابقة محفوظة.</p>
<p><a href="{{ url_for('distribution_preview_page') }}">معاينة توزيع البوالص والأحمال قبل التنفيذ</a> · <a href="{{ url_for('source_statuses_page') }}">فحص حالات الشحنات الأصلية</a></p>
<form method="post" action="{{ url_for('auto_distribute') }}"><input type="hidden" name="csrf" value="{{ csrf }}"><button>توزيع البوالص الجديدة تلقائياً</button></form></section>
<section class="panel"><h2>البوالص المسندة وسجل العمل</h2><div class="scroll">
<table><tr><th>البوليصة / الشحنة</th><th>الموظف المسؤول</th><th>عدد المراجعات</th><th>المعتمدة</th><th>آخر إرسال</th><th>التفاصيل</th></tr>
{% for a in assignments %}<tr><td>#{{ a.shipment_id }}</td><td>{{ a.operator_name }}</td>
<td>{{ a.reviews }}</td><td>{{ a.approved_reviews }}</td><td>{{ a.last_submitted_at or '—' }}</td>
<td><a href="{{ url_for('shipment_history',shipment_id=a.shipment_id) }}">سجل المراجعات</a></td></tr>
{% else %}<tr><td colspan="6">لم تُوزع بوالص بعد</td></tr>{% endfor %}</table></div></section>
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
<label>العنوان<input name="title" required></label><label>الرسالة<textarea name="body" required></textarea></label><button>إرسال</button></form></section>""", stats=stats, users=users, pending=pending, assignments=assignments)


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


@app.get("/source-statuses")
@require("admin","supervisor")
def source_statuses_page():
    try:
        rows = dist.source_status_summary()
        configured = [x.strip() for x in os.environ.get("PORTEXA_OPS_ACTIVE_STATUSES","").split(",") if x.strip()]
        return page("""<section class="panel"><h2>حالات الشحنات في PORTEXA</h2>
<p class="muted">قراءة فقط من قاعدة المصدر. الحالات المسموحة حالياً: {{ configured|join('، ') if configured else 'لم تُضبط بعد' }}</p>
<div class="scroll"><table><tr><th>الحالة الأصلية</th><th>عدد الشحنات</th></tr>
{% for r in rows %}<tr><td>{{ r.status }}</td><td>{{ r.count }}</td></tr>
{% else %}<tr><td colspan="2">لا توجد شحنات</td></tr>{% endfor %}</table></div></section>""",rows=rows,configured=configured)
    except (ValueError,RuntimeError,OSError) as exc:
        return page("""<section class="panel"><h2>تعذر قراءة حالات المصدر</h2><p>{{ error }}</p></section>""",error=str(exc)),503


@app.get("/distribution/preview")
@require("admin","supervisor")
def distribution_preview_page():
    try:
        with db() as conn:
            plan = dist.distribution_preview(conn)
            cycle = cycle_preview(conn)
        return page("""<section class="panel"><h2>معاينة التوزيع العادل</h2>
<p>البوالص المؤهلة: <strong>{{ p.eligible }}</strong> · المسندة مسبقاً: <strong>{{ p.already_assigned }}</strong>
· الجديدة: <strong>{{ p.unassigned }}</strong></p>
<p>مهام الدورة الحالية المتوقعة من الإسنادات الحالية: <strong>{{ cycle.new }}</strong></p>
<p class="muted">المعاينة لا تحفظ أي إسناد ولا تنشئ مهام أو أجوراً.</p></section>
<section class="panel"><h2>الحمل المتوقع لكل موظف</h2><div class="scroll"><table>
<tr><th>الموظف</th><th>الهاتف</th><th>عدد البوالص بعد التوزيع</th></tr>
{% for u in p.users %}<tr><td>{{ u.full_name }}</td><td>{{ u.phone }}</td><td>{{ u.load }}</td></tr>
{% else %}<tr><td colspan="3">لا يوجد موظفون نشطون</td></tr>{% endfor %}</table></div></section>
<section class="panel"><h2>البوالص الجديدة المقترحة</h2><div class="scroll"><table>
<tr><th>رقم الشحنة</th><th>الموظف المقترح</th></tr>
{% for shipment_id,user_id in p.plan %}<tr><td>#{{ shipment_id }}</td>
<td>{{ names.get(user_id,'—') }}</td></tr>
{% else %}<tr><td colspan="2">لا توجد بوالص جديدة للتوزيع</td></tr>{% endfor %}</table></div>
<form method="post" action="{{ url_for('auto_distribute') }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<button>تأكيد التوزيع</button></form></section>""",
p=plan,cycle=cycle,names={u["id"]:u["full_name"] for u in plan["users"]})
    except (ValueError,RuntimeError,OSError) as exc:
        return page("""<section class="panel"><h2>تعذر عرض المعاينة</h2>
<p>{{ error }}</p><p>راجع إعداد الحالات النشطة وقاعدة بيانات المصدر.</p></section>""",error=str(exc)),503


@app.post("/auto-distribute")
@require("admin","supervisor")
def auto_distribute():
    return action(lambda c,a: dist.distribute(c,a))


@app.get("/shipments/<int:shipment_id>/history")
@require("admin","supervisor")
def shipment_history(shipment_id):
    with db() as conn:
        rows=dist.review_history(conn,shipment_id)
        owner=conn.execute("SELECT u.full_name FROM ops_assignments a JOIN ops_users u ON u.id=a.user_id WHERE a.shipment_id=?",(shipment_id,)).fetchone()
    if not owner: abort(404)
    return page("""<section class="panel"><h2>سجل مراجعات الشحنة #{{ shipment_id }}</h2>
<p>المسؤول الحالي: {{ owner }}</p><div class="scroll"><table>
<tr><th>الدورة</th><th>الموظف المنفذ</th><th>الحالة</th><th>الملاحظات</th><th>المصدر</th><th>المعتمد بواسطة</th></tr>
{% for r in rows %}<tr><td>{{ r.cycle_at }}</td><td>{{ r.operator_name }}</td>
<td>{{ r.status }}</td><td>{{ r.review_note or '—' }}</td><td>{{ r.evidence_source or '—' }}</td>
<td>{{ r.approver_name or '—' }}</td></tr>
{% else %}<tr><td colspan="6">لا توجد مراجعات بعد</td></tr>{% endfor %}</table></div></section>""",
shipment_id=shipment_id,owner=owner["full_name"],rows=rows)


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
