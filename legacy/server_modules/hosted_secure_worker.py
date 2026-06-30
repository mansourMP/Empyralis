from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

try:
    import resource
except Exception:  # pragma: no cover - non-posix fallback
    resource = None  # type: ignore[assignment]

from server_modules.agent_manifest import AgentManifest
from server_modules import rust_runtime_kernel_client, universal_operator


def _safe_int(value: Any, default: int) -> int:
    try:
        return max(int(value), 1)
    except Exception:
        return int(default)


def _apply_resource_limits() -> None:
    if resource is None:
        return
    cpu_seconds = _safe_int(os.getenv("EMPYRALIS_SANDBOX_CPU_SECONDS"), 20)
    memory_mb = _safe_int(os.getenv("EMPYRALIS_SANDBOX_MEMORY_MB"), 512)
    max_open_files = _safe_int(os.getenv("EMPYRALIS_SANDBOX_MAX_OPEN_FILES"), 64)
    max_file_bytes = _safe_int(os.getenv("EMPYRALIS_SANDBOX_MAX_FILE_BYTES"), 16 * 1024 * 1024)

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (max_open_files, max_open_files))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
    except Exception:
        pass


def _write_workspace_output(result: Dict[str, Any]) -> None:
    output_path = str(os.getenv("EMPYRALIS_SANDBOX_OUTPUT_FILE") or "").strip()
    if not output_path:
        return
    target = Path(output_path).expanduser().resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass
    _enforce_hosted_worker_output_state_decision(target=target, result=result)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


class HostedWorkerRustGateError(RuntimeError):
    pass


def _enforce_hosted_worker_output_state_decision(*, target: Path, result: Dict[str, Any]) -> None:
    payload = {
        "output_path": str(target),
        "status": str(dict(result or {}).get("status") or "").strip(),
        "keys": sorted(str(key) for key in dict(result or {}).keys()),
        "byte_size": len(json.dumps(result or {}, ensure_ascii=False, default=str).encode("utf-8")),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_hosted_worker_output",
            state_class="hosted_worker_output",
            actor_id="system",
            status=payload["status"] or "completed",
            payload=payload,
            payload_bytes=len(json.dumps(payload, sort_keys=True).encode("utf-8")),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_hosted_worker_output":
            raise HostedWorkerRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HostedWorkerRustGateError(exc.reason) from exc


async def _main_async(payload: Dict[str, Any]) -> Dict[str, Any]:
    manifest = AgentManifest.model_validate(payload.get("manifest") or {})
    return await universal_operator.execute_customer_turn_in_process(
        manifest=manifest,
        tenant_id=str(payload.get("tenant_id") or "").strip(),
        workspace_id=str(payload.get("workspace_id") or "").strip(),
        goal=str(payload.get("goal") or "").strip(),
        runtime_mode="hosted_secure",
        runtime_profile=dict(payload.get("runtime_profile") or {}) if isinstance(payload.get("runtime_profile"), dict) else None,
        privileged_runtime_approved=False,
        runtime_scope=dict(payload.get("runtime_scope") or {}) if isinstance(payload.get("runtime_scope"), dict) else None,
    )


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        sys.stderr.write("Invalid Hosted Secure worker payload.\n")
        return 1
    if not isinstance(payload, dict):
        sys.stderr.write("Invalid Hosted Secure worker payload.\n")
        return 1

    _apply_resource_limits()
    try:
        result = asyncio.run(_main_async(payload))
    except Exception as error:
        sys.stderr.write(f"{error}\n")
        return 1

    if not isinstance(result, dict):
        sys.stderr.write("Hosted Secure worker returned an invalid payload.\n")
        return 1
    _write_workspace_output(result)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
