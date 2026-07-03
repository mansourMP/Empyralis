"""PM1 final: Durable memory with recognized format."""
import json, http.cookiejar, urllib.request, os
BASE = "http://127.0.0.1:8001"

def new_session():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    def gc(n):
        for c in cj:
            if c.name == n: return c.value
        return None
    def api(m, p, b=None):
        url = f"{BASE}{p}"
        d = json.dumps(b).encode() if b else None
        req = urllib.request.Request(url, data=d, method=m)
        req.add_header("Content-Type", "application/json")
        if m != "GET":
            csrf = gc("empyralis_csrf_token")
            if csrf: req.add_header("x-csrf-token", csrf)
        try:
            with opener.open(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    return api

email = f"pm1f_{os.urandom(4).hex()}@empyralis.dev"
pw = "pm1f_12345678"

# Session A: Write memory
print("=== SESSION A: Write ===")
api_a = new_session()
r = api_a("POST", "/api/auth/register", {"email":email,"password":pw,"channel":"web"})
ws = r[1]["default_workspace_id"]
api_a("POST", "/api/auth/login", {"email":email,"password":pw,"channel":"web"})

s, t1 = api_a("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "remember that client_code = ZEBRA-991",
})
print(f"Status: {s}")
print(f"message: {str(t1.get('message',''))[:300]}")
tc = t1.get('tool_calls',[])
print(f"tool_calls: {len(tc)}")
for t in tc:
    print(f"  → {t.get('name')}: {json.dumps(t.get('arguments',{}))[:200]}")
print(f"memory_updates: {t1.get('memory_updates')}")

# Session B: Read back
print("\n=== SESSION B: Read ===")
api_b = new_session()
api_b("POST", "/api/auth/login", {"email":email,"password":pw,"channel":"web"})
s, t2 = api_b("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "what is client_code",
})
print(f"Status: {s}")
print(f"message: {str(t2.get('message',''))[:300]}")
tc2 = t2.get('tool_calls',[])
print(f"tool_calls: {len(tc2)}")

has_it = 'ZEBRA-991' in str(t2.get('message',''))
print(f"\n{'✓ PM1 DURABLE MEMORY PROVEN' if has_it else '✗ NOT PROVEN'}")
