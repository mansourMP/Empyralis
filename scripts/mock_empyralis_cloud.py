#!/usr/bin/env python3
"""
Minimal Empyralis cloud API mock for testing the self-hosted command worker.

Implements the three endpoints the worker needs:
  - POST /runtime/self-hosted-nodes/{id}/commands/claim
  - POST /runtime/self-hosted-nodes/{id}/commands/{cmd_id}/result
  - POST /runtime/self-hosted-nodes/{id}/register

Pre-loaded with test commands for shell_exec, file_read, file_write.
"""
from __future__ import annotations

import json
import os
import time
import uuid
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Empyralis Mock Cloud")

# ── In-memory state ──────────────────────────────────────────────────────────

PROFILE_ID = "rp_test_vps_001"
NODE_SESSION_TOKEN = "ns_test_token_change_me_in_prod"
NODE_ID = "node_test_vps_001"

# Registered nodes
nodes: Dict[str, Dict[str, Any]] = {}

# Command queue — pre-loaded with 3 test commands
commands: Dict[str, Dict[str, Any]] = {}

def _make_cmd(capability_id: str, **args) -> str:
    cid = f"cmd_{uuid.uuid4().hex[:12]}"
    commands[cid] = {
        "id": cid,
        "command_id": cid,
        "command_payload": {
            "capability_id": capability_id,
            "workspace_id": "default",
            "arguments": args,
        },
        "state": "pending",
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "claimed_at": None,
        "claimed_by": None,
        "result": None,
    }
    return cid

# Pre-load test commands
_make_cmd("shell_exec", command="echo hello-from-empyralis-$(date +%s)", timeout_seconds=30)
_make_cmd("file.read", path="/etc/hostname")
_make_cmd("file.write", path="/tmp/empyralis-test.txt", content="Hello from Empyralis command worker E2E test!\n")

print(f"Pre-loaded {len(commands)} test commands: {list(commands.keys())}", file=sys.stderr)

# ── Pydantic models ──────────────────────────────────────────────────────────

class ClaimPayload(BaseModel):
    node_session_token: str
    max_commands: int = 5
    lease_seconds: int = 120

class ResultPayload(BaseModel):
    node_session_token: str
    status: str
    result_payload: Optional[Dict[str, Any]] = None
    artifacts: List[Any] = []
    error: Optional[str] = None

class RegisterPayload(BaseModel):
    node_session_token: Optional[str] = None
    enrollment_token: Optional[str] = None
    public_key: Optional[str] = None
    node_kind: str = "vps"
    capabilities: List[str] = ["shell_exec", "file_read", "file_write"]
    max_concurrent_sessions: int = 3
    root_policy: Optional[Dict[str, Any]] = None
    display_name: str = "Test VPS Node"


class VaultBackupPutPayload(BaseModel):
    node_session_token: str
    vault_blob_base64: str
    environment: str = "dev"
    backup_version: str = "vault_blob_v1"


class VaultBackupClaimPayload(BaseModel):
    node_session_token: str


# ── Vault backup storage (off-box — survives worker state wipe) ─────────────────

_VAULT_BACKUP_DIR = Path(__file__).parent / ".mock-cloud-backups"

# ── Auth helper ──────────────────────────────────────────────────────────────

def _auth(token: Optional[str], request: Optional[Request] = None) -> None:
    """Validate node session token from body or Authorization header."""
    if token and token == NODE_SESSION_TOKEN:
        return
    # Also check Authorization: Bearer header
    if request is not None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer = auth_header[7:].strip()
            if bearer == NODE_SESSION_TOKEN:
                return
    raise HTTPException(status_code=401, detail="Invalid node session token")

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/runtime/self-hosted-nodes/{profile_id}/register")
async def register_node(profile_id: str, payload: RegisterPayload):
    """Register a self-hosted node."""
    nodes[profile_id] = {
        "runtime_profile_id": profile_id,
        "runtime_node_id": NODE_ID,
        "node_session_token": NODE_SESSION_TOKEN,
        "workspace_id": "default",
        "node_kind": payload.node_kind,
        "capabilities": payload.capabilities,
        "status": "online",
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(f"[mock] Node registered: profile={profile_id} token={NODE_SESSION_TOKEN}", file=sys.stderr)
    return {
        "ok": True,
        "runtime_profile_id": profile_id,
        "runtime_node_id": NODE_ID,
        "workspace_id": "default",
        "node_session_token": NODE_SESSION_TOKEN,
        "owner_approval_required": False,
        "runtime_profile": nodes[profile_id],
    }


@app.post("/runtime/self-hosted-nodes/{profile_id}/commands/claim")
async def claim_commands(profile_id: str, payload: ClaimPayload, request: Request):
    """Claim pending commands for a self-hosted node."""
    _auth(payload.node_session_token, request)

    # Ensure node exists
    if profile_id not in nodes:
        nodes[profile_id] = {
            "runtime_profile_id": profile_id,
            "runtime_node_id": NODE_ID,
            "status": "online",
        }

    pending = [
        c for c in commands.values()
        if c.get("state") == "pending"
    ][:payload.max_commands]

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for c in pending:
        c["state"] = "claimed"
        c["status"] = "claimed"
        c["claimed_at"] = now
        c["claimed_by"] = profile_id

    print(f"[mock] Claim: profile={profile_id} returned={len(pending)} commands", file=sys.stderr)
    return {"commands": pending, "claimed": pending, "count": len(pending)}


@app.post("/runtime/self-hosted-nodes/{profile_id}/commands/{command_id}/result")
async def complete_command(profile_id: str, command_id: str, payload: ResultPayload, request: Request):
    """Accept a command result from a self-hosted node."""
    _auth(payload.node_session_token, request)

    cmd = commands.get(command_id)
    if not cmd:
        raise HTTPException(status_code=404, detail=f"Command not found: {command_id}")

    cmd["state"] = "completed" if payload.status == "completed" else "failed"
    cmd["status"] = cmd["state"]
    cmd["result"] = {
        "status": payload.status,
        "result_payload": payload.result_payload,
        "error": payload.error,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    result_summary = ""
    if payload.result_payload:
        if "stdout" in payload.result_payload:
            result_summary = payload.result_payload["stdout"][:100]
        elif "content" in payload.result_payload:
            result_summary = f"Read {payload.result_payload.get('size_bytes', 0)} bytes from {payload.result_payload.get('path', '?')}"
        elif payload.result_payload.get("path"):
            result_summary = f"Wrote {payload.result_payload.get('size_bytes', 0)} bytes to {payload.result_payload['path']}"

    print(f"[mock] Complete: cmd={command_id} status={payload.status} summary={result_summary}", file=sys.stderr)
    return {"ok": True, "command_id": command_id, "status": cmd["state"]}


@app.get("/mock/commands")
async def list_commands():
    """Debug endpoint: list all commands and their status."""
    return {
        "commands": {
            cid: {"id": c["id"], "state": c.get("state"), "result": c.get("result")}
            for cid, c in commands.items()
        }
    }


@app.post("/mock/reset")
async def reset_commands():
    """Reset all commands to pending for re-testing."""
    for c in commands.values():
        c["state"] = "pending"
        c["status"] = "pending"
        c["claimed_at"] = None
        c["claimed_by"] = None
        c["result"] = None
    return {"ok": True, "message": f"Reset {len(commands)} commands to pending"}


@app.get("/mock/nodes")
async def list_nodes():
    """Debug endpoint: list registered nodes."""
    return {"nodes": nodes, "session_token": NODE_SESSION_TOKEN, "profile_id": PROFILE_ID}


# ── Vault backup endpoints (off-box storage) ────────────────────────────────────


@app.post("/runtime/self-hosted-nodes/{profile_id}/vault-backup")
async def put_vault_backup(profile_id: str, payload: VaultBackupPutPayload, request: Request):
    """Accept an encrypted vault blob from a self-hosted node."""
    _auth(payload.node_session_token, request)
    _VAULT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    blob_path = _VAULT_BACKUP_DIR / f"{profile_id}.json"
    record = {
        "runtime_profile_id": profile_id,
        "vault_blob_base64": payload.vault_blob_base64,
        "environment": payload.environment,
        "backup_version": payload.backup_version,
        "stored_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "blob_size_bytes": len(payload.vault_blob_base64),
    }
    blob_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(blob_path, 0o600)
    except Exception:
        pass
    print(f"[mock] Vault backup stored: profile={profile_id} size={len(payload.vault_blob_base64)}", file=sys.stderr)
    return {
        "ok": True,
        "status": "stored",
        "runtime_profile_id": profile_id,
        "blob_size_bytes": len(payload.vault_blob_base64),
        "backup_version": payload.backup_version,
    }


@app.post("/runtime/self-hosted-nodes/{profile_id}/vault-backup/claim")
async def claim_vault_backup(profile_id: str, payload: VaultBackupClaimPayload, request: Request):
    """Return the encrypted vault blob for disaster recovery."""
    _auth(payload.node_session_token, request)
    blob_path = _VAULT_BACKUP_DIR / f"{profile_id}.json"
    if not blob_path.exists():
        return {"ok": False, "status": "not_found", "runtime_profile_id": profile_id}
    try:
        record = json.loads(blob_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "status": "read_error", "runtime_profile_id": profile_id, "error": str(exc)}
    print(f"[mock] Vault backup claimed: profile={profile_id}", file=sys.stderr)
    return {
        "ok": True,
        "status": "found",
        "runtime_profile_id": profile_id,
        "vault_blob_base64": str(record.get("vault_blob_base64") or ""),
        "environment": str(record.get("environment") or ""),
        "backup_version": str(record.get("backup_version") or ""),
        "stored_at_iso": str(record.get("stored_at_iso") or ""),
        "blob_size_bytes": int(record.get("blob_size_bytes") or 0),
    }


if __name__ == "__main__":
    print(f"[mock] Starting on 0.0.0.0:9999 — profile={PROFILE_ID} token={NODE_SESSION_TOKEN}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=9999, log_level="info")
