"""Phase V: Verify Reality v2 — with cookie + CSRF auth."""
import json, http.cookiejar, urllib.request, sys

BASE = "http://127.0.0.1:8001"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def get_cookie(name):
    for c in cj:
        if c.name == name:
            return c.value
    return None

def api(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    # Add CSRF token header for non-GET requests
    if method != "GET":
        csrf = get_cookie("empyralis_csrf_token")
        if csrf:
            req.add_header("x-csrf-token", csrf)
    try:
        with opener.open(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

email = f"vt3_{id({})}@empyralis.dev"
password = "vt3_12345678"

print("=== Register ===")
status, reg = api("POST", "/api/auth/register",
    {"email": email, "password": password, "channel": "web"})
print(f"  Status: {status}, ok={reg.get('ok')}")
ws_id = reg.get("default_workspace_id", "")
print(f"  Workspace: {ws_id}")

print("\n=== Login (captures cookie) ===")
status, login = api("POST", "/api/auth/login",
    {"email": email, "password": password, "channel": "web"})
print(f"  Status: {status}, ok={login.get('ok')}")
print(f"  CSRF token: {get_cookie('empyralis_csrf_token')[:20]}...")

print("\n=== V1: REAL CHAT TURN ===")
print(f"  Sending to workspace: {ws_id}")
status, chat = api("POST", "/api/sage/chat",
    {"workspace_id": ws_id, "message": "What is 2+2? Answer in one word."})
print(f"  Status: {status}")
if isinstance(chat, dict):
    print(f"  Keys: {sorted(chat.keys())}")
    print(f"  message: {str(chat.get('message', ''))[:300]}")
    print(f"  provider: {chat.get('provider', '')}")
    print(f"  error: {chat.get('error')}")
    print(f"  model: {chat.get('model')}")
    print(f"  trace_id: {chat.get('trace_id', '')}")
    print(f"  tool_calls: {len(chat.get('tool_calls', []))}")
    print(f"  memory_updates: {len(chat.get('memory_updates', []))}")
    print(f"  used_context: {len(chat.get('used_context', []))}")

print("\n=== FULL RESPONSE ===")
print(json.dumps(chat, indent=2, default=str)[:3000])
