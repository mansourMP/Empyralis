"""P3: Durable memory test — SEPARATE sessions."""
import json, http.cookiejar, urllib.request, sys, os
BASE = "http://127.0.0.1:8001"

def new_session():
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
    return api, cj

email = f"p3mem_{os.urandom(4).hex()}@empyralis.dev"
pw = "p3mem_12345678"

# Session 1: register + login
api1, cj1 = new_session()
s, r = api1("POST", "/api/auth/register", {"email": email, "password": pw, "channel": "web"})
assert r.get("ok"), f"Register failed: {r}"
ws = r["default_workspace_id"]
print(f"Workspace: {ws}")

s, l = api1("POST", "/api/auth/login", {"email": email, "password": pw, "channel": "web"})
assert l.get("ok"), "Login failed"

# Session 1 - Turn 1: Write memory explicitly using the tool
print("\n=== SESSION 1: Write to memory ===")
s, t1 = api1("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "Use the memory_update tool to write a memory with key 'p3_test_key' and value 'p3_secret_value_xyz'. Write it NOW using the tool, do not just say you will.",
})
print(f"Status: {s}")
print(f"message: {str(t1.get('message',''))[:300]}")
print(f"memory_updates: {t1.get('memory_updates')}")
print(f"tool_calls: {json.dumps(t1.get('tool_calls',[]))[:500]}")

# Session 2: completely new login (different thread)
print("\n=== SESSION 2: Fresh login, read memory ===")
api2, cj2 = new_session()
s, l2 = api2("POST", "/api/auth/login", {"email": email, "password": pw, "channel": "web"})
assert l2.get("ok"), "Login failed"

s, t2 = api2("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "Use the memory_get tool to read the value of key 'p3_test_key'. Use the tool NOW.",
})
print(f"Status: {s}")
print(f"message: {str(t2.get('message',''))[:300]}")
print(f"memory_updates: {t2.get('memory_updates')}")
print(f"tool_calls: {json.dumps(t2.get('tool_calls',[]))[:500]}")

# Also try direct memory_get to check if the key exists
print("\n=== Try with memory_search ===")
s, t3 = api2("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "Use the memory_search tool with query 'p3_test_key'. Use the tool NOW.",
})
print(f"Status: {s}")
print(f"message: {str(t3.get('message',''))[:300]}")

# Check if value was recalled across sessions
combined = str(t2.get('message','')) + str(t2.get('tool_calls',''))
has_value = 'p3_secret_value_xyz' in combined
print(f"\n{'✓ DURABLE MEMORY PROVEN' if has_value else '✗ DURABLE MEMORY NOT PROVEN'}")
