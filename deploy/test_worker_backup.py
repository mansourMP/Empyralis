"""Prove vault backup runs under worker Python (system python, no cloud venv).

The worker runs under system Python (/usr/bin/python3) which lacks pydantic.
vault_store.py MUST be loadable and _backup_vault_to_cloud() MUST succeed
without touching server_modules/__init__.py (which pulls in pydantic).

This script:
1. Sets env vars as the worker would
2. Loads vault_store via spec_from_file_location (bypasses __init__.py)
3. Calls _backup_vault_to_cloud() directly
4. Verifies the HTTP POST reached the mock cloud
"""
import json, os, sys, base64, urllib.request, importlib.util, time

CLOUD = "http://localhost:9999"
TOKEN = "ns_test_token_change_me_in_prod"
PROFILE = "rp_test_vps_001"
HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}

# ── Set worker env ──────────────────────────────────────────────────────────
os.environ["EMPYRALIS_RUNTIME_KERNEL_BIN"] = (
    "/opt/empyralis/empyralis-runtime-kernel/target/release/empyralis-runtime-kernel"
)
os.environ["EMPYRALIS_STATE_HOME"] = "/opt/empyralis/.empyralis/state"
os.environ["EMPYRALIS_CLOUD_URL"] = CLOUD
os.environ["EMPYRALIS_RUNTIME_PROFILE_ID"] = PROFILE
os.environ["EMPYRALIS_NODE_SESSION_TOKEN"] = TOKEN
os.environ["ENV"] = "dev"
os.environ["CREDENTIAL_VAULT_KEY"] = "test-worker-backup-key-2026"

# Verify we're NOT using the cloud venv
print(f"Python: {sys.executable}")
print(f"Python version: {sys.version}")
assert "/opt/empyralis-app/.venv" not in sys.executable, (
    "FAIL: Running under cloud venv — worker proof must use system Python"
)
print("OK: System Python (not cloud venv)")

# ── STEP 1: Load vault_store directly (bypass server_modules/__init__.py) ──
print("")
print("=== STEP 1: Load vault_store via spec_from_file_location ===")
spec = importlib.util.spec_from_file_location(
    "vault_store", "/opt/empyralis/server_modules/vault_store.py"
)
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)
print("OK: vault_store loaded without hitting __init__.py (no pydantic import)")

# Sanity check — functions should be available
for fn in ["_backup_vault_to_cloud", "_kernel_client", "_cloud_api_post"]:
    assert hasattr(vs, fn), f"FAIL: {fn} not found on vault_store module"
print("OK: All required functions present")

# ── STEP 2: Create a test vault file ───────────────────────────────────────
print("")
print("=== STEP 2: Create test vault file ===")
vault_dir = "/opt/empyralis/.empyralis/state"
os.makedirs(vault_dir, exist_ok=True)
vault_path = f"{vault_dir}/vault.json"

# Write a minimal vault (same format vault_store expects)
vault_content = {
    "version": 1,
    "credentials": [
        {
            "id": "worker_test_001",
            "provider": "openai",
            "label": "Worker Backup Proof",
            "value": "sk-worker-backup-proof-12345",
            "created_at": "2026-06-23T00:00:00Z",
        }
    ],
}
with open(vault_path, "w") as f:
    json.dump(vault_content, f)
print(f"Test vault written: {vault_path}")
print(f"Credential: worker_test_001 -> sk-worker-backup-proof-12345")

# ── STEP 3: Call _backup_vault_to_cloud ────────────────────────────────────
print("")
print("=== STEP 3: Call _backup_vault_to_cloud() ===")
from pathlib import Path

try:
    vs._backup_vault_to_cloud(Path(vault_path))
    print("OK: _backup_vault_to_cloud() completed without exception")
except Exception as exc:
    print(f"FAIL: _backup_vault_to_cloud() raised: {exc}")
    sys.exit(1)

# Give the mock cloud a moment
time.sleep(0.5)

# ── STEP 4: Verify cloud received the backup ───────────────────────────────
print("")
print("=== STEP 4: Verify cloud received the backup ===")
resp = urllib.request.urlopen(urllib.request.Request(
    f"{CLOUD}/runtime/self-hosted-nodes/{PROFILE}/vault-backup/claim",
    data=json.dumps({"node_session_token": TOKEN}).encode("utf-8"),
    headers=HEADERS,
    method="POST",
))
claim_data = json.loads(resp.read())
print(f"Claim response: ok={claim_data.get('ok')}, status={claim_data.get('status')}")

if claim_data.get("ok"):
    blob_b64 = claim_data.get("vault_blob_base64", "")
    blob_data = json.loads(base64.b64decode(blob_b64).decode())
    creds = blob_data.get("credentials", [])
    print(f"Cloud has backup: {len(blob_b64)} base64 chars, {len(creds)} credentials")
    for c in creds:
        print(f"  - {c['id']}: {c['label']} -> {c['value']}")

    if any(c["id"] == "worker_test_001" for c in creds):
        print("")
        print("========== WORKER-SIDE BACKUP PROVEN ==========")
        print("system Python → lazy imports → kernel approve → HTTP POST → cloud stored")
        print("Worker can back up vault WITHOUT the cloud venv.")
    else:
        print("")
        print("FAIL: worker_test_001 credential not found in cloud backup")
        sys.exit(1)
else:
    print(f"FAIL: Cloud has no backup: {claim_data}")
    # Check if the backup was saved locally instead
    local_path = "/opt/empyralis/.empyralis/state/cloud-backups/vault_blob.b64"
    if os.path.exists(local_path):
        print(f"Backup went to local fallback: {local_path}")
    sys.exit(1)
