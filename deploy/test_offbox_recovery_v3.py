"""Prove off-box vault backup via cloud API — zero vault_store imports, pure HTTP."""
import json, os, sys, base64, shutil, urllib.request

CLOUD = "http://localhost:9999"
TOKEN = "ns_test_token_change_me_in_prod"
PROFILE = "rp_test_vps_001"
HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}

def api_post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{CLOUD}{path}", data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

print("=== STEP 1: Create test vault blob ===")
vault_json = json.dumps({
    "version": 1,
    "credentials": [
        {"id": "offbox_test_001", "provider": "openai",
         "label": "S5 Off-Box Recovery Test",
         "value": "sk-offbox-proof-key-99999",
         "created_at": "2026-06-22T20:00:00Z"}
    ]
})
vault_blob_b64 = base64.b64encode(vault_json.encode()).decode()
print(f"Vault blob: {len(vault_blob_b64)} base64 chars")
print(f"Credential: offbox_test_001 -> sk-offbox-proof-key-99999")

print("")
print("=== STEP 2: POST vault blob to cloud API ===")
resp = api_post(f"/runtime/self-hosted-nodes/{PROFILE}/vault-backup", {
    "node_session_token": TOKEN,
    "vault_blob_base64": vault_blob_b64,
    "environment": "dev",
    "backup_version": "vault_blob_v1",
})
print(f"Response: ok={resp.get('ok')}, status={resp.get('status')}, size={resp.get('blob_size_bytes')}")

print("")
print("=== STEP 3: Confirm cloud persistence ===")
mock_file = f"/root/empyralis/scripts/.mock-cloud-backups/{PROFILE}.json"
with open(mock_file) as f:
    stored = json.load(f)
print(f"Stored at: {mock_file}")
print(f"Blob size: {stored.get('blob_size_bytes')} bytes")
print(f"Timestamp: {stored.get('stored_at_iso')}")
assert stored.get("ok") is None  # raw record, not response wrapper

print("")
print("=== STEP 4: WIPE worker state (full box loss simulation) ===")
for path in ["/opt/empyralis/.empyralis", "/opt/empyralis/.empyralis/state/cloud-backups"]:
    if os.path.exists(path):
        shutil.rmtree(path)
        print(f"Wiped: {path}")
print("ALL worker state destroyed")

print("")
print("=== STEP 5: Claim vault blob from cloud API ===")
resp2 = api_post(f"/runtime/self-hosted-nodes/{PROFILE}/vault-backup/claim", {
    "node_session_token": TOKEN,
})
print(f"Response: ok={resp2.get('ok')}, status={resp2.get('status')}")
assert resp2.get("ok"), f"Claim failed: {resp2}"

restored_b64 = resp2.get("vault_blob_base64", "")
restored_json = base64.b64decode(restored_b64).decode()
restored_data = json.loads(restored_json)
creds = restored_data.get("credentials", [])
print(f"Restored blob: {len(restored_b64)} base64 chars")
print(f"Restored credentials: {len(creds)}")
for c in creds:
    print(f"  - {c['id']}: {c['label']} -> {c['value']}")

assert any(c["id"] == "offbox_test_001" for c in creds), "Credential NOT recovered!"
assert creds[0]["value"] == "sk-offbox-proof-key-99999", "Credential value mismatch!"

print("")
print("========== OFF-BOX RECOVERY PROVEN ==========")
print("HTTP POST -> cloud stores blob -> full state wipe -> HTTP GET -> blob recovered -> credential intact")
print("The cloud (mock on port 9999) is the source of truth. Worker holds nothing persistent.")
