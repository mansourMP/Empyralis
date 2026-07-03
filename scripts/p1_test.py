"""P1: Real turn against Postgres."""
import json, http.cookiejar, urllib.request, sys, os
BASE = "http://127.0.0.1:8001"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def get_cookie(n):
    for c in cj:
        if c.name == n:
            return c.value
    return None

def api(m, p, b=None):
    url = f"{BASE}{p}"
    d = json.dumps(b).encode() if b else None
    req = urllib.request.Request(url, data=d, method=m)
    req.add_header("Content-Type", "application/json")
    if m != "GET":
        csrf = get_cookie("empyralis_csrf_token")
        if csrf:
            req.add_header("x-csrf-token", csrf)
    try:
        with opener.open(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

email = f"p1pg_{os.urandom(4).hex()}@empyralis.dev"
pw = "p1pg_12345678"

# Register
s, r = api("POST", "/api/auth/register", {"email": email, "password": pw, "channel": "web"})
assert r.get("ok"), f"Register failed: {r}"
ws = r["default_workspace_id"]
print(f"Workspace: {ws}")

# Login
s, l = api("POST", "/api/auth/login", {"email": email, "password": pw, "channel": "web"})
assert l.get("ok"), "Login failed"

# V1 real turn
print("\n--- REAL TURN (Postgres) ---")
s, chat = api("POST", "/api/sage/chat", {"workspace_id": ws, "message": "What is 7*8? Answer in one word."})
print(f"Status: {s}")
print(f"message: {chat.get('message','')}")
print(f"provider: {chat.get('provider','')}  model: {chat.get('model','')}")
print(f"error: {chat.get('error')}")
print(f"trace_id: {chat.get('trace_id','')}")

# Check health for Redis status
s, h = api("GET", "/api/health")
print(f"\nHealth: {json.dumps(h)}")

# Check if any Postgres-specific tables were written to
print(f"\nworkspace_id in response: {chat.get('workspace_id','')}")
print(f"tenant_id: {chat.get('tenant_id','')}")
print(f"Full keys: {sorted(chat.keys())}")

# Check activity ledger
print(f"\ntransparency_events count: {len(chat.get('transparency_events',[]))}")
print(f"trace_events count: {len(chat.get('trace_events',[]))}")
