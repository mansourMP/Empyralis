"""Phase V: Verify Reality — run against the live server at :8001."""
import json, urllib.request, sys

BASE = "http://127.0.0.1:8001"

def api(method, path, body=None, token=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

print("=== V0: Preflight ===")
status, health = api("GET", "/api/health")
print(f"  Health: {status} {json.dumps(health)}")

print("\n=== V0: Register test user ===")
email = f"vtest_{id({})}@empyralis.dev"
status, reg = api("POST", "/api/auth/register",
    {"email": email, "password": "vtest12345678", "channel": "web"})
print(f"  Register: {status} ok={reg.get('ok')}")
ws_id = reg.get("default_workspace_id", "")
print(f"  Workspace: {ws_id}")

print("\n=== V0: Login ===")
status, login = api("POST", "/api/auth/login",
    {"email": email, "password": "vtest12345678", "channel": "web"})
print(f"  Login: {status} ok={login.get('ok')}")
# Get token from auth_session... we need to extract it differently
# Let's use cookie-based approach instead

# Actually, the login response includes the token via Set-Cookie.
# Let's use the session-based approach from the code:
# In local dev, auth may not be required. Let's check.

print("\n=== V0: Check auth requirement ===")
# Try the chat endpoint without auth
status, resp = api("POST", "/api/sage/chat",
    {"workspace_id": ws_id, "message": "Say hello in one word."})
print(f"  Chat (no auth): {status} -> {json.dumps(resp)[:200]}")

print("\n=== Done ===")
