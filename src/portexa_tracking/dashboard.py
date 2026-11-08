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


HTML = "<!doctype html>\n<html lang=\"ar\" dir=\"rtl\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n<title>PORTEXA | Tracking Engine</title>\n<style>\n:root{font-family:Tahoma,Arial,sans-serif;color:#eaf2fa;background:#091728;color-scheme:dark}\n*{box-sizing:border-box}body{margin:0}header{padding:22px max(16px,4vw);background:#10263f;border-bottom:1px solid #29415a}\nh1{margin:0;color:#e6bf71;font-size:26px}small,.muted{color:#a9b9ca}main{max-width:1220px;margin:24px auto;padding:0 14px}\nsection{background:#122840;border:1px solid #29415a;border-radius:15px;padding:20px;margin-bottom:18px}\nh2{margin:0 0 16px;font-size:18px}.form{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}\ninput,select,button{font:inherit;border-radius:8px;padding:11px;min-width:0}input,select{background:#071c30;border:1px solid #45617c;color:#fff}\nbutton{border:0;background:#d9b46b;color:#132339;font-weight:700;cursor:pointer}button.secondary{background:#31516f;color:#fff}\nbutton:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #e6bf71;outline-offset:2px}\nlabel{display:flex;flex-direction:column;gap:6px;font-size:13px}.card{border:1px solid #35516c;border-radius:12px;margin:14px 0;padding:18px;background:#0c2034}\n.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}\n.tag{padding:5px 10px;border-radius:20px;background:#29445d;font-size:12px;display:inline-block}\n.error{background:#593236}.found{background:#225847}.planned{background:#344b70;color:#cfe0ff}.actual{background:#225847;color:#ddfff1}\n.meta{color:#b6c6d5;font-size:13px;line-height:1.9}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0}\n.stat{background:#102a43;border:1px solid #2d4b65;border-radius:10px;padding:12px;min-width:0}\n.stat small{display:block;margin-bottom:7px}.stat strong{display:block;font-size:14px;overflow-wrap:anywhere}\n.sort-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0}.sort-controls select{padding:7px}.vessel{margin-top:7px;color:#e6bf71}.timeline{border-top:1px solid #29445d;margin-top:16px;padding-top:12px}.event{position:relative;border-right:2px solid #49657e;padding:8px 20px 18px 8px;margin-right:7px}\n.event:before{content:'';position:absolute;right:-6px;top:15px;width:10px;height:10px;border-radius:50%;background:#d9b46b}\n.event.planned-event:before{background:#739ad2}.event-head{display:flex;align-items:center;gap:9px;flex-wrap:wrap}\n.event-title{font-size:14px;font-weight:700}.event-desc{font-size:13px;color:#b6c6d5;margin-top:7px;line-height:1.8}\n.ltr{direction:ltr;unicode-bidi:isolate;display:inline-block;text-align:left}#message{min-height:24px;color:#e6bf71}\n.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.details{margin-top:12px}.details summary{cursor:pointer;color:#e6bf71;font-weight:700}\n.source{margin-top:10px}.warning{color:#e8c782}\n@media(max-width:800px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.form{grid-template-columns:repeat(2,minmax(0,1fr))}}\n@media(max-width:520px){section{padding:14px}.grid,.form{grid-template-columns:1fr}.form button{grid-column:1/-1}.card{padding:12px}h1{font-size:22px}}\n</style></head><body>\n<header><h1>PORTEXA <small>Tracking Engine</small></h1><small>لوحة اختبار محلية مستقلة — غير متصلة بنظام الإنتاج</small></header>\n<main><section><h2>إضافة بوليصة أو حاوية</h2><form id=\"add\" class=\"form\">\n<label>شركة الشحن<select name=\"carrier\"><option value=\"CMA_CGM\">CMA CGM</option><option value=\"MSC\">MSC</option><option value=\"MAERSK\">Maersk</option><option value=\"HAPAG_LLOYD\">Hapag-Lloyd</option><option value=\"COSCO\">COSCO</option><option value=\"FIXTURE\">Fixture (اختبار)</option></select></label>\n<label>نوع المرجع<select name=\"type\"><option>BL</option><option>BOOKING</option><option>CONTAINER</option></select></label>\n<label>رقم المرجع<input name=\"reference\" required minlength=\"4\" maxlength=\"40\" placeholder=\"رقم البوليصة أو الحجز\"></label>\n<button type=\"submit\">إضافة للتتبع</button></form><p id=\"message\" role=\"status\" aria-live=\"polite\"></p></section>\n<section><div class=\"top\"><h2>الشحنات المسجلة</h2><div class=\"actions\"><button class=\"secondary\" id=\"refresh\">تحديث العرض</button><button id=\"run\">فحص شحنة مستحقة</button></div></div><div class=\"sort-controls\"><label for=\"order\">ترتيب الأحداث</label><select id=\"order\"><option value=\"asc\">من الأقدم إلى الأحدث</option><option value=\"desc\">من الأحدث إلى الأقدم</option></select></div><div id=\"shipments\">جاري التحميل...</div></section>\n<section><small>البيانات المستوردة يدوياً ليست تحديثاً مباشراً. الأحداث المخططة ليست دليلاً على وصول السفينة أو مغادرتها. فحص الشحنة المستحقة لا يفعّل اتصالاً آلياً مع شركات الشحن ما لم يتوفر موصل مصرح به.</small></section></main>\n<script>\nconst $=id=>document.getElementById(id);\nconst esc=x=>String(x??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;','\\u0027':'&#39;'}[c]));\nconst dir=x=>'<bdi class=\"ltr\">'+esc(x)+'</bdi>';\nfunction fmt(x){if(!x)return 'غير متوفر';const d=new Date(x);if(Number.isNaN(d.getTime()))return dir(x);return '<time class=\"ltr\" datetime=\"'+esc(x)+'\" title=\"'+esc(x)+'\">'+esc(new Intl.DateTimeFormat('ar-IQ',{year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'UTC',hour12:true}).format(d))+' UTC</time>'}\nfunction vesselInfo(desc){const m=String(desc||'').match(/(?: - )?(CMA CGM [A-Z0-9 -]+?)\\s*\\(([^)]+)\\)\\s*$/i);return m?'<div class=\"vessel\">السفينة: '+esc(m[1])+' · الرحلة: '+dir(m[2])+'</div>':''}\nfunction localEventTime(x){if(!x)return 'غير متوفر';const m=String(x).match(/^(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2})/);return m?dir(m[3]+'/'+m[2]+'/'+m[1]+' · '+m[4]+':'+m[5])+' <small>توقيت الميناء المحلي</small>':dir(x)}\nconst planned=e=>/PLANNED|ESTIMATED|EXPECTED|SCHEDULED/i.test(e.code);\nconst labels={READY_TO_LOAD:'جاهزة للتحميل',PLANNED_VESSEL_DEPARTURE:'مغادرة سفينة مخططة',PLANNED_VESSEL_ARRIVAL:'وصول سفينة مخطط',LOAD:'تحميل',DEPART:'مغادرة'};\nlet timelineOrder='asc';\nconst sourceLabel=s=>s==='operator_verified'?'متحقق منها يدوياً':s==='synthetic_fixture'?'بيانات تجريبية':s?'المصدر: '+s:'المصدر غير متوفر';\nasync function api(path,options){const r=await fetch(path,{credentials:'same-origin',...options});const data=await r.json();if(!r.ok)throw Error(data.error||'Request failed');return data}\nfunction render(s){\nconst ev=s.events||[], chronological=[...ev].sort((a,b)=>a.occurred_at.localeCompare(b.occurred_at)),first=chronological[0],last=chronological[chronological.length-1], actual=ev.filter(e=>!planned(e));\nconst heading=dir(s.carrier)+' — '+dir(s.reference);\nconst timeline=[...ev].sort((a,b)=>timelineOrder==='asc'?a.occurred_at.localeCompare(b.occurred_at):b.occurred_at.localeCompare(a.occurred_at)).map(e=>'<div class=\"event '+(planned(e)?'planned-event':'')+'\"><div class=\"event-head\"><span class=\"event-title\">'+esc(labels[e.code]||e.code)+'</span><span class=\"tag '+(planned(e)?'planned':'actual')+'\">'+(planned(e)?'مخطط':'غير مخطط')+'</span></div><div class=\"event-desc\">'+localEventTime(e.occurred_at)+'<div>'+esc(e.location)+'</div><div>'+esc(e.description)+'</div>'+vesselInfo(e.description)+(e.container?'<div>حاوية: '+dir(e.container)+'</div>':'')+'</div></div>').join('');\nreturn '<article class=\"card\"><div class=\"top\"><strong>'+heading+'</strong><span class=\"tag '+(s.latest_status==='found'?'found':s.latest_status==='temporary_error'?'error':'')+'\">'+esc(s.latest_status)+'</span></div>'+\n'<div class=\"grid\"><div class=\"stat\"><small>نوع المرجع</small><strong>'+esc(s.reference_type)+'</strong></div><div class=\"stat\"><small>أول موقع مسجل</small><strong>'+esc(first?.location||'غير متوفر')+'</strong></div><div class=\"stat\"><small>آخر موقع مجدول</small><strong>'+esc(last?.location||'غير متوفر')+'</strong></div><div class=\"stat\"><small>موعد الوصول المتوقع (محلي)</small><strong>'+(s.eta?localEventTime(s.eta):'غير متوفر')+'</strong></div></div>'+\n'<div class=\"meta\">آخر فحص: '+fmt(s.last_checked_at)+' · الفحص القادم: '+fmt(s.next_check_at)+'</div>'+\n'<div class=\"source\"><span class=\"tag\">'+esc(sourceLabel(s.source))+'</span> <span class=\"muted\">'+actual.length+' حدث غير مخطط · '+(ev.length-actual.length)+' مخطط</span></div>'+\n(s.checked_error?'<p class=\"warning\">'+esc(s.checked_error)+'</p>':'')+\n'<details class=\"details\" '+(ev.length?'open':'')+'><summary>الخط الزمني ('+ev.length+' أحداث)</summary><div class=\"timeline\">'+(timeline||'<p class=\"muted\">لا توجد أحداث</p>')+'</div></details></article>';\n}\nasync function refresh(){try{const rows=await api('/api/shipments');$('shipments').innerHTML=rows.length?rows.map(render).join(''):'لا توجد شحنات بعد'}catch(e){$('message').textContent=e.message}}\n$('add').addEventListener('submit',async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));try{await api('/api/shipments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});$('message').textContent='تم تسجيل المرجع';await refresh()}catch(e){$('message').textContent=e.message}});\n$('run').onclick=async()=>{try{const r=await api('/api/run-once',{method:'POST'});$('message').textContent=r?.status==='no_due_shipments'?'لا توجد شحنات مستحقة الآن':'نتيجة الفحص: '+r.status;await refresh()}catch(e){$('message').textContent=e.message}};\n$('order').onchange=e=>{timelineOrder=e.target.value;refresh()};$('refresh').onclick=refresh;refresh();\n</script></body></html>"



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
