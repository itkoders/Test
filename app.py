import os, sqlite3, secrets, string, json, hashlib, urllib.request, urllib.error, io
from datetime import datetime
from functools import wraps
from flask import Flask, request, redirect, url_for, session, flash, render_template_string, abort, send_file, Response
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-before-production")
file_signer = URLSafeTimedSerializer(app.secret_key, salt="neuron-test-file-v1")
app.config["MAX_CONTENT_LENGTH"] = 18 * 1024 * 1024
DB = os.environ.get("DATABASE_PATH", "mirzayev_group.db")
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS tests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL, duration INTEGER NOT NULL DEFAULT 30,
            questions TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS results(
            id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER NOT NULL,
            full_name TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL, total INTEGER NOT NULL, percent INTEGER NOT NULL,
            cert_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL)""")
        cols = {r["name"] for r in c.execute("PRAGMA table_info(tests)").fetchall()}
        for name, typ in [("file_name","TEXT"),("file_data","BLOB"),("answer_key","TEXT"),("question_count","INTEGER NOT NULL DEFAULT 0"),("manual_answers","TEXT"),("review_status","TEXT NOT NULL DEFAULT 'checked'"),("focus_warning","INTEGER NOT NULL DEFAULT 0"),("manual_score","INTEGER NOT NULL DEFAULT 0")]:
            if name not in cols:
                c.execute(f"ALTER TABLE tests ADD COLUMN {name} {typ}")
        # Safe migration for existing databases created before manual review fields existed.
        result_cols = {r["name"] for r in c.execute("PRAGMA table_info(results)").fetchall()}
        for name, typ in [("manual_answers","TEXT NOT NULL DEFAULT '[]'"),("review_status","TEXT NOT NULL DEFAULT 'checked'"),("focus_warning","INTEGER NOT NULL DEFAULT 0"),("manual_score","INTEGER NOT NULL DEFAULT 0")]:
            if name not in result_cols:
                c.execute(f"ALTER TABLE results ADD COLUMN {name} {typ}")

BASE = """
<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#101b36"><title>{{ title }} — NEURON</title>
<style>
:root{--navy:#101b36;--blue:#315efb;--ink:#18233b;--muted:#718096;--line:#e7ebf2;--bg:#f3f6fc;--green:#0d9b72}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px Inter,system-ui,Arial,sans-serif}
header{background:linear-gradient(120deg,#101b36,#243d75);color:white;padding:17px max(18px,calc((100% - 1080px)/2));display:flex;align-items:center;justify-content:space-between}
.brand{font-size:19px;font-weight:850;letter-spacing:1.5px}.brand span{color:#9db5ff}
main{max-width:1080px;margin:28px auto;padding:0 16px 36px}.card{background:white;border:1px solid var(--line);border-radius:20px;padding:24px;box-shadow:0 12px 35px #1c31500b;margin-bottom:18px}
h1{font-size:clamp(24px,5vw,34px);line-height:1.15;margin:0 0 12px}h2{font-size:21px;margin:0 0 15px}.muted{color:var(--muted);line-height:1.6}.eyebrow{color:var(--blue);font-size:12px;font-weight:800;letter-spacing:1.5px;text-transform:uppercase}
input,select,textarea{width:100%;padding:14px;border:1px solid #d8deea;border-radius:12px;margin:7px 0 16px;font:inherit;background:#fff;color:var(--ink)}
input:focus,textarea:focus,select:focus{outline:3px solid #315efb22;border-color:var(--blue)}
label{font-weight:650;font-size:14px}.btn,button{display:inline-flex;align-items:center;justify-content:center;gap:8px;background:var(--blue);color:white;border:0;border-radius:12px;padding:14px 19px;font-weight:750;font-size:14px;text-decoration:none;cursor:pointer;min-height:48px}
.btn.secondary,button.secondary{background:#eef2ff;color:#2448c7}.btn.dark{background:var(--navy)}.btn.full{width:100%}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px}.notice{background:#fff5dc;color:#805b12;padding:13px 15px;border-radius:12px;margin-bottom:14px}
.stat{padding:17px;background:#f6f8ff;border-radius:14px}.stat b{display:block;font-size:25px}.filebox{border:1px dashed #9eafd2;background:#f7f9ff;border-radius:16px;padding:20px;text-align:center}
.question{border:1px solid var(--line);border-radius:15px;padding:16px;margin:12px 0}.answer-row{display:grid;grid-template-columns:48px 1fr;align-items:center;gap:12px;margin:10px 0}.qnum{background:#eef2ff;color:#315efb;border-radius:12px;padding:11px;text-align:center;font-weight:800}
iframe{width:100%;height:68vh;border:1px solid var(--line);border-radius:14px;background:white}.docx-view{max-height:68vh;overflow:auto;border:1px solid var(--line);border-radius:14px;padding:18px;background:white;line-height:1.7}
.bubbles{display:flex;gap:9px;flex-wrap:wrap;margin:10px 0}.bubble{display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;border:1px solid #ccd5e5;border-radius:50%;background:#fff;color:#263653;font-weight:800;cursor:pointer}.bubble input{display:none}.bubble:has(input:checked){background:#315efb;color:#fff;border-color:#315efb}.timer{position:sticky;top:8px;z-index:3;background:#101b36;color:white;border-radius:13px;padding:12px 16px;text-align:center;font-weight:800}
footer{text-align:center;color:#8994a8;padding:22px;font-size:12px}
@media(max-width:600px){main{margin:16px auto}.card{padding:18px;border-radius:16px}header{padding:15px 18px}.answer-row{grid-template-columns:40px 1fr}.btn,button{width:100%}}
</style></head><body><header><div class="brand">NEURON<span>.</span></div><div>ONLAYN TEST PLATFORMASI</div></header><main>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class="notice">{{m}}</div>{% endfor %}{% endwith %}
{{ content|safe }}</main><footer>NEURON · Bilimni sinash va natijalarni baholash platformasi</footer></body></html>
"""

def page(title, body, **ctx):
    return render_template_string(BASE, title=title, content=render_template_string(body, **ctx))

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"): return redirect(url_for("admin_login"))
        return fn(*a, **kw)
    return wrapper

def get_test(test_id):
    with db() as c: return c.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()

@app.get("/")
def home():
    return page("Bosh sahifa", """
    <section class="card"><p class="eyebrow">NEURON EDUCATION</p><h1>Bilimingizni sinab ko‘ring.</h1>
    <p class="muted">Test kodini kiriting, savollar faylini ko‘ring va javoblarni Mini App ichida belgilang.</p>
    <form method="post" action="/start"><label>Ism va familiya</label><input name="full_name" required maxlength="100" placeholder="Masalan: Ali Valiyev">
    <label>Test kodi</label><input name="code" required maxlength="30" placeholder="O‘qituvchi bergan kod"><button class="full">Testni boshlash →</button></form>
    <p class="muted">Adminmisiz? <a href="/admin">Admin panelga kirish</a></p></section>
    """)

@app.post("/start")
def start_test():
    name=request.form.get("full_name","").strip()
    code=request.form.get("code","").strip().upper()
    with db() as c: test=c.execute("SELECT * FROM tests WHERE code=?", (code,)).fetchone()
    if not name or not test:
        flash("Ism yoki test kodi noto‘g‘ri.")
        return redirect(url_for("home"))
    session["candidate"]={"name":name,"test_id":test["id"]}
    session["started_at"]=datetime.utcnow().timestamp()
    return redirect(url_for("take_test", test_id=test["id"]))

@app.get("/test/<int:test_id>")
def take_test(test_id):
    candidate=session.get("candidate")
    test=get_test(test_id)
    if not candidate or candidate["test_id"]!=test_id or not test: return redirect(url_for("home"))
    count=int(test["question_count"] or 0)
    if count<1: count=len([x for x in (test["questions"] or "").splitlines() if x.strip()])
    return page("Test ishlash", """
    <div class="grid"><section class="card"><p class="eyebrow">TEST TOPSHIRISH</p><h1>{{test['title']}}</h1>
    <p class="muted">{{candidate['name']}} · {{count}} ta savol</p><div class="timer">⏱ Qolgan vaqt: <span id="clock">{{test['duration']}}:00</span></div>
    <p class="muted">Savollarni hujjatdan o‘qing. Javoblarni o‘ng/ pastdagi bo‘limda belgilang.</p>
    {% if test['file_name'] %}<div class="filebox"><b>📄 Test savollari</b><p class="muted">Savollar alohida brauzer oynasida ochiladi. Javoblarni belgilash uchun test oynasiga qayting.</p><a id="testFileLink" class="btn secondary full" href="{{file_url}}" target="_blank" rel="noopener noreferrer">📖 Savollarni yangi oynada ochish</a></div>{% else %}<div class="notice">Test savollari fayli biriktirilmagan.</div>{% endif %}
    </section><section class="card"><h2>Javoblar varaqasi</h2><p class="muted">Har bir savol uchun variantni bosing. Yopiq savollarga yozma javob kiriting.</p><form id="answerForm" method="post" action="/submit/{{test['id']}}">
    {% for i in range(1,count+1) %}<div class="question"><div class="answer-row"><div class="qnum">{{i}}</div><div><b>{{i}}-savol</b><div class="bubbles">
    {% for v in ['A','B','C','D'] %}<label class="bubble"><input type="radio" name="q{{i}}" value="{{v}}"><span>{{v}}</span></label>{% endfor %}
    </div><input name="q{{i}}_text" placeholder="Yopiq savol: javobni shu yerga yozing" aria-label="{{i}}-savol yozma javobi"></div></div></div>{% endfor %}
    <button class="full" type="submit">Testni yakunlash ✓</button></form></section></section></div>
    <script>let left={{test['duration']}}*60;const el=document.getElementById('clock');const form=document.getElementById('answerForm');let focusWarnings=0,openingTestFile=false,submitted=false;function finish(){if(submitted)return;submitted=true;const h=document.createElement('input');h.type='hidden';h.name='focus_warning_count';h.value=String(focusWarnings);form.appendChild(h);form.submit();}function tick(){let m=Math.floor(left/60),s=left%60;el.textContent=m+':'+String(s).padStart(2,'0');if(left<=0){finish();return;}left--;}const fileLink=document.getElementById("testFileLink");if(fileLink){fileLink.addEventListener("click",()=>{openingTestFile=true;setTimeout(()=>{openingTestFile=false;},20000);});}document.addEventListener("visibilitychange",()=>{if(document.hidden){if(openingTestFile)return;focusWarnings++;alert("Diqqat! Oyna almashishi qayd etildi ("+focusWarnings+"/2). Ikkinchi ogohlantirishda test avtomatik yakunlanadi.");if(focusWarnings>=2){alert("Ikki ogohlantirish: test yakunlanib, o‘qituvchi tekshiruviga yuboriladi.");finish();}}else{openingTestFile=false;}});form.addEventListener('submit',e=>{if(!submitted){e.preventDefault();finish();}});tick();setInterval(tick,1000);</script>
    """,test=test,candidate=candidate,count=count,file_url=url_for("test_file",test_id=test_id,token=file_signer.dumps({"test_id":test_id,"name":candidate["name"]})))

@app.get("/testfile/<int:test_id>/<token>")
def test_file(test_id,token):
    try:
        payload=file_signer.loads(token,max_age=4*60*60)
        if int(payload.get("test_id",-1))!=test_id: abort(404)
    except (BadSignature,SignatureExpired,ValueError,TypeError):
        abort(404)
    test=get_test(test_id)
    if not test or not test["file_data"]: abort(404)
    name=test["file_name"] or "test"
    if name.lower().endswith(".pdf"):
        return send_file(io.BytesIO(test["file_data"]), mimetype="application/pdf", download_name=name, as_attachment=False)
    try:
        from docx import Document
        doc=Document(io.BytesIO(test["file_data"]))
        paras="".join("<p>"+p.text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")+"</p>" for p in doc.paragraphs if p.text.strip())
        tables=""
        for table in doc.tables:
            tables+="<table border='1' cellpadding='7' cellspacing='0' style='border-collapse:collapse;width:100%'>"
            for row in table.rows:
                tables+="<tr>"+"".join("<td>"+cell.text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")+"</td>" for cell in row.cells)+"</tr>"
            tables+="</table>"
        return page("Test hujjati", "<section class='card'><p class='eyebrow'>TEST HUJJATI</p><h2>"+secure_filename(name)+"</h2><div class='docx-view'>"+paras+tables+"</div></section>")
    except Exception:
        return "DOCX faylni o‘qib bo‘lmadi. Faylni qayta yuklang.", 422

@app.post("/submit/<int:test_id>")
def submit_test(test_id):
    candidate=session.get("candidate")
    test=get_test(test_id)
    if not candidate or candidate["test_id"]!=test_id or not test: return redirect(url_for("home"))
    try: key=json.loads(test["answer_key"] or "[]")
    except Exception: key=[]
    if not key: abort(400, "Javoblar kaliti mavjud emas.")
    manual=[]
    score=0; auto_total=0
    for i,correct in enumerate(key,1):
        answer=(request.form.get(f"q{i}_text","").strip() or request.form.get(f"q{i}","").strip())
        if str(correct).strip().upper()=="MANUAL": manual.append({"question":i,"answer":answer})
        else:
            auto_total+=1
            if answer.casefold()==str(correct).strip().casefold(): score+=1
    total=len(key)
    try: warnings=max(0,min(10,int(request.form.get("focus_warning_count","0"))))
    except Exception: warnings=0
    percent=round(score*100/total) if total else 0
    cert="NR-"+secrets.token_hex(4).upper()
    with db() as c:
        cur=c.execute("INSERT INTO results(test_id,full_name,student_id,score,total,percent,cert_id,created_at,manual_answers,review_status,focus_warning) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (test_id,candidate["name"],"",score,total,percent,cert,datetime.utcnow().isoformat(),json.dumps(manual,ensure_ascii=False),"pending" if manual else "checked",warnings))
        result_id=cur.lastrowid
    try: notify_teacher("🧪 NEURON — Yangi test\\nO‘quvchi: "+candidate["name"]+"\\nTest: "+test["title"]+"\\nNatija: "+str(score)+"/"+str(total)+" ("+str(percent)+"%)\\nYopiq savollar: "+str(len(manual))+"\\nOgohlantirishlar: "+str(warnings)+"\\nID: "+str(result_id))
    except Exception: app.logger.exception("Teacher notification failed")
    session.pop("candidate",None); session.pop("started_at",None)
    return redirect(url_for("certificate", cert_id=cert))

@app.get("/certificate/<cert_id>")
def certificate(cert_id):
    with db() as c: r=c.execute("SELECT results.*,tests.title FROM results JOIN tests ON tests.id=results.test_id WHERE cert_id=?", (cert_id,)).fetchone()
    if not r: abort(404)
    level="O‘qituvchi tekshiruvi kutilmoqda" if r["review_status"]=="pending" or int(r["focus_warning"] or 0)>=2 else "A’lo" if r["percent"]>=90 else "Yaxshi" if r["percent"]>=70 else "Qoniqarli" if r["percent"]>=50 else "Ishtirok etdi"
    return page("Natija va sertifikat", """
    <section class="card" style="text-align:center"><p class="eyebrow">NEURON NATIJA</p><h1>{{r['full_name']}}</h1><p class="muted">{{r['title']}}</p>
    <div class="stat"><b>{{r['score']}} / {{r['total']}}</b>{{r['percent']}}% avtomatik baholangan natija</div><h2>{{level}}</h2>
    {% if r['review_status']=='pending' %}<p class="muted">Yopiq savollaringiz o‘qituvchi tomonidan tekshirilgach yakuniy natija va sertifikat tayyor bo‘ladi.</p>{% else %}<p class="muted">Sertifikat ID: {{r['cert_id']}}</p><a class="btn full" href="{{url_for('certificate_pdf',cert_id=r['cert_id'])}}">⬇ Sertifikatni PDF yuklab olish</a><p class="muted">Sertifikatni tekshirish: {{request.host_url}}certificate/{{r['cert_id']}}</p>{% endif %}</section>
    """,r=r,level=level)

@app.get("/certificate/<cert_id>/pdf")
def certificate_pdf(cert_id):
    with db() as c:
        r=c.execute("SELECT results.*,tests.title FROM results JOIN tests ON tests.id=results.test_id WHERE cert_id=?",(cert_id,)).fetchone()
    if not r: abort(404)
    if r["review_status"]=="pending" or int(r["focus_warning"] or 0)>=2: return "O‘qituvchi tekshiruvi tugamaguncha sertifikat yuklab olinmaydi.",409
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    buff=io.BytesIO()
    doc=SimpleDocTemplate(buff,pagesize=landscape(A4),rightMargin=22*mm,leftMargin=22*mm,topMargin=18*mm,bottomMargin=18*mm)
    styles=getSampleStyleSheet()
    title_style=ParagraphStyle("CertTitle",parent=styles["Title"],fontName="Helvetica-Bold",fontSize=28,leading=34,textColor=colors.HexColor("#18336b"),alignment=TA_CENTER,spaceAfter=8)
    center=ParagraphStyle("CertCenter",parent=styles["Normal"],fontSize=13,leading=20,alignment=TA_CENTER)
    name_style=ParagraphStyle("CertName",parent=styles["Heading1"],fontSize=25,leading=31,textColor=colors.HexColor("#172b55"),alignment=TA_CENTER)
    story=[Spacer(1,12*mm),Paragraph("NEURON",title_style),Paragraph("SERTIFIKAT",ParagraphStyle("CertSub",parent=title_style,fontSize=20,leading=25)),Spacer(1,7*mm),Paragraph("Ushbu sertifikat quyidagi ishtirokchiga taqdim etiladi:",center),Spacer(1,5*mm),Paragraph(escape(r["full_name"]),name_style),Spacer(1,5*mm),Paragraph("“"+escape(r["title"])+"” test sinovida qatnashib, quyidagi natijani qayd etdi.",center),Spacer(1,7*mm)]
    data=[[Paragraph("<b>Natija</b>",center),Paragraph("<b>Foiz</b>",center),Paragraph("<b>Sertifikat ID</b>",center)],[Paragraph(f'{r["score"]} / {r["total"]}',center),Paragraph(f'{r["percent"]}%',center),Paragraph(escape(r["cert_id"]),center)]]
    table=Table(data,colWidths=[65*mm,65*mm,75*mm],rowHeights=[13*mm,18*mm])
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0ff")),("BOX",(0,0),(-1,-1),1,colors.HexColor("#315efb")),("INNERGRID",(0,0),(-1,-1),.5,colors.HexColor("#cbd5e8")),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    story.extend([table,Spacer(1,10*mm),Paragraph("Berilgan sana: "+escape(str(r["created_at"])[:10]),center),Spacer(1,5*mm),Paragraph("Tekshirish havolasi: "+escape(request.host_url+"certificate/"+r["cert_id"]),ParagraphStyle("CertVerify",parent=center,fontSize=9,textColor=colors.HexColor("#52617a")))])
    doc.build(story); buff.seek(0)
    return send_file(buff,mimetype="application/pdf",as_attachment=True,download_name=f"NEURON_{r['cert_id']}.pdf")

@app.route("/admin", methods=["GET","POST"])
def admin_login():
    if session.get("admin"): return redirect(url_for("dashboard"))
    if request.method=="POST":
        if ADMIN_PASSWORD and request.form.get("username")==ADMIN_USER and secrets.compare_digest(request.form.get("password",""),ADMIN_PASSWORD):
            session["admin"]=True; return redirect(url_for("dashboard"))
        flash("Login yoki parol noto‘g‘ri. Render ENV sozlamalarini tekshiring.")
    return page("Admin kirish", """<section class="card"><p class="eyebrow">NEURON ADMIN</p><h1>Boshqaruv paneli</h1><form method="post">
    <label>Login</label><input name="username" required><label>Parol</label><input name="password" type="password" required><button class="full">Kirish</button></form></section>""")

@app.get("/admin/dashboard")
@admin_required
def dashboard():
    with db() as c:
        tests=c.execute("SELECT * FROM tests ORDER BY id DESC").fetchall()
        raw_results=c.execute("SELECT results.*,tests.title FROM results JOIN tests ON tests.id=results.test_id ORDER BY results.id DESC LIMIT 100").fetchall()
    results=[]
    for item in raw_results:
        item=dict(item)
        try: item["manual_items"]=json.loads(item.get("manual_answers") or "[]")
        except Exception: item["manual_items"]=[]
        results.append(item)
    return page("Admin panel", """
    <section class="card"><p class="eyebrow">NEURON ADMINISTRATORI</p><h1>Testlarni boshqarish</h1>
    <p class="muted">Test savollari fayl ko‘rinishida yuklanadi. O‘quvchi faylni ko‘rib, Mini App’da javoblarni belgilaydi.</p>
    <form method="post" action="/admin/create" enctype="multipart/form-data">
    <div class="grid"><div><label>Test nomi</label><input name="title" required placeholder="Masalan: Tarix — 1-bosqich"></div>
    <div><label>Test kodi (ixtiyoriy)</label><input name="code" placeholder="Avtomatik yaratiladi"></div>
    <div><label>Davomiyligi (daqiqada)</label><input name="duration" type="number" min="1" max="240" value="30" required></div>
    <div><label>Savollar soni</label><input name="question_count" type="number" min="1" max="200" required placeholder="Masalan: 25"></div></div>
    <label>Test fayli (PDF yoki DOCX, 15 MB gacha)</label><div class="filebox">📎 <b>Test hujjatini yuklang</b><input type="file" name="test_file" accept=".pdf,.docx" required></div>
    <h2 style="margin-top:20px">Javoblar kaliti</h2><p class="muted">EvalBee uslubida har bir savol uchun to‘g‘ri javobni tanlang. Savol turini ham o‘zgartirishingiz mumkin.</p>
    <div id="keyProgress" class="muted" style="margin:10px 0">0 / 0 ta javob belgilandi</div>
    <div id="keyRows" class="key-grid"></div>
    <input type="hidden" name="answer_key" id="answerKey" required>
    <style>.key-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:10px}.key-row{border:1px solid #e5eaf3;border-radius:14px;padding:12px;background:#fbfcff}.key-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;font-weight:800}.bubbles{display:flex;gap:7px;flex-wrap:wrap}.bubble{display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;border:1px solid #ccd5e5;border-radius:50%;background:#fff;color:#263653;font-weight:800;cursor:pointer}.bubble input{display:none}.bubble:has(input:checked){background:#315efb;color:white;border-color:#315efb;box-shadow:0 3px 10px #315efb33}.key-row select,.key-row input[type=text]{margin:0 0 8px;padding:10px}.key-row .short-answer{display:none}.key-row[data-type=short] .short-answer{display:block}</style>
    <button class="full">＋ Testni yaratish</button></form></section>
    <script>
    const countInput=document.querySelector('input[name="question_count"]');
    const keyRows=document.getElementById('keyRows');
    const keyHidden=document.getElementById('answerKey');
    const keyProgress=document.getElementById('keyProgress');
    function drawKeys(){
      const n=Math.max(0,Math.min(200,Number(countInput.value)||0));
      keyRows.innerHTML='';
      for(let i=1;i<=n;i++){
        const row=document.createElement('div');row.className='key-row';row.dataset.type='mcq';
        row.innerHTML='<div class="key-head"><span>'+i+'-savol</span><span class="muted">Kalit</span></div><select class="key-type"><option value="mcq">A / B / C / D</option><option value="tf">To‘g‘ri / Noto‘g‘ri</option><option value="short">Qisqa yozma javob (avtomatik)</option><option value="manual">Yopiq savol (o‘qituvchi tekshiradi)</option></select><div class="bubbles"></div><input class="short-answer" type="text" maxlength="80" placeholder="To‘g‘ri javob matni">';
        const type=row.querySelector('.key-type');
        function options(){
          row.dataset.type=type.value;
          const box=row.querySelector('.bubbles');box.innerHTML='';
          const vals=type.value==='mcq'?['A','B','C','D']:type.value==='tf'?['TRUE','FALSE']:[];
          vals.forEach(v=>{const lab=document.createElement('label');lab.className='bubble';lab.innerHTML='<input type="radio" name="key_'+i+'" value="'+v+'"><span>'+(v==='TRUE'?'T':v==='FALSE'?'N':v)+'</span>';box.appendChild(lab);});
          syncKeys();
        }
        type.addEventListener('change',options);
        row.addEventListener('change',syncKeys);
        row.querySelector('.short-answer').addEventListener('input',syncKeys);
        options();keyRows.appendChild(row);
      }
      syncKeys();
    }
    function syncKeys(){
      const rows=[...keyRows.querySelectorAll('.key-row')];
      const vals=rows.map(row=>row.dataset.type==='short'?row.querySelector('.short-answer').value.trim():row.dataset.type==='manual'?'MANUAL':(row.querySelector('input[type=radio]:checked')?.value||''));
      const done=vals.filter(Boolean).length;
      keyProgress.textContent=done+' / '+rows.length+' ta javob belgilandi';
      keyHidden.value=vals.join(',');
      keyHidden.setCustomValidity(done===rows.length&&vals.every(v=>!v.includes(','))?'':'Barcha savollarga javob kalitini belgilang. Javob matnida vergul ishlatmang.');
    }
    countInput.addEventListener('input',drawKeys);drawKeys();
    </script>
    <section class="card"><h2>Mavjud testlar</h2>{% for t in tests %}<div class="question"><b>{{t['title']}}</b><p class="muted">Kod: {{t['code']}} · {{t['question_count']}} savol · {{t['duration']}} daqiqa · {{t['file_name'] or 'Eski test'}}</p></div>{% else %}<p class="muted">Hozircha test yo‘q.</p>{% endfor %}</section>
    <section class="card"><h2>Natijalar va tekshiruvlar</h2>{% for r in results %}<div class="question"><p><b>{{r['full_name']}}</b> — {{r['title']}} — <b>{{r['percent']}}%</b></p><p class="muted">Holat: {{'Tekshiruv kutilmoqda' if r['review_status']=='pending' else 'Sertifikat tayyor' if r['review_status']=='checked' else r['review_status']}} · ID: {{r['cert_id']}}</p>{% if r['focus_warning']|int >= 2 %}<p><b>⚠️ Test bloklangan: {{r['focus_warning']}} ogohlantirish.</b></p><form method="post" action="/admin/clearflag/{{r['id']}}"><button class="secondary">Blokni bekor qilish</button></form>{% endif %}{% if r['review_status']=='pending' %}<form method="post" action="/admin/review/{{r['id']}}"><h3>Yopiq savollar — rubrika</h3>{% for item in r['manual_items'] %}<div class="filebox"><b>{{item['question']}}-savol</b><p>{{item['answer'] or 'Javob kiritilmagan'}}</p><label>Baholash (1 ballik mezon)</label><select name="manual_score_{{loop.index0}}"><option value="0">Noto‘g‘ri — 0</option><option value="0.5">Qisman — 0.5</option><option value="1">To‘g‘ri — 1</option></select></div>{% endfor %}<button>Baholashni saqlash</button></form>{% endif %}</div>{% else %}<p>Natijalar yo‘q.</p>{% endfor %}<a class="btn dark" href="/admin/logout">Chiqish</a></section>
    """,tests=tests,results=results)

@app.post("/admin/create")
@admin_required
def create_test():
    title=request.form.get("title","").strip()
    duration=max(1,min(240,int(request.form.get("duration",30))))
    count=int(request.form.get("question_count",0))
    raw_key=request.form.get("answer_key","").strip()
    uploaded=request.files.get("test_file")
    if not title or count<1 or count>200 or not uploaded or not uploaded.filename:
        flash("Barcha maydonlarni to‘ldiring va PDF yoki DOCX fayl yuklang."); return redirect(url_for("dashboard"))
    filename=secure_filename(uploaded.filename)
    ext=filename.rsplit(".",1)[-1].lower() if "." in filename else ""
    if ext not in ("pdf","docx"):
        flash("Faqat PDF yoki DOCX fayl qabul qilinadi."); return redirect(url_for("dashboard"))
    data=uploaded.read()
    if not data or len(data)>15*1024*1024:
        flash("Fayl bo‘sh yoki 15 MB dan katta."); return redirect(url_for("dashboard"))
    answers=[x.strip() for x in raw_key.replace("\n",",").replace(";",",").split(",") if x.strip()]
    if len(answers)!=count:
        flash(f"Savollar soni {count}, javoblar kaliti esa {len(answers)} ta. Ikkalasini teng qiling."); return redirect(url_for("dashboard"))
    allowed={"A","B","C","D","TRUE","FALSE","TO‘G‘RI","NOTO‘G‘RI"}
    if any(a.upper() not in allowed and a.upper()!="MANUAL" and len(a)>80 for a in answers):
        flash("Javob kalitidagi matn juda uzun."); return redirect(url_for("dashboard"))
    code=request.form.get("code","").strip().upper() or "".join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(6))
    try:
        with db() as c:
            c.execute("INSERT INTO tests(title,code,duration,questions,created_at,file_name,file_data,answer_key,question_count) VALUES(?,?,?,?,?,?,?,?,?)",
            (title,code,duration,"",datetime.utcnow().isoformat(),filename,data,json.dumps(answers,ensure_ascii=False),count))
        flash("Test muvaffaqiyatli yaratildi. Test kodi: "+code)
    except sqlite3.IntegrityError: flash("Bu test kodi band. Boshqa kod kiriting.")
    return redirect(url_for("dashboard"))

@app.post("/admin/review/<int:result_id>")
@admin_required
def review_result(result_id):
    with db() as c:
        r=c.execute("SELECT * FROM results WHERE id=?",(result_id,)).fetchone()
        if not r or r["review_status"]!="pending": abort(404)
        try: manual=json.loads(r["manual_answers"] or "[]")
        except Exception: manual=[]
        n=len(manual)
        try: got=sum(float(request.form.get(f"manual_score_{i}","0")) for i in range(n))
        except Exception: got=-1
        if got<0 or got>n:
            flash("Rubrika ballari noto‘g‘ri."); return redirect(url_for("dashboard"))
        final_score=float(r["score"])+got
        percent=round(final_score*100/float(r["total"])) if r["total"] else 0
        c.execute("UPDATE results SET score=?,percent=?,manual_score=?,review_status='checked' WHERE id=?",(final_score,percent,got,result_id))
        student=r["full_name"]; cert=r["cert_id"]
    try: notify_teacher("✅ NEURON — Rubrika saqlandi\\nO‘quvchi: "+student+"\\nYakuniy ball: "+str(final_score)+"/"+str(r["total"])+"\\nNatija: "+str(percent)+"%\\nSertifikat: "+cert)
    except Exception: app.logger.exception("Teacher review notification failed")
    flash("Yopiq savollar baholandi. Agar test bloklangan bo‘lsa, blokni alohida bekor qiling.")
    return redirect(url_for("dashboard"))

@app.post("/admin/clearflag/<int:result_id>")
@admin_required
def clear_test_flag(result_id):
    with db() as c:
        r=c.execute("SELECT id FROM results WHERE id=?",(result_id,)).fetchone()
        if not r: abort(404)
        c.execute("UPDATE results SET focus_warning=0 WHERE id=?",(result_id,))
    flash("Test blokidan chiqarildi.")
    return redirect(url_for("dashboard"))

@app.get("/admin/logout")
def logout():
    session.clear(); return redirect(url_for("admin_login"))

# Telegram webhook integration
BOT_TOKEN=os.environ.get("BOT_TOKEN","").strip()
MINI_APP_URL=os.environ.get("MINI_APP_URL","https://mirzayev-group.onrender.com").strip()
TELEGRAM_WEBHOOK_SECRET=hashlib.sha256((os.environ.get("SECRET_KEY","change-this-before-production")+":telegram-webhook").encode()).hexdigest()[:40]

def telegram_api(method,payload):
    if not BOT_TOKEN: return None
    req=urllib.request.Request(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=12) as response: return json.loads(response.read().decode())

TEACHER_CHAT_ID=os.environ.get("TEACHER_CHAT_ID","").strip()
def notify_teacher(message):
    if not BOT_TOKEN or not TEACHER_CHAT_ID:
        app.logger.error("Teacher alert not configured: BOT_TOKEN or TEACHER_CHAT_ID missing")
        return False
    result=telegram_api("sendMessage",{"chat_id":TEACHER_CHAT_ID,"text":message,"reply_markup":{"inline_keyboard":[[{"text":"📋 O‘qituvchi paneli","url":"https://mirzayev-group.onrender.com/admin"}]]}})
    if not result or not result.get("ok"):
        app.logger.error("Teacher Telegram notification failed: %s",result)
        return False
    return True

@app.post("/telegram/webhook")
def telegram_webhook():
    if not BOT_TOKEN: return {"ok":False,"error":"bot_not_configured"},503
    if not secrets.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token",""),TELEGRAM_WEBHOOK_SECRET): abort(403)
    update=request.get_json(silent=True) or {}; message=update.get("message") or {}; chat=message.get("chat") or {}; msg=(message.get("text") or "").strip(); chat_id=chat.get("id")
    if not chat_id: return {"ok":True}
    if msg=="/myid":
        telegram_api("sendMessage",{"chat_id":chat_id,"text":"Telegram ID: "+str(chat_id)+"\nRender Environment sozlamasida TEACHER_CHAT_ID nomi bilan shu raqamni kiriting."})
    elif msg.startswith("/start"):
        telegram_api("sendMessage",{"chat_id":chat_id,"text":"Assalomu alaykum! 👋\nNEURON onlayn test platformasiga xush kelibsiz. Testni boshlash uchun tugmani bosing.","reply_markup":{"inline_keyboard":[[{"text":"🚀 Testni boshlash","web_app":{"url":MINI_APP_URL}}]]}})
    elif msg=="/help": telegram_api("sendMessage",{"chat_id":chat_id,"text":"Test platformasini ochish uchun /start buyrug‘ini yuboring."})
    return {"ok":True}

@app.get("/telegram/status")
def telegram_status():
    return {"bot_configured":bool(BOT_TOKEN),"mini_app_url":MINI_APP_URL,"webhook_path":"/telegram/webhook"}

def register_telegram_webhook():
    if not BOT_TOKEN: app.logger.warning("BOT_TOKEN is not configured."); return
    try:
        result=telegram_api("setWebhook",{"url":"https://mirzayev-group.onrender.com/telegram/webhook","secret_token":TELEGRAM_WEBHOOK_SECRET,"allowed_updates":["message"]})
        app.logger.info("Telegram webhook registration: %s",result)
    except Exception: app.logger.exception("Telegram webhook registration failed")

init_db()
register_telegram_webhook()
