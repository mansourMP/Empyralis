from __future__ import annotations

import asyncio
import json
import os
import shutil
import site
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from server_modules.agent_manifest import AgentManifest
from server_modules.docker_execution_sandbox import (
    DOCKER_DRIVER,
    DEFAULT_DOCKER_IMAGE,
    _is_docker_available,
    run_docker_worker,
    docker_sandbox_result,
)
from server_modules import rust_runtime_kernel_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOSTED_SECURE_BASE_IMAGE = "empyralis-hosted-secure-v1"
HOSTED_SECURE_DRIVER_SANDBOX_EXEC = "sandbox_exec"
HOSTED_SECURE_DRIVER_SUBPROCESS = "subprocess_fallback"
HOSTED_SECURE_DRIVER_DOCKER = DOCKER_DRIVER
DEFAULT_HOSTED_LIMITS = {
    "cpu_seconds": 20,
    "memory_mb": 512,
    "timeout_seconds": 25,
    "max_open_files": 64,
    "max_file_bytes": 16 * 1024 * 1024,
}
DEFAULT_NETWORK_POLICY = {
    "mode": "allowlist_hooks",
    "allow_outbound": True,
    "allow_private_hosts": False,
    "allowed_hosts": [],
    "allowed_providers": [
        "openai",
        "anthropic",
        "google",
        "groq",
        "openrouter",
        "xai",
        "azure_openai",
        "deepseek",
    ],
    "hooks_ready": True,
}
_HOST_RUNTIME_LIBRARY_ROOTS = (
    Path("/opt/homebrew/Cellar"),
    Path("/opt/homebrew/opt"),
    Path("/opt/homebrew/lib"),
    Path("/usr/lib"),
    Path("/System/Library"),
    Path("/private/etc"),
)
_MINIMAL_ENV_ALLOWLIST = (
    "DATABASE_URL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
)
_SCRUBBED_ENV_PREFIXES = (
    "AWS_",
    "AZURE_",
    "DOCKER_",
    "GCP_",
    "GOOGLE_",
    "KUBECONFIG",
    "OPENAI_",
    "SSH_",
)
STATE_LAYER_IDS = (
    "captain_private_memory",
    "specialist_private_memory",
    "shared_operational_board",
    "artifacts_history",
)


class HostedSecureSandboxError(RuntimeError):
    pass


_SANDBOX_EXECUTION_NEXT_ACTIONS = {
    "prepare_sandbox": {"prepare_isolated_workspace"},
    "launch_worker": {"launch_hosted_worker"},
    "sandbox_profile": {"build_restricted_sandbox_profile"},
    "environment": {"scrub_environment"},
    "cleanup_policy": {"cleanup_ephemeral_workspace"},
}


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _require_sandbox_next_action(decision: Dict[str, Any], *, operation: str) -> Dict[str, Any]:
    allowed_next_actions = _SANDBOX_EXECUTION_NEXT_ACTIONS.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if allowed_next_actions is None:
        raise HostedSecureSandboxError(f"unexpected sandbox operation: {operation}")
    if next_action not in allowed_next_actions:
        expected = ", ".join(sorted(allowed_next_actions))
        raise HostedSecureSandboxError(
            f"unexpected next_action for sandbox-execution-decision: {next_action or '<missing>'} "
            f"(expected {expected})"
        )
    return decision


def _enforce_hosted_sandbox_preparation(*, sandbox_root: Path, scope: Dict[str, Any]) -> Dict[str, Any]:
    limits = _coerce_dict(scope.get("limits"))
    network_policy = _coerce_dict(scope.get("network_policy"))
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "sandbox-execution-decision",
            {
                "operation": "prepare_sandbox",
                "runtime_mode": str(scope.get("mode") or "hosted_secure"),
                "driver": str(scope.get("driver") or ""),
                "sandbox_root": str(sandbox_root),
                "image": str(scope.get("base_image_id") or DEFAULT_DOCKER_IMAGE),
                "image_approved": True,
                "base_image_approved": True,
                "network_policy": network_policy,
                "read_only": bool(scope.get("read_only_base_image", True)),
                "host_mounts_allowed": bool(scope.get("host_mounts_allowed")),
                "docker_socket_exposed": bool(scope.get("docker_socket_exposed")),
                "privileged": False,
                "cap_drop_all": True,
                "no_new_privileges": True,
                "approval_provided": False,
                "timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "max_timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "cpu_seconds": int(limits.get("cpu_seconds") or DEFAULT_HOSTED_LIMITS["cpu_seconds"]),
                "memory_mb": int(limits.get("memory_mb") or DEFAULT_HOSTED_LIMITS["memory_mb"]),
                "max_open_files": int(limits.get("max_open_files") or DEFAULT_HOSTED_LIMITS["max_open_files"]),
                "max_file_bytes": int(limits.get("max_file_bytes") or DEFAULT_HOSTED_LIMITS["max_file_bytes"]),
                "output_file": str(sandbox_root / "workspace" / "outputs" / "turn-result.json"),
            },
        )
        return _require_sandbox_next_action(decision, operation="prepare_sandbox")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HostedSecureSandboxError(
            f"Rust sandbox preparation blocked hosted worker: {exc.reason}"
        ) from exc


def _enforce_hosted_worker_launch(
    *,
    sandbox_root: Path,
    scope: Dict[str, Any],
    output_file: Path,
) -> Dict[str, Any]:
    limits = _coerce_dict(scope.get("limits"))
    network_policy = _coerce_dict(scope.get("network_policy"))
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "sandbox-execution-decision",
            {
                "operation": "launch_worker",
                "runtime_mode": str(scope.get("mode") or "hosted_secure"),
                "driver": str(scope.get("driver") or ""),
                "sandbox_root": str(sandbox_root),
                "image": str(scope.get("base_image_id") or DEFAULT_DOCKER_IMAGE),
                "image_approved": True,
                "base_image_approved": True,
                "network_policy": network_policy,
                "read_only": bool(scope.get("read_only_base_image", True)),
                "host_mounts_allowed": bool(scope.get("host_mounts_allowed")),
                "docker_socket_exposed": bool(scope.get("docker_socket_exposed")),
                "privileged": False,
                "cap_drop_all": True,
                "no_new_privileges": True,
                "approval_provided": False,
                "timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "max_timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "cpu_seconds": int(limits.get("cpu_seconds") or DEFAULT_HOSTED_LIMITS["cpu_seconds"]),
                "memory_mb": int(limits.get("memory_mb") or DEFAULT_HOSTED_LIMITS["memory_mb"]),
                "max_open_files": int(limits.get("max_open_files") or DEFAULT_HOSTED_LIMITS["max_open_files"]),
                "max_file_bytes": int(limits.get("max_file_bytes") or DEFAULT_HOSTED_LIMITS["max_file_bytes"]),
                "output_file": str(output_file),
            },
        )
        return _require_sandbox_next_action(decision, operation="launch_worker")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HostedSecureSandboxError(
            f"Rust sandbox launch blocked hosted worker: {exc.reason}"
        ) from exc


def _enforce_hosted_sandbox_profile_policy(
    *,
    sandbox_root: Path,
    scope: Dict[str, Any],
) -> Dict[str, Any]:
    limits = _coerce_dict(scope.get("limits"))
    network_policy = _coerce_dict(scope.get("network_policy"))
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "sandbox-execution-decision",
            {
                "operation": "sandbox_profile",
                "runtime_mode": str(scope.get("mode") or "hosted_secure"),
                "driver": str(scope.get("driver") or ""),
                "sandbox_root": str(sandbox_root),
                "image": str(scope.get("base_image_id") or DEFAULT_DOCKER_IMAGE),
                "image_approved": True,
                "base_image_approved": True,
                "network_policy": network_policy,
                "read_only": bool(scope.get("read_only_base_image", True)),
                "host_mounts_allowed": bool(scope.get("host_mounts_allowed")),
                "docker_socket_exposed": bool(scope.get("docker_socket_exposed")),
                "privileged": False,
                "cap_drop_all": True,
                "no_new_privileges": True,
                "approval_provided": False,
                "timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "max_timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "cpu_seconds": int(limits.get("cpu_seconds") or DEFAULT_HOSTED_LIMITS["cpu_seconds"]),
                "memory_mb": int(limits.get("memory_mb") or DEFAULT_HOSTED_LIMITS["memory_mb"]),
                "max_open_files": int(limits.get("max_open_files") or DEFAULT_HOSTED_LIMITS["max_open_files"]),
                "max_file_bytes": int(limits.get("max_file_bytes") or DEFAULT_HOSTED_LIMITS["max_file_bytes"]),
            },
        )
        return _require_sandbox_next_action(decision, operation="sandbox_profile")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HostedSecureSandboxError(
            f"Rust sandbox profile policy blocked hosted worker: {exc.reason}"
        ) from exc


def _enforce_hosted_sandbox_environment_policy(
    *,
    sandbox_root: Path,
    output_path: Path,
    scope: Dict[str, Any],
    env: Dict[str, str],
) -> Dict[str, Any]:
    limits = _coerce_dict(scope.get("limits"))
    network_policy = _coerce_dict(scope.get("network_policy"))
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "sandbox-execution-decision",
            {
                "operation": "environment",
                "runtime_mode": str(scope.get("mode") or "hosted_secure"),
                "driver": str(scope.get("driver") or ""),
                "sandbox_root": str(sandbox_root),
                "image": str(scope.get("base_image_id") or DEFAULT_DOCKER_IMAGE),
                "image_approved": True,
                "base_image_approved": True,
                "network_policy": network_policy,
                "read_only": bool(scope.get("read_only_base_image", True)),
                "host_mounts_allowed": bool(scope.get("host_mounts_allowed")),
                "docker_socket_exposed": bool(scope.get("docker_socket_exposed")),
                "privileged": False,
                "cap_drop_all": True,
                "no_new_privileges": True,
                "approval_provided": False,
                "timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "max_timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "cpu_seconds": int(limits.get("cpu_seconds") or DEFAULT_HOSTED_LIMITS["cpu_seconds"]),
                "memory_mb": int(limits.get("memory_mb") or DEFAULT_HOSTED_LIMITS["memory_mb"]),
                "max_open_files": int(limits.get("max_open_files") or DEFAULT_HOSTED_LIMITS["max_open_files"]),
                "max_file_bytes": int(limits.get("max_file_bytes") or DEFAULT_HOSTED_LIMITS["max_file_bytes"]),
                "output_file": str(output_path),
                "env_names": sorted(env.keys()),
            },
        )
        decision = _require_sandbox_next_action(decision, operation="environment")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HostedSecureSandboxError(
            f"Rust sandbox environment policy blocked hosted worker: {exc.reason}"
        ) from exc

    env_policy = _coerce_dict(decision.get("env_policy"))
    for name in _list_strings(env_policy.get("scrubbed_env_names")):
        env.pop(name, None)
    return decision


def _enforce_hosted_sandbox_cleanup_policy(
    *,
    sandbox_root: Path,
    scope: Dict[str, Any],
) -> Dict[str, Any]:
    limits = _coerce_dict(scope.get("limits"))
    network_policy = _coerce_dict(scope.get("network_policy"))
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "sandbox-execution-decision",
            {
                "operation": "cleanup_policy",
                "runtime_mode": str(scope.get("mode") or "hosted_secure"),
                "driver": str(scope.get("driver") or ""),
                "sandbox_root": str(sandbox_root),
                "image": str(scope.get("base_image_id") or DEFAULT_DOCKER_IMAGE),
                "image_approved": True,
                "base_image_approved": True,
                "network_policy": network_policy,
                "read_only": bool(scope.get("read_only_base_image", True)),
                "host_mounts_allowed": bool(scope.get("host_mounts_allowed")),
                "docker_socket_exposed": bool(scope.get("docker_socket_exposed")),
                "privileged": False,
                "cap_drop_all": True,
                "no_new_privileges": True,
                "approval_provided": False,
                "timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "max_timeout_seconds": int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]),
                "cpu_seconds": int(limits.get("cpu_seconds") or DEFAULT_HOSTED_LIMITS["cpu_seconds"]),
                "memory_mb": int(limits.get("memory_mb") or DEFAULT_HOSTED_LIMITS["memory_mb"]),
                "max_open_files": int(limits.get("max_open_files") or DEFAULT_HOSTED_LIMITS["max_open_files"]),
                "max_file_bytes": int(limits.get("max_file_bytes") or DEFAULT_HOSTED_LIMITS["max_file_bytes"]),
            },
        )
        return _require_sandbox_next_action(decision, operation="cleanup_policy")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HostedSecureSandboxError(
            f"Rust sandbox cleanup policy blocked hosted worker: {exc.reason}"
        ) from exc


def _classify_hosted_worker_outcome(
    completed: subprocess.CompletedProcess,
    *,
    parsed_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    artifacts: list[Any] = []
    if isinstance(parsed_result, dict) and isinstance(parsed_result.get("artifacts"), list):
        artifacts = list(parsed_result.get("artifacts") or [])
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


def _enforce_hosted_base_image_state_decision(
    *,
    image_root: Path,
    payload: Dict[str, Any],
    scope: Dict[str, Any],
) -> Dict[str, Any]:
    decision_payload = {
        "image_root": str(image_root),
        "manifest_present": isinstance(payload.get("manifest"), dict),
        "base_image_id": str(scope.get("base_image_id") or HOSTED_SECURE_BASE_IMAGE),
        "driver": str(scope.get("driver") or ""),
        "read_only": bool(scope.get("read_only_base_image", True)),
        "network_policy": _coerce_dict(scope.get("network_policy")),
        "limits": _coerce_dict(scope.get("limits")),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_hosted_sandbox_base_image",
            state_class="execution_sandbox_image",
            actor_id="system",
            status="active",
            payload=decision_payload,
            payload_bytes=len(json.dumps(decision_payload, sort_keys=True, default=str).encode("utf-8")),
            workspace_access=True,
            owner_access=True,
        )
        enforced = rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(enforced.get("next_action") or "").strip()
        if next_action != "write_hosted_sandbox_base_image":
            raise HostedSecureSandboxError(
                "Rust hosted base-image write returned unexpected next_action: "
                f"{next_action or 'missing'}"
            )
        return enforced
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise HostedSecureSandboxError(
            f"Rust hosted base-image write blocked: {exc.reason}"
        ) from exc


def _list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value:
        token = str(raw or "").strip()
        if token:
            items.append(token)
    return items


def state_layer_policy(*, runtime_mode: str) -> Dict[str, Any]:
    normalized_mode = str(runtime_mode or "").strip().lower() or "hosted_secure"
    local_private_access = "cloud_safe_summaries_only" if normalized_mode == "hosted_secure" else "allowed_locally"
    return {
        "layer_ids": list(STATE_LAYER_IDS),
        "captain_private_memory": {
            "default_access": "captain_only",
            "specialists_allowed": False,
        },
        "specialist_private_memory": {
            "default_access": "owning_install_only",
            "cross_install_allowed": False,
            "captain_raw_access_by_default": False,
        },
        "shared_operational_board": {
            "default_access": "permissioned_shared_read",
            "write_requires_permission_tier": True,
        },
        "artifacts_history": {
            "default_access": "explicit_artifact_exchange",
            "cross_install_exchange_mode": "artifacts_only",
            "raw_private_memory_embeds_allowed": False,
        },
        "local_private_memory_access": local_private_access,
        "cross_install_private_memory_allowed": False,
        "specialist_to_captain_private_access": False,
    }


def hosted_secure_driver(*, prefer: str = "") -> str:
    requested = str(prefer or "").strip().lower()
    if requested == DOCKER_DRIVER and _is_docker_available():
        return DOCKER_DRIVER
    if requested == HOSTED_SECURE_DRIVER_SANDBOX_EXEC and shutil.which("sandbox-exec"):
        return HOSTED_SECURE_DRIVER_SANDBOX_EXEC
    if requested == HOSTED_SECURE_DRIVER_SUBPROCESS:
        return HOSTED_SECURE_DRIVER_SUBPROCESS
    if requested and requested not in {DOCKER_DRIVER, HOSTED_SECURE_DRIVER_SANDBOX_EXEC, HOSTED_SECURE_DRIVER_SUBPROCESS}:
        raise ValueError(f"Unknown sandbox driver: {requested}")
    if not requested and _is_docker_available():
        return DOCKER_DRIVER
    if shutil.which("sandbox-exec"):
        return HOSTED_SECURE_DRIVER_SANDBOX_EXEC
    return HOSTED_SECURE_DRIVER_SUBPROCESS


def runtime_scope(
    *,
    runtime_mode: str,
    runtime_profile: Dict[str, Any] | None = None,
    install: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized_mode = str(runtime_mode or "").strip().lower() or "hosted_secure"
    profile = _coerce_dict(runtime_profile)
    profile_metadata = _coerce_dict(profile.get("metadata"))
    install_payload = _coerce_dict(install)

    root_folder_uri = (
        str(
            install_payload.get("root_folder_uri")
            or profile.get("root_folder_uri")
            or profile_metadata.get("root_folder_uri")
            or ""
        ).strip()
        or None
    )
    approved_folders = []
    if root_folder_uri:
        approved_folders.append(root_folder_uri)
    for token in _list_strings(profile_metadata.get("allowed_folders")):
        if token not in approved_folders:
            approved_folders.append(token)

    approved_applications = _list_strings(profile_metadata.get("allowed_applications"))

    if normalized_mode == "hosted_secure":
        limits = {
            **DEFAULT_HOSTED_LIMITS,
            **_coerce_dict(profile_metadata.get("hosted_secure_limits")),
        }
        network_policy = {
            **DEFAULT_NETWORK_POLICY,
            **_coerce_dict(profile_metadata.get("network_policy")),
        }
        return {
            "mode": "hosted_secure",
            "driver": hosted_secure_driver(),
            "workspace_kind": "ephemeral",
            "read_only_base_image": True,
            "base_image_id": str(profile_metadata.get("base_image_id") or HOSTED_SECURE_BASE_IMAGE),
            "host_mounts_allowed": False,
            "docker_socket_exposed": False,
            "approved_folders": [],
            "approved_applications": [],
            "limits": {
                "cpu_seconds": max(int(limits.get("cpu_seconds") or DEFAULT_HOSTED_LIMITS["cpu_seconds"]), 1),
                "memory_mb": max(int(limits.get("memory_mb") or DEFAULT_HOSTED_LIMITS["memory_mb"]), 128),
                "timeout_seconds": max(int(limits.get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]), 1),
                "max_open_files": max(int(limits.get("max_open_files") or DEFAULT_HOSTED_LIMITS["max_open_files"]), 32),
                "max_file_bytes": max(int(limits.get("max_file_bytes") or DEFAULT_HOSTED_LIMITS["max_file_bytes"]), 1024 * 1024),
            },
            "network_policy": {
                "mode": str(network_policy.get("mode") or DEFAULT_NETWORK_POLICY["mode"]).strip() or DEFAULT_NETWORK_POLICY["mode"],
                "allow_outbound": bool(network_policy.get("allow_outbound", True)),
                "allow_private_hosts": bool(network_policy.get("allow_private_hosts", False)),
                "allowed_hosts": _list_strings(network_policy.get("allowed_hosts") or network_policy.get("allowed_domains")),
                "allowed_providers": _list_strings(network_policy.get("allowed_providers")) or list(DEFAULT_NETWORK_POLICY["allowed_providers"]),
                "hooks_ready": bool(network_policy.get("hooks_ready", True)),
            },
            "state_layer_policy": state_layer_policy(runtime_mode=normalized_mode),
        }

    network_policy = {
        **DEFAULT_NETWORK_POLICY,
        **_coerce_dict(profile_metadata.get("network_policy")),
    }
    return {
        "mode": normalized_mode,
        "driver": "desktop_companion",
        "workspace_kind": "scoped_device",
        "read_only_base_image": False,
        "base_image_id": None,
        "host_mounts_allowed": bool(approved_folders),
        "docker_socket_exposed": False,
        "approved_folders": approved_folders,
        "approved_applications": approved_applications,
        "network_policy": {
            "mode": str(network_policy.get("mode") or DEFAULT_NETWORK_POLICY["mode"]).strip() or DEFAULT_NETWORK_POLICY["mode"],
            "allow_outbound": bool(network_policy.get("allow_outbound", True)),
            "allow_private_hosts": bool(network_policy.get("allow_private_hosts", False)),
            "allowed_hosts": _list_strings(network_policy.get("allowed_hosts") or network_policy.get("allowed_domains")),
            "allowed_providers": _list_strings(network_policy.get("allowed_providers")) or list(DEFAULT_NETWORK_POLICY["allowed_providers"]),
            "hooks_ready": bool(network_policy.get("hooks_ready", True)),
        },
        "state_layer_policy": state_layer_policy(runtime_mode=normalized_mode),
        "requires_explicit_approval": normalized_mode == "privileged_device",
    }


def summarize_runtime_scope(scope: Dict[str, Any]) -> str:
    mode = str(scope.get("mode") or "").strip() or "hosted_secure"
    if mode == "hosted_secure":
        limits = _coerce_dict(scope.get("limits"))
        network = _coerce_dict(scope.get("network_policy"))
        return (
            f"{scope.get('driver')} · ephemeral workspace · read-only base image · "
            f"cpu {limits.get('cpu_seconds')}s · memory {limits.get('memory_mb')}MB · "
            f"network {network.get('mode')}"
        )
    approved_folders = _list_strings(scope.get("approved_folders"))
    approved_apps = _list_strings(scope.get("approved_applications"))
    return (
        f"{scope.get('driver')} · "
        f"{len(approved_folders)} approved folder(s) · "
        f"{len(approved_apps)} approved app(s)"
    )


def _python_read_paths() -> list[Path]:
    raw_executable = Path(sys.executable)
    candidates: list[Path] = [PROJECT_ROOT, raw_executable, raw_executable.resolve(), raw_executable.parent]
    for value in sysconfig.get_paths().values():
        if value:
            candidates.append(Path(value).resolve())
    for entry in site.getsitepackages():
        if entry:
            candidates.append(Path(entry).resolve())
    try:
        user_site = site.getusersitepackages()
    except Exception:
        user_site = None
    if user_site:
        candidates.append(Path(user_site).resolve())
    for extra in (sys.prefix, sys.base_prefix):
        if extra:
            candidates.append(Path(extra).resolve())
    for candidate in _HOST_RUNTIME_LIBRARY_ROOTS:
        if candidate.exists():
            candidates.append(candidate.resolve())
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        token = str(candidate)
        if token in seen:
            continue
        seen.add(token)
        deduped.append(candidate)
    return deduped


def _python_metadata_paths() -> list[Path]:
    metadata_paths: list[Path] = []
    seen: set[str] = set()
    for candidate in _python_read_paths():
        current = candidate if candidate.is_dir() else candidate.parent
        while True:
            token = str(current)
            if token == "/" or token in seen:
                break
            seen.add(token)
            metadata_paths.append(current)
            parent = current.parent
            if parent == current:
                break
            current = parent
    return metadata_paths


def _python_import_paths() -> list[str]:
    paths: list[str] = [str(PROJECT_ROOT)]
    for path in _python_read_paths():
        token = str(path)
        if token not in paths and ("site-packages" in token or token == str(PROJECT_ROOT)):
            paths.append(token)
    return paths


def _sb_expr_for_path(path: Path) -> str:
    escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
    if path.is_file():
        return f'(literal "{escaped}")'
    return f'(subpath "{escaped}")'


def _sb_literal_expr(path: Path) -> str:
    escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'(literal "{escaped}")'


def _sandbox_profile(*, root: Path, scope: Dict[str, Any]) -> str:
    _enforce_hosted_sandbox_profile_policy(sandbox_root=root, scope=scope)
    read_paths = _python_read_paths()
    metadata_paths = _python_metadata_paths()
    write_paths = [root]
    lines = [
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(allow process*)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow sysctl-read)",
        "(allow file-read-metadata",
    ]
    lines.extend(f"  {_sb_literal_expr(path)}" for path in metadata_paths)
    lines.extend([
        ")",
        "(allow file-read*",
    ])
    lines.extend(f"  {_sb_expr_for_path(path)}" for path in read_paths)
    lines.append(")")
    lines.append("(allow file-map-executable")
    lines.extend(f"  {_sb_expr_for_path(path)}" for path in read_paths)
    lines.append(")")
    lines.append("(allow file-write*")
    lines.extend(f"  {_sb_expr_for_path(path)}" for path in write_paths)
    lines.append(")")
    if bool(_coerce_dict(scope.get("network_policy")).get("allow_outbound", True)):
        lines.append("(allow network-outbound)")
    return "\n".join(lines)


def _minimal_env(*, sandbox_root: Path, output_path: Path, scope: Dict[str, Any]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for key in _MINIMAL_ENV_ALLOWLIST:
        value = os.getenv(key)
        if value:
            env[key] = value
    for key, value in os.environ.items():
        if any(key.startswith(prefix) for prefix in _SCRUBBED_ENV_PREFIXES):
            continue
        if key.startswith("PG") and value:
            env[key] = value
    env["PATH"] = env.get("PATH") or "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
    env["PYTHONPATH"] = os.pathsep.join(_python_import_paths())
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["HOME"] = str((sandbox_root / "home").resolve())
    env["TMPDIR"] = str((sandbox_root / "tmp").resolve())
    env["EMPYRALIS_SANDBOX_ROOT"] = str(sandbox_root.resolve())
    env["EMPYRALIS_SANDBOX_OUTPUT_FILE"] = str(output_path.resolve())
    env["EMPYRALIS_SANDBOX_CPU_SECONDS"] = str(_coerce_dict(scope.get("limits")).get("cpu_seconds") or DEFAULT_HOSTED_LIMITS["cpu_seconds"])
    env["EMPYRALIS_SANDBOX_MEMORY_MB"] = str(_coerce_dict(scope.get("limits")).get("memory_mb") or DEFAULT_HOSTED_LIMITS["memory_mb"])
    env["EMPYRALIS_SANDBOX_MAX_OPEN_FILES"] = str(_coerce_dict(scope.get("limits")).get("max_open_files") or DEFAULT_HOSTED_LIMITS["max_open_files"])
    env["EMPYRALIS_SANDBOX_MAX_FILE_BYTES"] = str(_coerce_dict(scope.get("limits")).get("max_file_bytes") or DEFAULT_HOSTED_LIMITS["max_file_bytes"])
    env["EMPYRALIS_SANDBOX_NETWORK_MODE"] = str(_coerce_dict(scope.get("network_policy")).get("mode") or DEFAULT_NETWORK_POLICY["mode"])
    env["EMPYRALIS_SANDBOX_ALLOW_OUTBOUND"] = "1" if bool(_coerce_dict(scope.get("network_policy")).get("allow_outbound", True)) else "0"
    _enforce_hosted_sandbox_environment_policy(
        sandbox_root=sandbox_root,
        output_path=output_path,
        scope=scope,
        env=env,
    )
    return env


def _write_read_only_base_image(*, image_root: Path, payload: Dict[str, Any], scope: Dict[str, Any]) -> None:
    _enforce_hosted_base_image_state_decision(image_root=image_root, payload=payload, scope=scope)
    image_root.mkdir(parents=True, exist_ok=True)
    files = {
        "manifest.json": payload.get("manifest"),
        "policy.json": {
            "runtime_scope": scope,
            "base_image_id": scope.get("base_image_id"),
        },
    }
    for filename, content in files.items():
        target = image_root / filename
        target.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        target.chmod(0o444)
    image_root.chmod(0o555)


def _worker_command(scope: Dict[str, Any]) -> list[str]:
    command = [sys.executable, "-m", "server_modules.hosted_secure_worker"]
    if scope.get("driver") != HOSTED_SECURE_DRIVER_SANDBOX_EXEC:
        return command
    sandbox_exec = shutil.which("sandbox-exec")
    if not sandbox_exec:
        return command
    sandbox_root_value = os.environ.get("EMPYRALIS_SANDBOX_ROOT")
    if not sandbox_root_value:
        raise HostedSecureSandboxError("EMPYRALIS_SANDBOX_ROOT is required for sandbox-exec worker commands.")
    return [sandbox_exec, "-p", _sandbox_profile(root=Path(sandbox_root_value).resolve(), scope=scope), *command]


def _run_hosted_worker(
    *,
    manifest: AgentManifest,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    scope: Dict[str, Any],
    runtime_profile: Dict[str, Any] | None,
) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="empyralis-hosted-secure-") as tempdir:
        sandbox_root = Path(tempdir).resolve()
        workspace_root = sandbox_root / "workspace"
        image_root = sandbox_root / "image"
        tmp_root = sandbox_root / "tmp"
        home_root = sandbox_root / "home"
        outputs_root = workspace_root / "outputs"
        _enforce_hosted_sandbox_preparation(sandbox_root=sandbox_root, scope=scope)
        for path in (workspace_root, tmp_root, home_root, outputs_root):
            path.mkdir(parents=True, exist_ok=True)

        payload = {
            "manifest": manifest.model_dump(mode="json"),
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "goal": goal,
            "runtime_profile": runtime_profile or {},
            "runtime_scope": scope,
        }
        _write_read_only_base_image(image_root=image_root, payload=payload, scope=scope)
        output_file = outputs_root / "turn-result.json"

        if scope.get("driver") == DOCKER_DRIVER:
            limits = _coerce_dict(scope.get("limits", {}))
            completed = run_docker_worker(
                sandbox_root=str(sandbox_root),
                payload=payload,
                memory_mb=int(limits.get("memory_mb", DEFAULT_HOSTED_LIMITS["memory_mb"])),
                cpu_shares=1024,
                timeout_seconds=int(limits.get("timeout_seconds", DEFAULT_HOSTED_LIMITS["timeout_seconds"])),
                network_enabled=False,
            )
            return docker_sandbox_result(completed, sandbox_root=str(sandbox_root))

        env = _minimal_env(sandbox_root=sandbox_root, output_path=output_file, scope=scope)
        command = [str(Path(sys.executable).resolve()), "-m", "server_modules.hosted_secure_worker"]
        if scope.get("driver") == HOSTED_SECURE_DRIVER_SANDBOX_EXEC:
            sandbox_exec = shutil.which("sandbox-exec")
            if sandbox_exec:
                command = [sandbox_exec, "-p", _sandbox_profile(root=sandbox_root, scope=scope), *command]
        _enforce_hosted_worker_launch(
            sandbox_root=sandbox_root,
            scope=scope,
            output_file=output_file,
        )
        completed = subprocess.run(
            command,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=str(workspace_root),
            env=env,
            timeout=max(int(_coerce_dict(scope.get("limits")).get("timeout_seconds") or DEFAULT_HOSTED_LIMITS["timeout_seconds"]), 1),
            check=False,
        )
        if completed.returncode != 0:
            _classify_hosted_worker_outcome(completed)
            detail = (completed.stderr or completed.stdout or "Hosted Secure worker failed.").strip()
            raise HostedSecureSandboxError(detail)
        try:
            result = json.loads(str(completed.stdout or "{}"))
        except json.JSONDecodeError as error:
            raise HostedSecureSandboxError("Hosted Secure worker returned invalid JSON.") from error
        if not isinstance(result, dict):
            raise HostedSecureSandboxError("Hosted Secure worker returned an invalid payload.")
        result["execution_outcome"] = _classify_hosted_worker_outcome(completed, parsed_result=result)
        _enforce_hosted_sandbox_cleanup_policy(sandbox_root=sandbox_root, scope=scope)
        result["sandbox"] = {
            "mode": "hosted_secure",
            "driver": scope.get("driver"),
            "workspace_kind": "ephemeral",
            "read_only_base_image": True,
            "base_image_id": scope.get("base_image_id"),
            "host_mounts_allowed": False,
            "docker_socket_exposed": False,
            "network_policy": _coerce_dict(scope.get("network_policy")),
            "limits": _coerce_dict(scope.get("limits")),
            "workspace_output_written": output_file.exists(),
        }
        return result


async def execute_hosted_customer_turn(
    *,
    manifest: AgentManifest,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    runtime_profile: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    scope = runtime_scope(runtime_mode="hosted_secure", runtime_profile=runtime_profile)
    return await asyncio.to_thread(
        _run_hosted_worker,
        manifest=manifest,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        goal=goal,
        scope=scope,
        runtime_profile=runtime_profile,
    )
