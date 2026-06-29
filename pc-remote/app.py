#!/usr/bin/env python3
import os
import glob
import time
import secrets
import subprocess
from functools import wraps
from collections import deque
from flask import (Flask, request, jsonify, session, redirect,
                   render_template_string)
import hid_keyboard as hid
import crypto_secret as cs

# Config comes from environment variables (the .env file loaded via systemd
# EnvironmentFile) so the repo can be shared without editing source.
PC_MAC = os.environ.get("PC_MAC", "")          # PC NIC MAC — used for WoL
HOST = os.environ.get("REMOTE_HOST", "0.0.0.0")
PORT = int(os.environ.get("REMOTE_PORT", "5000"))

CFG_DIR = os.environ.get(
    "PC_REMOTE_CFG_DIR", os.path.expanduser("~/.config/pc-remote"))
# Windows login password, encrypted with a key derived from the PANEL password
# (see crypto_secret.py). Override the path with PC_LOGIN_SECRET_FILE.
WIN_SECRET_FILE = os.environ.get(
    "PC_LOGIN_SECRET_FILE", os.path.join(CFG_DIR, "login.secret.enc"))
PANEL_HASH_FILE = os.path.join(CFG_DIR, "panel.secret")
API_TOKEN_FILE = os.path.join(CFG_DIR, "api.token")

# How long a panel login stays valid. The panel password is cached in memory
# for this long so the "Log in" button can decrypt the Windows password.
SESSION_TTL = 12 * 3600


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


PANEL_HASH = _read(PANEL_HASH_FILE)   # scrypt hash; None = panel not configured
API_TOKEN = _read(API_TOKEN_FILE)     # bearer token for automation; may be None

# --- in-memory session store -------------------------------------------------
# sid -> {"pw": <panel password>, "exp": <unix ts>}. Cleared on restart, so a
# restart logs everyone out (and the cached panel passwords vanish with it).
SESSIONS = {}

# --- action log (in memory, dropped after 24h) ---
LOG = deque()           # entries: {t, ts, action, level, detail}
LOG_TTL = 24 * 3600     # how long (seconds) we keep entries
LOG_MAX = 500           # hard cap so RAM does not grow unbounded

def udc_state():
    """USB gadget state. 'configured' = the PC sees the keyboard and accepts
    reports. Anything else = keystrokes won't reach the PC, even if the write
    to /dev/hidg0 succeeds."""
    try:
        files = glob.glob("/sys/class/udc/*/state")
        if not files:
            return "no-udc"
        with open(files[0]) as f:
            return f.read().strip()
    except Exception as e:
        return f"err:{e}"

def log_event(action, level, detail=""):
    now = time.time()
    cutoff = now - LOG_TTL
    while LOG and LOG[0]["t"] < cutoff:   # drop entries older than 24h
        LOG.popleft()
    LOG.append({"t": now, "ts": time.strftime("%H:%M:%S", time.localtime(now)),
                "action": action, "level": level, "detail": detail})
    while len(LOG) > LOG_MAX:
        LOG.popleft()
    return LOG[-1]

def run_action(action, fn, detail=""):
    """Run a HID action, log the result + USB state, return JSON with the entry.

    level: ok  = write succeeded and USB is 'configured' (PC should see it),
           warn = write OK but USB not 'configured' (PC will NOT get the keys),
           error = the write raised (e.g. wrong USB port / charge-only cable)."""
    state = udc_state()
    try:
        fn()
    except Exception as e:
        entry = log_event(action, "error", f"usb={state} err={e}")
        return jsonify(ok=False, usb=state, error=str(e), entry=entry), 500
    level = "ok" if state == "configured" else "warn"
    entry = log_event(action, level, f"usb={state}" + (f" {detail}" if detail else ""))
    return jsonify(ok=True, usb=state, entry=entry)

app = Flask(__name__)
# Random per-process key: signs the session cookie. Regenerated every restart,
# which (together with the in-memory SESSIONS store) means a restart logs out.
app.secret_key = secrets.token_bytes(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",   # blocks cross-site (CSRF) POSTs
)

# --- authentication ----------------------------------------------------------

def current_session():
    """Return the live session dict (with the cached panel password) or None."""
    sid = session.get("sid")
    s = SESSIONS.get(sid) if sid else None
    if s and s["exp"] > time.time():
        return s
    if sid:
        SESSIONS.pop(sid, None)
        session.pop("sid", None)
    return None

def _bearer_token():
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        return h[7:].strip()
    return request.args.get("token")   # also allow ?token= for simple links

def is_authed():
    """True for a logged-in browser session OR a valid API token."""
    if current_session():
        return True
    tok = _bearer_token()
    return bool(API_TOKEN and tok and secrets.compare_digest(tok, API_TOKEN))

def require_auth(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if is_authed():
            return fn(*a, **kw)
        return jsonify(ok=False, error="unauthorized"), 401
    return wrapper

LOGIN_PAGE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PC Remote — unlock</title>
<style>
 :root{color-scheme:dark}
 body{font-family:system-ui,sans-serif;background:#15171c;color:#e7e9ee;
      margin:0;display:grid;place-items:center;height:100vh}
 form{max-width:320px;width:90%;text-align:center}
 h1{font-size:1.2rem;margin-bottom:1rem}
 input{width:100%;box-sizing:border-box;padding:14px;font-size:1rem;
       border-radius:12px;border:1px solid #333;background:#1c1f26;
       color:#e7e9ee;margin-bottom:10px}
 button{width:100%;font-size:1rem;padding:14px;border:0;border-radius:12px;
        background:#2a2e38;color:#e7e9ee;cursor:pointer}
 .err{color:#e96b6b;font-size:.85rem;min-height:1.2em;margin-bottom:6px}
 .note{color:#8b909c;font-size:.8rem;margin-top:1rem}
</style></head><body>
<form method="post" action="/auth">
 <h1>🔒 PC Remote</h1>
 <div class="err">{{ error }}</div>
 <input type="password" name="password" placeholder="Panel password" autofocus>
 <button>Unlock</button>
 {% if not configured %}
 <div class="note">No panel password set — run ./install.sh on the Pi.</div>
 {% endif %}
</form></body></html>
"""

PAGE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PC Remote</title>
<style>
 :root{color-scheme:dark}
 body{font-family:system-ui,sans-serif;background:#15171c;color:#e7e9ee;
      margin:0;padding:18px;max-width:560px;margin:auto}
 h1{font-size:1.2rem;margin:.2rem 0 1rem}
 h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;
    color:#8b909c;margin:1.3rem 0 .5rem}
 .grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
 button{font-size:1rem;padding:16px;border:0;border-radius:14px;
        background:#2a2e38;color:#e7e9ee;cursor:pointer}
 button:active{background:#3a4150}
 .wake{background:#1f6f43}.danger{background:#7a2230}
 input{width:100%;box-sizing:border-box;padding:14px;font-size:1rem;
       border-radius:12px;border:1px solid #333;background:#1c1f26;
       color:#e7e9ee;margin-bottom:8px}
 .full{grid-column:1/-1}
 #toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);
        background:#2a2e38;padding:10px 18px;border-radius:10px;opacity:0;
        transition:.2s}
 .row{display:flex;align-items:center;gap:8px}
 .top{display:flex;justify-content:space-between;align-items:center}
 .link{font-size:.75rem;color:#8b909c;background:none;padding:0;width:auto;
       text-transform:none;letter-spacing:0;cursor:pointer}
 #log{margin-top:.4rem;font:.78rem/1.45 ui-monospace,SFMono-Regular,monospace;
      max-height:230px;overflow:auto;background:#1c1f26;border-radius:12px;
      padding:8px;border:1px solid #23262e}
 #log .e{padding:2px 2px;border-bottom:1px solid #23262e;
         white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 #log .e:last-child{border-bottom:0}
 .lv-ok{color:#7fd99a}.lv-warn{color:#e7c15d}.lv-error{color:#e96b6b}
</style></head><body>
<div class="top"><h1>🖥️ PC Remote</h1>
 <button class="link" onclick="logout()">log out</button></div>

<h2>Power</h2>
<div class="grid">
 <button class="wake" onclick="post('/wake')">⏻ Wake PC</button>
 <button class="danger" onclick="confirmShutdown()">⏻ Shut down PC</button>
 <button class="full" onclick="post('/login')">🔑 Log in (type password)</button>
</div>

<h2>Run app / command</h2>
<input id="cmd" placeholder="e.g. steam, cmd, notepad, C:\\game\\game.exe">
<div class="grid">
 <button class="full" onclick="run()">▶ Run (Win+R)</button>
</div>

<h2>Type text</h2>
<input id="txt" placeholder="text to type on the PC">
<div class="grid">
 <button onclick="typeit()">⌨ Type</button>
 <button onclick="post('/key',{keys:['enter']})">↵ Enter</button>
</div>

<h2>Quick actions</h2>
<div class="grid">
 <button onclick="post('/key',{keys:['win','l']})">🔒 Lock</button>
 <button onclick="post('/key',{keys:['alt','f4']})">✕ Alt+F4</button>
 <button onclick="post('/key',{keys:['esc']})">Esc</button>
 <button class="danger" onclick="post('/key',{keys:['ctrl','alt','delete']})">Ctrl+Alt+Del</button>
</div>

<h2 class="row">Log <button class="link" onclick="loadLogs()">↻ refresh</button></h2>
<div id="log"></div>

<div id="toast"></div>
<script>
function toast(m){let t=document.getElementById('toast');
 t.textContent=m;t.style.opacity=1;setTimeout(()=>t.style.opacity=0,1600)}
function addEntry(e){let d=document.getElementById('log');
 let row=document.createElement('div');row.className='e lv-'+e.level;
 row.textContent=e.ts+'  '+e.action+'  ['+e.level+']'+(e.detail?'  '+e.detail:'');
 d.append(row);d.scrollTop=d.scrollHeight}
async function loadLogs(){let r=await fetch('/logs');
 if(r.status==401){location.reload();return}
 let j=await r.json();
 let d=document.getElementById('log');d.innerHTML='';
 (j.entries||[]).forEach(addEntry)}
async function post(url,body){
 try{
  let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify(body||{})});
  if(r.status==401){location.reload();return}
  let j=await r.json();
  if(j.entry)addEntry(j.entry);
  let lv=j.entry?j.entry.level:(j.ok?'ok':'error');
  toast(lv=='ok'?'OK':lv=='warn'?'⚠ USB not configured — PC will not receive keys'
        :'✕ '+(j.error||'error'));
 }catch(err){toast('✕ no connection to Pi')}}
function run(){let v=document.getElementById('cmd').value;
 if(v)post('/run',{cmd:v})}
function typeit(){let v=document.getElementById('txt').value;
 if(v)post('/type',{text:v})}
function confirmShutdown(){
 if(confirm('Really shut down the PC?'))post('/shutdown')}
async function logout(){await fetch('/logout',{method:'POST'});location.reload()}
window.addEventListener('load',loadLogs);
</script></body></html>
"""

@app.route("/")
def index():
    if not is_authed():
        return render_template_string(
            LOGIN_PAGE, error="", configured=bool(PANEL_HASH)), 401
    return render_template_string(PAGE)

@app.route("/auth", methods=["POST"])
def auth():
    pw = request.form.get("password", "")
    if not PANEL_HASH or not cs.verify_panel_password(pw, PANEL_HASH):
        return render_template_string(
            LOGIN_PAGE, error="Wrong password.",
            configured=bool(PANEL_HASH)), 401
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = {"pw": pw, "exp": time.time() + SESSION_TTL}
    session["sid"] = sid
    return redirect("/")

@app.route("/logout", methods=["POST"])
def logout():
    sid = session.pop("sid", None)
    if sid:
        SESSIONS.pop(sid, None)
    return jsonify(ok=True)

@app.route("/logs")
@require_auth
def logs():
    return jsonify(entries=list(LOG))

@app.route("/key", methods=["POST"])
@require_auth
def key():
    keys = (request.get_json(silent=True) or {}).get("keys", [])
    return run_action("key " + "+".join(keys), lambda: hid.send_key(*keys))

@app.route("/type", methods=["POST"])
@require_auth
def type_text():
    text = (request.get_json(silent=True) or {}).get("text", "")
    return run_action(f"type {len(text)} chars", lambda: hid.type_string(text))

@app.route("/run", methods=["POST"])
@require_auth
def run_cmd():
    cmd = (request.get_json(silent=True) or {}).get("cmd", "")
    return run_action(f"run: {cmd}", lambda: hid.run_command(cmd))

@app.route("/shutdown", methods=["POST"])
@require_auth
def shutdown():
    return run_action("shutdown", lambda: hid.run_command("shutdown /s /t 0"))

@app.route("/wake", methods=["POST"])
@require_auth
def wake():
    # WoL doesn't go over USB — we don't check the HID state here
    if not PC_MAC:
        entry = log_event("wake (WoL)", "error", "PC_MAC not set in config")
        return jsonify(ok=False, error="PC_MAC not set in config (.env)", entry=entry), 400
    try:
        subprocess.run(["wakeonlan", PC_MAC], check=True)
        entry = log_event("wake (WoL)", "ok", PC_MAC)
        return jsonify(ok=True, entry=entry)
    except Exception as e:
        entry = log_event("wake (WoL)", "error", str(e))
        return jsonify(ok=False, error=str(e), entry=entry), 500

@app.route("/login", methods=["POST"])
@require_auth
def login():
    # Typing the Windows password needs the panel password to decrypt it, so
    # this only works for an interactive browser session — not a token call.
    sess = current_session()
    if sess is None:
        entry = log_event("login", "error", "needs interactive panel login (not a token)")
        return jsonify(ok=False, error="this action requires logging in to the panel (not an API token)", entry=entry), 403
    blob = _read(WIN_SECRET_FILE)
    if not blob:
        entry = log_event("login", "error", f"missing password file: {WIN_SECRET_FILE}")
        return jsonify(ok=False, error=f"missing password file: {WIN_SECRET_FILE}", entry=entry), 400
    pw = cs.decrypt_windows_password(sess["pw"], blob)
    if pw is None:
        entry = log_event("login", "error", "could not decrypt Windows password")
        return jsonify(ok=False, error="could not decrypt the stored Windows password (re-run install.sh if you changed the panel password)", entry=entry), 500
    return run_action("login (type password)", lambda: hid.login(pw))

if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
