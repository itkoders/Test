import os, sqlite3, secrets, string, json, hashlib, urllib.request, urllib.error, io
import re
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None
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
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATABASE_SCHEMA = re.sub(r"[^a-zA-Z0-9_]", "", os.environ.get("DATABASE_SCHEMA", "neuron_test")) or "neuron_test"
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

class PgCursor:
    def __init__(self, cursor): self.cursor = cursor
    def fetchone(self): return self.cursor.fetchone()
    def fetchall(self): return self.cursor.fetchall()
    @property
    def lastrowid(self): return None

class PgConnection:
    def __init__(self):
        if not psycopg2: raise RuntimeError("psycopg2 package is required for Neon DATABASE_URL")
        self.conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor, connect_timeout=12)
        self.conn.autocommit = False
        with self.conn.cursor() as cur: cur.execute(f'SET search_path TO "{DATABASE_SCHEMA}"')
    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return PgCursor(cur)
    def __enter__(self): return self
    def __exit__(self, typ, value, tb):
        try:
            self.conn.rollback() if typ else self.conn.commit()
        finally: self.conn.close()

def db():
    if DATABASE_URL: return PgConnection()
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    if DATABASE_URL:
        with psycopg2.connect(DATABASE_URL, connect_timeout=12) as raw:
            with raw.cursor() as cur:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{DATABASE_SCHEMA}"')
                cur.execute(f'SET search_path TO "{DATABASE_SCHEMA}"')
                cur.execute("""CREATE TABLE IF NOT EXISTS tests(
                    id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
                    duration INTEGER NOT NULL DEFAULT 30, questions TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, file_name TEXT, file_data BYTEA, answer_key TEXT,
                    question_count INTEGER NOT NULL DEFAULT 0, manual_answers TEXT,
                    review_status TEXT NOT NULL DEFAULT 'checked', focus_warning INTEGER NOT NULL DEFAULT 0,
                    manual_score DOUBLE PRECISION NOT NULL DEFAULT 0, teacher_id TEXT, subject TEXT NOT NULL DEFAULT '', grade TEXT NOT NULL DEFAULT '')""")
                cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS teacher_id TEXT")
                cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS subject TEXT NOT NULL DEFAULT ''")
                cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS grade TEXT NOT NULL DEFAULT ''")
                cur.execute("""CREATE TABLE IF NOT EXISTS results(
                    id BIGSERIAL PRIMARY KEY, test_id BIGINT NOT NULL, full_name TEXT NOT NULL,
                    student_id TEXT NOT NULL DEFAULT '', score DOUBLE PRECISION NOT NULL,
                    total INTEGER NOT NULL, percent INTEGER NOT NULL, cert_id TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL, manual_answers TEXT NOT NULL DEFAULT '[]',
                    review_status TEXT NOT NULL DEFAULT 'checked', focus_warning INTEGER NOT NULL DEFAULT 0,
                    manual_score DOUBLE PRECISION NOT NULL DEFAULT 0)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS teacher_accounts(
                    telegram_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, full_name TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending', stage TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, username TEXT UNIQUE, password_hash TEXT)""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_neuron_test_results_test ON results(test_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_neuron_test_teachers_status ON teacher_accounts(status)")
        return
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS tests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL, duration INTEGER NOT NULL DEFAULT 30,
            questions TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, teacher_id TEXT, subject TEXT NOT NULL DEFAULT '', grade TEXT NOT NULL DEFAULT '')""")
        c.execute("""CREATE TABLE IF NOT EXISTS results(
            id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER NOT NULL,
            full_name TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL, total INTEGER NOT NULL, percent INTEGER NOT NULL,
            cert_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL)""")
        cols = {r["name"] for r in c.execute("PRAGMA table_info(tests)").fetchall()}
        for name, typ in [("file_name","TEXT"),("file_data","BLOB"),("answer_key","TEXT"),("question_count","INTEGER NOT NULL DEFAULT 0"),("manual_answers","TEXT"),("review_status","TEXT NOT NULL DEFAULT 'checked'"),("focus_warning","INTEGER NOT NULL DEFAULT 0"),("manual_score","INTEGER NOT NULL DEFAULT 0"),("teacher_id","TEXT"),("subject","TEXT NOT NULL DEFAULT ''"),("grade","TEXT NOT NULL DEFAULT ''")]:
            if name not in cols: c.execute(f"ALTER TABLE tests ADD COLUMN {name} {typ}")
        result_cols = {r["name"] for r in c.execute("PRAGMA table_info(results)").fetchall()}
        for name, typ in [("manual_answers","TEXT NOT NULL DEFAULT '[]'"),("review_status","TEXT NOT NULL DEFAULT 'checked'"),("focus_warning","INTEGER NOT NULL DEFAULT 0"),("manual_score","INTEGER NOT NULL DEFAULT 0")]:
            if name not in result_cols: c.execute(f"ALTER TABLE results ADD COLUMN {name} {typ}")
        c.execute("""CREATE TABLE IF NOT EXISTS teacher_accounts(telegram_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, full_name TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', stage TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, username TEXT, password_hash TEXT)""")
        tc={r["name"] for r in c.execute("PRAGMA table_info(teacher_accounts)").fetchall()}
        for n,t in [("username","TEXT"),("password_hash","TEXT")]:
            if n not in tc: c.execute(f"ALTER TABLE teacher_accounts ADD COLUMN {n} {t}")

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

/* NEURON V2 responsive controls */
.btn,button{max-width:100%;white-space:normal;line-height:1.25;touch-action:manipulation}
@media(max-width:600px){.btn,button{width:auto}.btn.full,button.full,.grid>.btn{width:100%}.grid{grid-template-columns:minmax(0,1fr)}.bubbles{gap:7px}.bubble,button.bubble{width:42px!important;min-width:42px!important;height:42px;min-height:42px!important;padding:0!important;flex:0 0 42px}.card{overflow:hidden}.key-grid{grid-template-columns:1fr}}
button:disabled{opacity:.4;cursor:not-allowed}.panel-hero{background:linear-gradient(135deg,#101b36,#315efb);color:#fff;border-radius:20px;padding:22px;margin-bottom:18px}.panel-hero .muted{color:#dbe5ff}.panel-nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:16px 0}.panel-nav a{min-height:52px}.teacher-screen{display:none}.teacher-screen.active{display:block}.panel-nav button{min-height:52px}.panel-nav button.primary{background:#315efb;color:#fff}.cert-preview{border:2px solid #315efb;border-radius:18px;padding:20px;text-align:center;background:#fff;aspect-ratio:16/9;display:flex;flex-direction:column;justify-content:center;gap:7px;max-width:760px;margin:12px auto;outline:1px solid #c7d5ff;outline-offset:-10px}.cert-preview .cert-name{font-size:clamp(18px,4vw,28px);font-weight:850;color:#18336b;margin:8px 0}.cert-preview h2{margin:5px 0}@media(max-width:600px){.panel-nav{grid-template-columns:repeat(2,minmax(0,1fr))}.cert-preview{padding:12px;gap:3px}.cert-preview .cert-name{margin:3px 0}}.cert-preview .cert-name{font-size:23px;font-weight:850;color:#18336b;margin:12px 0}.status-pill{display:inline-flex;border-radius:999px;padding:6px 10px;background:#fff2d6;color:#805b12;font-size:12px;font-weight:800}.status-pill.good{background:#e3f8ef;color:#08734f}.warning-card{border-left:4px solid #e29b24;background:#fffaf0}.key-help{background:#f3f6ff;border-radius:12px;padding:12px;margin:10px 0}
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
    <p class="muted">Fan va sinfga mos test kodini kiriting. Savollarni ketma-ket ishlang, javoblaringizni tekshirib yakunlang.</p>
    <form method="post" action="/start"><label>Ism va familiya</label><input name="full_name" required maxlength="100" placeholder="Masalan: Ali Valiyev">
    <label>Test kodi</label><input name="code" required maxlength="40" placeholder="Masalan: TARIX-8-AB-4F2A"><button class="full">Testni boshlash →</button></form>
    <div class="grid"><a class="btn secondary full" href="/teacher/login">👩‍🏫 O‘qituvchi kabineti</a><a class="btn dark full" href="/admin">🔐 Admin paneli</a></div></section>
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
    session["test_deadline"]=session["started_at"]+int(test["duration"] or 30)*60
    return redirect(url_for("take_test", test_id=test["id"]))

@app.get("/test/<int:test_id>")
def take_test(test_id):
    candidate=session.get("candidate")
    test=get_test(test_id)
    if not candidate or candidate["test_id"]!=test_id or not test: return redirect(url_for("home"))
    count=int(test["question_count"] or 0)
    if count<1: count=len([x for x in (test["questions"] or "").splitlines() if x.strip()])
    remaining=max(0,int(session.get("test_deadline",datetime.utcnow().timestamp())-datetime.utcnow().timestamp()))
    return page("Test ishlash", """
    <div class="grid"><section class="card"><p class="eyebrow">TEST TOPSHIRISH</p><h1>{{test['title']}}</h1>
    <p class="muted">{{candidate['name']}} · {{test['subject'] or 'Fan ko‘rsatilmagan'}} · {{test['grade'] or 'Sinf ko‘rsatilmagan'}} · {{count}} ta savol</p><div class="timer">⏱ Qolgan vaqt: <span id="clock">{{remaining//60}}:{{"%02d"|format(remaining%60)}}</span></div><p id="focusNotice" class="notice" hidden></p>
    <p class="muted">Savollarni hujjatdan o‘qing. Javoblarni o‘ng/ pastdagi bo‘limda belgilang.</p>
    {% if test['file_name'] %}<div class="filebox"><b>📄 Test savollari</b><p class="muted">Savollar alohida brauzer oynasida ochiladi. Javoblarni belgilash uchun test oynasiga qayting.</p><a id="testFileLink" class="btn secondary full" href="{{file_url}}" target="_blank" rel="noopener noreferrer">📖 Savollarni yangi oynada ochish</a></div>{% else %}<div class="notice">Test savollari fayli biriktirilmagan.</div>{% endif %}
    </section><section class="card"><h2>Javoblar varaqasi</h2><p class="muted">Har bir savol uchun variantni bosing. Yopiq savollarga yozma javob kiriting.</p><form id="answerForm" method="post" action="/submit/{{test['id']}}">
    <div id="studentProgress" class="muted">1 / {{count}}-savol</div><div class="bubbles" id="studentNav">{% for i in range(1,count+1) %}<button type="button" class="bubble" data-go="{{i}}">{{i}}</button>{% endfor %}</div>
    {% for i in range(1,count+1) %}<div class="question student-question" data-q="{{i}}" {% if i!=1 %}hidden{% endif %}><div class="answer-row"><div class="qnum">{{i}}</div><div><b>{{i}}-savol</b><p class="muted">Javobingizni tanlang yoki yozing.</p><div class="bubbles">
    {% for v in ['A','B','C','D'] %}<label class="bubble"><input type="radio" name="q{{i}}" value="{{v}}"><span>{{v}}</span></label>{% endfor %}
    </div><input name="q{{i}}_text" placeholder="Yozma javob (agar kerak bo‘lsa)" aria-label="{{i}}-savol yozma javobi"></div></div></div>{% endfor %}
    <div class="grid"><button type="button" class="secondary" id="prevQ">← Oldingi</button><button type="button" id="nextQ">Keyingi →</button></div><button class="full" type="submit">Testni yakunlash ✓</button></form></section></section></div>
    <script>
const deadline={{(session.get('test_deadline',0)*1000)|int}};
const el=document.getElementById('clock'),form=document.getElementById('answerForm');
let focusWarnings=0,openingTestFile=false,submitted=false;
function finish(){if(submitted)return;submitted=true;let h=form.querySelector('[name="focus_warning_count"]');if(!h){h=document.createElement('input');h.type='hidden';h.name='focus_warning_count';form.appendChild(h);}h.value=String(focusWarnings);form.submit();}
function tick(){const left=Math.max(0,Math.ceil((deadline-Date.now())/1000));el.textContent=Math.floor(left/60)+':'+String(left%60).padStart(2,'0');if(left<=0)finish();}
const qs=[...document.querySelectorAll('.student-question')],nav=[...document.querySelectorAll('#studentNav [data-go]')];let current=0;
function show(n){current=Math.max(0,Math.min(qs.length-1,n));qs.forEach((q,i)=>q.hidden=i!==current);document.getElementById('studentProgress').textContent=(current+1)+' / '+qs.length+'-savol';document.getElementById('prevQ').disabled=current===0;document.getElementById('nextQ').textContent=current===qs.length-1?'Oxirgi savol':'Keyingi →';nav.forEach((b,i)=>{const q=qs[i],txt=q.querySelector('input:not([type=radio])');b.style.opacity=q.querySelector('input:checked')||(txt&&txt.value.trim())?'1':'.55';b.style.background=i===current?'#315efb':'';b.style.color=i===current?'white':'';});}
document.getElementById('prevQ').onclick=()=>show(current-1);document.getElementById('nextQ').onclick=()=>show(current+1);nav.forEach((b,i)=>b.onclick=()=>show(i));form.addEventListener('change',()=>show(current));
const fileLink=document.getElementById('testFileLink');if(fileLink)fileLink.addEventListener('click',()=>{openingTestFile=true;});
document.addEventListener('visibilitychange',()=>{if(document.hidden){if(openingTestFile)return;focusWarnings=Math.min(10,focusWarnings+1);const note=document.getElementById('focusNotice');note.hidden=false;note.textContent='⚠️ Test oynasidan chiqish qayd etildi ('+focusWarnings+'/2). Ikkinchi ogohlantirishda test avtomatik yakunlanadi.';if(focusWarnings>=2)finish();}else{openingTestFile=false;tick();}});
form.addEventListener('submit',e=>{if(!submitted){e.preventDefault();if(!confirm('Javoblaringizni yakunlab yuborasizmi?'))return;finish();}});
show(0);tick();setInterval(tick,500);</script>
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
        cur=c.execute("INSERT INTO results(test_id,full_name,student_id,score,total,percent,cert_id,created_at,manual_answers,review_status,focus_warning) VALUES(?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (test_id,candidate["name"],"",score,total,percent,cert,datetime.utcnow().isoformat(),json.dumps(manual,ensure_ascii=False),"pending" if manual else "checked",warnings))
        result_id=cur.fetchone()["id"]
    try:
        review_line="✍️ Yopiq savollar: "+str(len(manual))+" ta" if manual else "✍️ Yopiq savollar: yo‘q"
        warning_line="⚠️ Ogohlantirishlar: "+str(warnings)+" ta" if warnings else "✅ Ogohlantirishlar: yo‘q"
        status_line="🕒 O‘qituvchi tekshiruvi kutilmoqda" if manual or warnings>=2 else "🏅 Sertifikat tayyor"
        notify_teacher("📊 NEURON — TEST NATIJASI\\n\\n👤 O‘quvchi: "+candidate["name"]+"\\n📝 Test: "+test["title"]+"\\n🎯 Natija: "+str(score)+"/"+str(total)+" ("+str(percent)+"%)\\n"+review_line+"\\n"+warning_line+"\\n"+status_line+"\\n🆔 Natija ID: "+str(result_id))
    except Exception: app.logger.exception("Teacher notification failed")
    session.pop("candidate",None); session.pop("started_at",None); session.pop("test_deadline",None)
    return redirect(url_for("certificate", cert_id=cert))

@app.get("/certificate/<cert_id>")
def certificate(cert_id):
    with db() as c: r=c.execute("SELECT results.*,tests.title FROM results JOIN tests ON tests.id=results.test_id WHERE cert_id=?", (cert_id,)).fetchone()
    if not r: abort(404)
    level="O‘qituvchi tekshiruvi kutilmoqda" if r["review_status"]=="pending" or int(r["focus_warning"] or 0)>=2 else "A’lo" if r["percent"]>=90 else "Yaxshi" if r["percent"]>=70 else "Qoniqarli" if r["percent"]>=50 else "Ishtirok etdi"
    return page("Natija va sertifikat", """
    <section class="card" style="text-align:center"><p class="eyebrow">NEURON NATIJA</p><h1>{{r['full_name']}}</h1><p class="muted">{{r['title']}}</p>
    <div class="stat"><b>{{r['score']}} / {{r['total']}}</b>{{r['percent']}}% avtomatik baholangan natija</div><h2>{{level}}</h2>
    {% if r['review_status']=='pending' or r['focus_warning']|int >= 2 %}<p class="muted">O‘qituvchi tekshiruvi tugagach sertifikat yuklab olish ochiladi.</p>{% else %}<p class="muted">Sertifikat ID: {{r['cert_id']}}</p><a class="btn full" href="{{url_for('certificate_pdf',cert_id=r['cert_id'])}}">⬇ Sertifikatni PDF yuklab olish</a><p class="muted">Sertifikatni tekshirish: {{request.host_url}}certificate/{{r['cert_id']}}</p>{% endif %}</section>
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
    page_w=297*mm; page_h=167*mm
    doc=SimpleDocTemplate(buff,pagesize=(page_w,page_h),rightMargin=8*mm,leftMargin=8*mm,topMargin=8*mm,bottomMargin=8*mm)
    styles=getSampleStyleSheet()
    def style(name,size,color,leading=None):
        return ParagraphStyle(name,parent=styles["Normal"],fontName="Helvetica",fontSize=size,leading=leading or size*1.35,textColor=colors.HexColor(color),alignment=TA_CENTER)
    story=[
        Spacer(1,5*mm),
        Paragraph("NEURON",style("NeuronTitle",29,"#18336b",34)),
        Spacer(1,2*mm),
        Paragraph("SERTIFIKAT",style("NeuronSubtitle",21,"#315efb",26)),
        Spacer(1,5*mm),
        Paragraph("Ushbu sertifikat taqdim etiladi",style("AwardText",11,"#52617a",15)),
        Spacer(1,4*mm),
        Paragraph(escape(str(r["full_name"])),style("StudentName",25,"#172b55",31)),
        Spacer(1,4*mm),
        Paragraph("“"+escape(str(r["title"]))+"” test sinovi natijasi",style("TestTitle",11,"#52617a",15)),
        Spacer(1,8*mm),
        Paragraph(f'{float(r["score"]):.1f} / {r["total"]} &nbsp;&nbsp; | &nbsp;&nbsp; {r["percent"]}%',style("ScoreLine",21,"#18336b",27)),
        Spacer(1,7*mm),
        Paragraph("Sertifikat ID: "+escape(str(r["cert_id"])),style("CertId",9,"#52617a",12)),
        Spacer(1,2*mm),
        Paragraph("Sana: "+escape(str(r["created_at"])[:10]),style("CertDate",9,"#52617a",12))
    ]
    inner=Table([[story]],colWidths=[page_w-34*mm],rowHeights=[page_h-34*mm])
    inner.setStyle(TableStyle([("BOX",(0,0),(-1,-1),1.1,colors.HexColor("#c7d5ff")),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    outer=Table([[inner]],colWidths=[page_w-16*mm],rowHeights=[page_h-16*mm])
    outer.setStyle(TableStyle([("BOX",(0,0),(-1,-1),2.2,colors.HexColor("#315efb")),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)]))
    doc.build([outer]); buff.seek(0)
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
    <section class="panel-hero"><p class="eyebrow" style="color:#c7d7ff">ITKODERS TEST · ADMIN</p><h1>Boshqaruv markazi</h1><p class="muted">Platforma, o‘qituvchilar va nazorat natijalarini boshqaring.</p><div class="panel-nav"><a class="btn secondary" href="/admin/teachers">👩‍🏫 O‘qituvchilar</a><a class="btn secondary" href="#adminTests">📚 Testlar</a><a class="btn secondary" href="#adminResults">📊 Natijalar</a><a class="btn dark" href="/admin/logout">Chiqish</a></div></section>
    <div class="grid"><div class="stat"><b>{{tests|length}}</b>Jami testlar</div><div class="stat"><b>{{results|length}}</b>Natijalar</div><div class="stat"><b>{{results|selectattr('review_status','equalto','pending')|list|length}}</b>Tekshiruv kutilmoqda</div></div>
    <section class="card" id="adminTests"><h2>📚 Platformadagi testlar</h2><p class="muted">Testlarni faqat o‘qituvchilar o‘z kabinetidan yaratadi. Admin panelida test yaratish formasi mavjud emas.</p>{% for t in tests %}<div class="question"><b>{{t['title']}}</b><p class="muted">Kod: {{t['code']}} · {{t['question_count']}} savol · {{t['duration']}} daqiqa · {{t['file_name'] or 'Fayl biriktirilmagan'}}</p></div>{% else %}<p class="muted">Hozircha test yo‘q.</p>{% endfor %}</section>
    <section class="card" id="adminResults"><h2>📊 Natijalar va nazorat</h2>{% for r in results %}<div class="question {% if r['focus_warning']|int>0 %}warning-card{% endif %}"><p><b>{{r['full_name']}}</b> — {{r['title']}} · <b>{{r['percent']}}%</b></p><p class="muted">{{r['score']}} / {{r['total']}} ball · {{r['created_at']}}</p><p class="muted">Holat: {{'O‘qituvchi tekshiruvi kutilmoqda' if r['review_status']=='pending' else 'Sertifikat tayyor' if r['review_status']=='checked' else r['review_status']}} · Sertifikat ID: {{r['cert_id']}}</p>{% if r['focus_warning']|int>0 %}<p><span class="status-pill">⚠️ {{r['full_name']}} — {{r['focus_warning']}} ta ogohlantirish</span></p>{% endif %}{% if r['focus_warning']|int>=2 %}<form method="post" action="/admin/clearflag/{{r['id']}}"><button class="secondary">Blokni bekor qilish</button></form>{% endif %}{% if r['review_status']=='pending' %}<form method="post" action="/admin/review/{{r['id']}}"><h3>Yopiq savollar — rubrika</h3>{% for item in r['manual_items'] %}<div class="filebox"><b>{{item['question']}}-savol</b><p>{{item['answer'] or 'Javob kiritilmagan'}}</p><label>Baholash</label><select name="manual_score_{{loop.index0}}"><option value="0">Noto‘g‘ri — 0</option><option value="0.5">Qisman — 0.5</option><option value="1">To‘g‘ri — 1</option></select></div>{% endfor %}<button>Baholashni saqlash</button></form>{% endif %}</div>{% else %}<p>Natijalar yo‘q.</p>{% endfor %}</section>
    """,tests=tests,results=results)

@app.post("/admin/create")
@admin_required
def create_test():
    abort(404)

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
    deliver_certificate(cert)
    flash("Yopiq savollar baholandi. Sertifikat tayyor bo‘lsa rasm ko‘rinishida o‘qituvchi chatiga yuborildi.")
    return redirect(url_for("dashboard"))

@app.post("/admin/clearflag/<int:result_id>")
@admin_required
def clear_test_flag(result_id):
    with db() as c:
        r=c.execute("SELECT id FROM results WHERE id=?",(result_id,)).fetchone()
        if not r: abort(404)
        c.execute("UPDATE results SET focus_warning=0 WHERE id=?",(result_id,))
    with db() as c: r=c.execute("SELECT cert_id FROM results WHERE id=?",(result_id,)).fetchone()
    if r: deliver_certificate(r["cert_id"])
    flash("Test blokidan chiqarildi. Sertifikat tayyor bo‘lsa Telegramga yuborildi.")
    return redirect(url_for("dashboard"))

@app.get("/admin/teachers")
@admin_required
def admin_teachers():
    with db() as c: teachers=c.execute("SELECT * FROM teacher_accounts ORDER BY created_at DESC").fetchall()
    return page("O‘qituvchilar", """<section class="card"><p class="eyebrow">NEURON ADMIN</p><h1>O‘qituvchilar</h1><p><a href="/admin/dashboard">← Testlar paneliga qaytish</a></p><p class="muted">Bu bo‘lim test yaratish va test natijalari panelidan alohida ishlaydi.</p>{% for t in teachers %}<div class="question"><b>{{t['full_name'] or 'Ma’lumot kutilmoqda'}}</b> — {{t['subject']}}<p class="muted">📞 {{t['phone']}} · Telegram: {{t['telegram_id']}} · Holat: {{t['status']}}</p>{% if t['status']=='pending' and t['full_name'] %}<form method="post" action="/admin/teacher/{{t['telegram_id']}}/approve"><button class="full">✅ Tasdiqlash va login/parol berish</button></form><form method="post" action="/admin/teacher/{{t['telegram_id']}}/reject"><button class="secondary full">Rad etish</button></form>{% endif %}</div>{% else %}<p class="muted">Arizalar yo‘q.</p>{% endfor %}<a class="btn dark" href="/admin/dashboard">Testlar paneliga qaytish</a></section>""",teachers=teachers)

@app.post("/admin/teacher/<telegram_id>/approve")
@admin_required
def approve_teacher(telegram_id):
    username="teach_"+secrets.token_hex(3)
    password=secrets.token_urlsafe(9)
    pwh=hashlib.pbkdf2_hmac("sha256",password.encode(),b"neuron-teacher-v1",180000).hex()
    with db() as c:
        t=c.execute("SELECT chat_id,full_name,status FROM teacher_accounts WHERE telegram_id=?",(telegram_id,)).fetchone()
        if not t or t["status"]!="pending": flash("Kutilayotgan ariza topilmadi."); return redirect(url_for("admin_teachers"))
        c.execute("UPDATE teacher_accounts SET status='approved',stage='',username=?,password_hash=? WHERE telegram_id=?",(username,pwh,telegram_id))
    try: telegram_api("sendMessage",{"chat_id":t["chat_id"],"text":"🎉 Tabriklaymiz, arizangiz tasdiqlandi!\n\nNEURON o‘qituvchi kabineti\nLogin: "+username+"\nParol: "+password+"\n\nKabinet: https://mirzayev-group.onrender.com/teacher/login\nParolni xavfsiz saqlang."})
    except Exception: app.logger.exception("Teacher credentials delivery failed")
    flash("O‘qituvchi tasdiqlandi. Login va parol Telegram orqali yuborildi."); return redirect(url_for("admin_teachers"))

@app.post("/admin/teacher/<telegram_id>/reject")
@admin_required
def reject_teacher(telegram_id):
    with db() as c:
        t=c.execute("SELECT chat_id FROM teacher_accounts WHERE telegram_id=?",(telegram_id,)).fetchone()
        c.execute("UPDATE teacher_accounts SET status='rejected',stage='' WHERE telegram_id=?",(telegram_id,))
    if t: telegram_api("sendMessage",{"chat_id":t["chat_id"],"text":"Arizangiz rad etildi. Ma’lumotlarni aniqlashtirib, /teacher orqali qayta ariza yuborishingiz mumkin."})
    flash("Ariza rad etildi."); return redirect(url_for("admin_teachers"))

def teacher_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        if not session.get("teacher_id"): return redirect(url_for("teacher_login"))
        with db() as c: t=c.execute("SELECT * FROM teacher_accounts WHERE telegram_id=? AND status='approved'",(session["teacher_id"],)).fetchone()
        if not t: session.pop("teacher_id",None); return redirect(url_for("teacher_login"))
        return fn(*a,**kw)
    return wrapper

@app.route("/teacher/login",methods=["GET","POST"])
def teacher_login():
    if request.method=="POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        with db() as c: t=c.execute("SELECT * FROM teacher_accounts WHERE username=? AND status='approved'",(username,)).fetchone()
        check=hashlib.pbkdf2_hmac("sha256",password.encode(),b"neuron-teacher-v1",180000).hex()
        if t and t["password_hash"] and secrets.compare_digest(check,t["password_hash"]):
            session["teacher_id"]=t["telegram_id"]; return redirect(url_for("teacher_cabinet"))
        flash("Login yoki parol noto‘g‘ri.")
    return page("O‘qituvchi kirish", """<section class="card"><p class="eyebrow">NEURON TEACHER</p><h1>O‘qituvchi kabinetiga kirish</h1><p class="muted">Admin tasdiqlagach, login va parol Telegram bot orqali yuboriladi. Hali ro‘yxatdan o‘tmagan bo‘lsangiz, avval ariza yuboring.</p><form method="post"><label>Login</label><input name="username" required autocomplete="username"><label>Parol</label><input name="password" type="password" required autocomplete="current-password"><button class="full">Kabinetga kirish</button></form><a class="btn secondary full" href="/teacher/register">📝 O‘qituvchi sifatida ro‘yxatdan o‘tish</a><p class="muted"><a href="/">Bosh sahifaga qaytish</a></p></section>""")

@app.get("/teacher/register")
def teacher_register_page():
    return page("O‘qituvchi ro‘yxati", """<section class="card"><h1>O‘qituvchi bo‘lish</h1><p>Telegram botga o‘ting va pastdagi tugmani bosing.</p><a class="btn full" href="https://t.me/Test2026robot" target="_blank" rel="noopener">Telegram botni ochish</a><p class="muted">Botda /teacher buyrug‘i orqali telefon raqamingizni tasdiqlang, ism va faningizni kiriting.</p></section>""")

@app.get("/teacher/cabinet")
@teacher_required
def teacher_cabinet():
    with db() as c:
        t=c.execute("SELECT * FROM teacher_accounts WHERE telegram_id=?",(session["teacher_id"],)).fetchone()
        rows=c.execute("SELECT results.*,tests.title,tests.code,tests.subject,tests.grade FROM results JOIN tests ON tests.id=results.test_id WHERE tests.teacher_id=? ORDER BY results.id DESC LIMIT 100", (session["teacher_id"],)).fetchall()
        tests=c.execute("SELECT * FROM tests WHERE teacher_id=? ORDER BY id DESC LIMIT 100",(session["teacher_id"],)).fetchall()
    return page("O‘qituvchi kabineti", """<section class="panel-hero"><p class="eyebrow" style="color:#c7d7ff">NEURON V2 · TEACHER</p><h1>Assalomu alaykum, {{t['full_name']}}</h1><p class="muted">{{t['subject']}} · {{t['phone']}}</p><div class="panel-nav"><button type="button" class="secondary" data-panel-target="create">＋ Test yaratish</button><button type="button" class="secondary" data-panel-target="tests">📚 Testlarim</button><button type="button" class="secondary" data-panel-target="results">📊 Natijalar</button><button type="button" class="secondary" data-panel-target="certificates">🏅 Sertifikatlar</button></div></section>
<div class="grid"><div class="stat"><b>{{tests|length}}</b>Yaratilgan testlar</div><div class="stat"><b>{{rows|length}}</b>O‘quvchilar</div><div class="stat"><b>{{rows|selectattr('review_status','equalto','pending')|list|length}}</b>Tekshiruv kutilmoqda</div></div>
<section class="card teacher-screen active" id="create"><p class="eyebrow">TEST KONSTRUKTORI</p><h2>＋ Yangi test yaratish</h2><p class="muted">Fan va sinfni tanlang. Javoblar kalitini savol turi bo‘yicha belgilang — yopiq savollarni keyin o‘zingiz baholaysiz.</p>
<form method="post" action="/teacher/create" enctype="multipart/form-data"><div class="grid"><div><label>Fan nomi</label><input name="subject" value="{{t['subject']}}" required placeholder="Masalan: Tarix"></div><div><label>Sinf / kurs</label><input name="grade" required placeholder="Masalan: 8-sinf"></div></div><label>Test nomi</label><input name="title" required placeholder="Masalan: 1-mavzu nazorati"><div class="grid"><div><label>Davomiyligi (daqiqada)</label><input name="duration" type="number" min="1" max="240" value="30" required></div><div><label>Savollar soni</label><input name="question_count" type="number" min="1" max="100" value="10" required></div></div>
<label>Savollar fayli (PDF yoki DOCX, 15 MB gacha)</label><div class="filebox">📎 <b>Test hujjatini tanlang</b><input type="file" name="test_file" accept=".pdf,.docx" required></div>
<h3>EvalBee uslubidagi javoblar kaliti</h3><div class="key-help muted">🔵 Variantli · 🟢 To‘g‘ri/Noto‘g‘ri · ✍️ Qisqa javob · 📝 Yopiq savol — o‘qituvchi tekshiradi.</div><div id="teacherKeyRows"></div><input type="hidden" name="answer_key" id="teacherAnswerKey" required><p id="teacherKeyProgress" class="muted">0 ta javob belgilandi</p><button class="full">🚀 Testni yaratish</button></form></section>
<section class="card teacher-screen" id="tests"><h2>📚 Mening testlarim</h2>{% for x in tests %}<div class="question"><b>{{x['title']}}</b><p class="muted">{{x['subject']}} · {{x['grade']}} · {{x['question_count']}} savol · {{x['duration']}} daqiqa</p><p class="eyebrow">TEST KODI</p><div class="stat"><b style="font-size:20px">{{x['code']}}</b></div><button type="button" class="secondary full" onclick="navigator.clipboard.writeText('{{x['code']}}').then(()=>this.textContent='✓ Nusxalandi')">📋 Kodni nusxalash</button></div>{% else %}<p class="muted">Hali test yaratmagansiz.</p>{% endfor %}</section>
<section class="card teacher-screen" id="results"><h2>📊 O‘quvchilar natijalari</h2>{% for r in rows %}<div class="question {% if r['focus_warning']|int>0 %}warning-card{% endif %}"><b>{{r['full_name']}}</b> — {{r['title']}}<p class="muted">{{r['score']}} / {{r['total']}} · {{r['percent']}}% · {{r['created_at']}}</p>{% if r['focus_warning']|int>0 %}<p><span class="status-pill">⚠️ {{r['focus_warning']}} ta ogohlantirish</span> <b>{{r['full_name']}}</b></p>{% endif %}{% if r['review_status']=='pending' %}<p><span class="status-pill">Yopiq savollar tekshirilmoqda</span></p><a class="btn secondary full" href="/teacher/review/{{r['id']}}">📝 Yopiq savollarni tekshirish</a>{% else %}<span class="status-pill good">✓ Baholash yakunlangan</span>{% endif %}<a class="btn full" href="/certificate/{{r['cert_id']}}">🏅 Natija va sertifikat</a></div>{% else %}<p class="muted">Hozircha natija yo‘q.</p>{% endfor %}</section>
<section class="card teacher-screen" id="certificates"><h2>🏅 Sertifikat ko‘rinishi</h2><p class="muted">O‘quvchi sertifikati NEURON dizaynida ism, test nomi, ball, foiz va ID bilan shakllanadi. Tekshiruv yoki ogohlantirish cheklovi bo‘lsa, avval tekshiruv yakunlanadi.</p>{% for r in rows %}<div class="cert-preview"><div class="eyebrow">NEURON EDUCATION</div><h2 style="color:#315efb;margin:8px 0">SERTIFIKAT</h2><div>Ushbu sertifikat taqdim etiladi</div><div class="cert-name">{{r['full_name']}}</div><div class="muted">{{r['title']}}</div><h2>{{'%.1f'|format(r['score']|float)}} / {{r['total']}} &nbsp; | &nbsp; {{r['percent']}}%</h2><p class="muted">Sertifikat ID: {{r['cert_id']}}</p><p class="muted">Sana: {{r['created_at'][:10]}}</p>{% if r['review_status']=='pending' or r['focus_warning']|int>=2 %}<span class="status-pill">Tekshiruv / cheklov mavjud</span>{% else %}<span class="status-pill good">Sertifikat tayyor</span><a class="btn secondary full" href="/certificate/{{r['cert_id']}}/pdf">PDF sertifikatni ko‘rish</a>{% endif %}</div>{% else %}<p class="muted">Sertifikatlar natija kelgach shu yerda ko‘rinadi.</p>{% endfor %}</section>
<a class="btn dark full" href="/teacher/logout">Chiqish</a><script>
(function(){const count=document.querySelector('input[name="question_count"]');const box=document.getElementById('teacherKeyRows');const hidden=document.getElementById('teacherAnswerKey');const progress=document.getElementById('teacherKeyProgress');if(!count||!box)return;
function sync(){const rows=[...box.querySelectorAll('.teacher-key-row')];const vals=rows.map(r=>{const type=r.querySelector('select').value;if(type==='manual')return 'MANUAL';if(type==='short')return r.querySelector('.short-answer').value.trim();return r.querySelector('input:checked')?.value||'';});const done=vals.filter(Boolean).length;hidden.value=vals.join(',');progress.textContent=done+' / '+rows.length+' ta javob belgilandi';hidden.setCustomValidity(done===rows.length&&vals.every(Boolean)?'':'Har bir savol turini tanlab, javobini belgilang.');}
function draw(){const n=Math.max(1,Math.min(100,Number(count.value)||1));box.innerHTML='';for(let i=1;i<=n;i++){const r=document.createElement('div');r.className='question teacher-key-row';r.innerHTML='<b>'+i+'-savol</b><select aria-label="'+i+'-savol turi"><option value="mcq">Variantli — A / B / C / D</option><option value="tf">To‘g‘ri / Noto‘g‘ri</option><option value="short">Qisqa javob — avtomatik</option><option value="manual">Yopiq savol — o‘qituvchi tekshiradi</option></select><div class="bubbles"></div><input class="short-answer" maxlength="80" placeholder="To‘g‘ri javob matni" style="display:none"><p class="muted key-type-hint"></p>';const sel=r.querySelector('select'),b=r.querySelector('.bubbles'),short=r.querySelector('.short-answer'),hint=r.querySelector('.key-type-hint');function options(){b.innerHTML='';short.style.display=sel.value==='short'?'block':'none';hint.textContent=sel.value==='manual'?'O‘quvchi javob yozadi. Yakuniy ballni kabinetda siz qo‘yasiz.':sel.value==='short'?'O‘quvchi yozgan javob kalit bilan avtomatik solishtiriladi.':'To‘g‘ri javob variantini belgilang.';const values=sel.value==='mcq'?['A','B','C','D']:sel.value==='tf'?['TRUE','FALSE']:[];values.forEach(v=>{const l=document.createElement('label');l.className='bubble';l.innerHTML='<input type="radio" name="teacher_key_'+i+'" value="'+v+'"><span>'+(v==='TRUE'?'T':v==='FALSE'?'N':v)+'</span>';b.appendChild(l);});sync();}sel.addEventListener('change',options);r.addEventListener('change',sync);short.addEventListener('input',sync);options();box.appendChild(r);}sync();}
count.addEventListener('input',draw);draw();document.querySelectorAll('[data-panel-target]').forEach(btn=>btn.addEventListener('click',()=>{const target=btn.dataset.panelTarget;document.querySelectorAll('.teacher-screen').forEach(panel=>panel.classList.toggle('active',panel.id===target));document.querySelectorAll('[data-panel-target]').forEach(tab=>tab.classList.toggle('primary',tab===btn));window.scrollTo({top:0,behavior:'smooth'});}));})();</script></section>""",t=t,rows=rows,tests=tests)

@app.post("/teacher/create")
@teacher_required
def teacher_create_test():
    subject=request.form.get("subject","").strip()[:60]
    grade=request.form.get("grade","").strip()[:30]
    title=request.form.get("title","").strip()[:120]
    try: duration=max(1,min(240,int(request.form.get("duration",30)))); count=int(request.form.get("question_count",0))
    except Exception: flash("Davomiylik yoki savollar soni noto‘g‘ri."); return redirect(url_for("teacher_cabinet"))
    raw=request.form.get("answer_key","").strip(); uploaded=request.files.get("test_file")
    if not subject or not grade or not title or count<1 or count>100 or not uploaded or not uploaded.filename:
        flash("Fan, sinf, test nomi, savollar soni va faylni to‘ldiring."); return redirect(url_for("teacher_cabinet"))
    filename=secure_filename(uploaded.filename); ext=filename.rsplit(".",1)[-1].lower() if "." in filename else ""
    data=uploaded.read()
    if ext not in ("pdf","docx") or not data or len(data)>15*1024*1024:
        flash("Faqat 15 MB gacha PDF yoki DOCX fayl yuklang."); return redirect(url_for("teacher_cabinet"))
    answers=[x.strip() for x in raw.replace("\\n",",").replace(";",",").split(",") if x.strip()]
    if len(answers)!=count:
        flash(f"{count} ta savol uchun {count} ta javob kalitini belgilang."); return redirect(url_for("teacher_cabinet"))
    prefix="".join(ch for ch in subject.upper() if ch.isalnum())[:5] or "FAN"
    gradecode="".join(ch for ch in grade.upper() if ch.isalnum())[:4] or "SIN"
    initials="".join(x[0] for x in tname.split()[:2]).upper() if (tname:=get_teacher_name(session["teacher_id"])) else "UST"
    code=prefix+"-"+gradecode+"-"+initials+"-"+secrets.token_hex(2).upper()
    with db() as c:
        c.execute("INSERT INTO tests(title,code,duration,questions,created_at,file_name,file_data,answer_key,question_count,teacher_id,subject,grade) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(title,code,duration,"",datetime.utcnow().isoformat(),filename,data,json.dumps(answers,ensure_ascii=False),count,session["teacher_id"],subject,grade))
    flash("Test yaratildi! O‘quvchilarga fan va sinfga mos kodni bering: "+code)
    return redirect(url_for("teacher_cabinet"))

def get_teacher_name(teacher_id):
    with db() as c: row=c.execute("SELECT full_name FROM teacher_accounts WHERE telegram_id=?",(teacher_id,)).fetchone()
    return row["full_name"] if row else ""

@app.route("/teacher/review/<int:result_id>",methods=["GET","POST"])
@teacher_required
def teacher_review(result_id):
    with db() as c:
        r=c.execute("SELECT results.* FROM results JOIN tests ON tests.id=results.test_id WHERE results.id=? AND tests.teacher_id=?",(result_id,session["teacher_id"])).fetchone()
        if not r: abort(404)
        try: items=json.loads(r["manual_answers"] or "[]")
        except Exception: items=[]
        if request.method=="POST":
            try: got=sum(float(request.form.get(f"manual_score_{i}","0")) for i in range(len(items)))
            except Exception: got=-1
            if got<0 or got>len(items): flash("Baholar noto‘g‘ri."); return redirect(url_for("teacher_review",result_id=result_id))
            final=float(r["score"])+got; percent=round(final*100/float(r["total"])) if r["total"] else 0
            c.execute("UPDATE results SET score=?,percent=?,manual_score=?,review_status='checked' WHERE id=?",(final,percent,got,result_id))
            cert=r["cert_id"]
        else: cert=""
    if request.method=="POST":
        deliver_certificate(cert)
        flash("Baholash saqlandi. Sertifikat tayyor bo‘lsa Telegram o‘qituvchi chatiga yuborildi.")
        return redirect(url_for("teacher_cabinet"))
    return page("Yopiq savollar", """<section class="card"><h1>Yopiq savollarni tekshirish</h1>{% for item in items %}<div class="question"><b>{{item['question']}}-savol</b><p>{{item['answer'] or 'Javob yo‘q'}}</p><label>Baholash</label><select name="manual_score_{{loop.index0}}" form="review"><option value="0">0 — noto‘g‘ri</option><option value="0.5">0.5 — qisman</option><option value="1">1 — to‘g‘ri</option></select></div>{% endfor %}<form id="review" method="post"><button class="full">Baholashni saqlash</button></form></section>""",items=items)

@app.get("/teacher/logout")
def teacher_logout():
    session.pop("teacher_id",None); return redirect(url_for("teacher_login"))



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
    if not BOT_TOKEN: return False
    with db() as c:
        chats=[r["chat_id"] for r in c.execute("SELECT chat_id FROM teacher_accounts WHERE status='approved'").fetchall()]
    if TEACHER_CHAT_ID and TEACHER_CHAT_ID not in chats: chats.append(TEACHER_CHAT_ID)
    sent=0
    for chat_id in chats:
        try:
            result=telegram_api("sendMessage",{"chat_id":chat_id,"text":message,"reply_markup":{"inline_keyboard":[[{"text":"📋 O‘qituvchi paneli","url":"https://mirzayev-group.onrender.com/teacher/cabinet"}]]}})
            if result and result.get("ok"): sent+=1
        except Exception: app.logger.exception("Teacher Telegram notification failed")
    return sent>0

def certificate_image(cert_id):
    from PIL import Image, ImageDraw, ImageFont
    with db() as c:
        r=c.execute("SELECT results.*,tests.title FROM results JOIN tests ON tests.id=results.test_id WHERE cert_id=?",(cert_id,)).fetchone()
    if not r or r["review_status"]=="pending" or int(r["focus_warning"] or 0)>=2: return None
    im=Image.new("RGB",(1600,900),"#f3f6fc"); d=ImageDraw.Draw(im)
    d.rounded_rectangle((35,35,1565,865),radius=34,outline="#315efb",width=8,fill="#ffffff")
    d.rounded_rectangle((68,68,1532,832),radius=24,outline="#c7d5ff",width=3)
    font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    def font(size):
        try: return ImageFont.truetype(font_path,size)
        except Exception: return ImageFont.load_default()
    def centered(y,text,size,color,max_width=1380):
        size=int(size)
        while size>16:
            f=font(size)
            box=d.textbbox((0,0),str(text),font=f)
            if box[2]-box[0]<=max_width: break
            size-=2
        d.text((800,y),str(text),font=f,fill=color,anchor="mm")
    centered(155,"NEURON",62,"#18336b")
    centered(255,"SERTIFIKAT",43,"#315efb")
    centered(340,"Ushbu sertifikat taqdim etiladi",25,"#52617a")
    centered(445,r["full_name"],47,"#172b55")
    centered(535,"“"+str(r["title"])+"” test sinovi natijasi",24,"#52617a")
    centered(665,f'{float(r["score"]):.1f} / {r["total"]}     |     {r["percent"]}%',39,"#18336b")
    centered(755,"Sertifikat ID: "+str(r["cert_id"]),20,"#52617a")
    centered(800,"Sana: "+str(r["created_at"])[:10],18,"#52617a")
    out=io.BytesIO(); im.save(out,format="PNG"); out.seek(0); return out

def deliver_certificate(cert_id):
    pic=certificate_image(cert_id)
    if not pic or not BOT_TOKEN: return False
    with db() as c: chats=[r["chat_id"] for r in c.execute("SELECT chat_id FROM teacher_accounts WHERE status='approved'").fetchall()]
    # Multipart upload for Telegram photo endpoint
    boundary="----Neuron"+secrets.token_hex(12); payload=pic.getvalue(); sent=False
    for chat_id in chats:
        try:
            body=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n🏅 NEURON sertifikati · {cert_id}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"certificate.png\"\r\nContent-Type: image/png\r\n\r\n").encode()+payload+f"\r\n--{boundary}--\r\n".encode()
            req=urllib.request.Request(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",data=body,headers={"Content-Type":"multipart/form-data; boundary="+boundary})
            with urllib.request.urlopen(req,timeout=20) as response: sent=sent or json.loads(response.read().decode()).get("ok",False)
        except Exception: app.logger.exception("Certificate image Telegram delivery failed")
    return sent

@app.post("/telegram/webhook")
def telegram_webhook():
    if not BOT_TOKEN: return {"ok":False,"error":"bot_not_configured"},503
    if not secrets.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token",""),TELEGRAM_WEBHOOK_SECRET): abort(403)
    u=request.get_json(silent=True) or {}
    cq=u.get("callback_query")
    if cq:
        data=cq.get("data",""); cqchat=(cq.get("message") or {}).get("chat") or {}; chat_id=cqchat.get("id"); uid=str((cq.get("from") or {}).get("id") or "")
        if data=="teacher_register":
            with db() as c: c.execute("INSERT INTO teacher_accounts(telegram_id,chat_id,stage,created_at) VALUES(?,?,'phone',?) ON CONFLICT(telegram_id) DO UPDATE SET chat_id=excluded.chat_id,stage='phone',status=CASE WHEN teacher_accounts.status='approved' THEN 'approved' ELSE 'pending' END",(uid,str(chat_id),datetime.utcnow().isoformat()))
            telegram_api("sendMessage",{"chat_id":chat_id,"text":"👩‍🏫 O‘qituvchi sifatida ro‘yxatdan o‘tish. O‘z telefon raqamingizni tugma orqali tasdiqlang.","reply_markup":{"keyboard":[[{"text":"📱 Telefon raqamimni tasdiqlash","request_contact":True}]],"resize_keyboard":True,"one_time_keyboard":True}})
        telegram_api("answerCallbackQuery",{"callback_query_id":cq.get("id")})
        return {"ok":True}
    m=u.get("message") or {}; chat=m.get("chat") or {}; sender=m.get("from") or {}
    chat_id=chat.get("id"); uid=str(sender.get("id") or ""); msg=(m.get("text") or "").strip(); contact=m.get("contact")
    if not chat_id: return {"ok":True}
    with db() as c: t=c.execute("SELECT * FROM teacher_accounts WHERE telegram_id=?",(uid,)).fetchone()
    if msg in ("/teacher","/register_teacher"):
        with db() as c: c.execute("INSERT INTO teacher_accounts(telegram_id,chat_id,stage,created_at) VALUES(?,?,'phone',?) ON CONFLICT(telegram_id) DO UPDATE SET chat_id=excluded.chat_id,stage='phone',status=CASE WHEN teacher_accounts.status='approved' THEN 'approved' ELSE 'pending' END",(uid,str(chat_id),datetime.utcnow().isoformat()))
        telegram_api("sendMessage",{"chat_id":chat_id,"text":"O‘qituvchi ro‘yxati uchun telefon raqamingizni tasdiqlang:","reply_markup":{"keyboard":[[{"text":"📱 Telefon raqamimni tasdiqlash","request_contact":True}]],"resize_keyboard":True,"one_time_keyboard":True}})
    elif contact and t and t["stage"]=="phone":
        if str(contact.get("user_id",""))!=uid: telegram_api("sendMessage",{"chat_id":chat_id,"text":"Faqat o‘zingizning raqamingizni yuboring."})
        else:
            with db() as c: c.execute("UPDATE teacher_accounts SET phone=?,stage='name' WHERE telegram_id=?",(contact.get("phone_number",""),uid))
            telegram_api("sendMessage",{"chat_id":chat_id,"text":"Telefon tasdiqlandi. Ism-familiyangizni yozing.","reply_markup":{"remove_keyboard":True}})
    elif t and t["stage"]=="name" and msg and not msg.startswith("/"):
        with db() as c: c.execute("UPDATE teacher_accounts SET full_name=?,stage='subject' WHERE telegram_id=?",(msg[:100],uid))
        telegram_api("sendMessage",{"chat_id":chat_id,"text":"Qaysi fan o‘qituvchisisiz?"})
    elif t and t["stage"]=="subject" and msg and not msg.startswith("/"):
        with db() as c: c.execute("UPDATE teacher_accounts SET subject=?,status='pending',stage='',created_at=? WHERE telegram_id=?",(msg[:100],datetime.utcnow().isoformat(),uid))
        telegram_api("sendMessage",{"chat_id":chat_id,"text":"Arizangiz yuborildi. Admin tasdiqlashini kuting."})
    elif msg.startswith("/start"):
        telegram_api("sendMessage",{"chat_id":chat_id,"text":"Assalomu alaykum! NEURON botiga xush kelibsiz. O‘qituvchi bo‘lish uchun /teacher buyrug‘ini yuboring.","reply_markup":{"inline_keyboard":[[{"text":"🚀 Testni boshlash","web_app":{"url":MINI_APP_URL}}],[{"text":"👩‍🏫 O‘qituvchi sifatida ro‘yxatdan o‘tish","callback_data":"teacher_register"}]]}})
    elif msg=="/myid": telegram_api("sendMessage",{"chat_id":chat_id,"text":"Telegram ID: "+str(chat_id)})
    elif msg=="/help": telegram_api("sendMessage",{"chat_id":chat_id,"text":"/start — testni ochish\n/teacher — o‘qituvchi ro‘yxati"})
    return {"ok":True}

@app.get("/telegram/status")
def telegram_status():
    return {"bot_configured":bool(BOT_TOKEN),"mini_app_url":MINI_APP_URL,"webhook_path":"/telegram/webhook"}

def register_telegram_webhook():
    if not BOT_TOKEN: app.logger.warning("BOT_TOKEN is not configured."); return
    try:
        result=telegram_api("setWebhook",{"url":"https://mirzayev-group.onrender.com/telegram/webhook","secret_token":TELEGRAM_WEBHOOK_SECRET,"allowed_updates":["message","callback_query"]})
        app.logger.info("Telegram webhook registration: %s",result)
    except Exception: app.logger.exception("Telegram webhook registration failed")

init_db()
register_telegram_webhook()
