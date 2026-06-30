from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from server_modules import rust_runtime_kernel_client


DOCKER_DRIVER = "docker"
DEFAULT_DOCKER_IMAGE = "empyralis-sandbox:latest"
DOCKER_HOME = "/home/sandbox"
DOCKER_WORKSPACE = "/workspace"
DOCKER_OUTPUT_PATH = "/workspace/outputs/turn-result.json"

_DOCKER_AVAILABLE: Optional[bool] = None


def _resolve_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _is_docker_available() -> bool:
    global _DOCKER_AVAILABLE
    if _DOCKER_AVAILABLE is not None:
        return _DOCKER_AVAILABLE
    if shutil.which("docker"):
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=5,
            )
            _DOCKER_AVAILABLE = result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            _DOCKER_AVAILABLE = False
    else:
        _DOCKER_AVAILABLE = False
    return _DOCKER_AVAILABLE


def docker_driver_available() -> bool:
    return _is_docker_available()


def docker_sandbox_command(
    *,
    sandbox_root: str,
    image: str = DEFAULT_DOCKER_IMAGE,
    memory_mb: int = 512,
    cpu_shares: int = 1024,
    timeout_seconds: int = 25,
    network_enabled: bool = False,
    read_only: bool = True,
) -> list[str]:
    uid = os.getuid() if sys.platform != "win32" else 1000
    gid = os.getgid() if sys.platform != "win32" else 1000
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "build-sandbox-command",
        {
            "sandbox_root": sandbox_root,
            "image": image,
            "memory_mb": max(32, int(memory_mb)),
            "cpu_shares": max(2, int(cpu_shares)),
            "timeout_seconds": max(1, int(timeout_seconds)),
            "network_enabled": bool(network_enabled),
            "read_only": bool(read_only),
            "uid": uid,
            "gid": gid,
            "worker_module": "server_modules.hosted_secure_worker",
            "output_file": DOCKER_OUTPUT_PATH,
        },
    )
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "build_hardened_container_command":
        raise RuntimeError("unexpected_next_action")
    command = decision.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise RuntimeError("Rust sandbox command builder returned an invalid command.")
    return list(command)


def run_docker_worker(
    *,
    sandbox_root: str,
    payload: Dict[str, Any],
    image: str = DEFAULT_DOCKER_IMAGE,
    memory_mb: int = 512,
    cpu_shares: int = 1024,
    timeout_seconds: int = 25,
    network_enabled: bool = False,
) -> subprocess.CompletedProcess:
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    command = docker_sandbox_command(
        sandbox_root=sandbox_root,
        image=image,
        memory_mb=memory_mb,
        cpu_shares=cpu_shares,
        timeout_seconds=timeout_seconds,
        network_enabled=network_enabled,
    )
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "sandbox-execution-decision",
        {
            "operation": "launch_worker",
            "runtime_mode": "hosted_secure",
            "driver": DOCKER_DRIVER,
            "sandbox_root": sandbox_root,
            "image": image,
            "memory_mb": max(32, int(memory_mb)),
            "cpu_shares": max(2, int(cpu_shares)),
            "timeout_seconds": max(1, int(timeout_seconds)),
            "network_enabled": bool(network_enabled),
            "read_only": True,
            "host_mounts_allowed": False,
            "docker_socket_exposed": False,
            "privileged": False,
            "cap_drop_all": True,
            "no_new_privileges": True,
            "approval_provided": False,
            "docker_args": command,
            "output_file": DOCKER_OUTPUT_PATH,
        },
    )
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "launch_hosted_worker":
        raise RuntimeError("unexpected_next_action")
    return subprocess.run(
        command,
        input=payload_json,
        text=True,
        capture_output=True,
        timeout=max(1, int(timeout_seconds) + 5),
        check=False,
    )


def build_docker_image_if_needed(image: str = DEFAULT_DOCKER_IMAGE) -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass
    project_root = _resolve_project_root()
    dockerfile = project_root / "Dockerfile.sandbox"
    if not dockerfile.exists():
        return False
    try:
        result = subprocess.run(
            ["docker", "build", "-t", image, "-f", str(dockerfile), str(project_root)],
            capture_output=True, text=True, timeout=120,
        )
        return result.returncode == 0
    except Exception:
        return False


def docker_sandbox_result(
    completed: subprocess.CompletedProcess,
    *,
    sandbox_root: str,
    image: str = DEFAULT_DOCKER_IMAGE,
) -> Dict[str, Any]:
    def _classify_outcome(parsed_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        artifacts = []
        if isinstance(parsed_result, dict):
            raw_artifacts = parsed_result.get("artifacts")
            if isinstance(raw_artifacts, list):
                artifacts = raw_artifacts
        return rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "execution-outcome",
            {
                "exit_code": int(getattr(completed, "returncode", 0)),
                "stdout": str(getattr(completed, "stdout", "") or ""),
                "stderr": str(getattr(completed, "stderr", "") or ""),
                "stdout_bytes": len(str(getattr(completed, "stdout", "") or "").encode("utf-8")),
                "stderr_bytes": len(str(getattr(completed, "stderr", "") or "").encode("utf-8")),
                "artifacts": artifacts,
                "max_preview_bytes": 2_000,
            },
        )

    output_path = Path(sandbox_root) / "outputs" / "turn-result.json"
    if completed.returncode != 0:
        _classify_outcome()
        detail = (completed.stderr or completed.stdout or "Docker sandbox worker failed.").strip()
        raise RuntimeError(detail)
    if output_path.exists():
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result_str = str(completed.stdout or "{}")
            try:
                result = json.loads(result_str)
            except json.JSONDecodeError as error:
                raise RuntimeError("Docker sandbox worker returned invalid JSON.") from error
    else:
        result_str = str(completed.stdout or "{}")
        try:
            result = json.loads(result_str)
        except json.JSONDecodeError as error:
            raise RuntimeError("Docker sandbox worker returned invalid JSON.") from error
    if not isinstance(result, dict):
        raise RuntimeError("Docker sandbox worker returned an invalid payload.")
    result["execution_outcome"] = _classify_outcome(result)
    result["sandbox"] = {
        "mode": "docker",
        "driver": DOCKER_DRIVER,
        "workspace_kind": "ephemeral_container",
        "read_only_base_image": True,
        "base_image_id": str(image or DEFAULT_DOCKER_IMAGE),
        "host_mounts_allowed": False,
        "docker_socket_exposed": False,
        "network_policy": {"mode": "none" if not bool(result.get("network_enabled")) else "allow"},
        "limits": {},
    }
    return result
