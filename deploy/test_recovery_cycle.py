"""Prove the full vault backup -> wipe -> restore cycle on the VPS."""
import sys, os, json, shutil

sys.path.insert(0, "/opt/empyralis")

os.environ["EMPYRALIS_RUNTIME_KERNEL_BIN"] = "/opt/empyralis/empyralis-runtime-kernel/target/release/empyralis-runtime-kernel"
os.environ["EMPYRALIS_STATE_HOME"] = "/opt/empyralis/.empyralis/state"
os.environ["ENV"] = "dev"
os.environ["CREDENTIAL_VAULT_KEY"] = "test-recovery-key-s4-proof-2026"

from server_modules import vault_store

# Force re-init after env setup
vault_store._server = None

print("=== STEP 1: Add a test credential ===")
vault = vault_store.load_vault()
print("Current credentials: {}".format(len(vault.get("credentials", []))))

test_cred = {
    "id": "recovery_test_001",
    "provider": "openai",
    "label": "S4 Recovery Test Key",
    "value": vault_store._openssl_encrypt("sk-test-recovery-proof-12345"),
    "created_at": "2026-06-22T00:00:00Z",
}
vault["credentials"].append(test_cred)
vault_store.save_vault(vault)
print("Credential added and vault saved")

vault2 = vault_store.load_vault()
print("Credentials after add: {}".format(len(vault2.get("credentials", []))))

print("")
print("=== STEP 2: Confirm backup to cloud ===")
vault_file = vault_store._server.VAULT_FILE
vault_store._backup_vault_to_cloud(vault_file)
blob_path = vault_store._cloud_backup_vault_blob_path()
print("Cloud backup path: {}".format(blob_path))
print("Cloud backup exists: {}".format(blob_path.exists()))
if blob_path.exists():
    blob_data = json.loads(blob_path.read_text())
    has_blob = bool(blob_data.get("vault_blob_base64"))
    print("Cloud backup has vault_blob_base64: {}".format(has_blob))

print("")
print("=== STEP 3: Wipe vault (simulate box loss) ===")
vault_dir = vault_file.parent
print("Vault file before wipe: {} ({})".format(vault_file.exists(), vault_file))
if vault_dir.exists():
    shutil.rmtree(vault_dir)
print("Vault file after wipe: {}".format(vault_file.exists()))

print("")
print("=== STEP 4: Restore from cloud ===")
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

if len(creds) == 1 and creds[0]["id"] == "recovery_test_001":
    print("")
    print("========== RECOVERY CYCLE PROVEN ==========")
    print("Add -> Backup -> Wipe -> Restore -> Recovered")
else:
    print("")
    print("FAIL: Recovery did not restore the expected credential")
    sys.exit(1)
