import os
import sqlite3
import secrets
import string
from datetime import datetime
from functools import wraps
from flask import Flask, request, redirect, url_for, session, flash, render_template_string, abort

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-before-production")
DB = os.environ.get("DATABASE_PATH", "mirzayev_group.db")
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS tests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL, duration INTEGER NOT NULL DEFAULT 30,
            questions TEXT NOT NULL, created_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS results(
            id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER NOT NULL,
            full_name TEXT NOT NULL, student_id TEXT NOT NULL,
            score INTEGER NOT NULL, total INTEGER NOT NULL, percent INTEGER NOT NULL,
            cert_id TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL)""")

BASE = """
<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} — MIRZAYEV GROUP</title><style>
:root{--navy:#08152e;--gold:#d6b56d;--cream:#f7f3e9}*{box-sizing:border-box}
body{margin:0;background:linear-gradient(140deg,#071329,#14284a);color:#f8f5ed;font:16px Arial,sans-serif;min-height:100vh}
header{padding:20px 5%;border-bottom:1px solid #b99a5944;display:flex;justify-content:space-between;align-items:center}
.brand{font-weight:800;letter-spacing:2px;color:var(--gold)}main{max-width:760px;margin:36px auto;padding:0 16px}
.card{background:#fff;color:#17233a;border-radius:18px;padding:26px;box-shadow:0 15px 50px #0005}
h1,h2{margin-top:0}h1{font-size:26px}.muted{color:#687386}.gold{color:#a77c2b}
input,textarea,select{width:100%;padding:13px;border:1px solid #cbd2dc;border-radius:9px;margin:7px 0 15px;font-size:16px}
button,.btn{display:inline-block;background:var(--navy);color:white;padding:13px 18px;border:0;border-radius:9px;text-decoration:none;font-weight:700;cursor:pointer}
button:hover,.btn:hover{background:#1b3764}.notice{padding:12px;background:#fff4d8;border-radius:9px;margin:12px 0;color:#654b16}
.question{padding:14px 0;border-bottom:1px solid #e3e7ed}.option{display:block;padding:8px}
footer{text-align:center;color:#b8c3d5;padding:24px;font-size:13px}
</style></head><body><header><div class="brand">MIRZAYEV GROUP</div><div>ONLINE TEST & CERTIFICATE</div></header><main>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class="notice">{{m}}</div>{% endfor %}{% endwith %}
{{ content|safe }}</main><footer>© MIRZAYEV GROUP · Ta’lim va natijalar platformasi</footer></body></html>
"""

def page(title, body, **ctx):
    return render_template_string(BASE, title=title, content=render_template_string(body, **ctx))

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*a, **kw)
    return wrapper

@app.get("/")
def home():
    return page("Bosh sahifa", """
    <section class="card"><p class="gold"><b>ROYAL PREMIUM EDUCATION</b></p>
    <h1>Bilimingizni sinab ko‘ring.</h1><p class="muted">Test kodini kiriting, savollarga javob bering va natijangiz bo‘yicha sertifikat oling.</p>
    <form method="post" action="/start">
      <label>To‘liq ism-familiya</label><input name="full_name" required maxlength="100" placeholder="Masalan: Ali Valiyev">
      <label>Shaxsiy ID</label><input name="student_id" required maxlength="40" placeholder="Shaxsiy ID">
      <label>Test kodi</label><input name="code" required maxlength="30" placeholder="Admin bergan kod">
      <button type="submit">Testni boshlash →</button>
    </form><p class="muted">Adminmisiz? <a href="/admin" class="gold">Admin panel</a></p></section>
    """)

@app.post("/start")
def start_test():
    name=request.form.get("full_name","").strip()
    sid=request.form.get("student_id","").strip()
    code=request.form.get("code","").strip().upper()
    with db() as c:
        test=c.execute("SELECT * FROM tests WHERE code=?",(code,)).fetchone()
    if not name or not sid or not test:
        flash("Ma’lumotlarni tekshiring. Test kodi topilmadi.")
        return redirect(url_for("home"))
    session["candidate"]={"name":name,"sid":sid,"test_id":test["id"]}
    return redirect(url_for("take_test", test_id=test["id"]))

@app.get("/test/<int:test_id>")
def take_test(test_id):
    candidate=session.get("candidate")
    if not candidate or candidate["test_id"]!=test_id: return redirect(url_for("home"))
    with db() as c: test=c.execute("SELECT * FROM tests WHERE id=?",(test_id,)).fetchone()
    if not test: abort(404)
    questions=[x.split("|") for x in test["questions"].splitlines() if x.strip()]
    return page("Test", """
    <section class="card"><p class="gold"><b>{{test['title']}}</b></p><h1>Test topshirish</h1>
    <p class="muted">{{candidate['name']}} · {{test['duration']}} daqiqa</p>
    <form method="post" action="/submit/{{test['id']}}">
    {% for q in questions %}<div class="question"><b>{{loop.index}}. {{q[0]}}</b>
    {% for opt in q[1:5] %}<label class="option"><input style="width:auto;margin:0 8px 0 0" type="radio" name="q{{loop.index0}}" value="{{'ABCD'[loop.index0]}}" required>{{'ABCD'[loop.index0]}}) {{opt}}</label>{% endfor %}
    </div>{% endfor %}<button type="submit">Javoblarni yuborish</button></form></section>
    """, test=test, candidate=candidate, questions=questions)

@app.post("/submit/<int:test_id>")
def submit_test(test_id):
    candidate=session.get("candidate")
    if not candidate or candidate["test_id"]!=test_id: return redirect(url_for("home"))
    with db() as c: test=c.execute("SELECT * FROM tests WHERE id=?",(test_id,)).fetchone()
    if not test: abort(404)
    questions=[x.split("|") for x in test["questions"].splitlines() if x.strip()]
    score=sum(1 for i,q in enumerate(questions) if len(q)>=6 and request.form.get(f"q{i}","").upper()==q[5].strip().upper())
    total=len(questions); percent=round(score*100/total) if total else 0
    cert="MG-"+secrets.token_hex(4).upper()
    with db() as c:
        c.execute("INSERT INTO results(test_id,full_name,student_id,score,total,percent,cert_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
          (test_id,candidate["name"],candidate["sid"],score,total,percent,cert,datetime.utcnow().isoformat()))
    session.pop("candidate",None)
    return redirect(url_for("certificate", cert_id=cert))

@app.get("/certificate/<cert_id>")
def certificate(cert_id):
    with db() as c:
        r=c.execute("SELECT results.*,tests.title FROM results JOIN tests ON tests.id=results.test_id WHERE cert_id=?",(cert_id,)).fetchone()
    if not r: abort(404)
    level="A’lo" if r["percent"]>=90 else "Yaxshi" if r["percent"]>=70 else "Qoniqarli" if r["percent"]>=50 else "Ishtirok etdi"
    return page("Sertifikat", """
    <section class="card" style="text-align:center;border:7px double #c6a45e;background:#fbf7ed;padding:34px">
    <p class="gold"><b>MIRZAYEV GROUP</b></p><h1 style="font-family:Georgia;font-size:34px;color:#14284a">SERTIFIKAT</h1>
    <p class="muted">Ushbu sertifikat quyidagi ishtirokchiga taqdim etiladi</p>
    <h2 style="color:#9a742d">{{r['full_name']}}</h2><p>{{r['title']}} testida qatnashgani uchun</p>
    <h2 style="color:#14284a">{{r['score']}} / {{r['total']}} · {{r['percent']}}%</h2>
    <p><b>{{level}}</b></p><hr><p class="muted">Sertifikat ID: {{r['cert_id']}}</p>
    <p class="muted">Sana: {{r['created_at'][:10]}}</p><button onclick="window.print()">Sertifikatni chop etish / PDF</button>
    </section>
    """,r=r,level=level)

@app.route("/admin", methods=["GET","POST"])
def admin_login():
    if session.get("admin"): return redirect(url_for("dashboard"))
    if request.method=="POST":
        if ADMIN_PASSWORD and request.form.get("username")==ADMIN_USER and secrets.compare_digest(request.form.get("password",""),ADMIN_PASSWORD):
            session["admin"]=True; return redirect(url_for("dashboard"))
        flash("Kirish ma’lumotlari noto‘g‘ri yoki ADMIN_PASSWORD Render’da sozlanmagan.")
    return page("Admin kirish", """
    <section class="card"><h1>Admin panelga kirish</h1><form method="post">
    <label>Login</label><input name="username" required value="admin">
    <label>Parol</label><input name="password" type="password" required>
    <button>Kirish</button></form></section>""")

@app.get("/admin/dashboard")
@admin_required
def dashboard():
    with db() as c:
        tests=c.execute("SELECT * FROM tests ORDER BY id DESC").fetchall()
        results=c.execute("SELECT results.*,tests.title FROM results JOIN tests ON tests.id=results.test_id ORDER BY results.id DESC LIMIT 100").fetchall()
    return page("Admin", """
    <section class="card"><h1>Admin boshqaruv paneli</h1><p class="muted">Testlar va ishtirokchilar natijalari</p>
    <h2>Yangi test yaratish</h2><form method="post" action="/admin/create">
    <label>Test nomi</label><input name="title" required>
    <label>Test kodi (bo‘sh qoldirilsa avtomatik yaratiladi)</label><input name="code">
    <label>Davomiyligi (daqiqa)</label><input name="duration" type="number" min="1" max="240" value="30" required>
    <label>Savollar — har qatorda: Savol|A|B|C|D|To‘g‘ri javob (A/B/C/D)</label>
    <textarea name="questions" rows="8" required placeholder="O‘zbekiston poytaxti?|Toshkent|Samarqand|Buxoro|Xiva|A"></textarea>
    <button>Test yaratish</button></form><hr><h2>Mavjud testlar</h2>
    {% for t in tests %}<p><b>{{t['title']}}</b> · Kod: <b>{{t['code']}}</b> · {{t['duration']}} daqiqa</p>{% else %}<p>Hozircha test yo‘q.</p>{% endfor %}
    <hr><h2>Oxirgi natijalar</h2>{% for r in results %}<p>{{r['full_name']}} — {{r['title']}} — {{r['percent']}}% · {{r['cert_id']}}</p>{% else %}<p>Natijalar hali yo‘q.</p>{% endfor %}
    <a class="btn" href="/admin/logout">Chiqish</a></section>
    """,tests=tests,results=results)

@app.post("/admin/create")
@admin_required
def create_test():
    title=request.form.get("title","").strip()
    questions=request.form.get("questions","").strip()
    duration=max(1,min(240,int(request.form.get("duration",30))))
    code=request.form.get("code","").strip().upper() or "".join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(6))
    valid=[x.split("|") for x in questions.splitlines() if x.strip()]
    if not title or not valid or any(len(q)!=6 or q[5].strip().upper() not in "ABCD" for q in valid):
        flash("Savollar formati xato. Har qatorda 6 ta qism va oxirida A/B/C/D bo‘lishi kerak.")
        return redirect(url_for("dashboard"))
    try:
        with db() as c: c.execute("INSERT INTO tests(title,code,duration,questions,created_at) VALUES(?,?,?,?,?)",(title,code,duration,questions,datetime.utcnow().isoformat()))
        flash("Test yaratildi. Kod: "+code)
    except sqlite3.IntegrityError: flash("Bu test kodi band. Boshqa kod kiriting.")
    return redirect(url_for("dashboard"))

@app.get("/admin/logout")
def logout():
    session.clear(); return redirect(url_for("home"))

init_db()
