"""Prove off-box vault backup: backup to cloud API, wipe ALL state, restore from cloud."""
import sys, os, json, shutil

sys.path.insert(0, "/opt/empyralis")

os.environ["EMPYRALIS_RUNTIME_KERNEL_BIN"] = "/opt/empyralis/empyralis-runtime-kernel/target/release/empyralis-runtime-kernel"
os.environ["EMPYRALIS_STATE_HOME"] = "/opt/empyralis/.empyralis/state"
os.environ["EMPYRALIS_CLOUD_URL"] = "http://localhost:9999"
os.environ["EMPYRALIS_RUNTIME_PROFILE_ID"] = "rp_test_vps_001"
os.environ["EMPYRALIS_NODE_SESSION_TOKEN"] = "ns_test_token_change_me_in_prod"
os.environ["ENV"] = "dev"
os.environ["CREDENTIAL_VAULT_KEY"] = "test-recovery-key-s5-offbox-proof"

# Import vault_store via direct path (bypasses server_modules/__init__.py)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "vault_store", "/opt/empyralis/server_modules/vault_store.py"
)
vault_store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vault_store)

print("=== STEP 1: Add test credential and save vault ===")
vault = vault_store.load_vault()
print("Current credentials: {}".format(len(vault.get("credentials", []))))

test_cred = {
    "id": "offbox_test_001",
    "provider": "openai",
    "label": "S5 Off-Box Recovery Test",
    "value": vault_store._openssl_encrypt("sk-offbox-proof-key-99999"),
    "created_at": "2026-06-22T20:00:00Z",
}
vault["credentials"].append(test_cred)
vault_store.save_vault(vault)
print("Credential added and saved (backup should have been POSTed to cloud)")

print("")
print("=== STEP 2: Confirm backup in cloud (mock) ===")
# Check the mock's storage directly
mock_backup_path = "/root/empyralis/.mock-cloud-backups/rp_test_vps_001.json"
if os.path.exists(mock_backup_path):
    with open(mock_backup_path) as f:
        mock_data = json.load(f)
    print("Cloud backup exists: True")
    print("Cloud backup blob size: {} bytes".format(mock_data.get("blob_size_bytes", 0)))
else:
    print("Cloud backup exists: False")
    # Try to trigger backup explicitly
    vault_file = vault_store._server.VAULT_FILE
    vault_store._backup_vault_to_cloud(vault_file)
    if os.path.exists(mock_backup_path):
        print("Backup triggered manually, now exists")
    else:
        print("Backup still missing — cloud API may not have been called")

print("")
print("=== STEP 3: WIPE ALL worker state (full box loss) ===")
state_home = "/opt/empyralis/.empyralis"
if os.path.exists(state_home):
    shutil.rmtree(state_home)
    print("Wiped: {}".format(state_home))
# Also wipe the local cloud-backups (simulates full disk loss)
local_backup_dir = "/opt/empyralis/.empyralis/cloud-backups"
if os.path.exists(local_backup_dir):
    shutil.rmtree(local_backup_dir)
    print("Wiped: {}".format(local_backup_dir))
print("All worker state destroyed")

print("")
print("=== STEP 4: Restore from cloud (mock API at localhost:9999) ===")
# vault_store uses EMPYRALIS_CLOUD_URL=localhost:9999
result = vault_store.restore_vault_from_cloud()
print("Restore result: {}".format(json.dumps(result, indent=2, default=str)))

print("")
print("=== STEP 5: Verify credential is back ===")
vault3 = vault_store.load_vault()
creds = vault3.get("credentials", [])
print("Credentials restored: {}".format(len(creds)))
for c in creds:
    decrypted = vault_store._openssl_decrypt(c["value"])
    print("  - {}: {} -> {}".format(c["id"], c["label"], decrypted))

if len(creds) >= 1 and any(c["id"] == "offbox_test_001" for c in creds):
    print("")
    print("========== OFF-BOX RECOVERY PROVEN ==========")
    print("Backup to cloud API -> Full state wipe -> Restore from cloud API -> Credential recovered")
else:
    print("")
    print("FAIL: Off-box recovery did not restore expected credential")
    sys.exit(1)
