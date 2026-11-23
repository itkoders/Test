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
from datetime import timedelta
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True, PERMANENT_SESSION_LIFETIME=timedelta(days=30))
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
                    manual_score DOUBLE PRECISION NOT NULL DEFAULT 0, teacher_id TEXT, subject TEXT NOT NULL DEFAULT '', grade TEXT NOT NULL DEFAULT '', certificate_mode TEXT NOT NULL DEFAULT 'manual', certificate_min_score DOUBLE PRECISION NOT NULL DEFAULT 0)""")
                cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS teacher_id TEXT")
                cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS subject TEXT NOT NULL DEFAULT ''")
                cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS grade TEXT NOT NULL DEFAULT ''")
                cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS certificate_mode TEXT NOT NULL DEFAULT 'manual'")
                cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS certificate_min_score DOUBLE PRECISION NOT NULL DEFAULT 0")
                cur.execute("ALTER TABLE tests ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
                cur.execute("""CREATE TABLE IF NOT EXISTS results(
                    id BIGSERIAL PRIMARY KEY, test_id BIGINT NOT NULL, full_name TEXT NOT NULL,
                    student_id TEXT NOT NULL DEFAULT '', score DOUBLE PRECISION NOT NULL,
                    total INTEGER NOT NULL, percent INTEGER NOT NULL, cert_id TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL, manual_answers TEXT NOT NULL DEFAULT '[]',
                    review_status TEXT NOT NULL DEFAULT 'checked', focus_warning INTEGER NOT NULL DEFAULT 0,
                    manual_score DOUBLE PRECISION NOT NULL DEFAULT 0, certificate_approved BOOLEAN NOT NULL DEFAULT FALSE)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS teacher_accounts(
                    telegram_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, full_name TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending', stage TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, username TEXT UNIQUE, password_hash TEXT)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS blocked_students(test_id BIGINT NOT NULL,full_name TEXT NOT NULL,full_name_key TEXT NOT NULL,reason TEXT NOT NULL DEFAULT 'Ogohlantirish',created_at TEXT NOT NULL,PRIMARY KEY(test_id,full_name_key))""")
                cur.execute("ALTER TABLE results ADD COLUMN IF NOT EXISTS certificate_approved BOOLEAN NOT NULL DEFAULT FALSE")
                cur.execute("ALTER TABLE teacher_accounts ADD COLUMN IF NOT EXISTS certificate_org_name TEXT NOT NULL DEFAULT ''")
                cur.execute("ALTER TABLE teacher_accounts ADD COLUMN IF NOT EXISTS certificate_logo TEXT")
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
        for name, typ in [("file_name","TEXT"),("file_data","BLOB"),("answer_key","TEXT"),("question_count","INTEGER NOT NULL DEFAULT 0"),("manual_answers","TEXT"),("review_status","TEXT NOT NULL DEFAULT 'checked'"),("focus_warning","INTEGER NOT NULL DEFAULT 0"),("manual_score","INTEGER NOT NULL DEFAULT 0"),("teacher_id","TEXT"),("subject","TEXT NOT NULL DEFAULT ''"),("grade","TEXT NOT NULL DEFAULT ''"),("certificate_mode","TEXT NOT NULL DEFAULT 'manual'"),("certificate_min_score","REAL NOT NULL DEFAULT 0"),("is_active","INTEGER NOT NULL DEFAULT 1")]:
            if name not in cols: c.execute(f"ALTER TABLE tests ADD COLUMN {name} {typ}")
        result_cols = {r["name"] for r in c.execute("PRAGMA table_info(results)").fetchall()}
        for name, typ in [("manual_answers","TEXT NOT NULL DEFAULT '[]'"),("review_status","TEXT NOT NULL DEFAULT 'checked'"),("focus_warning","INTEGER NOT NULL DEFAULT 0"),("manual_score","INTEGER NOT NULL DEFAULT 0"),("certificate_approved","INTEGER NOT NULL DEFAULT 0")]:
            if name not in result_cols: c.execute(f"ALTER TABLE results ADD COLUMN {name} {typ}")
        c.execute("""CREATE TABLE IF NOT EXISTS blocked_students(test_id INTEGER NOT NULL,full_name TEXT NOT NULL,full_name_key TEXT NOT NULL,reason TEXT NOT NULL DEFAULT 'Ogohlantirish',created_at TEXT NOT NULL,PRIMARY KEY(test_id,full_name_key))""")
        c.execute("""CREATE TABLE IF NOT EXISTS teacher_accounts(telegram_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, full_name TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', stage TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, username TEXT, password_hash TEXT)""")
        tc={r["name"] for r in c.execute("PRAGMA table_info(teacher_accounts)").fetchall()}
        for n,t in [("username","TEXT"),("password_hash","TEXT"),("certificate_org_name","TEXT NOT NULL DEFAULT ''"),("certificate_logo","TEXT")]:
            if n not in tc: c.execute(f"ALTER TABLE teacher_accounts ADD COLUMN {n} {t}")

BASE = """
<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#080f1e"><title>{{ title }} — NEURON</title>
<style>
:root{color-scheme:dark;--bg:#070d1b;--surface:rgba(17,29,52,.78);--surface2:#152340;--text:#eef4ff;--muted:#93a6c6;--line:rgba(148,178,220,.16);--brand:#6366f1;--cyan:#22d3ee;--ok:#34d399;--warn:#fbbf24;--bad:#f87171;--soft:rgba(34,211,238,.12);--grad:linear-gradient(120deg,#6366f1,#22d3ee);--shadow:0 24px 60px -30px rgba(0,0,0,.85);--r:26px}
:root[data-theme="light"]{color-scheme:light;--bg:#f2f6ff;--surface:rgba(255,255,255,.86);--surface2:#f4f7ff;--text:#0f1b33;--muted:#5b6b86;--line:rgba(60,90,140,.15);--brand:#4f46e5;--cyan:#0891b2;--soft:rgba(8,145,178,.1);--shadow:0 24px 50px -32px rgba(40,60,120,.38)}
[hidden]{display:none!important}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:15px/1.5 Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;-webkit-font-smoothing:antialiased}
body{min-height:100vh;padding-bottom:calc(112px + env(safe-area-inset-bottom));background:radial-gradient(900px 520px at 8% -12%,rgba(99,102,241,.25),transparent 60%),radial-gradient(760px 460px at 100% 0%,rgba(34,211,238,.14),transparent 55%),var(--bg);background-attachment:fixed}
:root[data-theme="light"] body{background:radial-gradient(900px 520px at 8% -12%,rgba(99,102,241,.14),transparent 60%),radial-gradient(760px 460px at 100% 0%,rgba(34,211,238,.12),transparent 55%),var(--bg)}
button,input,select,textarea{font:inherit}
a{color:var(--brand);text-decoration:none}
header{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:12px;padding:12px 16px;background:rgba(7,13,27,.72);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--line)}
:root[data-theme="light"] header{background:rgba(242,246,255,.85)}
.brand{flex:1;font-size:18px;font-weight:850;letter-spacing:1.4px}.brand span{color:var(--cyan)}.brand small{display:block;font-size:10px;letter-spacing:2px;color:var(--muted);font-weight:700}
.back-button,.theme-button{display:grid;place-items:center;width:42px;height:42px;min-width:42px;border:1px solid var(--line);border-radius:14px;background:var(--surface2);color:var(--text);text-decoration:none;font-size:20px;cursor:pointer}
main{width:min(100%,760px);margin:18px auto 0;padding:0 16px 28px}
@media(min-width:800px){main{margin-top:28px}}
.card,.panel-hero{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:22px;box-shadow:var(--shadow);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);margin-bottom:16px}
.panel-hero{background:linear-gradient(135deg,rgba(99,102,241,.3),rgba(34,211,238,.14)),var(--surface);position:relative;overflow:hidden}
.panel-hero:after{content:"";position:absolute;width:220px;height:220px;right:-90px;top:-110px;border-radius:50%;background:radial-gradient(circle,rgba(34,211,238,.25),transparent 70%);pointer-events:none}
body[data-role="teacher"]{--brand:#22d3ee;--cyan:#67e8f9;--grad:linear-gradient(120deg,#0891b2,#2563eb 72%,#4f46e5)}
body[data-role="teacher"] .panel-hero{background:linear-gradient(135deg,rgba(34,211,238,.25),rgba(37,99,235,.2)),var(--surface)}
body[data-role="admin"]{--brand:#a78bfa;--cyan:#67e8f9;--grad:linear-gradient(120deg,#6d28d9,#2563eb 62%,#0891b2)}
body[data-role="admin"] .panel-hero{background:linear-gradient(135deg,rgba(167,139,250,.3),rgba(37,99,235,.15)),var(--surface)}
h1{font-size:clamp(25px,5.4vw,36px);line-height:1.12;letter-spacing:-.035em;margin:0 0 10px;font-weight:850}
h2{font-size:20px;letter-spacing:-.025em;margin:0 0 12px;font-weight:780}
h3{font-size:16px;margin:16px 0 8px}
.card>h1:first-child,.card>h2:first-child{margin-top:0}
.muted,.hint{color:var(--muted);line-height:1.6}
.small{font-size:13px}
.eyebrow{display:inline-block;color:var(--cyan);font-size:11px;font-weight:850;letter-spacing:1.7px;text-transform:uppercase;margin-bottom:8px}
.grad-text{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
label{display:block;font-weight:650;font-size:13px;color:var(--muted);margin-top:4px}
input,select,textarea{width:100%;min-height:54px;padding:14px 16px;margin:6px 0 14px;border:1px solid var(--line);border-radius:16px;background:var(--surface2);color:var(--text);transition:border-color .15s,box-shadow .15s}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--cyan);box-shadow:0 0 0 4px var(--soft)}
.btn,button{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:52px;max-width:100%;padding:14px 20px;border:0;border-radius:16px;background:var(--grad);color:#fff;font-weight:750;font-size:15px;line-height:1.25;cursor:pointer;text-decoration:none;box-shadow:0 14px 30px -16px rgba(99,102,241,.95);transition:transform .15s ease,filter .15s ease,box-shadow .15s ease;touch-action:manipulation}
.btn:hover,button:hover:not(:disabled){filter:brightness(1.08);transform:translateY(-1px)}
.btn:active,button:active:not(:disabled){transform:scale(.98)}
.btn.secondary,button.secondary{background:var(--surface2);color:var(--text);border:1px solid var(--line);box-shadow:none}
.btn.dark{background:#111a33;color:#fff}
.btn.full,button.full{width:100%}
.btn.big,button.big{min-height:58px;font-size:16px;border-radius:18px}
button:disabled{opacity:.4;cursor:not-allowed;transform:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:12px 0}
.grid.two{grid-template-columns:1fr 1fr}
.stat{padding:18px;border-radius:20px;background:var(--surface2);border:1px solid var(--line);color:var(--muted);text-align:center}
.stat b{display:block;font-size:26px;color:var(--text);letter-spacing:-.02em;margin-bottom:2px}
.notice{padding:14px 16px;border-radius:16px;background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.3);color:var(--warn);margin-bottom:14px;font-weight:600}
.filebox{border:1px dashed var(--line);background:var(--surface2);border-radius:20px;padding:18px;text-align:center;margin:14px 0}
.question{border:1px solid var(--line);border-radius:22px;padding:18px;margin:12px 0;background:var(--surface2)}
.q-head{display:flex;align-items:center;gap:12px;margin-bottom:6px}
.qnum{display:inline-grid;place-items:center;min-width:44px;height:44px;padding:0 10px;border-radius:14px;background:var(--soft);color:var(--cyan);font-weight:850}
.bubbles{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 8px}
.bubble{position:relative;display:inline-flex;align-items:center;justify-content:center;width:56px;height:56px;border:1px solid var(--line);border-radius:18px;background:var(--surface);color:var(--text);font-weight:800;cursor:pointer;transition:transform .12s,box-shadow .12s,background .12s}
.bubble.wide{width:auto;padding:0 20px;border-radius:999px}
.bubble input{position:absolute;opacity:0;width:1px;height:1px;margin:0;padding:0;min-height:0;border:0}
.bubble:active{transform:scale(.94)}
.bubble:has(input:checked){background:var(--grad);color:#fff;border-color:transparent;box-shadow:0 12px 24px -12px rgba(99,102,241,.95)}
.screen{display:none}
.screen.active{display:block;animation:fadeUp .28s ease}
@keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.topbar{position:sticky;top:72px;z-index:5;padding:16px 18px}
.topbar-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-weight:700}
.clock-pill{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:999px;background:var(--grad);color:#fff;font-weight:800;font-variant-numeric:tabular-nums;box-shadow:0 10px 22px -12px rgba(99,102,241,.9)}
.progress{height:8px;border-radius:999px;background:var(--surface2);overflow:hidden;border:1px solid var(--line)}
.progress i{display:block;height:100%;width:0;border-radius:999px;background:var(--grad);transition:width .35s ease}
.qdots{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
.qdot{min-width:40px;min-height:40px;padding:0 8px;border-radius:12px;background:var(--surface2);color:var(--muted);border:1px solid var(--line);box-shadow:none;font-weight:750}
.qdot.done{color:var(--ok);border-color:rgba(52,211,153,.4)}
.qdot.current{background:var(--grad);color:#fff;border-color:transparent}
.rules{padding-left:20px;color:var(--muted);line-height:1.8}
.rev-item{display:flex;align-items:center;gap:12px;width:100%;margin:8px 0;padding:12px 14px;border-radius:18px;background:var(--surface2);border:1px solid var(--line);color:var(--text);text-align:left;box-shadow:none}
.rev-item .qnum{min-width:40px;height:40px}
.rev-text{flex:1;min-width:0;display:flex;flex-direction:column}
.rev-text small{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rev-status{font-weight:800;color:var(--muted)}
.rev-item.done .rev-status{color:var(--ok)}
.timer{position:sticky;top:72px;z-index:3;background:var(--grad);color:#fff;border-radius:18px;padding:12px 16px;text-align:center;font-weight:800;box-shadow:var(--shadow)}
.panel-nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:16px 0}
.teacher-screen,.admin-screen{display:none}
.teacher-screen.active,.admin-screen.active{display:block;animation:fadeUp .28s ease}
.status-pill{display:inline-flex;align-items:center;gap:4px;border-radius:999px;padding:6px 12px;background:rgba(251,191,36,.14);color:var(--warn);font-size:12px;font-weight:800;border:1px solid rgba(251,191,36,.25)}
.status-pill.good{background:rgba(52,211,153,.14);color:var(--ok);border-color:rgba(52,211,153,.3)}
.warning-card{border-color:rgba(251,191,36,.38);background:rgba(251,191,36,.07)}
.cert-preview{position:relative;border:1px solid #c7d7ff;border-radius:22px;padding:26px 20px;text-align:center;background:linear-gradient(135deg,#ffffff,#eef4ff);color:#18336b;aspect-ratio:16/9;display:flex;flex-direction:column;justify-content:center;gap:6px;max-width:760px;margin:14px auto;box-shadow:0 22px 44px -24px rgba(30,64,175,.5)}
.cert-preview.mini{aspect-ratio:auto;max-width:none}
.cert-preview h2{margin:4px 0;color:#4f46e5}
.cert-preview .cert-name{font-size:clamp(18px,4vw,30px);font-weight:850;margin:8px 0;color:#0f1b33}
.cert-preview .muted{color:#5b6b86}
.result-card{background:linear-gradient(160deg,rgba(14,58,108,.97),rgba(15,23,48,.97));border-color:rgba(103,232,249,.3);text-align:center}
.result-card .stat{background:rgba(255,255,255,.05);border-color:rgba(103,232,249,.25);color:#cfe6ff;max-width:380px;margin:16px auto}
.result-card .stat b{font-size:36px;background:linear-gradient(120deg,#67e8f9,#60a5fa);-webkit-background-clip:text;background-clip:text;color:transparent}
.hero{position:relative;overflow:hidden;padding:30px 24px}
.hero:after{content:"";position:absolute;width:260px;height:260px;right:-120px;top:-120px;border-radius:50%;background:radial-gradient(circle,rgba(99,102,241,.35),transparent 70%);pointer-events:none}
.hero-badge{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border-radius:999px;background:var(--soft);color:var(--cyan);font-size:12px;font-weight:800;letter-spacing:.4px;margin-bottom:14px}
.start-form label{margin-top:10px}
.feature-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0}
.feature{margin:0;padding:18px}
.feature b{display:block;font-size:16px;margin-bottom:4px}
.app-dock{position:fixed;z-index:50;left:12px;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));max-width:560px;margin:0 auto;display:flex;gap:6px;justify-content:space-around;padding:8px;border:1px solid var(--line);border-radius:26px;background:rgba(12,22,40,.86);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);box-shadow:0 20px 50px -20px rgba(0,0,0,.6);overflow-x:auto;scrollbar-width:none}
.app-dock::-webkit-scrollbar{display:none}
.app-dock a,.app-dock button{flex:1 0 62px;min-width:58px;min-height:58px;padding:6px 4px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;border:0;border-radius:18px;background:transparent;color:#8ea3c2;font-size:10px;font-weight:750;text-decoration:none;white-space:nowrap;box-shadow:none}
.app-dock .active,.app-dock button.active{background:var(--soft);color:var(--cyan)}
.app-dock .ico{font-size:19px;line-height:1.2}
.app-dock a:hover,.app-dock button:hover{filter:none;transform:none}
:root[data-theme="light"] .app-dock{background:rgba(255,255,255,.92);box-shadow:0 16px 40px -20px rgba(30,60,110,.35)}
:root[data-theme="light"] .app-dock a,:root[data-theme="light"] .app-dock button{color:#60758d}
.key-help{background:var(--surface2);border:1px solid var(--line);border-radius:16px;padding:12px;margin:10px 0}
.docx-view p{margin:0 0 10px}
footer{display:none}
@media(max-width:600px){main{padding:0 12px 24px;margin-top:12px}.card,.panel-hero{padding:18px;border-radius:22px}.grid.two{grid-template-columns:1fr 1fr}.topbar{top:68px}.timer{top:68px}.bubble{width:50px;height:50px}.bubble.wide{width:auto}}
</style><script src="https://telegram.org/js/telegram-web-app.js"></script></head><body data-role="{{ 'teacher' if session.get('teacher_id') else 'admin' if session.get('admin') else 'student' }}">
<header><a class="back-button" href="{{back_url}}" onclick="if(history.length>1){history.back();return false}" aria-label="Orqaga qaytish">‹</a><div class="brand">NEURON<span>.</span><small>SMART TEST PLATFORM</small></div><button class="theme-button" id="themeToggle" type="button" aria-label="Mavzuni almashtirish">◐</button></header>
<main>{% with messages=get_flashed_messages() %}{% for m in messages %}<div class="notice">{{m}}</div>{% endfor %}{% endwith %}{{ content|safe }}</main>
{% if session.get('teacher_id') %}<nav class="app-dock" aria-label="O‘qituvchi menyusi">
{% if request.path=='/teacher/cabinet' %}<button data-panel-target="create"><span class="ico">＋</span>Yaratish</button><button data-panel-target="tests"><span class="ico">📚</span>Testlar</button><button data-panel-target="results"><span class="ico">📊</span>Natija</button><button data-panel-target="warnings"><span class="ico">⚠️</span>Ogohlantirish</button><button data-panel-target="certificates"><span class="ico">🏅</span>Sertifikat</button>
{% else %}<a href="/teacher/cabinet"><span class="ico">⌂</span>Kabinet</a><a href="/teacher/account"><span class="ico">⚙️</span>Sozlamalar</a><a href="/teacher/logout"><span class="ico">↪</span>Chiqish</a>{% endif %}
</nav>{% elif session.get('admin') %}<nav class="app-dock" aria-label="Admin menyusi">
{% if request.path=='/admin/dashboard' %}<button data-admin-target="overview"><span class="ico">⌂</span>Umumiy</button><button data-admin-target="adminTests"><span class="ico">📚</span>Testlar</button><button data-admin-target="adminResults"><span class="ico">📊</span>Natijalar</button><button data-admin-target="adminWarnings"><span class="ico">⚠️</span>Ogohlantirish</button><a href="/admin/teachers"><span class="ico">👩‍🏫</span>Ustozlar</a><a href="/admin/logout"><span class="ico">↪</span>Chiqish</a>
{% else %}<a href="/admin/dashboard"><span class="ico">⌂</span>Boshqaruv</a><a href="/admin/logout"><span class="ico">↪</span>Chiqish</a>{% endif %}
</nav>{% endif %}
<script>
(function(){const root=document.documentElement,stored=localStorage.getItem('neuron-test-theme');root.dataset.theme=stored||'dark';const btn=document.getElementById('themeToggle');btn.addEventListener('click',()=>{root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';localStorage.setItem('neuron-test-theme',root.dataset.theme);});
document.querySelectorAll('[data-panel-target]').forEach(btn=>btn.addEventListener('click',()=>{const id=btn.dataset.panelTarget;document.querySelectorAll('.teacher-screen').forEach(x=>x.classList.toggle('active',x.id===id));document.querySelectorAll('[data-panel-target]').forEach(x=>x.classList.toggle('active',x===btn));window.scrollTo({top:0,behavior:'smooth'});}));const firstTeacher=document.querySelector('[data-panel-target="create"]');if(firstTeacher)firstTeacher.classList.add('active');
document.querySelectorAll('[data-admin-target]').forEach(btn=>btn.addEventListener('click',()=>{const id=btn.dataset.adminTarget;document.querySelectorAll('.admin-screen').forEach(x=>x.classList.toggle('active',x.id===id));document.querySelectorAll('[data-admin-target]').forEach(x=>x.classList.toggle('active',x===btn));window.scrollTo({top:0,behavior:'smooth'});}));const hashTarget=location.hash.slice(1);const hashBtn=[...document.querySelectorAll('[data-panel-target],[data-admin-target]')].find(b=>(b.dataset.panelTarget||b.dataset.adminTarget)===hashTarget);if(hashBtn)hashBtn.click();else{const firstAdmin=document.querySelector('[data-admin-target="overview"]');if(firstAdmin)firstAdmin.classList.add('active');}
window.copyText=function(btn,t){const done=()=>{btn.textContent='✓ Nusxalandi'};const fallback=()=>{const ta=document.createElement('textarea');ta.value=t;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();try{document.execCommand('copy');done();}catch(e){btn.textContent='Kodni qo‘lda yozing: '+t;}ta.remove();};if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(done,fallback);}else{fallback();}};
if(window.Telegram&&Telegram.WebApp){Telegram.WebApp.ready();Telegram.WebApp.expand();}
})();
</script></body></html>
"""

def page(title, body, **ctx):
    back_url=("/teacher/cabinet" if request.path.startswith("/teacher/") and request.path!="/teacher/cabinet" else "/") if session.get("teacher_id") else ("/admin/dashboard" if request.path!="/admin/dashboard" else "/") if session.get("admin") else "/"
    return render_template_string(BASE, title=title, content=render_template_string(body, **ctx), back_url=back_url)

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
    with db() as c: test=c.execute("SELECT * FROM tests WHERE code=? AND is_active=TRUE", (code,)).fetchone()
    if not name or not test:
        flash("Ism yoki test kodi noto‘g‘ri.")
        return redirect(url_for("home"))
    with db() as c:
        blocked=c.execute("SELECT 1 FROM blocked_students WHERE test_id=? AND full_name_key=?",(test["id"],name.strip().lower())).fetchone()
    if blocked:
        flash("Bu test uchun kirishingiz o‘qituvchi tomonidan vaqtincha bloklangan. O‘qituvchingizga murojaat qiling.")
        return redirect(url_for("home"))
    session["candidate"]={"name":name,"test_id":test["id"]}
    session["started_at"]=datetime.utcnow().timestamp()
    session["test_deadline"]=session["started_at"]+int(test["duration"] or 30)*60
    session["focus_warning_count"]=0
    return redirect(url_for("take_test", test_id=test["id"]))

@app.post("/test/<int:test_id>/warning")
def record_test_warning(test_id):
    candidate=session.get("candidate")
    if not candidate or int(candidate.get("test_id",-1))!=test_id:
        return {"ok":False},403
    test=get_test(test_id)
    if not test or not test["is_active"]:
        return {"ok":False,"closed":True},409
    deadline=float(session.get("test_deadline",0) or 0)
    if not deadline or datetime.utcnow().timestamp()>=deadline:
        return {"ok":False,"closed":True},409
    try: count=max(0,min(10,int(request.form.get("count","0"))))
    except Exception: count=0
    session["focus_warning_count"]=max(int(session.get("focus_warning_count",0) or 0),count)
    return {"ok":True,"count":session["focus_warning_count"]}

@app.get("/test/<int:test_id>/status")
def test_status(test_id):
    candidate=session.get("candidate")
    if not candidate or int(candidate.get("test_id",-1))!=test_id:
        return {"active":False,"remaining":0,"reason":"session"},403
    test=get_test(test_id)
    if not test or not test["is_active"]:
        return {"active":False,"remaining":0,"reason":"closed"}
    deadline=float(session.get("test_deadline",0) or 0)
    remaining=max(0,int(deadline-datetime.utcnow().timestamp()))
    return {"active":remaining>0,"remaining":remaining,"reason":"time" if remaining<=0 else "ok"}

@app.get("/test/<int:test_id>")
def take_test(test_id):
    candidate=session.get("candidate")
    test=get_test(test_id)
    if not candidate or candidate["test_id"]!=test_id or not test or not test["is_active"]:
        session.pop("candidate",None); session.pop("started_at",None); session.pop("test_deadline",None)
        flash("Bu test o‘qituvchi tomonidan yopilgan yoki o‘chirilgan.")
        return redirect(url_for("home"))
    count=int(test["question_count"] or 0)
    if count<1: count=len([x for x in (test["questions"] or "").splitlines() if x.strip()])
    remaining=max(0,int(session.get("test_deadline",datetime.utcnow().timestamp())-datetime.utcnow().timestamp()))
    try: keys=json.loads(test["answer_key"] or "[]")
    except Exception: keys=[]
    return page("Test ishlash", """
    <div class="grid"><section class="card"><p class="eyebrow">TEST TOPSHIRISH</p><h1>{{test['title']}}</h1>
    <p class="muted">{{candidate['name']}} · {{test['subject'] or 'Fan ko‘rsatilmagan'}} · {{test['grade'] or 'Sinf ko‘rsatilmagan'}} · {{count}} ta savol</p><div class="timer">⏱ Qolgan vaqt: <span id="clock">{{remaining//60}}:{{"%02d"|format(remaining%60)}}</span></div><p id="focusNotice" class="notice" hidden></p>
    <p class="muted">Savollarni hujjatdan o‘qing. Javoblarni o‘ng/ pastdagi bo‘limda belgilang.</p>
    {% if test['file_name'] %}<div class="filebox"><b>📄 Test savollari</b><p class="muted">Savollar alohida brauzer oynasida ochiladi. Javoblarni belgilash uchun test oynasiga qayting.</p><a id="testFileLink" class="btn secondary full" href="{{file_url}}" target="_blank" rel="noopener noreferrer">📖 Savollarni yangi oynada ochish</a></div>{% else %}<div class="notice">Test savollari fayli biriktirilmagan.</div>{% endif %}
    </section><section class="card"><h2>Javoblar varaqasi</h2><p class="muted">Har bir savol uchun variantni bosing. Yopiq savollarga yozma javob kiriting.</p><form id="answerForm" method="post" action="/submit/{{test['id']}}">
    <div id="studentProgress" class="muted">1 / {{count}}-savol</div><div class="bubbles" id="studentNav">{% for i in range(1,count+1) %}<button type="button" class="bubble" data-go="{{i}}">{{i}}</button>{% endfor %}</div>
    {% for i in range(1,count+1) %}{% set k=(keys[i-1]|string|upper) if keys|length>=i else '' %}{% set opts=['TRUE','FALSE'] if k in ['TRUE','FALSE'] else (['A','B','C','D'] if k in ['A','B','C','D'] else []) %}<div class="question student-question" data-q="{{i}}" {% if i!=1 %}hidden{% endif %}><div class="answer-row"><div class="qnum">{{i}}</div><div><b>{{i}}-savol</b><p class="muted">{{'Variantni tanlang yoki yozing' if opts else 'Javobingizni yozing'}}</p>{% if opts %}<div class="bubbles">{% for v in opts %}<label class="bubble"><input type="radio" name="q{{i}}" value="{{v}}"><span>{{'T' if v=='TRUE' else 'N' if v=='FALSE' else v}}</span></label>{% endfor %}</div>{% endif %}<input name="q{{i}}_text" placeholder="Javobingiz" aria-label="{{i}}-savol yozma javobi"></div></div></div>{% endfor %}
    <div class="grid"><button type="button" class="secondary" id="prevQ">← Oldingi</button><button type="button" id="nextQ">Keyingi →</button></div><button class="full" type="submit">Testni yakunlash ✓</button></form></section></section></div>
    <script>
const deadline={{(session.get('test_deadline',0)*1000)|int}};
const el=document.getElementById('clock'),form=document.getElementById('answerForm');
let focusWarnings={{session.get('focus_warning_count',0)|int}},openingTestFile=false,submitted=false;
function finish(){if(submitted)return;submitted=true;let h=form.querySelector('[name="focus_warning_count"]');if(!h){h=document.createElement('input');h.type='hidden';h.name='focus_warning_count';form.appendChild(h);}h.value=String(focusWarnings);form.submit();}
function tick(){const left=Math.max(0,Math.ceil((deadline-Date.now())/1000));el.textContent=Math.floor(left/60)+':'+String(left%60).padStart(2,'0');if(left<=0)finish();}
const qs=[...document.querySelectorAll('.student-question')],nav=[...document.querySelectorAll('#studentNav [data-go]')];let current=0;
function show(n){current=Math.max(0,Math.min(qs.length-1,n));qs.forEach((q,i)=>q.hidden=i!==current);document.getElementById('studentProgress').textContent=(current+1)+' / '+qs.length+'-savol';document.getElementById('prevQ').disabled=current===0;document.getElementById('nextQ').textContent=current===qs.length-1?'Oxirgi savol':'Keyingi →';nav.forEach((b,i)=>{const q=qs[i],txt=q.querySelector('input:not([type=radio])');b.style.opacity=q.querySelector('input:checked')||(txt&&txt.value.trim())?'1':'.55';b.style.background=i===current?'#315efb':'';b.style.color=i===current?'white':'';});}
document.getElementById('prevQ').onclick=()=>show(current-1);document.getElementById('nextQ').onclick=()=>show(current+1);nav.forEach((b,i)=>b.onclick=()=>show(i));form.addEventListener('change',()=>show(current));
const fileLink=document.getElementById('testFileLink');if(fileLink)fileLink.addEventListener('click',()=>{openingTestFile=true;});
document.addEventListener('visibilitychange',()=>{if(document.hidden){if(openingTestFile)return;focusWarnings=Math.min(10,focusWarnings+1);const note=document.getElementById('focusNotice');note.hidden=false;note.textContent='⚠️ Sizga ogohlantirish berildi ('+focusWarnings+'/2). Bu holat o‘qituvchiga ko‘rinadi.';fetch('/test/{{test["id"]}}/warning',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'count='+encodeURIComponent(focusWarnings),keepalive:true}).catch(()=>{});if(focusWarnings>=2)finish();}else{openingTestFile=false;tick();}});
form.addEventListener('submit',e=>{if(!submitted){e.preventDefault();if(!confirm('Javoblaringizni yakunlab yuborasizmi?'))return;finish();}});
show(0);tick();setInterval(tick,500);
async function syncServer(){try{const r=await fetch('/test/{{test["id"]}}/status',{cache:'no-store'});const data=await r.json();if(!r.ok||!data.active){const note=document.getElementById('focusNotice');note.hidden=false;note.textContent=data.reason==='closed'?'⛔ O‘qituvchi testni yopdi. Javoblar yakunlanmoqda.':'⏰ Belgilangan vaqt tugadi. Test yakunlanmoqda.';finish();}}catch(e){}}
syncServer();setInterval(syncServer,2000);</script>
    """,test=test,candidate=candidate,count=count,keys=keys,remaining=remaining,file_url=url_for("test_file",test_id=test_id,token=file_signer.dumps({"test_id":test_id,"name":candidate["name"]})))

@app.get("/testfile/<int:test_id>/<token>")
def test_file(test_id,token):
    try:
        payload=file_signer.loads(token,max_age=4*60*60)
        if int(payload.get("test_id",-1))!=test_id: abort(404)
    except (BadSignature,SignatureExpired,ValueError,TypeError):
        abort(404)
    test=get_test(test_id)
    if not test or not test["is_active"] or not test["file_data"]: abort(404)
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
    if not session.get("test_deadline"):
        abort(403, "Test muddati tugagan yoki sessiya bekor qilingan.")
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
    try: warnings=max(int(session.get("focus_warning_count",0) or 0),max(0,min(10,int(request.form.get("focus_warning_count","0")))))
    except Exception: warnings=max(0,min(10,int(session.get("focus_warning_count",0) or 0)))
    percent=round(score*100/total) if total else 0
    cert="NR-"+secrets.token_hex(4).upper()
    with db() as c:
        cur=c.execute("INSERT INTO results(test_id,full_name,student_id,score,total,percent,cert_id,created_at,manual_answers,review_status,focus_warning,certificate_approved) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (test_id,candidate["name"],"",score,total,percent,cert,datetime.utcnow().isoformat(),json.dumps(manual,ensure_ascii=False),"pending" if manual else "checked",warnings,bool(test["certificate_mode"]=="threshold" and not manual and warnings<2 and score>=float(test["certificate_min_score"] or 0))))
        result_id=cur.fetchone()["id"]
    try:
        review_line="✍️ Yopiq savollar: "+str(len(manual))+" ta" if manual else "✍️ Yopiq savollar: yo‘q"
        warning_line="⚠️ Ogohlantirishlar: "+str(warnings)+" ta" if warnings else "✅ Ogohlantirishlar: yo‘q"
        status_line="🕒 O‘qituvchi tekshiruvi kutilmoqda" if manual or warnings>=2 else "🏅 Sertifikat tayyor"
        notify_teacher("📊 NEURON — TEST NATIJASI\n\n👤 O‘quvchi: "+candidate["name"]+"\n📝 Test: "+test["title"]+"\n🎯 Natija: "+str(score)+"/"+str(total)+" ("+str(percent)+"%)\n"+review_line+"\n"+warning_line+"\n"+status_line+"\n🆔 Natija ID: "+str(result_id))
    except Exception: app.logger.exception("Teacher notification failed")
    if not manual and warnings<2 and test["certificate_mode"]=="threshold" and score>=float(test["certificate_min_score"] or 0):
        try: deliver_certificate(cert)
        except Exception: app.logger.exception("Automatic certificate delivery failed")
    session.pop("candidate",None); session.pop("started_at",None); session.pop("test_deadline",None)
    return redirect(url_for("certificate", cert_id=cert))

@app.get("/certificate/<cert_id>/image")
def certificate_image_route(cert_id):
    pic=certificate_image(cert_id)
    if not pic: abort(404)
    return send_file(pic,mimetype="image/png",download_name=f"NEURON_{cert_id}.png")

@app.get("/certificate/<cert_id>")
def certificate(cert_id):
    with db() as c:
        r=c.execute("SELECT results.*,tests.title,teacher_accounts.full_name AS teacher_full_name,teacher_accounts.certificate_org_name FROM results JOIN tests ON tests.id=results.test_id LEFT JOIN teacher_accounts ON teacher_accounts.telegram_id=tests.teacher_id WHERE cert_id=?", (cert_id,)).fetchone()
    if not r: abort(404)
    allowed=not (r["review_status"]=="pending" or int(r["focus_warning"] or 0)>=2)
    status="Haqiqiy sertifikat" if allowed else "O‘qituvchi tekshiruvi kutilmoqda"
    org=(r["certificate_org_name"] or r["teacher_full_name"] or "NEURON TEST BOT").strip()
    return render_template_string("""<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#10284b"><title>Sertifikatni tekshirish — NEURON</title><style>*{box-sizing:border-box}body{margin:0;padding:24px 14px;background:#081426;color:#edf4ff;font:16px/1.55 system-ui,Arial}.wrap{max-width:680px;margin:5vh auto;background:#10294b;border:1px solid #315477;border-radius:24px;padding:26px;box-shadow:0 20px 60px #0006}.tag{color:#9bc4ef;letter-spacing:2px;font-size:12px;font-weight:800}.state{padding:12px 16px;border-radius:12px;background:#123c37;color:#8df0ca;display:inline-block;font-weight:800}.name{font-size:30px;font-weight:800;margin:14px 0 4px}.muted{color:#9db2d0}.row{padding:13px 0;border-bottom:1px solid #ffffff18;display:flex;justify-content:space-between;gap:16px}.row b{text-align:right}.btn{display:block;margin-top:18px;padding:15px;border-radius:14px;text-align:center;text-decoration:none;color:white;background:linear-gradient(110deg,#0891b2,#4f46e5);font-weight:800}.brand{font-size:20px;font-weight:900;letter-spacing:2px;margin-bottom:18px}@media(max-width:440px){.wrap{padding:20px}.row{font-size:14px}.name{font-size:25px}}</style></head><body><main class="wrap"><div class="brand">NEURON<span style="color:#22d3ee">.</span><small style="display:block;font-size:10px;color:#9db2d0;letter-spacing:3px">SERTIFIKAT TEKSHIRUVI</small></div><p class="tag">RASMIY TEKSHIRUV SAHIFASI</p><div class="state">{{status}}</div><h1 class="name">{{r['full_name']}}</h1><p class="muted">{{org}}</p><div class="row"><span>Fan / test</span><b>{{r['title']}}</b></div><div class="row"><span>Natija</span><b>{{r['score']}} / {{r['total']}} ({{r['percent']}}%)</b></div><div class="row"><span>Sertifikat ID</span><b>{{r['cert_id']}}</b></div><div class="row"><span>Berilgan sana</span><b>{{r['created_at'][:10]}}</b></div>{% if r['focus_warning']|int>0 %}<div style="margin-top:16px;padding:14px;border-radius:12px;background:#4a3213;color:#ffe0a3;border:1px solid #a8792d">⚠️ Siz test davomida {{r['focus_warning']}} marta ogohlantirish oldingiz. Bu holat natijangizda qayd etilgan.</div>{% endif %}{% if allowed %}<a class="btn" href="{{url_for('certificate_image_route',cert_id=r['cert_id'])}}">Sertifikat rasmini ko‘rish / yuklab olish</a><a class="btn" href="{{url_for('certificate_pdf',cert_id=r['cert_id'])}}">PDF yuklab olish</a>{% endif %}<p class="muted" style="font-size:12px;margin-top:24px">Bu sahifa faqat sertifikatni tekshirish uchun. O‘qituvchi kabineti menyusi ko‘rsatilmaydi.</p></main></body></html>""",r=r,status=status,allowed=allowed,org=org)


@app.get("/certificate/<cert_id>/pdf")
def certificate_pdf(cert_id):
    with db() as c:
        r=c.execute("SELECT results.*,tests.title,teacher_accounts.full_name AS teacher_full_name,teacher_accounts.certificate_org_name,teacher_accounts.certificate_logo FROM results JOIN tests ON tests.id=results.test_id LEFT JOIN teacher_accounts ON teacher_accounts.telegram_id=tests.teacher_id WHERE cert_id=?",(cert_id,)).fetchone()
    if not r: abort(404)
    if r["review_status"]=="pending" or int(r["focus_warning"] or 0)>=2:
        return "O‘qituvchi tekshiruvi tugamaguncha sertifikat yuklab olinmaydi.",409
    pic=certificate_image(cert_id)
    if not pic: abort(404)
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    buff=io.BytesIO()
    page_w,page_h=landscape(A4)
    pdf=canvas.Canvas(buff,pagesize=(page_w,page_h),pageCompression=1)
    pdf.drawImage(ImageReader(pic),0,0,width=page_w,height=page_h,preserveAspectRatio=False,mask="auto")
    pdf.showPage(); pdf.save(); buff.seek(0)
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
        raw_results=c.execute("SELECT results.*,tests.title,EXISTS(SELECT 1 FROM blocked_students b WHERE b.test_id=results.test_id AND b.full_name_key=lower(trim(results.full_name))) AS is_blocked FROM results JOIN tests ON tests.id=results.test_id ORDER BY results.id DESC LIMIT 100").fetchall()
    results=[]
    for item in raw_results:
        item=dict(item)
        try: item["manual_items"]=json.loads(item.get("manual_answers") or "[]")
        except Exception: item["manual_items"]=[]
        results.append(item)
    return page("Admin panel", """
    <section class="panel-hero admin-screen active" id="overview"><p class="eyebrow">NEURON V2 · SUPER ADMIN</p><h1>Boshqaruv markazi</h1><p class="muted">Test platformasi, o‘qituvchilar va xavfsizlik nazorati.</p><div class="grid"><div class="stat"><b>{{tests|length}}</b>Jami testlar</div><div class="stat"><b>{{results|length}}</b>Natijalar</div><div class="stat"><b>{{results|selectattr('review_status','equalto','pending')|list|length}}</b>Tekshiruv kutilmoqda</div><div class="stat"><b>{{results|selectattr('focus_warning')|list|length}}</b>Ogohlantirishlar</div></div></section>
    <section class="card admin-screen" id="adminTests"><h2>📚 Platformadagi testlar</h2><p class="muted">Testlarni o‘qituvchilar o‘z kabinetidan yaratadi. Admin panelida test yaratish yo‘q.</p>{% for t in tests %}<div class="question"><b>{{t['title']}}</b><p class="muted">Kod: {{t['code']}} · {{t['question_count']}} savol · {{t['duration']}} daqiqa · {{t['file_name'] or 'Fayl biriktirilmagan'}}</p></div>{% else %}<p class="muted">Hozircha test yo‘q.</p>{% endfor %}</section>
    <section class="card admin-screen" id="adminResults"><h2>📊 Barcha natijalar</h2>{% for r in results %}<div class="question {% if r['focus_warning']|int>0 %}warning-card{% endif %}"><p><b>{{r['full_name']}}</b> — {{r['title']}} · <b>{{r['percent']}}%</b></p><p class="muted">{{r['score']}} / {{r['total']}} ball · {{r['created_at']}}</p><p class="muted">Holat: {{'O‘qituvchi tekshiruvi kutilmoqda' if r['review_status']=='pending' else 'Sertifikat tayyor' if r['review_status']=='checked' else r['review_status']}} · ID: {{r['cert_id']}}</p>{% if r['review_status']=='pending' %}<form method="post" action="/admin/review/{{r['id']}}"><h3>Yopiq savollar — rubrika</h3>{% for item in r['manual_items'] %}<div class="filebox"><b>{{item['question']}}-savol</b><p>{{item['answer'] or 'Javob kiritilmagan'}}</p><label>Baholash</label><select name="manual_score_{{loop.index0}}"><option value="0">Noto‘g‘ri — 0</option><option value="0.5">Qisman — 0.5</option><option value="1">To‘g‘ri — 1</option></select></div>{% endfor %}<button>Baholashni saqlash</button></form>{% endif %}</div>{% else %}<p>Natijalar yo‘q.</p>{% endfor %}</section>
    <section class="card admin-screen" id="adminWarnings"><h2>⚠️ Ogohlantirish va bloklash</h2><p class="muted">O‘quvchini tanlangan test uchun bloklash yoki blokdan chiqarish mumkin.</p>{% for r in results if r['focus_warning']|int>0 %}<div class="question warning-card"><b>{{r['full_name']}}</b><p class="muted">{{r['title']}} · {{r['focus_warning']}} ta ogohlantirish · {{r['created_at']}}</p>{% if r['is_blocked'] %}<span class="status-pill">🚫 Bloklangan</span><form method="post" action="/admin/block/{{r['id']}}"><button class="secondary full">Blokdan chiqarish</button></form>{% else %}<span class="status-pill">⚠️ Nazoratda</span><form method="post" action="/admin/block/{{r['id']}}"><button class="full">🚫 Shu testdan bloklash</button></form>{% endif %}</div>{% else %}<p class="muted">Ogohlantirishlar mavjud emas.</p>{% endfor %}</section>
    """,tests=tests,results=results)

@app.post("/admin/block/<int:result_id>")
@admin_required
def admin_toggle_block(result_id):
    with db() as c:
        r=c.execute("SELECT id,full_name,focus_warning,test_id FROM results WHERE id=?",(result_id,)).fetchone()
        if not r or int(r["focus_warning"] or 0)<1: abort(404)
        key=r["full_name"].strip().lower()
        existing=c.execute("SELECT 1 FROM blocked_students WHERE test_id=? AND full_name_key=?",(r["test_id"],key)).fetchone()
        if existing:
            c.execute("DELETE FROM blocked_students WHERE test_id=? AND full_name_key=?",(r["test_id"],key)); message="O‘quvchi ushbu test uchun blokdan chiqarildi."
        else:
            c.execute("INSERT INTO blocked_students(test_id,full_name,full_name_key,reason,created_at) VALUES(?,?,?,?,?) ON CONFLICT(test_id,full_name_key) DO NOTHING",(r["test_id"],r["full_name"],key,"Admin tomonidan bloklangan",datetime.utcnow().isoformat())); message="O‘quvchi ushbu test uchun bloklandi."
    flash(message); return redirect(url_for("dashboard")+"#adminWarnings")

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
    try: notify_teacher("✅ NEURON — Rubrika saqlandi\nO‘quvchi: "+student+"\nYakuniy ball: "+str(final_score)+"/"+str(r["total"])+"\nNatija: "+str(percent)+"%\nSertifikat: "+cert)
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

@app.post("/teacher/block/<int:result_id>")
@teacher_required
def teacher_toggle_block(result_id):
    with db() as c:
        r=c.execute("SELECT results.id,results.full_name,results.focus_warning,results.test_id FROM results JOIN tests ON tests.id=results.test_id WHERE results.id=? AND tests.teacher_id=?",(result_id,session["teacher_id"])).fetchone()
        if not r or int(r["focus_warning"] or 0)<1: abort(404)
        key=r["full_name"].strip().lower()
        existing=c.execute("SELECT 1 FROM blocked_students WHERE test_id=? AND full_name_key=?",(r["test_id"],key)).fetchone()
        if existing:
            c.execute("DELETE FROM blocked_students WHERE test_id=? AND full_name_key=?",(r["test_id"],key)); message="O‘quvchi ushbu test uchun blokdan chiqarildi."
        else:
            c.execute("INSERT INTO blocked_students(test_id,full_name,full_name_key,reason,created_at) VALUES(?,?,?,?,?) ON CONFLICT(test_id,full_name_key) DO NOTHING",(r["test_id"],r["full_name"],key,"Test vaqtida ogohlantirish",datetime.utcnow().isoformat())); message="O‘quvchi ushbu test uchun bloklandi."
    flash(message); return redirect(url_for("teacher_cabinet")+"#warnings")

@app.route("/teacher/login",methods=["GET","POST"])
def teacher_login():
    if session.get("teacher_id"):
        return redirect(url_for("teacher_cabinet"))
    if request.method=="POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        remember=request.form.get("remember")=="on"
        with db() as c: t=c.execute("SELECT * FROM teacher_accounts WHERE username=? AND status='approved'",(username,)).fetchone()
        check=hashlib.pbkdf2_hmac("sha256",password.encode(),b"neuron-teacher-v1",180000).hex()
        if t and t["password_hash"] and secrets.compare_digest(check,t["password_hash"]):
            session.clear()
            session["teacher_id"]=t["telegram_id"]
            session.permanent=remember
            return redirect(url_for("teacher_cabinet"))
        flash("Login yoki parol noto‘g‘ri.")
    return page("O‘qituvchi kirish", """<section class="card"><p class="eyebrow">NEURON TEACHER</p><h1>O‘qituvchi kabinetiga kirish</h1><p class="muted">Admin tasdiqlagach, login va parol Telegram bot orqali yuboriladi. Hali ro‘yxatdan o‘tmagan bo‘lsangiz, avval ariza yuboring.</p><form method="post"><label>Login</label><input name="username" required autocomplete="username"><label>Parol</label><input name="password" type="password" required autocomplete="current-password"><label style="display:flex;align-items:center;gap:9px;margin:12px 0"><input type="checkbox" name="remember" checked style="width:auto"> Meni 30 kun eslab qol</label><button class="full">Kabinetga kirish</button></form><a class="btn secondary full" href="/teacher/register">📝 O‘qituvchi sifatida ro‘yxatdan o‘tish</a><p class="muted"><a href="/">Bosh sahifaga qaytish</a></p></section>""")


@app.route("/teacher/account",methods=["GET","POST"])
@teacher_required
def teacher_account():
    with db() as c:
        t=c.execute("SELECT * FROM teacher_accounts WHERE telegram_id=?",(session["teacher_id"],)).fetchone()
    if request.method=="POST":
        current=request.form.get("current_password","")
        new_username=request.form.get("username","").strip()
        new_password=request.form.get("new_password","")
        confirm=request.form.get("confirm_password","")
        org_name=request.form.get("certificate_org_name","").strip()[:100]
        logo_file=request.files.get("certificate_logo")
        remove_logo=request.form.get("remove_certificate_logo")=="on"
        logo_data=t["certificate_logo"] if t else None
        check=hashlib.pbkdf2_hmac("sha256",current.encode(),b"neuron-teacher-v1",180000).hex()
        if not t or not t["password_hash"] or not secrets.compare_digest(check,t["password_hash"]):
            flash("Amaldagi parol noto‘g‘ri.")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{4,32}",new_username):
            flash("Login 4–32 belgidan iborat bo‘lsin.")
        elif new_password and len(new_password)<8:
            flash("Yangi parol kamida 8 belgidan iborat bo‘lishi kerak.")
        elif new_password and new_password!=confirm:
            flash("Yangi parollar mos kelmadi.")
        else:
            try:
                if remove_logo: logo_data=None
                if logo_file and logo_file.filename:
                    from PIL import Image
                    raw=logo_file.read()
                    if len(raw)>2*1024*1024: raise ValueError("Logotip 2 MB dan kichik bo‘lsin.")
                    check_img=Image.open(io.BytesIO(raw)); check_img.verify()
                    if check_img.format not in ("PNG","JPEG","WEBP"): raise ValueError("Faqat PNG, JPG yoki WEBP qabul qilinadi.")
                    import base64
                    logo_data=base64.b64encode(raw).decode("ascii")
                with db() as c:
                    duplicate=c.execute("SELECT telegram_id FROM teacher_accounts WHERE username=? AND telegram_id<>?",(new_username,session["teacher_id"])).fetchone()
                    if duplicate:
                        flash("Bu login band. Boshqa login tanlang.")
                    else:
                        new_hash=hashlib.pbkdf2_hmac("sha256",new_password.encode(),b"neuron-teacher-v1",180000).hex() if new_password else t["password_hash"]
                        c.execute("UPDATE teacher_accounts SET username=?,password_hash=?,certificate_org_name=?,certificate_logo=? WHERE telegram_id=?",(new_username,new_hash,org_name,logo_data,session["teacher_id"]))
                        flash("Hisob va sertifikat sozlamalari saqlandi.")
                        return redirect(url_for("teacher_account"))
            except Exception as e:
                flash(str(e) if isinstance(e,ValueError) else "Logotip faylini o‘qib bo‘lmadi.")
    with db() as c:
        t=c.execute("SELECT * FROM teacher_accounts WHERE telegram_id=?",(session["teacher_id"],)).fetchone()
    return page("Kabinet sozlamalari", """<section class="card"><p class="eyebrow">NEURON TEACHER</p><h1>🔐 Hisob va sertifikat sozlamalari</h1><form method="post" enctype="multipart/form-data"><label>Yangi login</label><input name="username" value="{{t['username'] or ''}}" minlength="4" maxlength="32" pattern="[A-Za-z0-9_.-]+" required><label>Yangi parol (ixtiyoriy)</label><input name="new_password" type="password" minlength="8"><label>Yangi parolni takrorlang</label><input name="confirm_password" type="password"><hr><h2>🏅 Sertifikat ko‘rinishi</h2><p class="muted">Bu nom sertifikatda logotip ostida chiqadi. O‘zingizning ismingiz yoki o‘quv markazingiz nomini yozing.</p><label>Sertifikatda ko‘rinadigan nom</label><input name="certificate_org_name" value="{{t['certificate_org_name'] or t['full_name']}}" maxlength="100" placeholder="Masalan: NEURON o‘quv markazi"><label>Logotip (PNG, JPG yoki WEBP, 2 MB gacha)</label><input type="file" name="certificate_logo" accept="image/png,image/jpeg,image/webp"><label><input type="checkbox" name="remove_certificate_logo" style="width:auto;min-height:auto"> Saqlangan logotipni olib tashlash</label><p class="muted">Logotipni bir marta yuklaysiz, keyingi sertifikatlarda avtomatik ishlatiladi.</p><hr><label>Amaldagi parol — o‘zgarishlarni tasdiqlash uchun</label><input name="current_password" type="password" required><button class="full">💾 O‘zgarishlarni saqlash</button></form><a class="btn secondary full" href="/teacher/cabinet">← Kabinetga qaytish</a></section>""",t=t)


@app.get("/teacher/register")
def teacher_register_page():
    return page("O‘qituvchi ro‘yxati", """<section class="card"><h1>O‘qituvchi bo‘lish</h1><p>Telegram botga o‘ting va pastdagi tugmani bosing.</p><a class="btn full" href="https://t.me/Test2026robot" target="_blank" rel="noopener">Telegram botni ochish</a><p class="muted">Botda /teacher buyrug‘i orqali telefon raqamingizni tasdiqlang, ism va faningizni kiriting.</p></section>""")

@app.get("/teacher/cabinet")
@teacher_required
def teacher_cabinet():
    with db() as c:
        t=c.execute("SELECT * FROM teacher_accounts WHERE telegram_id=?",(session["teacher_id"],)).fetchone()
        rows=c.execute("SELECT results.*,tests.title,tests.code,tests.subject,tests.grade,tests.certificate_mode,tests.certificate_min_score,EXISTS(SELECT 1 FROM blocked_students b WHERE b.test_id=results.test_id AND b.full_name_key=lower(trim(results.full_name))) AS is_blocked FROM results JOIN tests ON tests.id=results.test_id WHERE tests.teacher_id=? ORDER BY results.id DESC LIMIT 100", (session["teacher_id"],)).fetchall()
        tests=c.execute("SELECT * FROM tests WHERE teacher_id=? AND is_active=TRUE ORDER BY id DESC LIMIT 100",(session["teacher_id"],)).fetchall()
    return page("O‘qituvchi kabineti", """<section class="panel-hero"><p class="eyebrow" style="color:#c7d7ff">NEURON V2 · TEACHER</p><h1>Assalomu alaykum, {{t['full_name']}}</h1><p class="muted">{{t['subject']}} · {{t['phone']}}</p><p class="muted">Kabinet bo‘limlarini pastdagi ilova menyusidan oching.</p></section>
<div class="grid"><div class="stat"><b>{{tests|length}}</b>Yaratilgan testlar</div><div class="stat"><b>{{rows|length}}</b>O‘quvchilar</div><div class="stat"><b>{{rows|selectattr('review_status','equalto','pending')|list|length}}</b>Tekshiruv kutilmoqda</div></div>
<section class="card teacher-screen active" id="create"><p class="eyebrow">TEST KONSTRUKTORI</p><h2>＋ Yangi test yaratish</h2><p class="muted">Fan va sinfni tanlang. Javoblar kalitini savol turi bo‘yicha belgilang — yopiq savollarni keyin o‘zingiz baholaysiz.</p>
<form method="post" action="/teacher/create" enctype="multipart/form-data"><div class="grid"><div><label>Fan nomi</label><input name="subject" value="{{t['subject']}}" required placeholder="Masalan: Tarix"></div><div><label>Sinf / kurs</label><input name="grade" required placeholder="Masalan: 8-sinf"></div></div><label>Test nomi</label><input name="title" required placeholder="Masalan: 1-mavzu nazorati"><div class="grid"><div><label>Davomiyligi (daqiqada)</label><input name="duration" type="number" min="1" max="240" value="30" required></div><div><label>Savollar soni</label><input name="question_count" type="number" min="1" max="100" value="10" required></div></div>
<div class="question"><h3>🏅 Sertifikat berish tartibi</h3><label>Berish usuli</label><select name="certificate_mode"><option value="manual">Qo‘lda — o‘qituvchi tanlaydi</option><option value="threshold">Avtomatik — minimal ball bo‘yicha</option></select><label>Minimal ball</label><input name="certificate_min_score" type="number" min="0" step="0.5" value="7"><p class="muted">Avtomatik rejimda shu balldan kam bo‘lmagan natija olgan o‘quvchiga, tekshiruv tugagach, sertifikat yuboriladi.</p></div>
<label>Savollar fayli (PDF yoki DOCX, 15 MB gacha)</label><div class="filebox">📎 <b>Test hujjatini tanlang</b><input type="file" name="test_file" accept=".pdf,.docx" required></div>
<h3>EvalBee uslubidagi javoblar kaliti</h3><div class="key-help muted">🔵 Variantli · 🟢 To‘g‘ri/Noto‘g‘ri · ✍️ Qisqa javob · 📝 Yopiq savol — o‘qituvchi tekshiradi.</div><div id="teacherKeyRows"></div><input type="hidden" name="answer_key" id="teacherAnswerKey" required><p id="teacherKeyProgress" class="muted">0 ta javob belgilandi</p><button class="full">🚀 Testni yaratish</button></form></section>
<section class="card teacher-screen" id="tests"><h2>📚 Mening testlarim</h2>{% for x in tests %}<div class="question"><b>{{x['title']}}</b><p class="muted">{{x['subject']}} · {{x['grade']}} · {{x['question_count']}} savol · {{x['duration']}} daqiqa</p><p class="eyebrow">TEST KODI</p><div class="stat"><b style="font-size:20px">{{x['code']}}</b></div><button type="button" class="secondary full" onclick="copyText(this,'{{x['code']}}')">📋 Kodni nusxalash</button><form method="post" action="/teacher/test/{{x['id']}}/delete" onsubmit="return confirm('Testni yopib, faol ro‘yxatdan olib tashlaysizmi? Eski natijalar saqlanadi.')"><button class="secondary full" style="color:#fca5a5">🗑 Testni o‘chirish</button></form></div>{% else %}<p class="muted">Hali test yaratmagansiz.</p>{% endfor %}</section>
<section class="card teacher-screen" id="results"><h2>📊 O‘quvchilar natijalari</h2>{% for r in rows %}<div class="question {% if r['focus_warning']|int>0 %}warning-card{% endif %}"><b>{{r['full_name']}}</b> — {{r['title']}}<p class="muted">{{r['score']}} / {{r['total']}} · {{r['percent']}}% · {{r['created_at']}}</p>{% if r['focus_warning']|int>0 %}<p><span class="status-pill">⚠️ {{r['focus_warning']}} ta ogohlantirish</span> <b>{{r['full_name']}}</b></p>{% endif %}{% if r['review_status']=='pending' %}<p><span class="status-pill">Yopiq savollar tekshirilmoqda</span></p><a class="btn secondary full" href="/teacher/review/{{r['id']}}">📝 Yopiq savollarni tekshirish</a>{% else %}<span class="status-pill good">✓ Baholash yakunlangan</span>{% endif %}{% if r['review_status']!='pending' and r['focus_warning']|int<2 and not r['certificate_approved'] %}<form method="post" action="/teacher/certificate/{{r['id']}}"><button class="full">🏅 Sertifikat berish va Telegramga yuborish</button></form>{% elif r['certificate_approved'] %}<span class="status-pill good">🏅 Sertifikat berishga ruxsat berilgan</span>{% endif %}<a class="btn full" href="/certificate/{{r['cert_id']}}">🏅 Natija va sertifikat</a></div>{% else %}<p class="muted">Hozircha natija yo‘q.</p>{% endfor %}</section>
<section class="card teacher-screen" id="warnings"><h2>⚠️ Ogohlantirish olgan o‘quvchilar</h2><p class="muted">Bu bo‘limda test oynasidan chiqqan o‘quvchilarni ko‘rasiz. Bloklash shu test uchun o‘quvchi ismi bo‘yicha ishlaydi.</p>{% for r in rows if r['focus_warning']|int>0 %}<div class="question warning-card"><b>{{r['full_name']}}</b><p class="muted">{{r['title']}} · {{r['focus_warning']}} ta ogohlantirish · {{r['created_at']}}</p>{% if r['is_blocked'] %}<span class="status-pill">🚫 Bloklangan</span><form method="post" action="/teacher/block/{{r['id']}}"><button class="secondary full">Blokdan chiqarish</button></form>{% else %}<span class="status-pill">⚠️ Nazoratda</span><form method="post" action="/teacher/block/{{r['id']}}"><button class="full">🚫 Shu testdan bloklash</button></form>{% endif %}</div>{% else %}<p class="muted">Ogohlantirish olgan o‘quvchilar yo‘q.</p>{% endfor %}</section><section class="card teacher-screen" id="certificates"><h2>🏅 Sertifikat ko‘rinishi</h2><p class="muted">Sertifikat namunasi va o‘quvchilar olgan sertifikatlar shu bo‘limda ko‘rinadi.</p><div class="cert-preview"><div class="eyebrow">NEURON</div><h2 style="color:#315efb;margin:4px 0">SERTIFIKAT</h2><div>Ushbu sertifikat taqdim etiladi</div><div class="cert-name">O‘quvchi F.I.Sh.</div><div class="muted">“Test nomi” test sinovi natijasi</div><h2>20.0 / 20 &nbsp; | &nbsp; 100%</h2><p class="muted">Sertifikat ID: NR-XXXXXXXX</p><p class="muted">Sana: YYYY-MM-DD</p></div>{% for r in rows %}<div class="cert-preview"><div class="eyebrow">NEURON EDUCATION</div><h2 style="color:#315efb;margin:8px 0">SERTIFIKAT</h2><div>Ushbu sertifikat taqdim etiladi</div><div class="cert-name">{{r['full_name']}}</div><div class="muted">{{r['title']}}</div><h2>{{'%.1f'|format(r['score']|float)}} / {{r['total']}} &nbsp; | &nbsp; {{r['percent']}}%</h2><p class="muted">Sertifikat ID: {{r['cert_id']}}</p><p class="muted">Sana: {{r['created_at'][:10]}}</p>{% if r['review_status']=='pending' or r['focus_warning']|int>=2 %}<span class="status-pill">Tekshiruv / cheklov mavjud</span>{% else %}<span class="status-pill good">Sertifikat tayyor</span><a class="btn secondary full" href="/certificate/{{r['cert_id']}}/pdf">PDF sertifikatni ko‘rish</a>{% endif %}</div>{% else %}<p class="muted">Sertifikatlar natija kelgach shu yerda ko‘rinadi.</p>{% endfor %}</section>
<a class="btn dark full" href="/teacher/logout">Chiqish</a><script>
(function(){const count=document.querySelector('input[name="question_count"]');const box=document.getElementById('teacherKeyRows');const hidden=document.getElementById('teacherAnswerKey');const progress=document.getElementById('teacherKeyProgress');if(!count||!box)return;
function sync(){const rows=[...box.querySelectorAll('.teacher-key-row')];const vals=rows.map(r=>{const type=r.querySelector('select').value;if(type==='manual')return 'MANUAL';if(type==='short')return r.querySelector('.short-answer').value.trim();return r.querySelector('input:checked')?.value||'';});const done=vals.filter(Boolean).length;hidden.value=vals.join(',');progress.textContent=done+' / '+rows.length+' ta javob belgilandi';hidden.setCustomValidity(done===rows.length&&vals.every(Boolean)?'':'Har bir savol turini tanlab, javobini belgilang.');}
function draw(){const n=Math.max(1,Math.min(100,Number(count.value)||1));box.innerHTML='';for(let i=1;i<=n;i++){const r=document.createElement('div');r.className='question teacher-key-row';r.innerHTML='<b>'+i+'-savol</b><select aria-label="'+i+'-savol turi"><option value="mcq">Variantli — A / B / C / D</option><option value="tf">To‘g‘ri / Noto‘g‘ri</option><option value="short">Qisqa javob — avtomatik</option><option value="manual">Yopiq savol — o‘qituvchi tekshiradi</option></select><div class="bubbles"></div><input class="short-answer" maxlength="80" placeholder="To‘g‘ri javob matni" style="display:none"><p class="muted key-type-hint"></p>';const sel=r.querySelector('select'),b=r.querySelector('.bubbles'),short=r.querySelector('.short-answer'),hint=r.querySelector('.key-type-hint');function options(){b.innerHTML='';short.style.display=sel.value==='short'?'block':'none';hint.textContent=sel.value==='manual'?'O‘quvchi javob yozadi. Yakuniy ballni kabinetda siz qo‘yasiz.':sel.value==='short'?'O‘quvchi yozgan javob kalit bilan avtomatik solishtiriladi.':'To‘g‘ri javob variantini belgilang.';const values=sel.value==='mcq'?['A','B','C','D']:sel.value==='tf'?['TRUE','FALSE']:[];values.forEach(v=>{const l=document.createElement('label');l.className='bubble';l.innerHTML='<input type="radio" name="teacher_key_'+i+'" value="'+v+'"><span>'+(v==='TRUE'?'T':v==='FALSE'?'N':v)+'</span>';b.appendChild(l);});sync();}sel.addEventListener('change',options);r.addEventListener('change',sync);short.addEventListener('input',sync);options();box.appendChild(r);}sync();}
count.addEventListener('input',draw);draw();})();</script></section>""",t=t,rows=rows,tests=tests)

@app.post("/teacher/test/<int:test_id>/delete")
@teacher_required
def teacher_delete_test(test_id):
    with db() as c:
        test=c.execute("SELECT id,title FROM tests WHERE id=? AND teacher_id=?",(test_id,session["teacher_id"])).fetchone()
        if not test: abort(404)
        c.execute("UPDATE tests SET is_active=FALSE WHERE id=? AND teacher_id=?",(test_id,session["teacher_id"]))
    flash("Test yopildi va faol testlar ro‘yxatidan olib tashlandi. Eski natijalar saqlab qolindi.")
    return redirect(url_for("teacher_cabinet")+"#tests")

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
        cert_mode=request.form.get("certificate_mode","manual")
    if cert_mode not in ("manual","threshold"): cert_mode="manual"
    try: cert_min=max(0.0,min(float(count),float(request.form.get("certificate_min_score","0"))))
    except Exception: cert_min=0.0
    c.execute("INSERT INTO tests(title,code,duration,questions,created_at,file_name,file_data,answer_key,question_count,teacher_id,subject,grade,certificate_mode,certificate_min_score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(title,code,duration,"",datetime.utcnow().isoformat(),filename,data,json.dumps(answers,ensure_ascii=False),count,session["teacher_id"],subject,grade,cert_mode,cert_min))
    flash("Test yaratildi! O‘quvchilarga fan va sinfga mos kodni bering: "+code)
    return redirect(url_for("teacher_cabinet"))

def get_teacher_name(teacher_id):
    with db() as c: row=c.execute("SELECT full_name FROM teacher_accounts WHERE telegram_id=?",(teacher_id,)).fetchone()
    return row["full_name"] if row else ""

@app.post("/teacher/certificate/<int:result_id>")
@teacher_required
def teacher_grant_certificate(result_id):
    with db() as c:
        r=c.execute("SELECT results.*,tests.certificate_mode,tests.certificate_min_score FROM results JOIN tests ON tests.id=results.test_id WHERE results.id=? AND tests.teacher_id=?",(result_id,session["teacher_id"])).fetchone()
        if not r: abort(404)
        if r["review_status"]=="pending" or int(r["focus_warning"] or 0)>=2:
            flash("Avval yopiq savollar va ogohlantirishlarni tekshirish kerak.")
            return redirect(url_for("teacher_cabinet"))
        c.execute("UPDATE results SET certificate_approved=TRUE WHERE id=?",(result_id,))
        cert=r["cert_id"]
    sent=deliver_certificate(cert)
    flash("Sertifikatga ruxsat saqlandi. PNG va PDF natija sahifasida tayyor.")
    return redirect(url_for("teacher_cabinet"))

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
            c.execute("UPDATE results SET score=?,percent=?,manual_score=?,review_status='checked',certificate_approved=CASE WHEN (SELECT certificate_mode FROM tests WHERE tests.id=results.test_id)='threshold' AND ? >= (SELECT certificate_min_score FROM tests WHERE tests.id=results.test_id) AND COALESCE(focus_warning,0)<2 THEN TRUE ELSE FALSE END WHERE id=?",(final,percent,got,final,result_id))
            cert=r["cert_id"]
        else: cert=""
    if request.method=="POST":
        deliver_certificate(cert)
        flash("Baholash saqlandi. Sertifikat tayyor bo‘lsa, natija sahifasida PNG va PDF ko‘rinadi.")
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
    import qrcode
    from urllib.parse import quote
    with db() as c:
        r=c.execute("SELECT results.*,tests.title,teacher_accounts.full_name AS teacher_full_name,teacher_accounts.certificate_org_name,teacher_accounts.certificate_logo FROM results JOIN tests ON tests.id=results.test_id LEFT JOIN teacher_accounts ON teacher_accounts.telegram_id=tests.teacher_id WHERE cert_id=?",(cert_id,)).fetchone()
    if not r or r["review_status"]=="pending" or int(r["focus_warning"] or 0)>=2: return None

    W,H=1600,1131
    paper="#fffdf5"; gold="#bd8b22"; gold_light="#e5c879"; blue="#183d63"; ink="#182536"; muted="#586779"
    im=Image.new("RGB",(W,H),paper); d=ImageDraw.Draw(im)
    # Double gold frame
    d.rectangle((28,28,W-28,H-28),outline=gold,width=8)
    d.rectangle((45,45,W-45,H-45),outline=gold_light,width=3)
    d.rectangle((66,66,W-66,H-66),outline="#ead9a8",width=2)

    font_candidates=[
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    bold_candidates=[
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]
    def font(size,bold=False):
        for path in (bold_candidates if bold else font_candidates):
            try: return ImageFont.truetype(path,size)
            except Exception: pass
        return ImageFont.load_default()
    def center(y,text,size,color=ink,bold=False,max_width=1360):
        text=str(text)
        fs=size
        while fs>14:
            ft=font(fs,bold)
            box=d.textbbox((0,0),text,font=ft)
            if box[2]-box[0] <= max_width: break
            fs-=2
        d.text((W//2,y),text,font=font(fs,bold),fill=color,anchor="mm")
    def line(y,x1=90,x2=1510,width=4):
        d.line((x1,y,x2,y),fill=gold,width=width)
    def ornament(cx,cy,scale=1):
        pts=[(cx,cy-22*scale),(cx+22*scale,cy),(cx,cy+22*scale),(cx-22*scale,cy)]
        d.polygon(pts,outline=gold,width=3)
    # Corner geometric ornaments
    for base_x,base_y,sx,sy in [(68,68,1,1),(1532,68,-1,1),(68,1063,1,-1),(1532,1063,-1,-1)]:
        for n in range(3):
            x=base_x+sx*n*24; y=base_y+sy*n*24
            d.line((x,y,x+sx*78,y+sy*78),fill=gold_light,width=5)
            d.line((x+sx*78,y+sy*78,x+sx*156,y),fill=gold_light,width=5)
    # Emblem
    d.ellipse((710,82,890,262),outline=gold,width=5,fill="#fffefa")
    d.ellipse((722,94,878,250),outline=gold_light,width=2)
    logo_data=r["certificate_logo"]
    try:
        if logo_data:
            import base64
            logo=Image.open(io.BytesIO(base64.b64decode(logo_data))).convert("RGBA")
        else:
            # Default NEURON TEST BOT logo supplied by the owner.
            logo_path=os.path.join(os.path.dirname(__file__),"static","neuron_default_logo.png.b64")
            with open(logo_path,"r",encoding="ascii") as logo_file:
                logo=Image.open(io.BytesIO(base64.b64decode(logo_file.read().strip()))).convert("RGBA")
        logo.thumbnail((148,148))
        mask=Image.new("L",logo.size,0)
        ImageDraw.Draw(mask).ellipse((0,0,logo.width-1,logo.height-1),fill=255)
        im.paste(logo,(800-logo.width//2,172-logo.height//2),mask)
    except Exception:
        app.logger.exception("Certificate logo could not be loaded")
        center(145,"N",76,blue,True,130)
    org_name=(r["certificate_org_name"] or r["teacher_full_name"] or "NEURON TEST BOT").strip()
    center(300,org_name,28,blue,True,1320)
    center(345,"BILIM VA MALAKALARNI BAHOLASH TIZIMI",19,muted,True)
    line(382)
    center(445,"UMUMTA'LIM FANINI BILISH DARAJASI",43,ink,True)
    center(500,"TO'G'RISIDA SERTIFIKAT",43,ink,True)
    line(548)

    # Student and assessment information
    d.text((125,600),"SERTIFIKAT TAQDIM ETILADI",font=font(22,True),fill=blue)
    center(665,r["full_name"],43,ink,True,1250)
    center(718,'“'+str(r["title"])+'” fanidan test sinovi natijasi',22,muted,False,1300)
    line(760,125,1475,3)

    left_x=145; value_x=610
    labels=[
        ("Umumiy to'plagan ball:",f'{float(r["score"]):.1f} / {r["total"]}'),
        ("Foiz ko'rsatkichi:",f'{float(r["percent"]):.1f}%'),
        ("Sertifikat darajasi:", "A" if float(r["percent"])>=90 else "B" if float(r["percent"])>=75 else "C" if float(r["percent"])>=60 else "Ishtirok"),
    ]
    for i,(label,value) in enumerate(labels):
        y=805+i*48
        d.text((left_x,y),label,font=font(24),fill=blue)
        d.text((value_x,y),value,font=font(25,True),fill=ink)

    # QR verification code
    verify_url=request.host_url.rstrip("/")+"/certificate/"+quote(str(r["cert_id"]))
    qr=qrcode.QRCode(version=None,error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=5,border=2)
    qr.add_data(verify_url); qr.make(fit=True)
    qr_img=qr.make_image(fill_color="#172536",back_color="#ffffff").convert("RGB").resize((176,176))
    im.paste(qr_img,(1280,820))
    d.text((1368,1010),"QR: tekshirish",font=font(16,True),fill=blue,anchor="mm")

    line(1015,125,1475,3)
    d.text((145,1044),"Sertifikat ID:",font=font(18),fill=blue)
    d.text((300,1044),str(r["cert_id"]),font=font(19,True),fill=ink)
    d.text((790,1044),"Berilgan sana:",font=font(18),fill=blue)
    d.text((965,1044),str(r["created_at"])[:10],font=font(19,True),fill=ink)
    d.line((1120,1080,1460,1080),fill=gold,width=2)
    d.text((1290,1098),"Mas'ul rahbar",font=font(15),fill=muted,anchor="mm")

    out=io.BytesIO(); im.save(out,format="PNG",dpi=(180,180)); out.seek(0); return out
def deliver_certificate(cert_id):
    # Student Telegram chat_id is not stored in results yet.
    # Never broadcast a student's certificate to every approved teacher.
    with db() as c:
        r=c.execute("SELECT certificate_approved,review_status,focus_warning FROM results WHERE cert_id=?",(cert_id,)).fetchone()
    return bool(r and r["certificate_approved"] and r["review_status"]!="pending" and int(r["focus_warning"] or 0)<2)

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
        welcome=("🧠 NEURON TEST — Bilimni sinash platformasi\n\n"
                 "Assalomu alaykum! NEURON’ga xush kelibsiz.\n\n"
                 "🎓 O‘quvchilar: test kodini kiriting, belgilangan vaqt ichida savollarga javob bering va natijangizni ko‘ring.\n\n"
                 "👩‍🏫 O‘qituvchilar: testlarni boshqaring, javoblarni tekshiring, natijalarni kuzating va sertifikatlarni belgilang.\n\n"
                 "👇 Kerakli bo‘limni tanlang.")
        telegram_api("sendMessage",{"chat_id":chat_id,"text":welcome,"reply_markup":{"inline_keyboard":[[{"text":"🚀 Testni boshlash","web_app":{"url":MINI_APP_URL}}],[{"text":"👩‍🏫 O‘qituvchi kabinetiga kirish","url":MINI_APP_URL+"/teacher/login"}],[{"text":"📝 O‘qituvchi sifatida ro‘yxatdan o‘tish","callback_data":"teacher_register"}]]}})
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