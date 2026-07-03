"""P4: Test task_complete tool end-to-end."""
import json, http.cookiejar, urllib.request, os
BASE = "http://127.0.0.1:8001"
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

email = f"p4_{os.urandom(4).hex()}@empyralis.dev"
pw = "p4_12345678"

s, r = api("POST", "/api/auth/register", {"email": email, "password": pw, "channel": "web"})
ws = r["default_workspace_id"]
api("POST", "/api/auth/login", {"email": email, "password": pw, "channel": "web"})

# Ask the agent to do a simple task and call task_complete when done
print("=== Test: agent uses task_complete ===")
s, chat = api("POST", "/api/sage/chat", {
    "workspace_id": ws,
    "message": "Calculate 15 * 7. When you have the answer, call the task_complete tool with summary='Calculated 15*7=105'. Do it now.",
})
print(f"Status: {s}")
print(f"message: {str(chat.get('message',''))[:200]}")
tc = chat.get('tool_calls', [])
print(f"tool_calls count: {len(tc)}")
for t in tc:
    print(f"  tool: {t.get('name')} args: {json.dumps(t.get('arguments',{}))[:200]}")

# Check if task_complete was called
task_complete_calls = [t for t in tc if t.get('name') == 'task_complete']
if task_complete_calls:
    summary = task_complete_calls[0].get('arguments', {}).get('summary', '')
    print(f"\n✓ TASK_COMPLETE CALLED. Summary: {summary}")
else:
    print(f"\n✗ task_complete NOT called by model")
    # Check if the model at least mentioned it
    if 'task_complete' in str(chat.get('message','')).lower():
        print("  Model mentioned task_complete in text but didn't call it as a tool")

# Check available tools in response
at = [t['id'] for t in chat.get('available_tools', [])]
print(f"\nAvailable tools in response: {len(at)}")
print(f"task_complete in available_tools: {'task_complete' in at}")
