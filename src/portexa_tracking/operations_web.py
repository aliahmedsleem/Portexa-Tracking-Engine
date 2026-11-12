"""Authenticated staff operations UI, isolated from the legacy local dashboard.

Run on loopback behind a trusted HTTPS reverse proxy. Uses Flask sessions, CSRF, and role checks.
"""
import os
import json
import secrets
from functools import wraps
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from portexa_tracking import operations_shipment_edit as edit
from portexa_tracking import manual_routes as routes
from portexa_tracking.portexa_bridge import get_source_shipment, read_container_movements, read_container_history, save_shipment_bundle, MOVEMENT_FIELDS, valid_bill_of_lading
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
    MAX_CONTENT_LENGTH=131072,
)
CSS = """
<style>
:root{font-family:system-ui,Tahoma,Arial,sans-serif;color-scheme:dark;background:#091522;color:#e9f0f7;--bg:#091522;--panel:#14273a;--line:#2c4359;--gold:#e8bf78;--muted:#aebed0}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(circle at 85% 0%,#17334a 0%,var(--bg) 55%);line-height:1.65}
a{color:var(--gold);text-decoration:none}a:hover{text-decoration:underline}
.app-shell{min-height:100vh;display:grid;grid-template-columns:260px minmax(0,1fr)}
.sidebar{position:sticky;top:0;height:100vh;overflow-y:auto;background:#0e2032;border-left:1px solid var(--line);padding:26px 16px;display:flex;flex-direction:column;gap:26px}
.brand{display:flex;align-items:center;gap:12px;padding:4px 9px 22px;border-bottom:1px solid var(--line)}
.brand-mark{display:grid;place-items:center;width:44px;height:44px;flex-shrink:0;border:1px solid #a7844c;border-radius:13px;background:#2c302e;color:#f5d59a;font-weight:900;font-size:24px}
.brand strong{display:block;color:#f5d59a;font-size:18px;line-height:1.3;letter-spacing:.6px}
.brand small{display:block;color:var(--muted);font-size:11px}
.side-label{font-size:11px;color:#91a9bc;padding:0 13px;margin-bottom:8px;letter-spacing:.4px}
.side-nav{display:flex;flex-direction:column;gap:6px}
.side-nav a{display:flex;align-items:center;gap:10px;padding:11px 14px;border-radius:10px;color:#d3e0eb;font-size:14px;font-weight:600}
.side-nav a:hover,.side-nav a.active{background:#243b50;color:#f5d59a;text-decoration:none}
.side-nav .nav-symbol{width:24px;text-align:center;font-size:18px}
.sidebar-footer{margin-top:auto;border-top:1px solid var(--line);padding:16px 8px 0}
.profile-name{font-weight:700;overflow-wrap:anywhere}
.profile-role{font-size:12px;color:var(--muted);margin:3px 0 12px}
.sidebar-footer form{margin:0}
.sidebar-footer button{width:100%;background:#253a4d;color:#f3d9ab;border:1px solid #456078}
.workspace{min-width:0}
.topbar{min-height:78px;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:16px clamp(18px,3vw,40px);border-bottom:1px solid var(--line);background:#102235aa}
.topbar h1{margin:0;font-size:clamp(17px,2vw,23px);color:#f3d19a}
.topbar p{margin:3px 0 0;color:var(--muted);font-size:12px}
.workspace-tag{font-size:12px;color:#b8c9d8;border:1px solid #36536b;border-radius:999px;padding:5px 12px;white-space:nowrap}
main{max-width:1500px;margin:0 auto;padding:clamp(16px,2.5vw,36px);min-width:0}
h2{font-size:20px;color:#f2d39b;margin:0 0 18px}h3{font-size:16px;margin:0 0 10px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:clamp(16px,2vw,24px);margin:0 0 18px;box-shadow:0 10px 28px #0002;min-width:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:16px}
label{display:block;margin:0 0 16px;font-size:14px;color:#c8d7e6}
input,select,textarea{display:block;width:100%;min-height:44px;margin-top:7px;padding:11px 13px;border-radius:9px;border:1px solid #49657e;background:#091c2e;color:#fff;font:inherit}
textarea{min-height:95px;resize:vertical}
input:focus-visible,select:focus-visible,textarea:focus-visible,button:focus-visible,a:focus-visible{outline:2px solid #f2cc85;outline-offset:2px}
button{width:auto;min-height:42px;padding:10px 20px;background:var(--gold);color:#10243b;border:0;border-radius:9px;font:inherit;font-weight:700;cursor:pointer}
button:hover{filter:brightness(1.09)}
form{max-width:100%}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;min-width:620px}
td,th{text-align:right;padding:13px;border-bottom:1px solid #29465d;font-size:14px}
th{color:#e8c688;background:#1c3951}
tbody tr:hover{background:#20394d}
.muted{color:var(--muted)}.error{color:#ffc6a9}strong{font-size:19px}
.flash{border-color:#7e6945;background:#2a2c2b}
input[type="hidden"],input[hidden],#nav-open{display:none!important}\n.mobile-toggle,.mobile-backdrop{display:none}
.auth-shell{min-height:100vh;display:grid;place-items:center;padding:20px}
.auth-container{width:min(100%,450px)}
.auth-brand{justify-content:center;border:0;padding-bottom:25px}
.auth-container .panel{padding:clamp(24px,4vw,38px)}
.auth-container .panel h2{text-align:center;margin-bottom:24px}
.auth-container .panel form button{width:100%}
@media(max-width:980px){
.app-shell{grid-template-columns:minmax(0,1fr)}
.sidebar{position:fixed;z-index:30;right:0;top:0;width:min(300px,85vw);height:100dvh;transform:translateX(105%);transition:transform .2s ease;box-shadow:-10px 0 30px #0005}
.mobile-toggle{display:inline-flex;align-items:center;justify-content:center;min-width:42px;height:42px;padding:0 10px;background:#20384b;color:#f5d59a;border:1px solid #456078;font-size:23px}
.mobile-backdrop{display:block;position:fixed;inset:0;z-index:20;background:#0009;opacity:0;pointer-events:none;transition:opacity .2s}
#nav-open:checked~.app-shell .sidebar{transform:translateX(0)}
#nav-open:checked~.app-shell .mobile-backdrop{opacity:1;pointer-events:auto}
}
@media(max-width:600px){
.topbar{padding:13px 14px;min-height:66px}.workspace-tag{display:none}
main{padding:14px}.panel{margin-bottom:12px}.grid{gap:10px}table{min-width:550px}
}
@media(prefers-reduced-motion:reduce){.sidebar,.mobile-backdrop{transition:none}}
.container-item{border:1px solid #456078;border-radius:12px;margin:14px 0;padding:16px;min-width:0}.container-item legend{color:#f2d39b;font-weight:700}.bundle-actions{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}.bundle-actions button,.remove-container{background:#273e51;color:#f2d39b;border:1px solid #4a6276}.bundle-save{width:100%;max-width:420px}</style>
"""
BASE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PORTEXA Operations</title>""" + CSS + """</head><body>
{% if user %}
<input type="checkbox" id="nav-open" hidden aria-label="فتح القائمة الجانبية">
<div class="app-shell">
<label for="nav-open" class="mobile-backdrop" aria-label="إغلاق القائمة"></label>
<aside class="sidebar" aria-label="القائمة الرئيسية">
  <div class="brand"><span class="brand-mark">P</span><div><strong>PORTEXA</strong><small>OPERATIONS CENTER</small></div></div>
  <div><div class="side-label">مساحة العمل</div><nav class="side-nav" aria-label="التنقل">
    <a href="{{ url_for('home') }}" class="{{ 'active' if request.path == url_for('home') else '' }}"><span class="nav-symbol" aria-hidden="true">▦</span>{{ 'مهامي وشحناتي' if user.role == 'operator' else 'لوحة التحكم' }}</a>
    {% if user.role in ('admin','supervisor') %}<a href="{{ url_for('users_page') }}" class="{{ 'active' if request.path.startswith('/users') else '' }}"><span class="nav-symbol" aria-hidden="true">♙</span>إدارة المستخدمين</a>{% endif %}\n    <a href="{{ url_for('notifications') }}" class="{{ 'active' if request.path == url_for('notifications') else '' }}"><span class="nav-symbol" aria-hidden="true">◉</span>الإشعارات</a>
    {% if user.role in ('admin','supervisor') %}
    <a href="{{ url_for('distribution_preview_page') }}" class="{{ 'active' if request.path == url_for('distribution_preview_page') else '' }}"><span class="nav-symbol" aria-hidden="true">⇄</span>معاينة التوزيع</a>
    <a href="{{ url_for('source_statuses_page') }}" class="{{ 'active' if request.path == url_for('source_statuses_page') else '' }}"><span class="nav-symbol" aria-hidden="true">≋</span>حالات الشحنات</a>
    {% endif %}
  </nav></div>
  <div class="sidebar-footer">
    <div class="profile-name">{{ user.full_name }}</div>
    <div class="profile-role">{{ {'admin':'مدير النظام','supervisor':'مشرف','operator':'موظف تتبع'}.get(user.role,user.role) }}</div>
    <form method="post" action="{{ url_for('logout') }}"><input type="hidden" name="csrf" value="{{ csrf }}"><button type="submit">تسجيل الخروج</button></form>
  </div>
</aside>
<div class="workspace">
  <div class="topbar"><div style="display:flex;align-items:center;gap:12px"><label for="nav-open" class="mobile-toggle" role="button" tabindex="0" aria-label="فتح القائمة">☰</label><div><h1>{{ 'إدارة مهام التتبع' if user.role == 'operator' else 'مركز إدارة العمليات' }}</h1><p>PORTEXA / Operations</p></div></div></div>
  <main>{% for msg in get_flashed_messages() %}<p class="panel flash" role="status">{{ msg }}</p>{% endfor %}{{ body|safe }}</main>
</div></div>
{% else %}
<div class="auth-shell"><div class="auth-container">
<div class="brand auth-brand"><span class="brand-mark">P</span><div><strong>PORTEXA</strong><small>OPERATIONS CENTER</small></div></div>
<main>{% for msg in get_flashed_messages() %}<p class="panel flash" role="status">{{ msg }}</p>{% endfor %}{{ body|safe }}</main>
</div></div>
{% endif %}
<script>document.addEventListener('click',function(e){const add=e.target.closest('.add-container'),remove=e.target.closest('.remove-container'),copy=e.target.closest('.copy-movement');if(!add&&!remove&&!copy)return;const form=e.target.closest('.shipment-bundle');if(!form)return;const list=form.querySelector('.container-list');if(add){list.append(form.querySelector('.container-template').content.cloneNode(true));return;}if(remove){remove.closest('.container-item').remove();return;}if(copy){const items=[...list.querySelectorAll('.container-item')];if(items.length<2)return;if(!window.confirm('هل تأكدت أن جميع الحاويات في هذه البوليصة تتحرك معاً؟ سيتم استبدال حقول الحركة للحاويات الأخرى بحركة الحاوية الأولى، دون تغيير أرقامها.'))return;for(const item of items.slice(1)){for(const source of items[0].querySelectorAll('[name^="move_"]')){const dest=item.querySelector('[name="'+source.name+'"]');if(dest)dest.value=source.value;}}}});</script></body></html>"""

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


ROUTE_LABELS = (
    ("loading_port","ميناء الشحن"),("current_port","الميناء الحالي"),
    ("next_port","الميناء القادم"),("discharge_port","ميناء الوجهة / التفريغ"),
    ("transshipment_port","ميناء الترانزيت"),("vessel_name","اسم السفينة"),
    ("voyage_number","رقم الرحلة"),("container_number","رقم الحاوية"),
    ("etd","تاريخ المغادرة المتوقع"),("eta","تاريخ الوصول المتوقع"),
    ("current_status","حالة المسار"),("operator_notes","ملاحظات الموظف")
)


def date_input_value(value):
    """Normalize legacy display values for native HTML date pickers."""
    from datetime import datetime, date
    if not value:
        return ""
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        pass
    try:
        return datetime.strptime(raw, "%d-%m-%Y").date().isoformat()
    except ValueError:
        return ""


app.add_template_filter(date_input_value, "date_input_value")


def snapshot_signer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="portexa-shipment-edit-v1")


def snapshot_token(task_id, shipment):
    return snapshot_signer().dumps({"task_id":task_id, "values":{k:shipment[k] for k in edit.FIELDS}})


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
            for task in tasks:
                try:
                    task["shipment"] = get_source_shipment(task["shipment_id"])
                    task["source_error"] = None
                    task["can_edit"] = False
                    task["route"] = routes.get_routes([task["shipment_id"]]).get(task["shipment_id"]) if task["shipment"] else None
                    task["containers"] = read_container_movements(task["shipment_id"]) if task["shipment"] else []
                    task["movement_history"] = read_container_history(task["shipment_id"], limit=30) if task["shipment"] else []
                    if task["shipment"] and task["status"] in ("pending", "rejected"):
                        edit.authorized_task(conn, u["id"], task["id"])
                        task["can_edit"] = True
                        task["snapshot"] = snapshot_token(task["id"], task["shipment"])
                        task["bundle_snapshot"] = snapshot_signer().dumps({"task_id":task["id"],"values":{k:task["shipment"][k] for k in edit.FIELDS},"containers":task["containers"]})
                except (OSError, ValueError, RuntimeError, PermissionError) as exc:
                    task["shipment"] = None
                    task["source_error"] = "تعذر قراءة بيانات الشحنة"
            bol_filter = request.args.get("bol_filter", "with")
            if bol_filter not in ("all", "with", "without"):
                bol_filter = "with"
            all_count = len(tasks)
            with_bol = sum(1 for t in tasks if t.get("shipment") and valid_bill_of_lading(t["shipment"].get("bol_no")))
            without_bol = sum(1 for t in tasks if t.get("shipment") and not valid_bill_of_lading(t["shipment"].get("bol_no")))
            if bol_filter == "with":
                tasks = [t for t in tasks if t.get("shipment") and valid_bill_of_lading(t["shipment"].get("bol_no"))]
            elif bol_filter == "without":
                tasks = [t for t in tasks if t.get("shipment") and not valid_bill_of_lading(t["shipment"].get("bol_no"))]
            return page("""<section class="panel"><h2>البوالص المسندة إليّ</h2>
<form method="get" action="{{ url_for('home') }}">
<label>فلترة الشحنات حسب رقم بوليصة الشحن
<select name="bol_filter" onchange="this.form.submit()">
<option value="all" {% if bol_filter == 'all' %}selected{% endif %}>جميع الشحنات ({{ all_count }})</option>
<option value="with" {% if bol_filter == 'with' %}selected{% endif %}>تحتوي على بوليصة ({{ with_bol }})</option>
<option value="without" {% if bol_filter == 'without' %}selected{% endif %}>بدون بوليصة ({{ without_bol }})</option>
</select></label>
<noscript><button type="submit">تطبيق الفلتر</button></noscript>
</form>
<p>عدد المهام المعروضة: {{ tasks|length }}</p>
<p class="muted">الشحنات التي تعذرت قراءتها لا تُحسب ضمن فلتر وجود البوليصة.</p>
<p class="muted">التعديل يحفظ بيانات الشحنة مباشرة عند تفعيل الكتابة، واعتماد المراجعة والأجر إجراء منفصل.</p></section>
{% for t in tasks %}<section class="panel"><h3>شحنة #{{ t.shipment_id }}</h3>
<p>الدورة: {{ t.cycle_at }} · الحالة: {{ t.status }} · الأجر: {{ t.price_iqd }} د.ع</p>
{% if t.shipment %}
<div class="grid">
<div><label>رقم الفاتورة (للعرض)</label><p>{{ t.shipment.invoice_no or 'غير مسجل' }}</p></div>
<div><label>حالة الشحنة (للعرض)</label><p>{{ t.shipment.status or 'غير محددة' }}</p></div>
</div>
{% if t.can_edit %}
<form method="post" action="{{ url_for('save_shipment_bundle_page',task_id=t.id) }}" class="shipment-bundle">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="snapshot" value="{{ t.bundle_snapshot }}">
<h3>معلومات الشحنة</h3><div class="grid">
<label>رقم البوليصة<input name="bol_no" maxlength="120" value="{{ t.shipment.bol_no or '' }}"></label>
<label>رقم التتبع<input name="msc_tracking" maxlength="300" value="{{ t.shipment.msc_tracking or '' }}"></label>
<label>تاريخ الشحن<input type="date" name="ship_date" value="{{ t.shipment.ship_date|date_input_value }}"></label>
<label>تاريخ الوصول<input type="date" name="arrival_date" value="{{ t.shipment.arrival_date|date_input_value }}"></label></div>
<label>تفاصيل الشحن<textarea name="shipping_details" maxlength="2000">{{ t.shipment.shipping_details or '' }}</textarea></label>
<h3>الحاويات وحركة النقل</h3><p class="muted">البوليصة تجمع الحاويات ولا تفرض مساراً موحداً. للحاوية الواحدة حدّث سجلها فقط؛ ولعدة حاويات أدخل حركة كل واحدة مستقلة، أو انسخ حركة الأولى للبقية فقط إذا تأكدت أنها تتحرك معاً. النسخ لا يغيّر أرقام الحاويات.</p><div class="container-list">
{% for c in t.containers %}<fieldset class="container-item"><legend>حاوية {{ loop.index }}</legend>
<div class="grid"><label>رقم الحاوية<input name="container_number" value="{{ c.container_number }}" required maxlength="30"></label>
{% for key,label in movement_labels %}<label>{{ label }}<input {% if key in ('etd','eta') %}type="date" value="{{ c[key]|date_input_value }}"{% else %}type="text" value="{{ c[key] or '' }}" maxlength="{{ 1500 if key=='operator_notes' else 200 }}"{% endif %} name="move_{{ key }}"></label>{% endfor %}
</div><button type="button" class="remove-container">حذف الحاوية</button></fieldset>{% endfor %}</div>
<template class="container-template"><fieldset class="container-item"><legend>حاوية جديدة</legend><div class="grid">
<label>رقم الحاوية<input name="container_number" maxlength="30" required></label>
{% for key,label in movement_labels %}<label>{{ label }}<input {% if key in ('etd','eta') %}type="date"{% else %}type="text" maxlength="{{ 1500 if key=='operator_notes' else 200 }}"{% endif %} name="move_{{ key }}"></label>{% endfor %}
</div><button type="button" class="remove-container">حذف الحاوية</button></fieldset></template>
<div class="bundle-actions"><button type="button" class="add-container">+ إضافة حاوية</button><button type="button" class="copy-movement">تحديث جماعي: نسخ حركة الأولى للبقية</button></div>
<button type="submit" class="bundle-save">حفظ جميع التحديثات في PORTEXA</button></form>
<details class="panel"><summary>سجل تغييرات الحاويات (آخر 30 حدثاً)</summary>
{% if t.movement_history %}
<div class="table-wrap"><table><thead><tr><th>الوقت (UTC)</th><th>الحاوية</th><th>الإجراء</th><th>رقم الموظف</th><th>التغييرات</th></tr></thead><tbody>
{% for event in t.movement_history %}
<tr><td>{{ event.recorded_at }}</td><td>{{ event.container_number }}</td>
<td>{{ {'created':'إضافة','updated':'تعديل','removed':'حذف'}.get(event.action,event.action) }}</td>
<td>{{ event.actor_id if event.actor_id is not none else '—' }}</td>
<td><details><summary>عرض التفاصيل</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">{{ event.previous_state or '—' }}
→
{{ event.new_state or '—' }}</pre></details></td></tr>
{% endfor %}</tbody></table></div>
{% else %}<p class="muted">لا يوجد تاريخ مسجل بعد؛ يبدأ التسجيل مع عمليات الحفظ الجديدة.</p>{% endif %}
</details>
{% else %}
<div class="grid">
<div>البوليصة: {{ t.shipment.bol_no or '—' }}</div>
<div>التتبع: {{ t.shipment.msc_tracking or '—' }}</div>
<div>تاريخ الشحن: {{ t.shipment.ship_date or '—' }}</div>
<div>تاريخ الوصول: {{ t.shipment.arrival_date or '—' }}</div></div>
<p>ميناء الشحن: {{ t.route.loading_port if t.route and t.route.loading_port else 'غير مسجل' }} · ميناء الوجهة: {{ t.route.discharge_port if t.route and t.route.discharge_port else 'غير مسجل' }}</p>
<p style="white-space:pre-wrap;overflow-wrap:anywhere">{{ t.shipment.shipping_details or 'لا توجد تفاصيل' }}</p>
{% endif %}
{% else %}<p class="error">{{ t.source_error or 'الشحنة غير موجودة في المصدر' }}</p>{% endif %}
{% if t.status in ('pending','rejected') %}
<form method="post" action="{{ url_for('submit',task_id=t.id) }}">
<input type="hidden" name="csrf" value="{{ csrf }}">
<label>مصدر المراجعة<input name="source" required maxlength="500"></label>
<label>ملاحظات المراجعة<textarea name="note" required maxlength="2000"></textarea></label>
<label>نتيجة المراجعة<select name="changed">
<option value="0">تم التحقق — لا يوجد تغيير</option>
<option value="1">تم تحديث بيانات الشحنة</option></select></label>
<button>إرسال للمراجعة</button></form>{% endif %}</section>
{% else %}<section class="panel">لا توجد مهام حالياً</section>{% endfor %}""",tasks=tasks,route_labels=ROUTE_LABELS,movement_labels=[(k,v) for k,v in ROUTE_LABELS if k!="container_number"],bol_filter=bol_filter,all_count=all_count,with_bol=with_bol,without_bol=without_bol)
        # Audit active assignments against the current read-only Portexa source.
        from portexa_tracking.portexa_bridge import source_connection, check_schema
        with source_connection() as source:
            check_schema(source)
            valid_source_ids = {int(row["id"]) for row in source.execute(
                "SELECT id,bol_no FROM shipment"
            ) if valid_bill_of_lading(row["bol_no"])}
        active_assignments = [dict(row) for row in conn.execute(
            "SELECT shipment_id,user_id FROM ops_assignments WHERE active=1"
        )]
        valid_assigned_ids = {int(row["shipment_id"]) for row in active_assignments
                              if int(row["shipment_id"]) in valid_source_ids}
        invalid_assignment_count = len(active_assignments) - sum(
            int(row["shipment_id"]) in valid_source_ids for row in active_assignments
        )
        unassigned_valid_count = len(valid_source_ids - valid_assigned_ids)
        stats = ops.dashboard_stats(conn, u["id"])
        stats["assignments"] = len(valid_assigned_ids)
        stats["invalid_assignments"] = invalid_assignment_count
        stats["unassigned_valid"] = unassigned_valid_count
        users = [dict(r) for r in conn.execute("SELECT id,full_name,phone,username,role,active FROM ops_users ORDER BY id")]
        assignments = [a for a in dist.assignment_history(conn) if a["active"] and int(a["shipment_id"]) in valid_source_ids]
        pending = [dict(r) for r in conn.execute("SELECT t.id,t.shipment_id,t.user_id,t.review_note,t.evidence_source,t.route_changed FROM ops_tasks t WHERE t.status='submitted' ORDER BY t.id LIMIT 100")]
        return page("""<div class="grid">
<section class="panel"><h3>المستخدمون</h3><strong>{{ stats.users }}</strong></section>
<section class="panel"><h3>البوالص المسندة الصالحة</h3><strong>{{ stats.assignments }}</strong></section>
<section class="panel"><h3>بوالص صالحة غير مسندة</h3><strong>{{ stats.unassigned_valid }}</strong></section>\n<section class="panel"><h3>إسنادات قديمة أو غير صالحة</h3><strong>{{ stats.invalid_assignments }}</strong></section>\n<section class="panel"><h3>نسبة الاعتماد</h3><strong>{{ stats.approval_rate }}%</strong></section>
<section class="panel"><h3>بانتظار المراجعة</h3><strong>{{ stats.tasks.get('submitted',0) }}</strong></section></div>
<section class="panel"><h2>إدارة المستخدمين</h2><p class="muted">عرض حسابات الموظفين وتفاصيلهم وتعديلها من صفحة مستقلة.</p><a href="{{ url_for('users_page') }}">فتح إدارة المستخدمين</a></section>
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


@app.post("/tasks/<int:task_id>/bundle/save")
@require("operator")
def save_shipment_bundle_page(task_id):
    try:
        signed = snapshot_signer().loads(request.form.get("snapshot", ""), max_age=3600)
        if not isinstance(signed, dict) or signed.get("task_id") != task_id:
            abort(403)
        numbers = request.form.getlist("container_number")
        movements = {k: request.form.getlist("move_" + k) for k in MOVEMENT_FIELDS}
        if any(len(items) != len(numbers) for items in movements.values()):
            raise ValueError("حقول الحاويات غير متطابقة")
        containers = [dict(container_number=number, **{k: movements[k][i] for k in MOVEMENT_FIELDS})
                      for i, number in enumerate(numbers)]
        with db() as conn:
            task = edit.authorized_task(conn, current_user()["id"], task_id)
            values = {k: request.form.get(k, "") for k in edit.FIELDS}
            changed = save_shipment_bundle(task["shipment_id"], values, signed["values"],
                                           containers, signed["containers"], actor_id=current_user()["id"])
            with conn:
                ops.audit(conn, current_user()["id"], "shipment.bundle.saved", "shipment",
                          task["shipment_id"], "changed=" + str(bool(changed)) +
                          ";containers=" + str(len(containers)))
        flash("تم حفظ معلومات الشحنة والحاويات مباشرة في PORTEXA" if changed else "لا توجد تغييرات للحفظ")
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        flash("انتهت صلاحية النموذج؛ حدّث الصفحة")
    except (ValueError, LookupError, PermissionError, RuntimeError, OSError) as exc:
        flash("تعذر حفظ التحديثات: " + str(exc))
    return redirect(url_for("home"))


@app.post("/tasks/<int:task_id>/route/save")
@require("operator")
def save_shipment_route(task_id):
    try:
        signed = snapshot_signer().loads(request.form.get("route_snapshot", ""), max_age=3600)
        if not isinstance(signed, dict) or signed.get("task_id") != task_id:
            abort(403)
        with db() as conn:
            task = edit.authorized_task(conn, current_user()["id"], task_id)
            values = {key:request.form.get(key, "") for key in routes.FIELDS}
            saved = routes.save_route(task["shipment_id"], values, signed.get("updated_at"))
            with conn:
                ops.audit(conn, current_user()["id"], "shipment.route.updated", "shipment",
                          task["shipment_id"], "updated_at="+saved["updated_at"])
        flash("تم حفظ الموانئ ومسار الشحنة")
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        flash("انتهت صلاحية النموذج؛ حدّث الصفحة")
    except (ValueError, LookupError, PermissionError, RuntimeError, OSError) as exc:
        flash("تعذر حفظ المسار: " + str(exc))
    return redirect(url_for("home"))


@app.post("/tasks/<int:task_id>/shipment/save")
@require("operator")
def save_shipment_edit(task_id):
    try:
        signed = snapshot_signer().loads(request.form.get("snapshot", ""), max_age=3600)
        if not isinstance(signed, dict) or signed.get("task_id") != task_id:
            abort(403)
        values = {key:request.form.get(key, "") for key in edit.FIELDS}

        with db() as conn:
            result = edit.save_shipment(conn, current_user()["id"], task_id, values, signed["values"])
        flash("تم حفظ التحديث في PORTEXA" if result["changed"] else "لا توجد تغييرات للحفظ")
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        flash("انتهت صلاحية النموذج؛ حدّث الصفحة وحاول مجدداً")
    except (ValueError, LookupError, PermissionError, RuntimeError, OSError) as exc:
        flash("تعذر حفظ بيانات الشحنة: " + str(exc))
    return redirect(url_for("home"))


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


@app.get("/users")
@require("admin", "supervisor")
def users_page():
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT u.id,u.full_name,u.phone,u.username,u.role,u.active,u.created_at,
               (SELECT COUNT(*) FROM ops_assignments a WHERE a.user_id=u.id AND a.active=1) assigned,
               (SELECT COUNT(*) FROM ops_tasks t WHERE t.user_id=u.id) tasks
               FROM ops_users u ORDER BY u.active DESC,u.id DESC"""
        )]
    return page("""<section class="panel"><h2>إدارة المستخدمين</h2>
<p class="muted">الحسابات المسجلة: {{ rows|length }} · النشطة: {{ rows|selectattr('active')|list|length }}</p>
<div class="scroll"><table><thead><tr><th>الموظف</th><th>اسم الدخول</th><th>الصلاحية</th><th>الحالة</th><th>الشحنات</th><th>المهام</th><th>الإجراءات</th></tr></thead><tbody>
{% for x in rows %}<tr><td><b>{{ x.full_name }}</b><div class="muted">{{ x.phone }}</div></td>
<td>{{ x.username }}</td><td>{{ {'admin':'مدير','supervisor':'مشرف','operator':'موظف'}.get(x.role,x.role) }}</td>
<td>{{ 'نشط' if x.active else 'موقوف' }}</td><td>{{ x.assigned }}</td><td>{{ x.tasks }}</td>
<td><a href="{{ url_for('user_details',user_id=x.id) }}">التفاصيل</a> · <a href="{{ url_for('user_payroll',user_id=x.id) }}">المستحقات</a></td></tr>
{% else %}<tr><td colspan="7">لا يوجد مستخدمون</td></tr>{% endfor %}
</tbody></table></div></section>
{% if user.role=='admin' %}<section class="panel"><h2>إضافة مستخدم جديد</h2>
<form method="post" action="{{ url_for('add_user') }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<div class="grid"><label>الاسم الكامل<input name="name" maxlength="120" required></label>
<label>الهاتف<input name="phone" maxlength="40" required></label>
<label>اسم المستخدم<input name="username" maxlength="80" required></label>
<label>الصلاحية<select name="role"><option value="operator">موظف</option><option value="supervisor">مشرف</option></select></label></div>
<label>كلمة المرور الأولية<input type="password" id="new-user-password" name="password" minlength="12" required autocomplete="new-password"></label>
<label><input type="checkbox" style="display:inline;width:auto;min-height:auto;margin:0 0 0 8px" onchange="document.getElementById('new-user-password').type=this.checked?'text':'password'">إظهار كلمة المرور المدخلة</label>
<button type="submit">إنشاء الحساب</button></form></section>{% endif %}""", rows=rows)


@app.get("/users/<int:user_id>")
@require("admin", "supervisor")
def user_details(user_id):
    with db() as conn:
        row = conn.execute(
            "SELECT id,full_name,phone,username,role,active,created_at FROM ops_users WHERE id=?",
            (user_id,)
        ).fetchone()
    if row is None:
        abort(404)
    return page("""<section class="panel"><p><a href="{{ url_for('users_page') }}">العودة لإدارة المستخدمين</a></p>
<h2>تفاصيل المستخدم: {{ x.full_name }}</h2>
<div class="grid"><div><span class="muted">رقم الحساب</span><p>#{{ x.id }}</p></div>
<div><span class="muted">تاريخ الإنشاء</span><p>{{ x.created_at }}</p></div></div></section>
<section class="panel"><h2>معلومات الحساب</h2>
{% if user.role=='admin' %}
<form method="post" action="{{ url_for('save_user_details',user_id=x.id) }}"><input type="hidden" name="csrf" value="{{ csrf }}">
<div class="grid"><label>الاسم الكامل<input name="full_name" maxlength="120" value="{{ x.full_name }}" required></label>
<label>رقم الهاتف<input name="phone" maxlength="40" value="{{ x.phone }}" required></label>
<label>اسم الدخول<input name="username" maxlength="80" value="{{ x.username }}" required></label>
<label>الصلاحية<select name="role">{% for role,title in [('operator','موظف'),('supervisor','مشرف'),('admin','مدير')] %}
<option value="{{ role }}" {% if x.role==role %}selected{% endif %}>{{ title }}</option>{% endfor %}</select></label>
<label>حالة الحساب<select name="active"><option value="1" {% if x.active %}selected{% endif %}>نشط</option><option value="0" {% if not x.active %}selected{% endif %}>موقوف</option></select></label></div>
<button type="submit">حفظ التعديلات</button></form>
{% else %}<div class="grid"><p>الهاتف: {{ x.phone }}</p><p>اسم الدخول: {{ x.username }}</p><p>الصلاحية: {{ x.role }}</p><p>الحالة: {{ 'نشط' if x.active else 'موقوف' }}</p></div>{% endif %}
</section>
{% if user.role=='admin' %}<section class="panel"><h2>تغيير كلمة المرور</h2>
<p class="muted">لا يمكن عرض كلمة المرور القديمة لأنها محفوظة كتجزئة آمنة. يمكنك تعيين كلمة مرور جديدة.</p>
<form method="post" action="{{ url_for('change_user_password',user_id=x.id) }}">
<input type="hidden" name="csrf" value="{{ csrf }}">
<label>كلمة المرور الجديدة<input id="reset-password" type="password" name="password" minlength="12" required autocomplete="new-password"></label>
<label>تأكيد كلمة المرور<input id="reset-confirm" type="password" name="confirm" minlength="12" required autocomplete="new-password"></label>
<label><input type="checkbox" style="display:inline;width:auto;min-height:auto;margin:0 0 0 8px" onchange="document.getElementById('reset-password').type=this.checked?'text':'password';document.getElementById('reset-confirm').type=this.checked?'text':'password'">إظهار كلمة المرور الجديدة</label>
<button type="submit">تحديث كلمة المرور</button></form></section>{% endif %}
<section class="panel"><a href="{{ url_for('user_payroll',user_id=x.id) }}">عرض كشف المستحقات</a></section>""", x=dict(row))


@app.post("/users/<int:user_id>/edit")
@require("admin")
def save_user_details(user_id):
    try:
        active = request.form.get("active")
        if active not in ("0", "1"):
            raise ValueError("Invalid account status")
        with db() as conn:
            ops.update_user(conn, current_user()["id"], user_id,
                            request.form.get("full_name", ""), request.form.get("phone", ""),
                            request.form.get("username", ""), request.form.get("role", ""),
                            active == "1")
        flash("تم تحديث بيانات المستخدم")
    except (ValueError, LookupError, PermissionError) as exc:
        flash("تعذر تحديث المستخدم: " + str(exc))
    return redirect(url_for("user_details", user_id=user_id))


@app.post("/users/<int:user_id>/password")
@require("admin")
def change_user_password(user_id):
    password = request.form.get("password", "")
    if password != request.form.get("confirm", ""):
        flash("كلمتا المرور غير متطابقتين")
    else:
        try:
            with db() as conn:
                ops.reset_user_password(conn, current_user()["id"], user_id, password)
            flash("تم تغيير كلمة المرور بنجاح")
        except (ValueError, LookupError, PermissionError) as exc:
            flash("تعذر تغيير كلمة المرور: " + str(exc))
    return redirect(url_for("user_details", user_id=user_id))


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
    app.run(host=os.environ.get("PORTEXA_OPS_HOST", "127.0.0.1"),port=int(os.environ.get("PORTEXA_OPS_PORT","8766")),debug=False)


if __name__=="__main__":
    main()
