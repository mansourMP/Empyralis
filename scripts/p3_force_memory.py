"""P3: Force memory_write tool call, then verify from new session."""
import json, http.cookiejar, urllib.request, os
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
    return api

email = f"p3b_{os.urandom(4).hex()}@empyralis.dev"
pw = "p3b_12345678"

# Session 1
api1 = new_session()
s, r = api1("POST", "/api/auth/register", {"email": email, "password": pw, "channel": "web"})
ws = r["default_workspace_id"]
api1("POST", "/api/auth/login", {"email": email, "password": pw, "channel": "web"})

print("=== SESSION 1: Force memory_write tool call ===")
s, t1 = api1("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "Call the memory_write tool with path='test_p3.md' and content='P3_DURABLE_VALUE=alpha42' and mode='overwrite'. Do it now.",
})
print(f"Status: {s}")
print(f"message: {str(t1.get('message',''))[:300]}")
tc = t1.get('tool_calls', [])
print(f"tool_calls: {len(tc)} -> {json.dumps(tc)[:500]}")
print(f"memory_updates: {t1.get('memory_updates')}")

# Check disk for the file
print("\n=== Check filesystem ===")
mem_path = os.path.expanduser(f"~/.empyralis/memory/{ws}")
if os.path.exists(mem_path):
    files = os.listdir(mem_path)
    print(f"Memory dir: {mem_path}")
    print(f"Files: {files}")
    test_file = os.path.join(mem_path, "test_p3.md")
    if os.path.exists(test_file):
        content = open(test_file).read()
        print(f"test_p3.md content: {content}")
        has_value = "P3_DURABLE_VALUE" in content
        print(f"✓ VALUE FOUND ON DISK" if has_value else "✗ Value not in file")
    else:
        print("test_p3.md NOT FOUND on disk")
else:
    print(f"Memory dir does not exist: {mem_path}")

# Session 2: read back
print("\n=== SESSION 2: Read memory back ===")
api2 = new_session()
api2("POST", "/api/auth/login", {"email": email, "password": pw, "channel": "web"})
s, t2 = api2("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "Call the memory_read tool with path='test_p3.md'. Use the tool NOW.",
})
print(f"Status: {s}")
print(f"message: {str(t2.get('message',''))[:300]}")
tc2 = t2.get('tool_calls', [])
print(f"tool_calls: {len(tc2)} -> {json.dumps(tc2)[:500]}")

has_value = 'alpha42' in str(t2.get('message','')) or 'P3_DURABLE_VALUE' in json.dumps(tc2)
print(f"\n{'✓ DURABLE MEMORY PROVEN' if has_value else '✗ DURABLE MEMORY NOT PROVEN'}")
