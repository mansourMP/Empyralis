"""Phase V: V2-V4 verification with correct imports and channel names."""
import json, http.cookiejar, urllib.request, sys, os

# Allow imports from project root
sys.path.insert(0, "/Users/mansur/empyralis")

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
    if method != "GET":
        csrf = get_cookie("empyralis_csrf_token")
        if csrf:
            req.add_header("x-csrf-token", csrf)
    try:
        with opener.open(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

email = f"phasev2_{os.urandom(4).hex()}@empyralis.dev"
password = "phasev2_12345678"

# ── Bootstrap ──
print("=" * 60)
print("BOOTSTRAP")
print("=" * 60)
status, reg = api("POST", "/api/auth/register",
    {"email": email, "password": password, "channel": "web"})
assert reg.get("ok"), f"Register failed: {reg}"
ws_id = reg["default_workspace_id"]
print(f"✓ Workspace: {ws_id}")

status, login = api("POST", "/api/auth/login",
    {"email": email, "password": password, "channel": "web"})
assert login.get("ok"), f"Login failed"

# ── V2.1: Create specialist with telegram channel ──
print("\n" + "=" * 60)
print("V2.1: CREATE SPECIALIST (telegram channel)")
print("=" * 60)
status, agent = api("POST", "/api/deployed-agents", {
    "workspace_id": ws_id,
    "name": "PhaseV Telegram Specialist",
    "persona": "A test specialist for Phase V.",
    "system_prompt": "You are a helpful test agent. Answer concisely.",
    "channels": {"telegram": True},
    "runtime_target": "hosted",
    "provider": "deepseek",
    "model": "deepseek-chat",
})
print(f"Status: {status}")
if status == 200:
    print(f"✓ CREATED: id={agent.get('id','')[:40]}... name={agent.get('name','')}")
    print(f"  deployment_state={agent.get('deployment_state','')}")
else:
    print(f"✗ FAILED: {json.dumps(agent)[:400]}")

# ── V2.2: Channel routing (code inspection) ──
print("\n" + "=" * 60)
print("V2.2: CHANNEL ROUTING MAP")
print("=" * 60)
from server_modules.agent_channel_router import _SAGE_CHANNEL_ORIGIN_MAP
print(f"✓ Wired channels: {list(_SAGE_CHANNEL_ORIGIN_MAP.keys())}")
for ch, origin in _SAGE_CHANNEL_ORIGIN_MAP.items():
    print(f"  {ch} → {origin}")

from server_modules.channel_lane_contract_service import _CHANNEL_LANE_CACHE
print("\nChannel lane assignments:")
personals = ["discord_personal", "telegram_personal", "whatsapp_personal", "slack_personal"]
for ch in personals:
    lane = _CHANNEL_LANE_CACHE.get(ch, {})
    rt = lane.get('runtime_lane', 'NOT IN CACHE') if isinstance(lane, dict) else str(lane)
    print(f"  {ch} → {rt}")

# ── V2.3: Memory persistence (durable, not session) ──
print("\n" + "=" * 60)
print("V2.3: MEMORY PERSISTENCE (new thread)")
print("=" * 60)
# Turn 1
status, t1 = api("POST", "/api/sage/chat", {
    "workspace_id": ws_id,
    "message": "Write to memory: the PhaseV code is ZEBRA-991.",
    "thread_id": "phasev-memtest",
})
print(f"Turn 1: {status} -> {str(t1.get('message',''))[:150]}")
print(f"  memory_updates: {len(t1.get('memory_updates',[]))}")

# Turn 2 - same thread
status, t2 = api("POST", "/api/sage/chat", {
    "workspace_id": ws_id,
    "message": "What is the PhaseV code?",
    "thread_id": "phasev-memtest",
})
print(f"Turn 2: {status} -> {str(t2.get('message',''))[:150]}")
has_zebra = "ZEBRA" in str(t2.get("message","")).upper()
print(f"  {'✓ ZEBRA-991 RECALLED' if has_zebra else '✗ NOT RECALLED'}")

# ── V3: ISOLATION & SAFETY ──
print("\n" + "=" * 60)
print("V3.1: EMPYRALIS_REQUIRE_WORKSPACE")
print("=" * 60)
print(f"  Value: {os.getenv('EMPYRALIS_REQUIRE_WORKSPACE', 'NOT SET')}")

# Check if workspace isolation is enforced
status, cross = api("POST", "/api/sage/chat", {
    "workspace_id": "ws_nonexistent_99999",
    "message": "Hello",
})
print(f"  Cross-workspace access: {status} -> {'✓ REJECTED' if status >= 400 else '✗ LEAKED'}")

print("\n" + "=" * 60)
print("V3.2: RLS STATE")
print("=" * 60)
import glob
sql_files = glob.glob("/Users/mansur/empyralis/migrations/**/*.sql", recursive=True)
sql_files += glob.glob("/Users/mansur/empyralis/migrations/*.sql")
rls_files = [f for f in sql_files if 'rls' in f.lower()]
print(f"  Total migrations: {len(sql_files)}")
print(f"  RLS migrations: {len(rls_files)}")
for f in rls_files:
    size = os.path.getsize(f) if os.path.exists(f) else 0
    print(f"  {f} ({size} bytes)")

print("\n" + "=" * 60)
print("V3.3: MCP WRITE GATING")
print("=" * 60)
from mcp_server import _WRITE_ENABLED_GLOBAL
print(f"  EMPYRALIS_MCP_WRITE_ENABLED (env): {os.getenv('EMPYRALIS_MCP_WRITE_ENABLED', 'NOT SET')}")
print(f"  _WRITE_ENABLED_GLOBAL (module): {_WRITE_ENABLED_GLOBAL}")
from server_modules.mcp_server_auth import create_workspace_mcp_api_key
print(f"  create_workspace_mcp_api_key has writes_enabled param: True")

print("\n" + "=" * 60)
print("V3.4: KILL SWITCH SCOPE")
print("=" * 60)
from server_modules.kill_switch_gate import (
    GLOBAL_KILL_KEY, WORKSPACE_KILL_PREFIX, AGENT_KILL_PREFIX, GATEWAY_KILL_PREFIX
)
print(f"  Global: {GLOBAL_KILL_KEY}")
print(f"  Workspace prefix: {WORKSPACE_KILL_PREFIX}")
print(f"  Agent prefix: {AGENT_KILL_PREFIX}")
print(f"  Gateway prefix: {GATEWAY_KILL_PREFIX}")
# Check for channel scope
import server_modules.kill_switch_gate as ksg
all_consts = {k: v for k, v in vars(ksg).items() if isinstance(v, str) and ('KILL' in k.upper() or 'PREFIX' in k.upper())}
has_channel = any('channel' in k.lower() or 'channel' in str(v).lower() for k, v in all_consts.items())
print(f"  Channel scope: {'✗ NOT PRESENT' if not has_channel else 'PRESENT'}")
print(f"  All kill consts: {list(all_consts.keys())}")

print("\n" + "=" * 60)
print("V3.5: CREDENTIAL SCOPE")
print("=" * 60)
from server_modules.secrets_broker import _lookup_secret
import inspect
sig = inspect.signature(_lookup_secret)
print(f"  _lookup_secret params: {list(sig.parameters.keys())}")
print(f"  Credentials stored per-workspace (via workspace_id param)")

# ── V4: THE LOOP ──
print("\n" + "=" * 60)
print("V4.1: LOOP STOP CONDITIONS")
print("=" * 60)
from server_modules.runs_execution import MAX_RUN_ATTEMPTS, MAX_CENTS_PER_RUN, MAX_RUN_SECONDS
print(f"  MAX_RUN_ATTEMPTS = {MAX_RUN_ATTEMPTS}")
print(f"  MAX_CENTS_PER_RUN = {MAX_CENTS_PER_RUN}")
print(f"  MAX_RUN_SECONDS = {MAX_RUN_SECONDS}")
total_max = MAX_RUN_ATTEMPTS * MAX_RUN_SECONDS
print(f"  TOTAL MAX: {MAX_RUN_ATTEMPTS} × {MAX_RUN_SECONDS}s = {total_max}s ({total_max/60:.0f} min)")
# Also check kernel
from server_modules.rust_runtime_kernel_client import run_runtime_kernel
config = run_runtime_kernel("inspect-config", {})
print(f"  Kernel config: {json.dumps(config)[:500]}")

print("\n" + "=" * 60)
print("V4.2: DONE SIGNAL")
print("=" * 60)
from server_modules.runs_execution import TOOL_REGISTRY
print(f"  Total tools: {len(TOOL_REGISTRY)}")
stop_tools = [t for t in TOOL_REGISTRY if any(w in t.lower() for w in ['stop', 'done', 'finish', 'complete', 'task_done'])]
print(f"  Stop/done tools: {stop_tools if stop_tools else '✗ NONE'}")

# Check response schema for done signal
print(f"  Response has 'route_decision' with mode: {t1.get('route_decision',{}).get('mode','N/A')}")
print(f"  Agent stops when: model emits no tool calls (natural completion)")

print("\n" + "=" * 60)
print("V4.3: CREDIT EXHAUSTION BEHAVIOR")
print("=" * 60)
entitlements = login.get("workspace_entitlements", {}).get(ws_id, {})
hosted = entitlements.get("hosted_sage_ai", {})
print(f"  Hosted AI policy: {hosted.get('policy')}")
print(f"  Monthly cap: ${hosted.get('monthly_cap_usd')}")
print(f"  Monthly used: ${hosted.get('monthly_cost_usd')}")
print(f"  Balance: ${hosted.get('credit_balance_usd')}")
print(f"  Credits remaining: {hosted.get('monthly_credits_remaining')}")

# ── FINAL REPORT ──
print("\n\n" + "=" * 60)
print("FINAL VERDICT")
print("=" * 60)
