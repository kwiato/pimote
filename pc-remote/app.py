#!/usr/bin/env python3
import os
import glob
import time
import subprocess
from collections import deque
from flask import Flask, request, jsonify, render_template_string
import hid_keyboard as hid

# Config comes from environment variables (the .env file loaded via systemd
# EnvironmentFile) so the repo can be shared without editing source.
PC_MAC = os.environ.get("PC_MAC", "")          # PC NIC MAC — used for WoL
HOST = os.environ.get("REMOTE_HOST", "0.0.0.0")
PORT = int(os.environ.get("REMOTE_PORT", "5000"))

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

# The Windows login password is kept OUTSIDE the repo, in a 600 file.
# Override the path with the PC_LOGIN_SECRET_FILE env var if needed.
SECRET_FILE = os.environ.get(
    "PC_LOGIN_SECRET_FILE",
    os.path.expanduser("~/.config/pc-remote/login.secret"),
)

def load_password():
    """Return the password from the file (without the trailing \\n), or None
    when the file is missing."""
    try:
        with open(SECRET_FILE, "r", encoding="utf-8") as f:
            return f.read().rstrip("\n")
    except FileNotFoundError:
        return None

app = Flask(__name__)

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
<h1>🖥️ PC Remote</h1>

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
async function loadLogs(){let r=await fetch('/logs');let j=await r.json();
 let d=document.getElementById('log');d.innerHTML='';
 (j.entries||[]).forEach(addEntry)}
async function post(url,body){
 try{
  let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify(body||{})});
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
window.addEventListener('load',loadLogs);
</script></body></html>
"""

@app.route("/")
def index():
    return render_template_string(PAGE)

@app.route("/logs")
def logs():
    return jsonify(entries=list(LOG))

@app.route("/key", methods=["POST"])
def key():
    keys = request.json.get("keys", [])
    return run_action("key " + "+".join(keys), lambda: hid.send_key(*keys))

@app.route("/type", methods=["POST"])
def type_text():
    text = request.json.get("text", "")
    return run_action(f"type {len(text)} chars", lambda: hid.type_string(text))

@app.route("/run", methods=["POST"])
def run_cmd():
    cmd = request.json.get("cmd", "")
    return run_action(f"run: {cmd}", lambda: hid.run_command(cmd))

@app.route("/shutdown", methods=["POST"])
def shutdown():
    return run_action("shutdown", lambda: hid.run_command("shutdown /s /t 0"))

@app.route("/wake", methods=["POST"])
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
def login():
    pw = load_password()
    if not pw:
        entry = log_event("login", "error", f"missing password file: {SECRET_FILE}")
        return jsonify(ok=False, error=f"missing password file: {SECRET_FILE}", entry=entry), 400
    return run_action("login (type password)", lambda: hid.login(pw))

if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
