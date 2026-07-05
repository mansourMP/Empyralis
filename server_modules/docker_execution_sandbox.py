from __future__ import annotations

# ---------------------------------------------------------------------------
# Portions adapted from hermes-agent (MIT, Copyright (c) 2025 Nous Research):
# the hardened-container security posture below (cap-drop ALL + minimal
# re-adds, no-new-privileges, pids-limit, nosuid/noexec size-capped tmpfs,
# non-root --user, --init) is derived from hermes-agent
# tools/environments/docker.py `_BASE_SECURITY_ARGS` / `_PRIVDROP_CAP_ARGS`.
# Full license text: THIRD_PARTY_LICENSES at the repo root.
# ---------------------------------------------------------------------------

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

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


# ── Hermes-derived hardened container posture (Phase 3A) ────────────────────
# Adapted from hermes-agent tools/environments/docker.py (MIT, Nous Research).
# `--cap-drop ALL` then re-add only what a sandboxed build needs; block
# privilege escalation; cap PIDs (fork-bomb); mount writable dirs as size-
# limited nosuid (and noexec where exec isn't needed) tmpfs.
_HERMES_BASE_SECURITY_ARGS: List[str] = [
    "--cap-drop", "ALL",
    "--cap-add", "DAC_OVERRIDE",
    "--cap-add", "CHOWN",
    "--cap-add", "FOWNER",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "256",
    "--tmpfs", "/tmp:rw,nosuid,size=512m",
    "--tmpfs", "/var/tmp:rw,noexec,nosuid,size=256m",
    "--tmpfs", "/run:rw,noexec,nosuid,size=64m",
]
# Extra caps only needed when the container starts as root and an entrypoint
# must drop privileges (s6/gosu/su). Skipped when we pass --user, since the
# container already starts unprivileged and never switches.
_HERMES_PRIVDROP_CAP_ARGS: List[str] = ["--cap-add", "SETUID", "--cap-add", "SETGID"]


def _hardened_security_args(*, run_as_user: bool) -> List[str]:
    """Return cap/security/pids/tmpfs args tailored to the privilege mode."""
    args = list(_HERMES_BASE_SECURITY_ARGS)
    if not run_as_user:
        args += _HERMES_PRIVDROP_CAP_ARGS
    return args


def _flag_present(command: List[str], flag: str) -> bool:
    return flag in command


def hardened_run_flags(
    *,
    network_enabled: bool,
    run_as_uid: Optional[int],
    run_as_gid: Optional[int],
    memory_mb: int,
    cpus: float,
) -> List[str]:
    """The full Hermes-derived flag set for a hardened ``docker run``.

    network is ``none`` unless ``network_enabled`` opts into egress; memory is
    hard-capped with swap pinned to the same size (no swap escape); a non-root
    ``--user`` and ``--init`` (zombie reaping) are added when available.
    """
    flags: List[str] = ["--init"]
    flags += ["--network", "bridge"] if network_enabled else ["--network", "none"]
    if run_as_uid is not None and run_as_gid is not None:
        flags += ["--user", f"{int(run_as_uid)}:{int(run_as_gid)}"]
    mb = max(32, int(memory_mb))
    flags += ["--memory", f"{mb}m", "--memory-swap", f"{mb}m"]
    flags += ["--cpus", f"{max(0.25, float(cpus)):.2f}"]
    flags += _hardened_security_args(run_as_user=run_as_uid is not None)
    return flags


def inject_hardening(
    command: List[str],
    *,
    network_enabled: bool,
    run_as_uid: Optional[int],
    run_as_gid: Optional[int],
    memory_mb: int,
    cpus: float,
) -> List[str]:
    """Idempotently insert the hardened flag set into a ``docker run`` argv,
    right after the ``run`` subcommand and before the image/positional args.
    Flags already present in the base command are not duplicated, so this is
    safe to layer on top of a kernel-built command."""
    if len(command) < 2 or command[1] != "run":
        # Not a `docker run` argv we recognise — return unchanged rather than
        # risk corrupting it.
        return list(command)
    desired = hardened_run_flags(
        network_enabled=network_enabled,
        run_as_uid=run_as_uid,
        run_as_gid=run_as_gid,
        memory_mb=memory_mb,
        cpus=cpus,
    )
    # Drop any (flag, value) pair whose flag already appears in the base
    # command so we never contradict a stricter kernel-set value.
    additions: List[str] = []
    i = 0
    while i < len(desired):
        token = desired[i]
        if token.startswith("--"):
            takes_value = (i + 1 < len(desired)) and not desired[i + 1].startswith("--")
            if _flag_present(command, token):
                i += 2 if takes_value else 1
                continue
            additions.append(token)
            if takes_value:
                additions.append(desired[i + 1])
                i += 2
            else:
                i += 1
        else:
            additions.append(token)
            i += 1
    return [command[0], command[1], *additions, *command[2:]]


def build_hardened_docker_command(
    *,
    image: str,
    inner_args: List[str],
    sandbox_root: Optional[str] = None,
    network_enabled: bool = False,
    run_as_uid: Optional[int] = None,
    run_as_gid: Optional[int] = None,
    memory_mb: int = 512,
    cpus: float = 1.0,
    read_only: bool = True,
) -> List[str]:
    """Self-contained hardened ``docker run`` argv (used as a fallback when the
    Rust kernel command builder is unavailable, and by the Phase 3A verifier).
    """
    cmd: List[str] = ["docker", "run", "--rm"]
    if read_only:
        cmd.append("--read-only")
    cmd += hardened_run_flags(
        network_enabled=network_enabled,
        run_as_uid=run_as_uid,
        run_as_gid=run_as_gid,
        memory_mb=memory_mb,
        cpus=cpus,
    )
    if sandbox_root:
        cmd += ["-v", f"{sandbox_root}:{DOCKER_WORKSPACE}:rw", "-w", DOCKER_WORKSPACE]
    cmd.append(image)
    cmd += list(inner_args)
    return cmd


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
    # Phase 3A: layer the Hermes-derived hardening over the kernel-built
    # command (defense in depth — only adds stricter flags, never loosens;
    # deduplicates against anything the kernel already set).
    cpus = max(0.25, round(max(2, int(cpu_shares)) / 1024.0, 2))
    return inject_hardening(
        list(command),
        network_enabled=bool(network_enabled),
        run_as_uid=uid,
        run_as_gid=gid,
        memory_mb=max(32, int(memory_mb)),
        cpus=cpus,
    )


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
