"""PM1: Durable memory — SEPARATE sessions, tool-call proof."""
import json, http.cookiejar, urllib.request, os
BASE = "http://127.0.0.1:8001"

def new_session():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    def gc(n):
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
            csrf = gc("empyralis_csrf_token")
            if csrf:
                req.add_header("x-csrf-token", csrf)
        try:
            with opener.open(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    return api

email = f"pm1_{os.urandom(4).hex()}@empyralis.dev"
pw = "pm1_12345678"

# ── SESSION A: Write to memory ──
print("=" * 60)
print("SESSION A — Remember a fact")
print("=" * 60)
api_a = new_session()
s, r = api_a("POST", "/api/auth/register", {"email": email, "password": pw, "channel": "web"})
ws = r["default_workspace_id"]
print(f"Workspace: {ws}")
api_a("POST", "/api/auth/login", {"email": email, "password": pw, "channel": "web"})

s, t1 = api_a("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "Remember this: the client's security code is ZEBRA-991. Save it to memory NOW using the memory_write tool. Do not just say you'll remember — call the tool.",
})
print(f"Status: {s}")
print(f"message: {str(t1.get('message',''))[:300]}")
tc = t1.get('tool_calls', [])
print(f"tool_calls: {len(tc)}")
for t in tc:
    print(f"  → {t.get('name')}: {json.dumps(t.get('arguments',{}))[:200]}")
mu = t1.get('memory_updates', [])
print(f"memory_updates: {mu} {'← NON-EMPTY!' if mu else '← STILL EMPTY'}")

# ── SESSION B: Fresh login, read memory ──
print("\n" + "=" * 60)
print("SESSION B — Fresh login, recall the fact")
print("=" * 60)
api_b = new_session()
s, l = api_b("POST", "/api/auth/login", {"email": email, "password": pw, "channel": "web"})
print(f"Login: {s}")

s, t2 = api_b("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "Use the memory_search tool with query='ZEBRA' to find the client's security code. Use the tool NOW.",
})
print(f"Status: {s}")
print(f"message: {str(t2.get('message',''))[:300]}")
tc2 = t2.get('tool_calls', [])
print(f"tool_calls: {len(tc2)}")
for t in tc2:
    print(f"  → {t.get('name')}: {json.dumps(t.get('arguments',{}))[:200]}")

has_zebra = 'ZEBRA-991' in json.dumps(tc2) or 'ZEBRA-991' in str(t2.get('message',''))
print(f"\n{'✓ PM1 DURABLE MEMORY PROVEN' if has_zebra else '✗ PM1 NOT PROVEN'}")
