"""Local-only operator dashboard. Use SSH port forwarding; never expose publicly."""
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from portexa_tracking.database import add_shipment, connect, initialize, list_shipments
from portexa_tracking.scheduler import run_once

HOST = "127.0.0.1"
PORT = 8765


def overview(conn):
    shipments = []
    for row in list_shipments(conn):
        item = dict(row)
        events = conn.execute(
            "SELECT code,occurred_at,location,description,container,first_seen_at "
            "FROM tracking_events WHERE shipment_id=? ORDER BY occurred_at DESC LIMIT 50",
            (item["id"],),
        ).fetchall()
        item["events"] = [dict(event) for event in events]
        shipments.append(item)
    return shipments


HTML = """<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PORTEXA | Tracking Engine</title>
<style>
:root{font-family:Tahoma,Arial,sans-serif;color:#e8efff;background:#091728}
*{box-sizing:border-box}body{margin:0}header{padding:22px max(20px,5vw);background:#10263f;border-bottom:1px solid #29415a}
h1{margin:0;color:#e6bf71;font-size:26px}small{color:#a9b9ca}
main{max-width:1150px;margin:25px auto;padding:0 16px}
section{background:#122840;border:1px solid #29415a;border-radius:15px;padding:20px;margin-bottom:20px}
h2{margin-top:0;font-size:18px}.form{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
input,select,button{font:inherit;border-radius:8px;padding:12px;min-width:0}
input,select{background:#071c30;border:1px solid #45617c;color:#fff}
button{border:0;background:#d9b46b;color:#132339;font-weight:700;cursor:pointer}
button.secondary{background:#31516f;color:#fff}
label{display:flex;flex-direction:column;gap:6px;font-size:13px}
.card{border:1px solid #35516c;border-radius:12px;margin:12px 0;padding:16px;background:#0c2034}
.top{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.tag{padding:5px 10px;border-radius:20px;background:#29445d;font-size:12px}
.error{background:#593236}.found{background:#225847}
.meta{color:#b6c6d5;font-size:12px;line-height:1.9}
.events{border-top:1px solid #29445d;margin-top:12px;padding-top:10px}
.event{padding:8px;border-bottom:1px solid #243b52;font-size:13px}
#message{min-height:24px;color:#e6bf71}.actions{display:flex;gap:8px;align-items:center}
@media(max-width:700px){.form{grid-template-columns:1fr 1fr}.form button{grid-column:1/-1}}
@media(max-width:450px){.form{grid-template-columns:1fr}}
</style></head><body>
<header><h1>PORTEXA <small>Tracking Engine</small></h1>
<small>لوحة اختبار محلية مستقلة — ليست متصلة بنظام الإنتاج</small></header>
<main>
<section><h2>إضافة بوليصة أو حاوية</h2>
<form id="add" class="form">
<label>شركة الشحن<select name="carrier"><option value="CMA_CGM">CMA CGM</option>
<option value="MSC">MSC</option><option value="MAERSK">Maersk</option>
<option value="HAPAG_LLOYD">Hapag-Lloyd</option><option value="COSCO">COSCO</option>
<option value="FIXTURE">Fixture (اختبار)</option></select></label>
<label>نوع المرجع<select name="type"><option>BL</option><option>BOOKING</option><option>CONTAINER</option></select></label>
<label>رقم المرجع<input name="reference" required minlength="4" maxlength="40" placeholder="رقم البوليصة"></label>
<button type="submit">إضافة للتتبع</button></form><p id="message" role="status"></p></section>
<section><div class="top"><h2>الشحنات المسجلة</h2><div class="actions">
<button class="secondary" id="refresh">تحديث العرض</button>
<button id="run">فحص شحنة مستحقة</button></div></div>
<div id="shipments">جاري التحميل...</div></section>
<section><small>البيانات التجريبية مميزة بالمصدر synthetic_fixture. ظهور unavailable يعني أن موصل الشركة لم يُربط بعد بمصدر مصرح به. لا توجد بيانات مواقع حية حالياً.</small></section>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,options){const r=await fetch(path,{credentials:'same-origin',...options});
const data=await r.json();if(!r.ok)throw Error(data.error||'Request failed');return data}
async function refresh(){try{const rows=await api('/api/shipments');$('shipments').innerHTML=rows.length?rows.map(s=>{
const events=s.events.map(e=>'<div class="event"><b>'+esc(e.code)+'</b> — '+esc(e.occurred_at)+'<div>'+esc(e.location)+' · '+esc(e.description)+'</div></div>').join('');
return '<article class="card"><div class="top"><strong>'+esc(s.carrier)+' — '+esc(s.reference)+'</strong><span class="tag '+(s.latest_status==='found'?'found':s.latest_status==='temporary_error'?'error':'')+'">'+esc(s.latest_status)+'</span></div>'+
'<div class="meta">النوع: '+esc(s.reference_type)+' | آخر فحص: '+esc(s.last_checked_at||'لم يفحص')+
'<br>الفحص القادم: '+esc(s.next_check_at)+' | ETA: '+esc(s.eta||'غير متوفر')+
'<br>المصدر: '+esc(s.source||'غير متوفر')+' | '+esc(s.checked_error||'')+'</div>'+
(events?'<div class="events">'+events+'</div>':'')+'</article>'}).join(''):'لا توجد شحنات بعد';
}catch(e){$('message').textContent=e.message}}
$('add').addEventListener('submit',async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));try{await api('/api/shipments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});$('message').textContent='تم تسجيل المرجع';await refresh()}catch(e){$('message').textContent=e.message}});
$('run').onclick=async()=>{try{const r=await api('/api/run-once',{method:'POST'});$('message').textContent=r?.status==='no_due_shipments'?'لا توجد شحنات مستحقة الآن':'نتيجة الفحص: '+r.status;await refresh()}catch(e){$('message').textContent=e.message}};
$('refresh').onclick=refresh;refresh();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, payload):
        self._send(status, payload)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            return self._send(200, HTML, "text/html; charset=utf-8")
        if path == "/api/shipments":
            with connect() as conn:
                initialize(conn)
                return self._json(200, overview(conn))
        self._json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        # Same-origin requests only. This prevents drive-by browser POSTs over SSH tunnels.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if not origin or urlsplit(origin).netloc != host or urlsplit(origin).scheme != "http":
            return self._json(403, {"error": "Same-origin request required"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 4096 or size < 0:
                return self._json(413, {"error": "Invalid request size"})
            with connect() as conn:
                initialize(conn)
                if path == "/api/shipments":
                    data = json.loads(self.rfile.read(size))
                    row = add_shipment(conn, data["carrier"], data["reference"], data["type"])
                    return self._json(201, dict(row))
                if path == "/api/run-once":
                    result = run_once(conn)
                    return self._json(200, result or {"status": "no_due_shipments"})
            self._json(404, {"error": "Not found"})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})


def main():
    port = int(os.environ.get("PORTEXA_TRACKING_PORT", PORT))
    if not 1024 <= port <= 65535:
        raise SystemExit("PORTEXA_TRACKING_PORT must be between 1024 and 65535")
    print(f"PORTEXA Tracking Dashboard: http://{HOST}:{port}")
    print("Localhost only. Access remotely using SSH -L 8765:127.0.0.1:8765.")
    ThreadingHTTPServer((HOST, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
