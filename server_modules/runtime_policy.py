from __future__ import annotations
import os, json, time, uuid, queue, re, hashlib, sys, threading, shlex
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from pathlib import Path

from scripts.platform_execution import capability_metadata
from server_modules.capability_registry import canonical_capability_id, resolve_capability
from server_modules import rust_runtime_kernel_client

_server = None
_server_import_error: Optional[ModuleNotFoundError] = None
LOCAL_WORKER_REGISTRY: Dict[str, Any] = {}

def _init():
    global _server, _server_import_error
    if _server is not None: return
    if _server_import_error is not None:
        return
    try:
        import server as _s
    except ModuleNotFoundError as exc:
        _server_import_error = exc
        return
    _server = _s
    for k, v in vars(_s).items():
        if not k.startswith("__") and k not in globals():
            globals()[k] = v


TOOL_STATE_LOCK = threading.Lock()
TOOL_STATE: Dict[str, Any] = {
    "version": 1,
    "enabled": {},
    "updated_at": None,
}
_TOOL_STATE_LOADED = False


class RuntimePolicyRustGateError(RuntimeError):
    pass


def _enforce_runtime_tool_policy_state_decision(*, tool_id: str, enabled: bool) -> Dict[str, Any]:
    normalized_tool_id = str(tool_id or "").strip()
    payload = {
        "tool_id": normalized_tool_id,
        "enabled": bool(enabled),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="set_runtime_tool_enabled",
            state_class="runtime_tool_policy",
            actor_id="system",
            status="enabled" if enabled else "disabled",
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
        if next_action != "set_runtime_tool_enabled":
            raise RuntimePolicyRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise RuntimePolicyRustGateError(exc.reason) from exc


def _load_tool_state() -> None:
    _init()
    global _TOOL_STATE_LOADED
    if _TOOL_STATE_LOADED:
        return
    path_value = str(globals().get("ORION_TOOL_STATE_FILE") or "").strip()
    if not path_value:
        _TOOL_STATE_LOADED = True
        return
    try:
        path = Path(path_value).expanduser()
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                enabled = payload.get("enabled") if isinstance(payload.get("enabled"), dict) else {}
                TOOL_STATE["version"] = int(payload.get("version") or 1)
                TOOL_STATE["enabled"] = {
                    str(key).strip(): bool(value)
                    for key, value in enabled.items()
                    if str(key).strip()
                }
                TOOL_STATE["updated_at"] = str(payload.get("updated_at") or "").strip() or None
    except Exception:
        # Keep defaults if state load fails.
        pass
    _TOOL_STATE_LOADED = True


def _persist_tool_state() -> None:
    _init()
    path_value = str(globals().get("ORION_TOOL_STATE_FILE") or "").strip()
    if not path_value:
        return
    try:
        path = Path(path_value).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": TOOL_STATE.get("version", 1),
            "enabled": dict(TOOL_STATE.get("enabled") or {}),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def is_tool_enabled(tool_id: str) -> bool:
    _load_tool_state()
    normalized = normalize_action_id(tool_id) or str(tool_id or "").strip().lower()
    if not normalized:
        return True
    enabled_map = TOOL_STATE.get("enabled") if isinstance(TOOL_STATE.get("enabled"), dict) else {}
    if normalized in enabled_map:
        return bool(enabled_map[normalized])
    return True


def set_tool_enabled(tool_id: str, enabled: bool) -> bool:
    _load_tool_state()
    normalized = normalize_action_id(tool_id) or str(tool_id or "").strip().lower()
    if not normalized:
        return False
    _enforce_runtime_tool_policy_state_decision(tool_id=normalized, enabled=enabled)
    with TOOL_STATE_LOCK:
        enabled_map = TOOL_STATE.get("enabled") if isinstance(TOOL_STATE.get("enabled"), dict) else {}
        enabled_map = dict(enabled_map)
        enabled_map[normalized] = bool(enabled)
        TOOL_STATE["enabled"] = enabled_map
    _persist_tool_state()
    return True

RISKY_ACTION_KEYWORDS = [
    "delete",
    "remove",
    "payment",
    "charge",
    "invoice",
    "publish",
    "send",
    "email",
    "transfer",
    "terminate",
    "cancel",
    "refund",
]

SAFE_SHELL_COMMANDS = (
    "echo",
    "ls",
    "cat",
    "pwd",
    "grep",
    "python",
    "python3",
    "pip",
    "pip3",
    "py",
)
SAFE_SHELL_BLOCKED_TOKENS = (
    "&&",
    "||",
    ";",
    "|",
    ">",
    ">>",
    "<",
    "$(",
    "`",
    "\n",
    "\r",
)
SAFE_SHELL_RISKY_MARKERS = (
    "rm ",
    "rm-",
    "dd ",
    "mkfs",
    "diskutil erase",
    "shutdown",
    "reboot",
    "killall",
    "sudo ",
    "apt ",
    "apt-get ",
    "yum ",
    "dnf ",
    "brew install",
    "brew upgrade",
    "npm install",
    "npm uninstall",
)
PYTHON_RAW_EXECUTION_FLAGS = ("-c", "-m")
PIP_MUTATING_SUBCOMMANDS = ("install", "uninstall", "download", "wheel")
LOCAL_SYSTEM_INFO_SHELL_COMMAND = (
    'printf "===OS===\\n"; '
    '(sw_vers 2>/dev/null || uname -a); '
    'printf "\\n===CPU===\\n"; '
    '(sysctl -n machdep.cpu.brand_string 2>/dev/null || uname -m); '
    'printf "\\n===RAM_BYTES===\\n"; '
    '(sysctl -n hw.memsize 2>/dev/null || true); '
    'printf "\\n===CORES===\\n"; '
    '(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || true); '
    'printf "\\n===DISK===\\n"; '
    '(df -h / 2>/dev/null || true)'
)
LOCAL_SYSTEM_INFO_SHELL_COMMAND_COMPACT = re.sub(
    r"\s+",
    " ",
    LOCAL_SYSTEM_INFO_SHELL_COMMAND.strip(),
).lower()


def _iter_raw_shell_operations(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
    collected: List[Dict[str, Any]] = []
    for item in operations:
        if not isinstance(item, dict):
            continue
        tool = normalize_action_id(item.get("tool") or item.get("action"))
        if tool == "shell.execute":
            collected.append(item)
    if collected:
        return collected
    command = str(metadata.get("raw_shell_command") or "").strip()
    argv = metadata.get("raw_shell_argv") if isinstance(metadata.get("raw_shell_argv"), list) else None
    if command or argv:
        return [{"tool": "shell.execute", "command": command or None, "argv": argv}]
    return []


def _raw_shell_command_text(operation: Dict[str, Any]) -> str:
    command = str(operation.get("command") or "").strip()
    if command:
        return command
    argv = operation.get("argv") if isinstance(operation.get("argv"), list) else []
    return " ".join(str(item or "").strip() for item in argv if str(item or "").strip()).strip()


def _is_known_read_only_system_probe(operation: Dict[str, Any]) -> bool:
    command = _raw_shell_command_text(operation)
    compact = re.sub(r"\s+", " ", command).strip().lower()
    return bool(compact) and compact == LOCAL_SYSTEM_INFO_SHELL_COMMAND_COMPACT


def _metadata_is_known_read_only_system_probe(metadata: Dict[str, Any]) -> bool:
    operations = _iter_raw_shell_operations(metadata)
    if not operations:
        return False
    return all(_is_known_read_only_system_probe(item) for item in operations)


def _is_safe_raw_shell_command(operation: Dict[str, Any]) -> bool:
    command = _raw_shell_command_text(operation)
    compact = re.sub(r"\s+", " ", command).strip().lower()
    if not compact:
        return False
    if _is_known_read_only_system_probe(operation):
        return True
    if any(token in command for token in SAFE_SHELL_BLOCKED_TOKENS):
        return False
    if any(marker in compact for marker in SAFE_SHELL_RISKY_MARKERS):
        return False
    try:
        parts = [part.lower() for part in shlex.split(command)]
    except ValueError:
        return False
    if not parts:
        return False
    first_token = parts[0]
    if first_token not in SAFE_SHELL_COMMANDS:
        return False
    if first_token in {"python", "python3", "py"}:
        for part in parts[1:]:
            if any(part == flag or part.startswith(flag) for flag in PYTHON_RAW_EXECUTION_FLAGS):
                return False
    if first_token in {"pip", "pip3"} and any(part in PIP_MUTATING_SUBCOMMANDS for part in parts[1:]):
        return False
    if first_token == "py":
        return compact.startswith("py -3") or compact == "py"
    return True


def _metadata_allows_safe_raw_shell(metadata: Dict[str, Any]) -> bool:
    operations = _iter_raw_shell_operations(metadata)
    if not operations:
        return False
    return all(_is_safe_raw_shell_command(item) for item in operations)


def _browser_action_plan_row(raw_action: Any) -> Dict[str, Any]:
    item = raw_action if isinstance(raw_action, dict) else {}
    return {
        "action": str(item.get("action") or "").strip().lower(),
        "selector": str(item.get("selector") or "").strip(),
        "frame": str(item.get("frame") or item.get("frameSelector") or "").strip(),
        "tab": str(item.get("tab") or item.get("tabId") or "").strip(),
        "text": str(item.get("text") or ""),
        "url": str(item.get("url") or "").strip(),
        "ms": int(item.get("ms") or item.get("delayMs") or 0),
        "value": str(item.get("value") or ""),
        "attribute": str(item.get("attribute") or "").strip(),
        "path": str(item.get("path") or item.get("file_path") or "").strip(),
        "paths": [
            str(entry or "").strip()
            for entry in (item.get("paths") if isinstance(item.get("paths"), list) else [])
            if str(entry or "").strip()
        ],
        "label": str(item.get("label") or "").strip(),
        "prompt": str(item.get("prompt") or item.get("reason") or "").strip(),
    }


def _browser_operation_plan_row(raw_operation: Any) -> Dict[str, Any]:
    item = raw_operation if isinstance(raw_operation, dict) else {}
    raw_actions = item.get("browser_actions") if isinstance(item.get("browser_actions"), list) else item.get("browserActions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    return {
        "tool": normalize_action_id(item.get("tool") or item.get("action") or "browser_automation.interactive"),
        "mode": str(item.get("mode") or "extract_text").strip().lower(),
        "url": str(item.get("url") or "").strip(),
        "path": str(item.get("path") or "").strip(),
        "session_profile": str(item.get("session_profile") or item.get("sessionProfile") or "").strip(),
        "browser_actions": [_browser_action_plan_row(entry) for entry in actions],
    }


def browser_automation_plan_hash(operations: Any) -> str:
    items = operations if isinstance(operations, list) else [operations]
    normalized = [
        _browser_operation_plan_row(item)
        for item in items
        if isinstance(item, dict)
    ]
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def browser_automation_plan_hash_from_pack_inputs(pack_inputs: Any) -> str:
    payload = pack_inputs if isinstance(pack_inputs, dict) else {}
    operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
    browser_ops = [
        item
        for item in operations
        if isinstance(item, dict) and normalize_action_id(item.get("tool") or item.get("action")) == "browser_automation.interactive"
    ]
    if not browser_ops and str(payload.get("url") or "").strip():
        browser_ops = [payload]
    if not browser_ops:
        return ""
    return browser_automation_plan_hash(browser_ops)


LOCAL_OPERATOR_APPROVAL_ENV_KEYS = (
    "EMPYRALIS_DESKTOP_ELECTRON_BIN",
    "ORION_DESKTOP_ELECTRON_BIN",
    "EMPYRALIS_DESKTOP_DISABLE_GPU",
    "EMPYRALIS_DESKTOP_GL",
    "EMPYRALIS_DESKTOP_DISABLE_METAL",
    "EMPYRALIS_DESKTOP_DISABLE_VULKAN",
)


def local_operator_approval_env_snapshot(env: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    source = env if isinstance(env, dict) else os.environ
    snapshot: Dict[str, str] = {}
    for key in LOCAL_OPERATOR_APPROVAL_ENV_KEYS:
        value = str(source.get(key) or "").strip()
        if value:
            snapshot[key] = value
    return snapshot


def build_local_operator_execution_binding(
    *,
    argv: Any,
    cwd: Any,
    env_vars: Optional[Dict[str, Any]] = None,
    browser_plan_hash: str = "",
    session_profile: str = "",
) -> Dict[str, Any]:
    normalized_argv = [
        str(item).strip()
        for item in (argv if isinstance(argv, list) else [argv])
        if str(item).strip()
    ]
    normalized_env = {
        key: str(value).strip()
        for key, value in sorted((env_vars or {}).items())
        if str(key).strip() and str(value).strip()
    }
    return {
        "argv": normalized_argv,
        "cwd": str(cwd or "").strip(),
        "env_vars": normalized_env,
        "browser_plan_hash": str(browser_plan_hash or "").strip(),
        "session_profile": str(session_profile or "").strip(),
    }


def local_operator_execution_binding_hash(binding: Optional[Dict[str, Any]]) -> str:
    payload = build_local_operator_execution_binding(
        argv=(binding or {}).get("argv") if isinstance(binding, dict) else [],
        cwd=(binding or {}).get("cwd") if isinstance(binding, dict) else "",
        env_vars=(binding or {}).get("env_vars") if isinstance(binding, dict) else {},
        browser_plan_hash=(binding or {}).get("browser_plan_hash") if isinstance(binding, dict) else "",
        session_profile=(binding or {}).get("session_profile") if isinstance(binding, dict) else "",
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def local_operator_execution_binding_matches(expected: Optional[Dict[str, Any]], actual: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(expected, dict) or not expected:
        return False
    return local_operator_execution_binding_hash(expected) == local_operator_execution_binding_hash(actual)


def build_browser_execution_binding(project_root: Path, browser_plan_hash: str, session_profile: str = "") -> Dict[str, Any]:
    argv = [sys.executable, "-m", "server_modules.execution_router"]
    cwd = str(Path(project_root).resolve())
    return build_local_operator_execution_binding(
        argv=argv,
        cwd=cwd,
        env_vars=local_operator_approval_env_snapshot(),
        browser_plan_hash=browser_plan_hash,
        session_profile=session_profile,
    )

TRUST_MODE_AUTO = "auto"
TRUST_MODE_GUARDED = "guarded"
TRUST_MODE_STRICT = "strict"
TRUST_MODE_COST_GUARD = "cost_guard"
TRUST_MODE_SENSITIVE_GUARD = "sensitive_guard"
TRUST_MODE_ALIASES = {
    "ask": TRUST_MODE_GUARDED,
    "manual": TRUST_MODE_GUARDED,
    "review": TRUST_MODE_STRICT,
    "strict": TRUST_MODE_STRICT,
    "guarded": TRUST_MODE_GUARDED,
    "auto": TRUST_MODE_AUTO,
    "cost": TRUST_MODE_COST_GUARD,
    "cost_guard": TRUST_MODE_COST_GUARD,
    "sensitive": TRUST_MODE_SENSITIVE_GUARD,
    "sensitive_guard": TRUST_MODE_SENSITIVE_GUARD,
}
VALID_TRUST_MODES = {
    TRUST_MODE_AUTO,
    TRUST_MODE_GUARDED,
    TRUST_MODE_STRICT,
    TRUST_MODE_COST_GUARD,
    TRUST_MODE_SENSITIVE_GUARD,
}
ORION_RUNTIME_TRUST_MODE_DEFAULT = (
    str(os.getenv("ORION_RUNTIME_TRUST_MODE_DEFAULT") or TRUST_MODE_GUARDED).strip().lower() or TRUST_MODE_GUARDED
)
if ORION_RUNTIME_TRUST_MODE_DEFAULT not in VALID_TRUST_MODES:
    ORION_RUNTIME_TRUST_MODE_DEFAULT = TRUST_MODE_GUARDED

POLICY_MODE_LOCAL_DEFAULT = "local_default"
POLICY_MODE_TRUSTED_FULL_ACCESS = "trusted_full_access"
VALID_POLICY_MODES = {
    POLICY_MODE_LOCAL_DEFAULT,
    POLICY_MODE_TRUSTED_FULL_ACCESS,
}
POLICY_MODE_ALIASES = {
    "local": POLICY_MODE_LOCAL_DEFAULT,
    "local_default": POLICY_MODE_LOCAL_DEFAULT,
    "default": POLICY_MODE_LOCAL_DEFAULT,
    "trusted": POLICY_MODE_TRUSTED_FULL_ACCESS,
    "trusted_full_access": POLICY_MODE_TRUSTED_FULL_ACCESS,
    "full_access": POLICY_MODE_TRUSTED_FULL_ACCESS,
}
ORION_RUNTIME_POLICY_MODE_DEFAULT = (
    str(os.getenv("ORION_RUNTIME_POLICY_MODE_DEFAULT") or POLICY_MODE_LOCAL_DEFAULT).strip().lower()
    or POLICY_MODE_LOCAL_DEFAULT
)
if ORION_RUNTIME_POLICY_MODE_DEFAULT not in VALID_POLICY_MODES:
    ORION_RUNTIME_POLICY_MODE_DEFAULT = POLICY_MODE_LOCAL_DEFAULT

ACTION_TYPE_READ = "read"
ACTION_TYPE_DRAFT = "draft"
ACTION_TYPE_REVERSIBLE_WRITE = "reversible_write"
ACTION_TYPE_EXTERNAL_SEND = "external_send"
ACTION_TYPE_PUBLIC_PUBLISH = "public_publish"
ACTION_TYPE_DESTRUCTIVE = "destructive"

EXECUTION_TARGET_AUTO = "auto"
EXECUTION_TARGET_CLOUD = "cloud"
EXECUTION_TARGET_LOCAL_COMPANION = "local_companion"
VALID_EXECUTION_TARGETS = {
    EXECUTION_TARGET_AUTO,
    EXECUTION_TARGET_CLOUD,
    EXECUTION_TARGET_LOCAL_COMPANION,
}

RUNTIME_CHOICE_LOCAL = "local"
RUNTIME_CHOICE_VIRTUAL_BROWSER = "virtual_browser"
RUNTIME_CHOICE_VIRTUAL_DESKTOP = "virtual_desktop"
RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX = "virtual_code_sandbox"
VALID_RUNTIME_CHOICES = {
    RUNTIME_CHOICE_LOCAL,
    RUNTIME_CHOICE_VIRTUAL_BROWSER,
    RUNTIME_CHOICE_VIRTUAL_DESKTOP,
    RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
}
RUNTIME_CONTRACT_INTERFACE_ID = "virtual_computer_runtime.v1"
RUNTIME_CONTRACT_METHODS = [
    "create_session",
    "resume_session",
    "pause_session",
    "terminate_session",
    "execute_action",
    "stream_screenshot",
    "collect_artifact",
    "snapshot_session",
]
RUNTIME_CONTRACT_STATES = [
    "provisioning",
    "ready",
    "running",
    "paused",
    "degraded",
    "expired",
    "terminated",
    "failed",
]
RUNTIME_CONTRACT_KIND_LOCAL_GATEWAY = "local_gateway_runtime"
RUNTIME_CONTRACT_KIND_VIRTUAL_COMPUTER = "virtual_computer_runtime"
RUNTIME_CHOICE_ALIASES = {
    "local": RUNTIME_CHOICE_LOCAL,
    "local_companion": RUNTIME_CHOICE_LOCAL,
    "localcompanion": RUNTIME_CHOICE_LOCAL,
    "mac": RUNTIME_CHOICE_LOCAL,
    "local_computer": RUNTIME_CHOICE_LOCAL,
    "virtual_browser": RUNTIME_CHOICE_VIRTUAL_BROWSER,
    "virtual-browser": RUNTIME_CHOICE_VIRTUAL_BROWSER,
    "virtualbrowser": RUNTIME_CHOICE_VIRTUAL_BROWSER,
    "browser_vm": RUNTIME_CHOICE_VIRTUAL_BROWSER,
    "cloud_lite": RUNTIME_CHOICE_VIRTUAL_BROWSER,
    "virtual_computer": RUNTIME_CHOICE_VIRTUAL_BROWSER,
    "cloud_agent": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
    "virtual_desktop": RUNTIME_CHOICE_VIRTUAL_DESKTOP,
    "virtual-desktop": RUNTIME_CHOICE_VIRTUAL_DESKTOP,
    "virtualdesktop": RUNTIME_CHOICE_VIRTUAL_DESKTOP,
    "virtual_workspace": RUNTIME_CHOICE_VIRTUAL_DESKTOP,
    "virtual-workspace": RUNTIME_CHOICE_VIRTUAL_DESKTOP,
    "dedicated_virtual_workspace": RUNTIME_CHOICE_VIRTUAL_DESKTOP,
    "dedicated_agent": RUNTIME_CHOICE_VIRTUAL_DESKTOP,
    "virtual_code_sandbox": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
    "virtual-code-sandbox": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
    "virtualcodesandbox": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
    "code_sandbox": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
    "sandbox": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
}

RUNTIME_TRUST_ZONE_SHARED_CLOUD = "shared_cloud"
RUNTIME_TRUST_ZONE_USER_OWNED_LOCAL = "user_owned_local"
RUNTIME_TRUST_ZONE_OWNED_DEDICATED_RUNTIME = "owned_dedicated_runtime"
RUNTIME_TRUST_ZONE_ALIASES = {
    "cloud": RUNTIME_TRUST_ZONE_SHARED_CLOUD,
    "shared_cloud": RUNTIME_TRUST_ZONE_SHARED_CLOUD,
    "hosted": RUNTIME_TRUST_ZONE_SHARED_CLOUD,
    "user_local": RUNTIME_TRUST_ZONE_USER_OWNED_LOCAL,
    "user_owned_local": RUNTIME_TRUST_ZONE_USER_OWNED_LOCAL,
    "local": RUNTIME_TRUST_ZONE_USER_OWNED_LOCAL,
    "paired_device": RUNTIME_TRUST_ZONE_USER_OWNED_LOCAL,
    "dedicated": RUNTIME_TRUST_ZONE_OWNED_DEDICATED_RUNTIME,
    "owned": RUNTIME_TRUST_ZONE_OWNED_DEDICATED_RUNTIME,
    "owned_dedicated_runtime": RUNTIME_TRUST_ZONE_OWNED_DEDICATED_RUNTIME,
    "agent_machine": RUNTIME_TRUST_ZONE_OWNED_DEDICATED_RUNTIME,
}
VALID_RUNTIME_TRUST_ZONES = {
    RUNTIME_TRUST_ZONE_SHARED_CLOUD,
    RUNTIME_TRUST_ZONE_USER_OWNED_LOCAL,
    RUNTIME_TRUST_ZONE_OWNED_DEDICATED_RUNTIME,
}

ELEVATED_MODE_OFF = "off"
ELEVATED_MODE_ASK = "ask"
ELEVATED_MODE_FULL = "full"
ELEVATED_MODE_ALIASES = {
    "off": ELEVATED_MODE_OFF,
    "disabled": ELEVATED_MODE_OFF,
    "none": ELEVATED_MODE_OFF,
    "ask": ELEVATED_MODE_ASK,
    "default": ELEVATED_MODE_ASK,
    "guarded": ELEVATED_MODE_ASK,
    "manual": ELEVATED_MODE_ASK,
    "full": ELEVATED_MODE_FULL,
    "trusted": ELEVATED_MODE_FULL,
    "full_access": ELEVATED_MODE_FULL,
    "elevated": ELEVATED_MODE_FULL,
}
VALID_ELEVATED_MODES = {
    ELEVATED_MODE_OFF,
    ELEVATED_MODE_ASK,
    ELEVATED_MODE_FULL,
}

ACTION_TYPE_ALIASES = {
    ACTION_TYPE_READ: ACTION_TYPE_READ,
    "safe_read": ACTION_TYPE_READ,
    ACTION_TYPE_DRAFT: ACTION_TYPE_DRAFT,
    ACTION_TYPE_REVERSIBLE_WRITE: ACTION_TYPE_REVERSIBLE_WRITE,
    "write": ACTION_TYPE_REVERSIBLE_WRITE,
    "internal_write": ACTION_TYPE_REVERSIBLE_WRITE,
    ACTION_TYPE_EXTERNAL_SEND: ACTION_TYPE_EXTERNAL_SEND,
    "send": ACTION_TYPE_EXTERNAL_SEND,
    ACTION_TYPE_PUBLIC_PUBLISH: ACTION_TYPE_PUBLIC_PUBLISH,
    "publish": ACTION_TYPE_PUBLIC_PUBLISH,
    ACTION_TYPE_DESTRUCTIVE: ACTION_TYPE_DESTRUCTIVE,
    "destructive": ACTION_TYPE_DESTRUCTIVE,
}
VALID_ACTION_TYPES = {
    ACTION_TYPE_READ,
    ACTION_TYPE_DRAFT,
    ACTION_TYPE_REVERSIBLE_WRITE,
    ACTION_TYPE_EXTERNAL_SEND,
    ACTION_TYPE_PUBLIC_PUBLISH,
    ACTION_TYPE_DESTRUCTIVE,
}
ACTION_TYPES_REQUIRING_HARD_CONFIRMATION = {
    ACTION_TYPE_EXTERNAL_SEND,
    ACTION_TYPE_PUBLIC_PUBLISH,
    ACTION_TYPE_DESTRUCTIVE,
}
ELEVATED_FULL_DEFAULT_ACTION_TYPES = {
    ACTION_TYPE_READ,
    ACTION_TYPE_DRAFT,
    ACTION_TYPE_REVERSIBLE_WRITE,
}

CUSTOMER_OPS_PACK_ID = "customer-ops-autopilot"
WEEKLY_CONTENT_PACK_ID = "weekly-content-studio"
COMPETITOR_BRIEF_PACK_ID = "competitor-brief-digest"
SPREADSHEET_OPS_PACK_ID = "spreadsheet-ops-v1"
DOCUMENT_STUDIO_PACK_ID = "document-studio-v1"
LOCAL_EXECUTION_PACK_ID = "local-execution-v1"

# Normalized pack IDs are computed at call time inside each routing
# function via normalize_action_id(). Module-level pre-computation
# is unsafe because normalize_action_id is defined later in the file.
SUPPORTED_OUTCOME_PACKS = {
    CUSTOMER_OPS_PACK_ID,
    WEEKLY_CONTENT_PACK_ID,
    COMPETITOR_BRIEF_PACK_ID,
    SPREADSHEET_OPS_PACK_ID,
    DOCUMENT_STUDIO_PACK_ID,
    LOCAL_EXECUTION_PACK_ID,
}
PACK_PHASES = {
    CUSTOMER_OPS_PACK_ID: [
        "Phase 1/3: Triaging inbox.",
        "Phase 2/3: Drafting lead follow-ups.",
        "Phase 3/3: Proposing booking slots.",
    ],
    WEEKLY_CONTENT_PACK_ID: [
        "Phase 1/3: Parsing topics and channels.",
        "Phase 2/3: Building weekly content schedule.",
        "Phase 3/3: Writing post briefs with CTAs.",
    ],
    COMPETITOR_BRIEF_PACK_ID: [
        "Phase 1/3: Parsing competitor list.",
        "Phase 2/3: Scoring threat level.",
        "Phase 3/3: Building concise action brief.",
    ],
    SPREADSHEET_OPS_PACK_ID: [
        "Phase 1/3: Resolving spreadsheet target.",
        "Phase 2/3: Executing requested operation.",
        "Phase 3/3: Preparing preview and next steps.",
    ],
    DOCUMENT_STUDIO_PACK_ID: [
        "Phase 1/3: Resolving document target.",
        "Phase 2/3: Building Word or PowerPoint output.",
        "Phase 3/3: Syncing file and preparing preview.",
    ],
    LOCAL_EXECUTION_PACK_ID: [
        "Phase 1/3: Resolving local execution plan.",
        "Phase 2/3: Validating local tool policy.",
        "Phase 3/3: Executing Gateway operations.",
    ],
}


def _registry_risk_level(capability_id: str, default: str) -> str:
    contract = resolve_capability(capability_id)
    if contract is None:
        return default
    return str(contract.risk_level or default).strip() or default


ACTION_RISK_LEVELS: Dict[str, str] = {
    "send_message": "medium",
    "draft_email": "medium",
    "create_calendar_event": "medium",
    "publish_content": "medium",
    "external_research": "low",
    "spreadsheet_read": "low",
    "spreadsheet_update": "medium",
    "spreadsheet_append": "medium",
    "spreadsheet_create": "medium",
    "document_create": "medium",
    "document_update": "medium",
    "presentation_create": "medium",
    "presentation_update": "medium",
    "filesystem.read_write": _registry_risk_level("filesystem.read_write", "medium"),
    "browser_automation.interactive": _registry_risk_level("browser_automation.interactive", "medium"),
    "screenshot.capture": _registry_risk_level("screenshot.capture", "medium"),
    "computer_control": _registry_risk_level("computer_control", "critical"),
    "shell.execute": _registry_risk_level("shell.execute", "critical"),
    "http_request": _registry_risk_level("http_request", "high"),
    "connector.action.read": _registry_risk_level("connector.action.read", "low"),
    "connector.action.write": _registry_risk_level("connector.action.write", "high"),
    "connector.action.execute": _registry_risk_level("connector.action.execute", "high"),
    "delete_files": "critical",
    "delete_records": "critical",
    "transfer_funds": "critical",
}

DEFAULT_ACTION_POLICY = {
    "blocked_actions": {"shell.execute", "delete_files", "delete_records", "transfer_funds"},
    "approval_actions": {
        "send_message",
        "draft_email",
        "create_calendar_event",
        "publish_content",
        "filesystem.read_write",
        "browser_automation.interactive",
        "screenshot.capture",
        "computer_control",
        "http_request",
        "connector.action.write",
        "connector.action.execute",
        "spreadsheet_update",
        "spreadsheet_append",
        "spreadsheet_create",
        "document_create",
        "document_update",
        "presentation_create",
        "presentation_update",
    },
    "allow_actions": set(),
    "block_cloud_critical": True,
    "connector_cloud_readonly": True,
}

APP_PERMISSION_ACTION_MAP = {
    "files.read": {"tool": "filesystem.read_write", "requires_approval": True},
    "files.write": {"tool": "filesystem.read_write", "requires_approval": True},
    "device.control": {"tool": "shell.execute", "requires_approval": True},
}

ACTION_SIGNAL_PATTERNS: Dict[str, List[str]] = {
    "send_message": [
        "send email",
        "send message",
        "reply to customer",
        "outreach",
        "follow up",
    ],
    "draft_email": [
        "draft email",
        "prepare draft",
        "email draft",
    ],
    "create_calendar_event": [
        "calendar event",
        "book appointment",
        "schedule appointment",
        "meeting invite",
        "propose slot",
    ],
    "publish_content": [
        "publish",
        "post on",
        "schedule post",
        "push content live",
    ],
    "spreadsheet_read": [
        "read spreadsheet",
        "open spreadsheet",
        "read csv",
        "read excel",
    ],
    "spreadsheet_update": [
        "update spreadsheet",
        "edit spreadsheet",
        "update row",
        "modify cell",
    ],
    "spreadsheet_append": [
        "append row",
        "add rows",
        "insert rows",
        "append spreadsheet",
    ],
    "spreadsheet_create": [
        "create spreadsheet",
        "new spreadsheet",
        "create csv",
        "create excel",
    ],
    "document_create": [
        "create word",
        "create document",
        "new docx",
        "write brief",
    ],
    "document_update": [
        "update word",
        "update document",
        "edit docx",
        "append document",
    ],
    "presentation_create": [
        "create powerpoint",
        "create deck",
        "new presentation",
        "new pptx",
    ],
    "presentation_update": [
        "update powerpoint",
        "update deck",
        "edit presentation",
        "append slides",
    ],
    "filesystem.read_write": [
        "read file",
        "write file",
        "append file",
        "edit file",
        "open file",
    ],
    "browser_automation.interactive": [
        "open website",
        "browse website",
        "fetch page",
        "extract page text",
        "extract links",
        "read webpage",
    ],
    "screenshot.capture": [
        "take screenshot",
        "capture screenshot",
        "screen capture",
        "grab screenshot",
    ],
    "computer_control": [
        "read screen text",
        "ocr the screen",
        "click on screen",
        "type into app",
        "read clipboard",
        "write clipboard",
        "run applescript",
        "send notification",
        "launch app",
        "list running apps",
    ],
    "shell.execute": [
        "shell command",
        "terminal command",
        "run command",
        "bash script",
        "sudo ",
        "rm -rf",
    ],
    "delete_files": [
        "delete file",
        "remove file",
        "wipe folder",
        "erase folder",
        "drop database",
    ],
    "delete_records": [
        "delete record",
        "purge data",
        "truncate table",
        "drop table",
    ],
    "transfer_funds": [
        "wire transfer",
        "bank transfer",
        "charge card",
        "issue refund",
        "make payment",
    ],
}


def normalize_action_id(raw: Any) -> str:
    value = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"[^a-z0-9_.]", "", value)
    normalized = canonical_capability_id(normalized) or normalized
    contract = resolve_capability(normalized)
    if contract is not None:
        return str(contract.capability_id or normalized).strip().lower()
    return normalized


def _parse_action_set(raw: Any) -> Set[str]:
    out: Set[str] = set()
    if isinstance(raw, (list, set, tuple)):
        for item in raw:
            action = normalize_action_id(item)
            if action:
                out.add(action)
    elif isinstance(raw, str):
        for part in raw.split(","):
            action = normalize_action_id(part)
            if action:
                out.add(action)
    return out


def infer_actions_from_text(text: str) -> Dict[str, int]:
    raw = str(text or "").lower()
    actions: Dict[str, int] = {}
    if not raw.strip():
        return actions
    for action, patterns in ACTION_SIGNAL_PATTERNS.items():
        count = 0
        for pattern in patterns:
            if pattern in raw:
                count += raw.count(pattern)
        if count > 0:
            actions[action] = count
    return actions


def infer_actions_from_pack_result(pack_id: str, result_data: Dict[str, Any]) -> Dict[str, int]:
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    connector = result_data.get("connector") if isinstance(result_data.get("connector"), dict) else {}
    actions: Dict[str, int] = {}

    if pack_id == CUSTOMER_OPS_PACK_ID:
        follow_ups_payload = outputs.get("follow_ups") if isinstance(outputs.get("follow_ups"), list) else []
        bookings_payload = outputs.get("bookings") if isinstance(outputs.get("bookings"), list) else []
        follow_ups = len(follow_ups_payload)
        bookings = len(bookings_payload)
        drafts = parse_positive_int(connector.get("email_drafts_created", connector.get("gmail_drafts_created")), 0)
        sent = parse_positive_int(connector.get("email_sent_count"), 0)
        events = parse_positive_int(connector.get("calendar_events_created"), 0)

        if sent <= 0:
            sent = sum(1 for item in follow_ups_payload if isinstance(item, dict) and item.get("delivery") == "sent")
        if drafts <= 0:
            drafts = sum(1 for item in follow_ups_payload if isinstance(item, dict) and item.get("delivery") == "draft")

        draft_like = drafts if drafts > 0 else follow_ups
        if draft_like > 0:
            actions["draft_email"] = draft_like
        if bookings > 0 or events > 0:
            actions["create_calendar_event"] = max(bookings, events)
        if sent > 0:
            actions["send_message"] = sent

    elif pack_id == WEEKLY_CONTENT_PACK_ID:
        posts = len(outputs.get("content_plan", [])) if isinstance(outputs.get("content_plan"), list) else 0
        if posts > 0:
            actions["publish_content"] = posts

    elif pack_id == COMPETITOR_BRIEF_PACK_ID:
        briefs = len(outputs.get("briefs", [])) if isinstance(outputs.get("briefs"), list) else 0
        if briefs > 0:
            actions["external_research"] = briefs
    elif pack_id == SPREADSHEET_OPS_PACK_ID:
        operation = normalize_action_id(outputs.get("operation"))
        rows_read = parse_positive_int(outputs.get("rows_read"), 0)
        rows_written = parse_positive_int(outputs.get("rows_written"), 0)
        if operation == "read" or rows_read > 0:
            actions["spreadsheet_read"] = max(rows_read, 1)
        if operation == "append" and rows_written > 0:
            actions["spreadsheet_append"] = rows_written
        elif operation == "create" and rows_written >= 0:
            actions["spreadsheet_create"] = max(rows_written, 1)
        elif operation in {"update", "edit"} and rows_written > 0:
            actions["spreadsheet_update"] = rows_written
        elif rows_written > 0:
            actions["spreadsheet_update"] = rows_written
    elif pack_id == DOCUMENT_STUDIO_PACK_ID:
        operation = normalize_action_id(outputs.get("operation"))
        format_name = str(outputs.get("format") or "").strip().lower()
        item_count = parse_positive_int(outputs.get("items_written"), 0)
        if format_name == "docx":
            tool_id = "document_update" if operation in {"update", "edit", "append"} else "document_create"
            actions[tool_id] = max(item_count, 1)
        elif format_name == "pptx":
            tool_id = "presentation_update" if operation in {"update", "edit", "append"} else "presentation_create"
            actions[tool_id] = max(item_count, 1)
    elif pack_id == LOCAL_EXECUTION_PACK_ID:
        raw_actions = outputs.get("actions") if isinstance(outputs.get("actions"), list) else []
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            tool_id = normalize_action_id(item.get("action"))
            if not tool_id:
                continue
            actions[tool_id] = int(actions.get(tool_id, 0)) + 1

    return actions


def _action_policy_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    policy = {
        "blocked_actions": set(DEFAULT_ACTION_POLICY["blocked_actions"]),
        "approval_actions": set(DEFAULT_ACTION_POLICY["approval_actions"]),
        "allow_actions": set(DEFAULT_ACTION_POLICY["allow_actions"]),
        "block_cloud_critical": bool(DEFAULT_ACTION_POLICY["block_cloud_critical"]),
        "connector_cloud_readonly": bool(DEFAULT_ACTION_POLICY["connector_cloud_readonly"]),
    }
    raw = metadata.get("action_policy") if isinstance(metadata.get("action_policy"), dict) else {}
    if not isinstance(raw, dict):
        return policy

    blocked = _parse_action_set(raw.get("blocked_actions"))
    if blocked:
        policy["blocked_actions"] = blocked

    approval = _parse_action_set(raw.get("approval_actions"))
    if approval:
        policy["approval_actions"] = approval

    allow = _parse_action_set(raw.get("allow_actions"))
    if allow:
        policy["allow_actions"] = allow

    if "block_cloud_critical" in raw:
        policy["block_cloud_critical"] = bool(raw.get("block_cloud_critical"))
    if "connector_cloud_readonly" in raw:
        policy["connector_cloud_readonly"] = bool(raw.get("connector_cloud_readonly"))
    return policy


def action_policy_from_app_permissions(permissions: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Build a default-deny action policy for app-scoped runs.
    Any permitted actions are marked as approval-required by default.
    """
    perms = {str(item).strip().lower() for item in (permissions or []) if str(item).strip()}
    allowed: Set[str] = set()
    approval: Set[str] = set()
    blocked: Set[str] = set(ACTION_RISK_LEVELS.keys())

    for perm, mapping in APP_PERMISSION_ACTION_MAP.items():
        if perm not in perms:
            continue
        tool_id = normalize_action_id(mapping.get("tool"))
        if not tool_id:
            continue
        if mapping.get("requires_approval", True):
            approval.add(tool_id)
        else:
            allowed.add(tool_id)

    blocked = blocked.difference(approval).difference(allowed)
    return {
        "blocked_actions": sorted(blocked),
        "approval_actions": sorted(approval),
        "allow_actions": sorted(allowed),
        "block_cloud_critical": True,
        "connector_cloud_readonly": True,
    }


def merge_action_policies(base: Optional[Dict[str, Any]], enforced: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge two action policies with enforced taking precedence.
    - blocked_actions: union
    - approval_actions: union
    - allow_actions: base minus enforced blocked (enforced does not expand allow)
    """
    base_policy = _action_policy_from_metadata(base or {})
    enforced_policy = _action_policy_from_metadata(enforced or {})

    blocked = set(base_policy.get("blocked_actions", set())) | set(enforced_policy.get("blocked_actions", set()))
    approval = set(base_policy.get("approval_actions", set())) | set(enforced_policy.get("approval_actions", set()))
    allow = set(base_policy.get("allow_actions", set()))
    allow = allow.difference(blocked)

    return {
        "blocked_actions": sorted(blocked),
        "approval_actions": sorted(approval),
        "allow_actions": sorted(allow),
        "block_cloud_critical": bool(enforced_policy.get("block_cloud_critical", base_policy.get("block_cloud_critical", True))),
        "connector_cloud_readonly": bool(enforced_policy.get("connector_cloud_readonly", base_policy.get("connector_cloud_readonly", True))),
    }


def _legacy_decision_label(execution_decision: str) -> str:
    normalized = str(execution_decision or "").strip().lower()
    if normalized == "deny":
        return "blocked"
    if normalized == "require_confirmation":
        return "approval_required"
    return "allow"


def evaluate_action_policy(
    action_counts: Dict[str, int],
    policy_mode: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    selected_target: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    metadata = metadata if isinstance(metadata, dict) else {}
    policy = _action_policy_from_metadata(metadata)
    blocked_actions: Set[str] = set(policy.get("blocked_actions", set()))
    allow_actions: Set[str] = set(policy.get("allow_actions", set()))
    block_cloud_critical = bool(policy.get("block_cloud_critical", True))

    target = normalize_execution_target(selected_target or metadata.get("execution_target_selected") or metadata.get("execution_target"))
    runtime_policy = resolve_runtime_policy_mode(metadata, selected_target=target)
    effective_policy_mode = normalize_policy_mode(policy_mode or runtime_policy.get("policy_mode"))
    evaluated: List[Dict[str, Any]] = []
    denied_actions: List[str] = []
    confirmation_required_actions: List[str] = []

    for action, count in sorted(action_counts.items()):
        if count <= 0:
            continue
        normalized = normalize_action_id(action)
        if not normalized:
            continue
        classification = classify_runtime_action(normalized, count=count, target=target)
        execution_decision = "allow"
        reasons: List[str] = []

        if normalized in blocked_actions and normalized not in allow_actions:
            execution_decision = "deny"
            reasons.append("Blocked by action policy.")

        if (
            execution_decision != "deny"
            and block_cloud_critical
            and target == EXECUTION_TARGET_CLOUD
            and bool(classification.get("destructive_risk"))
            and normalized not in allow_actions
        ):
            execution_decision = "deny"
            reasons.append("Critical action is blocked in cloud runtime.")

        if execution_decision != "deny":
            runtime_decision = decide_runtime_action_execution(
                classification,
                target=target,
                policy_mode=effective_policy_mode,
                metadata=metadata,
            )
            execution_decision = str(runtime_decision.get("execution_decision") or "allow").strip().lower() or "allow"
            if runtime_decision.get("reason"):
                reasons.append(str(runtime_decision.get("reason")))

        if execution_decision == "deny":
            denied_actions.append(normalized)
        elif execution_decision == "require_confirmation":
            confirmation_required_actions.append(normalized)

        evaluated.append(
            {
                "action": normalized,
                "count": count,
                "risk": ACTION_RISK_LEVELS.get(normalized, "medium"),
                "policy_mode": effective_policy_mode,
                "classification": classification,
                "execution_decision": execution_decision,
                "decision": _legacy_decision_label(execution_decision),
                "reason": " ".join(reasons).strip() or None,
            }
        )

    confirmation_reason = ""
    if confirmation_required_actions:
        uniq = sorted(set(confirmation_required_actions))
        confirmation_reason = f"Runtime policy requires one-time confirmation for: {', '.join(uniq)}."
    return {
        "evaluated": evaluated,
        "policy_mode": effective_policy_mode,
        "denied_actions": sorted(set(denied_actions)),
        "confirmation_required_actions": sorted(set(confirmation_required_actions)),
        "requires_confirmation": bool(confirmation_required_actions),
        "confirmation_reason": confirmation_reason,
        "blocked_actions": sorted(set(denied_actions)),
        "approval_actions": sorted(set(confirmation_required_actions)),
        "requires_approval": bool(confirmation_required_actions),
        "approval_reason": confirmation_reason,
        "target": target,
    }


def summarize_action_policy_eval(eval_data: Dict[str, Any]) -> str:
    evaluated = eval_data.get("evaluated") if isinstance(eval_data.get("evaluated"), list) else []
    blocked = eval_data.get("denied_actions") if isinstance(eval_data.get("denied_actions"), list) else []
    approval = eval_data.get("confirmation_required_actions") if isinstance(eval_data.get("confirmation_required_actions"), list) else []
    if not evaluated:
        return "Action policy: no actionable operations detected."
    return (
        f"Action policy: evaluated={len(evaluated)} "
        f"confirmation_required={len(approval)} denied={len(blocked)}."
    )


TOOL_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "draft_email": {
        "tool_id": "draft_email",
        "description": "Prepare email drafts for follow-up outreach.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["lead_name", "draft_message"],
            "properties": {
                "lead_name": {"type": "string"},
                "draft_message": {"type": "string"},
            },
            "additionalProperties": True,
        },
    },
    "create_calendar_event": {
        "tool_id": "create_calendar_event",
        "description": "Propose or create booking events.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["lead_name", "proposed_slot"],
            "properties": {
                "lead_name": {"type": "string"},
                "proposed_slot": {"type": "string"},
            },
            "additionalProperties": True,
        },
    },
    "publish_content": {
        "tool_id": "publish_content",
        "description": "Generate publish-ready weekly content briefs.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["channel", "topic", "headline", "cta"],
            "properties": {
                "channel": {"type": "string"},
                "topic": {"type": "string"},
                "headline": {"type": "string"},
                "cta": {"type": "string"},
            },
            "additionalProperties": True,
        },
    },
    "spreadsheet_read": {
        "tool_id": "spreadsheet_read",
        "description": "Read spreadsheet rows and return a structured preview.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["file_path"],
            "properties": {
                "file_path": {"type": "string"},
                "sheet_name": {"type": "string"},
                "row_limit": {"type": "integer"},
            },
            "additionalProperties": True,
        },
    },
    "spreadsheet_update": {
        "tool_id": "spreadsheet_update",
        "description": "Update one spreadsheet row using key/value values.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["file_path", "row_index", "values"],
            "properties": {
                "file_path": {"type": "string"},
                "sheet_name": {"type": "string"},
                "row_index": {"type": "integer"},
                "values": {"type": "object"},
            },
            "additionalProperties": True,
        },
    },
    "spreadsheet_append": {
        "tool_id": "spreadsheet_append",
        "description": "Append rows into a spreadsheet file.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["file_path", "rows"],
            "properties": {
                "file_path": {"type": "string"},
                "sheet_name": {"type": "string"},
                "rows": {"type": "array"},
            },
            "additionalProperties": True,
        },
    },
    "spreadsheet_create": {
        "tool_id": "spreadsheet_create",
        "description": "Create a spreadsheet file with optional starter rows.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["file_path"],
            "properties": {
                "file_path": {"type": "string"},
                "sheet_name": {"type": "string"},
                "rows": {"type": "array"},
                "overwrite": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
    },
    "document_create": {
        "tool_id": "document_create",
        "description": "Create a Word document from structured sections.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["file_path", "title"],
            "properties": {
                "file_path": {"type": "string"},
                "title": {"type": "string"},
                "sections": {"type": "array"},
                "overwrite": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
    },
    "document_update": {
        "tool_id": "document_update",
        "description": "Append structured sections into an existing Word document.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["file_path", "title", "sections"],
            "properties": {
                "file_path": {"type": "string"},
                "title": {"type": "string"},
                "sections": {"type": "array"},
            },
            "additionalProperties": True,
        },
    },
    "presentation_create": {
        "tool_id": "presentation_create",
        "description": "Create a PowerPoint deck from structured slides.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["file_path", "title"],
            "properties": {
                "file_path": {"type": "string"},
                "title": {"type": "string"},
                "slides": {"type": "array"},
                "overwrite": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
    },
    "presentation_update": {
        "tool_id": "presentation_update",
        "description": "Append slides into an existing PowerPoint deck.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["file_path", "title", "slides"],
            "properties": {
                "file_path": {"type": "string"},
                "title": {"type": "string"},
                "slides": {"type": "array"},
            },
            "additionalProperties": True,
        },
    },
    "filesystem.read_write": {
        "tool_id": "filesystem.read_write",
        "description": "Read, write, or append text files inside the Gateway workspace folder.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["mode", "path"],
            "properties": {
                "mode": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
    },
    "browser_automation.interactive": {
        "tool_id": "browser_automation.interactive",
        "description": "Fetch a public web page and save HTML or extracted browser artifacts.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["mode", "url"],
            "properties": {
                "mode": {"type": "string"},
                "url": {"type": "string"},
                "path": {"type": "string"},
            },
            "additionalProperties": True,
        },
    },
    "screenshot.capture": {
        "tool_id": "screenshot.capture",
        "description": "Capture a screenshot into the Gateway artifact folder.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "capability": {"type": "string"},
            },
            "additionalProperties": True,
        },
    },
    "computer_control": {
        "tool_id": "computer_control",
        "description": "Control the local computer via OCR, mouse, keyboard, clipboard, notifications, AppleScript, and app launch helpers.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {"type": "string"},
                "region": {"type": "object"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "text": {"type": "string"},
                "script": {"type": "string"},
                "title": {"type": "string"},
                "message": {"type": "string"},
                "name_or_path": {"type": "string"},
            },
            "additionalProperties": True,
        },
    },
    "shell.execute": {
        "tool_id": "shell.execute",
        "description": "Run an allowlisted local command without shell interpolation.",
        "optional": True,
        "allowlist_roles": ["orion_operator"],
        "denylist_roles": [],
        "input_schema": {
            "type": "object",
            "required": [],
            "properties": {
                "command": {"type": "string"},
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
                "capability": {"type": "string"},
            },
            "additionalProperties": True,
        },
    },
}


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True


def _validate_json_schema(schema: Dict[str, Any], payload: Any, path: str = "$") -> List[str]:
    errors: List[str] = []
    expected_type = str(schema.get("type") or "").strip().lower()
    if expected_type and not _json_type_matches(payload, expected_type):
        return [f"{path}: expected {expected_type}"]

    if expected_type == "object":
        data = payload if isinstance(payload, dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        allow_extra = bool(schema.get("additionalProperties", True))

        for key in required:
            name = str(key)
            if name not in data:
                errors.append(f"{path}.{name}: required field missing")
        for key, value in data.items():
            if key in properties and isinstance(properties.get(key), dict):
                errors.extend(_validate_json_schema(properties[key], value, f"{path}.{key}"))
            elif not allow_extra:
                errors.append(f"{path}.{key}: additional property is not allowed")
    elif expected_type == "array":
        data = payload if isinstance(payload, list) else []
        items_schema = schema.get("items") if isinstance(schema.get("items"), dict) else None
        if items_schema:
            for idx, item in enumerate(data):
                errors.extend(_validate_json_schema(items_schema, item, f"{path}[{idx}]"))
    return errors


def validate_tool_contract(tool_id: str, role: str, payload: Any):
    _init()
    contract = TOOL_CONTRACTS.get(tool_id)
    if not isinstance(contract, dict):
        raise RuntimeError(f"Tool contract missing for '{tool_id}'.")
    if not is_tool_enabled(tool_id):
        raise RuntimeError(f"Tool '{tool_id}' is disabled.")
    allowlist = contract.get("allowlist_roles") if isinstance(contract.get("allowlist_roles"), list) else []
    denylist = contract.get("denylist_roles") if isinstance(contract.get("denylist_roles"), list) else []
    role_name = str(role or "").strip().lower()
    if any(str(item).strip().lower() == role_name for item in denylist):
        raise RuntimeError(f"Tool '{tool_id}' is denied for role '{role}'.")
    if allowlist and not any(str(item).strip().lower() == role_name for item in allowlist):
        raise RuntimeError(f"Tool '{tool_id}' is not allowlisted for role '{role}'.")
    schema = contract.get("input_schema") if isinstance(contract.get("input_schema"), dict) else {}
    errors = _validate_json_schema(schema, payload, "$")
    if errors:
        raise RuntimeError(f"Tool contract violation ({tool_id}): {'; '.join(errors[:3])}")


def validate_pack_tool_contracts(pack_id: str, result_data: Dict[str, Any], role: str = "orion_operator"):
    _init()
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    if pack_id == CUSTOMER_OPS_PACK_ID:
        follow_ups = outputs.get("follow_ups") if isinstance(outputs.get("follow_ups"), list) else []
        bookings = outputs.get("bookings") if isinstance(outputs.get("bookings"), list) else []
        for item in follow_ups:
            if isinstance(item, dict):
                validate_tool_contract("draft_email", role, item)
        for item in bookings:
            if isinstance(item, dict):
                validate_tool_contract("create_calendar_event", role, item)
        return
    if pack_id == WEEKLY_CONTENT_PACK_ID:
        content_plan = outputs.get("content_plan") if isinstance(outputs.get("content_plan"), list) else []
        for item in content_plan:
            if isinstance(item, dict):
                validate_tool_contract("publish_content", role, item)
        return
    if pack_id == SPREADSHEET_OPS_PACK_ID:
        actions = outputs.get("actions") if isinstance(outputs.get("actions"), list) else []
        for item in actions:
            if not isinstance(item, dict):
                continue
            tool_id = normalize_action_id(item.get("action"))
            if tool_id in {"spreadsheet_read", "spreadsheet_update", "spreadsheet_append", "spreadsheet_create"}:
                validate_tool_contract(tool_id, role, item)
        return
    if pack_id == DOCUMENT_STUDIO_PACK_ID:
        actions = outputs.get("actions") if isinstance(outputs.get("actions"), list) else []
        for item in actions:
            if not isinstance(item, dict):
                continue
            tool_id = normalize_action_id(item.get("action"))
            if tool_id in {"document_create", "document_update", "presentation_create", "presentation_update"}:
                validate_tool_contract(tool_id, role, item)
        return
    if pack_id == LOCAL_EXECUTION_PACK_ID:
        actions = outputs.get("actions") if isinstance(outputs.get("actions"), list) else []
        for item in actions:
            if not isinstance(item, dict):
                continue
            tool_id = normalize_action_id(item.get("action"))
            if tool_id in {"shell.execute", "filesystem.read_write", "browser_automation.interactive", "screenshot.capture", "computer_control"}:
                validate_tool_contract(tool_id, role, item)
        return

DEFAULT_BOOKING_SLOTS = [
    "Mon 10:00 AM",
    "Mon 2:00 PM",
    "Tue 11:30 AM",
    "Wed 3:00 PM",
]


def normalize_trust_mode(raw_value: Any) -> str:
    raw = str(raw_value or "").strip().lower()
    if not raw:
        return ORION_RUNTIME_TRUST_MODE_DEFAULT
    normalized = TRUST_MODE_ALIASES.get(raw, raw)
    if normalized in VALID_TRUST_MODES:
        return normalized
    return ORION_RUNTIME_TRUST_MODE_DEFAULT


def normalize_policy_mode(raw_value: Any) -> str:
    raw = str(raw_value or "").strip().lower()
    if not raw:
        return ORION_RUNTIME_POLICY_MODE_DEFAULT
    normalized = POLICY_MODE_ALIASES.get(raw, raw)
    if normalized in VALID_POLICY_MODES:
        return normalized
    return ORION_RUNTIME_POLICY_MODE_DEFAULT


def normalize_execution_target(raw_value: Any) -> str:
    raw = str(raw_value or "").strip().lower()
    if raw in {"local", "local-worker", "local_worker", "companion", "localcompanion", "local_companion"}:
        return EXECUTION_TARGET_LOCAL_COMPANION
    if raw in {
        "virtual_browser",
        "virtual-browser",
        "virtualbrowser",
        "virtual_desktop",
        "virtual-desktop",
        "virtualdesktop",
        "virtual_workspace",
        "virtual-workspace",
        "virtual_code_sandbox",
        "virtual-code-sandbox",
        "virtualcodesandbox",
        "sandbox",
    }:
        return EXECUTION_TARGET_CLOUD
    if raw in {"cloud", "server", "managed"}:
        return EXECUTION_TARGET_CLOUD
    if raw in {"auto", "hybrid", ""}:
        return EXECUTION_TARGET_AUTO
    return EXECUTION_TARGET_AUTO


def normalize_runtime_choice(raw_value: Any) -> str:
    raw = str(raw_value or "").strip().lower()
    if not raw:
        return ""
    normalized = RUNTIME_CHOICE_ALIASES.get(raw, raw)
    if normalized in VALID_RUNTIME_CHOICES:
        return normalized
    return ""


def runtime_choice_to_execution_target(runtime_choice: Any) -> str:
    normalized_choice = normalize_runtime_choice(runtime_choice)
    if normalized_choice == RUNTIME_CHOICE_LOCAL:
        return EXECUTION_TARGET_LOCAL_COMPANION
    if normalized_choice in {
        RUNTIME_CHOICE_VIRTUAL_BROWSER,
        RUNTIME_CHOICE_VIRTUAL_DESKTOP,
        RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
    }:
        return EXECUTION_TARGET_CLOUD
    return EXECUTION_TARGET_AUTO


def _runtime_contract_kind_from_choice(runtime_choice: Any) -> str:
    normalized_choice = normalize_runtime_choice(runtime_choice)
    if normalized_choice == RUNTIME_CHOICE_LOCAL:
        return RUNTIME_CONTRACT_KIND_LOCAL_GATEWAY
    return RUNTIME_CONTRACT_KIND_VIRTUAL_COMPUTER


def normalize_runtime_trust_zone(raw_value: Any, *, target: Optional[str] = None) -> str:
    raw = str(raw_value or "").strip().lower()
    normalized = RUNTIME_TRUST_ZONE_ALIASES.get(raw, raw)
    if normalized in VALID_RUNTIME_TRUST_ZONES:
        return normalized
    normalized_target = normalize_execution_target(target)
    if normalized_target == EXECUTION_TARGET_LOCAL_COMPANION:
        return RUNTIME_TRUST_ZONE_USER_OWNED_LOCAL
    return RUNTIME_TRUST_ZONE_SHARED_CLOUD


def normalize_elevated_mode(raw_value: Any, *, default: str = ELEVATED_MODE_ASK) -> str:
    raw = str(raw_value or "").strip().lower()
    if not raw:
        return default
    normalized = ELEVATED_MODE_ALIASES.get(raw, raw)
    if normalized in VALID_ELEVATED_MODES:
        return normalized
    return default


def normalize_action_type_token(raw_value: Any) -> str:
    raw = str(raw_value or "").strip().lower()
    normalized = ACTION_TYPE_ALIASES.get(raw, raw)
    if normalized in VALID_ACTION_TYPES:
        return normalized
    return ""


def normalize_action_type_list(raw_value: Any) -> List[str]:
    values = raw_value if isinstance(raw_value, list) else [raw_value]
    seen: Set[str] = set()
    normalized: List[str] = []
    for item in values:
        token = normalize_action_type_token(item)
        if token and token not in seen:
            seen.add(token)
            normalized.append(token)
    return normalized


def _parse_utc_datetime(raw_value: Any) -> Optional[datetime]:
    token = str(raw_value or "").strip()
    if not token:
        return None
    try:
        normalized = token.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def resolve_elevated_access(
    metadata: Optional[Dict[str, Any]] = None,
    *,
    selected_target: Optional[str] = None,
    policy_mode: Optional[str] = None,
) -> Dict[str, Any]:
    clean_metadata = metadata if isinstance(metadata, dict) else {}
    target = normalize_execution_target(
        selected_target
        or clean_metadata.get("execution_target_selected")
        or clean_metadata.get("execution_target")
    )
    raw_payload = clean_metadata.get("elevated")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    if isinstance(raw_payload, str):
        payload = {"mode": raw_payload}

    runtime_trust_zone = normalize_runtime_trust_zone(
        payload.get("runtime_trust_zone") or clean_metadata.get("runtime_trust_zone"),
        target=target,
    )
    explicit_policy_mode = normalize_policy_mode(policy_mode or clean_metadata.get("policy_mode"))
    raw_mode = payload.get("mode") or clean_metadata.get("elevated_mode")
    if str(raw_mode or "").strip():
        mode = normalize_elevated_mode(raw_mode)
        source = "metadata"
    elif explicit_policy_mode == POLICY_MODE_TRUSTED_FULL_ACCESS:
        mode = ELEVATED_MODE_FULL
        source = "policy_mode"
    elif runtime_trust_zone == RUNTIME_TRUST_ZONE_OWNED_DEDICATED_RUNTIME:
        mode = ELEVATED_MODE_FULL
        source = "runtime_trust_zone_default"
    else:
        mode = ELEVATED_MODE_ASK
        source = "default"

    granted_at_raw = payload.get("granted_at") or clean_metadata.get("elevated_granted_at")
    granted_at = _parse_utc_datetime(granted_at_raw)
    expires_at_raw = payload.get("expires_at") or clean_metadata.get("elevated_expires_at")
    expires_at = _parse_utc_datetime(expires_at_raw)
    ttl_raw = payload.get("ttl_seconds")
    if ttl_raw is None:
        ttl_raw = clean_metadata.get("elevated_ttl_seconds")
    try:
        ttl_seconds = max(0, int(ttl_raw)) if ttl_raw is not None else None
    except Exception:
        ttl_seconds = None
    if expires_at is None and granted_at is not None and ttl_seconds is not None:
        expires_at = granted_at + timedelta(seconds=ttl_seconds)

    now = datetime.now(timezone.utc)
    expired = bool(expires_at is not None and now >= expires_at)
    if expired:
        mode = ELEVATED_MODE_OFF

    allowed_action_classes = normalize_action_type_list(
        payload.get("allowed_action_classes") or clean_metadata.get("elevated_allowed_action_classes") or []
    )
    denied_action_classes = normalize_action_type_list(
        payload.get("denied_action_classes") or clean_metadata.get("elevated_denied_action_classes") or []
    )
    if mode == ELEVATED_MODE_FULL and not allowed_action_classes:
        allowed_action_classes = sorted(ELEVATED_FULL_DEFAULT_ACTION_TYPES)

    return {
        "mode": mode,
        "source": source,
        "active": mode != ELEVATED_MODE_OFF,
        "expired": expired,
        "runtime_trust_zone": runtime_trust_zone,
        "target": target,
        "policy_mode": explicit_policy_mode,
        "granted_at": granted_at.isoformat().replace("+00:00", "Z") if granted_at else None,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z") if expires_at else None,
        "ttl_seconds": ttl_seconds,
        "task_id": str(payload.get("task_id") or clean_metadata.get("elevated_task_id") or "").strip() or None,
        "granted_by": str(payload.get("granted_by") or clean_metadata.get("elevated_granted_by") or "").strip() or None,
        "reason": str(payload.get("reason") or clean_metadata.get("elevated_reason") or "").strip() or None,
        "allowed_action_classes": allowed_action_classes,
        "denied_action_classes": denied_action_classes,
    }


def decide_runtime_action_execution(
    classification: Dict[str, Any],
    *,
    target: Optional[str],
    policy_mode: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    clean_classification = classification if isinstance(classification, dict) else {}
    effective_target = normalize_execution_target(target)
    elevated = resolve_elevated_access(
        metadata,
        selected_target=effective_target,
        policy_mode=policy_mode,
    )
    action_type = str(clean_classification.get("action_type") or ACTION_TYPE_REVERSIBLE_WRITE).strip().lower()
    external_visibility = bool(clean_classification.get("external_visibility"))
    destructive_risk = bool(clean_classification.get("destructive_risk"))
    allowed_classes = set(elevated.get("allowed_action_classes") or [])
    denied_classes = set(elevated.get("denied_action_classes") or [])
    runtime_trust_zone = str(elevated.get("runtime_trust_zone") or RUNTIME_TRUST_ZONE_SHARED_CLOUD)

    if destructive_risk or action_type == ACTION_TYPE_DESTRUCTIVE:
        return {
            "execution_decision": "deny",
            "reason": "Destructive actions never auto-run.",
            "runtime_trust_zone": elevated.get("runtime_trust_zone"),
            "elevated": elevated,
        }

    if action_type in denied_classes:
        return {
            "execution_decision": "require_confirmation",
            "reason": f"Elevated access excludes {action_type} actions.",
            "runtime_trust_zone": elevated.get("runtime_trust_zone"),
            "elevated": elevated,
        }

    if elevated.get("mode") == ELEVATED_MODE_FULL and bool(elevated.get("active")):
        if action_type in ACTION_TYPES_REQUIRING_HARD_CONFIRMATION or external_visibility:
            return {
                "execution_decision": "require_confirmation",
                "reason": "External or irreversible actions still require approval.",
                "runtime_trust_zone": elevated.get("runtime_trust_zone"),
                "elevated": elevated,
            }
        if allowed_classes and action_type not in allowed_classes:
            return {
                "execution_decision": "require_confirmation",
                "reason": f"Elevated access is not scoped to {action_type} actions.",
                "runtime_trust_zone": elevated.get("runtime_trust_zone"),
                "elevated": elevated,
            }
        if action_type in {ACTION_TYPE_READ, ACTION_TYPE_DRAFT}:
            return {
                "execution_decision": "allow",
                "reason": "Elevated access allows safe read/draft actions.",
                "runtime_trust_zone": elevated.get("runtime_trust_zone"),
                "elevated": elevated,
            }
        if action_type == ACTION_TYPE_REVERSIBLE_WRITE and not external_visibility:
            return {
                "execution_decision": "allow",
                "reason": "Elevated access allows internal reversible actions.",
                "runtime_trust_zone": elevated.get("runtime_trust_zone"),
                "elevated": elevated,
            }
        return {
            "execution_decision": "require_confirmation",
            "reason": "This action falls outside elevated auto-approval.",
            "runtime_trust_zone": elevated.get("runtime_trust_zone"),
            "elevated": elevated,
        }

    if action_type in {ACTION_TYPE_READ, ACTION_TYPE_DRAFT}:
        return {
            "execution_decision": "allow",
            "reason": "Safe reads and drafts auto-run in default mode.",
            "runtime_trust_zone": runtime_trust_zone,
            "elevated": elevated,
        }
    if action_type == ACTION_TYPE_REVERSIBLE_WRITE and not external_visibility:
        if runtime_trust_zone == RUNTIME_TRUST_ZONE_SHARED_CLOUD:
            return {
                "execution_decision": "require_confirmation",
                "reason": "Default cloud mode still confirms internal write actions.",
                "runtime_trust_zone": runtime_trust_zone,
                "elevated": elevated,
            }
        return {
            "execution_decision": "allow",
            "reason": "Internal reversible actions auto-run in default mode.",
            "runtime_trust_zone": runtime_trust_zone,
            "elevated": elevated,
        }
    return {
        "execution_decision": "require_confirmation",
        "reason": "This action requires confirmation in default mode.",
        "runtime_trust_zone": runtime_trust_zone,
        "elevated": elevated,
    }


def resolve_runtime_policy_mode(
    metadata: Optional[Dict[str, Any]] = None,
    *,
    selected_target: Optional[str] = None,
) -> Dict[str, Any]:
    _init()
    clean_metadata = metadata if isinstance(metadata, dict) else {}
    target = normalize_execution_target(
        selected_target
        or clean_metadata.get("execution_target_selected")
        or clean_metadata.get("execution_target")
    )
    runtime_ids: List[str] = []
    seen: Set[str] = set()
    explicit_runtime_id = str(
        clean_metadata.get("runtime_id")
        or clean_metadata.get("execution_runtime_id")
        or clean_metadata.get("execution_target_runtime_id")
        or clean_metadata.get("execution_target_preferred_runtime_id")
        or ""
    ).strip()
    if explicit_runtime_id:
        runtime_ids.append(explicit_runtime_id)
        seen.add(explicit_runtime_id)
    for raw in clean_metadata.get("execution_target_matching_runtime_ids") or []:
        token = str(raw or "").strip()
        if token and token not in seen:
            seen.add(token)
            runtime_ids.append(token)
    resolved_modes: List[str] = []
    resolved_runtime_id: Optional[str] = None
    for runtime_id in runtime_ids:
        record = LOCAL_WORKER_REGISTRY.get(runtime_id) if isinstance(LOCAL_WORKER_REGISTRY.get(runtime_id), dict) else None
        if not isinstance(record, dict):
            continue
        resolved_mode = normalize_policy_mode(record.get("policy_mode"))
        if resolved_runtime_id is None:
            resolved_runtime_id = runtime_id
        resolved_modes.append(resolved_mode)
    if resolved_modes:
        unique_modes = sorted(set(resolved_modes))
        policy_mode = unique_modes[0] if len(unique_modes) == 1 else POLICY_MODE_LOCAL_DEFAULT
        source = "runtime_registration" if len(unique_modes) == 1 else "runtime_registration_mixed"
        return {
            "policy_mode": policy_mode,
            "runtime_id": resolved_runtime_id,
            "candidate_runtime_ids": runtime_ids,
            "source": source,
            "target": target,
        }
    explicit_policy_mode = clean_metadata.get("policy_mode")
    if explicit_policy_mode is not None:
        return {
            "policy_mode": normalize_policy_mode(explicit_policy_mode),
            "runtime_id": resolved_runtime_id,
            "candidate_runtime_ids": runtime_ids,
            "source": "metadata_fallback",
            "target": target,
        }
    return {
        "policy_mode": ORION_RUNTIME_POLICY_MODE_DEFAULT,
        "runtime_id": resolved_runtime_id,
        "candidate_runtime_ids": runtime_ids,
        "source": "runtime_default",
        "target": target,
    }


def classify_runtime_action(
    action_id: str,
    *,
    count: int = 1,
    target: Optional[str] = None,
) -> Dict[str, Any]:
    normalized = normalize_action_id(action_id) or str(action_id or "").strip().lower()
    contract = resolve_capability(normalized)
    classification_map: Dict[str, Dict[str, Any]] = {
        "external_research": {
            "action_type": ACTION_TYPE_READ,
            "target_system": "web",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
        },
        "spreadsheet_read": {
            "action_type": ACTION_TYPE_READ,
            "target_system": "spreadsheet",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
        },
        "draft_email": {
            "action_type": ACTION_TYPE_DRAFT,
            "target_system": "email",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
        },
        "send_message": {
            "action_type": ACTION_TYPE_EXTERNAL_SEND,
            "target_system": "messaging",
            "reversibility": False,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "create_calendar_event": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "calendar",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "publish_content": {
            "action_type": ACTION_TYPE_PUBLIC_PUBLISH,
            "target_system": "social",
            "reversibility": False,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "spreadsheet_update": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "spreadsheet",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "spreadsheet_append": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "spreadsheet",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "spreadsheet_create": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "spreadsheet",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "document_create": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "document",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "document_update": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "document",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "presentation_create": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "presentation",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "presentation_update": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "presentation",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "filesystem.read_write": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "local_workspace",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
        },
        "browser_automation.interactive": {
            "action_type": ACTION_TYPE_READ,
            "target_system": "browser",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
        },
        "screenshot.capture": {
            "action_type": ACTION_TYPE_READ,
            "target_system": "local_device",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
        },
        "computer_control": {
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "local_device",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
        },
        "shell.execute": {
            "action_type": ACTION_TYPE_DESTRUCTIVE,
            "target_system": "local_device",
            "reversibility": False,
            "external_visibility": False,
            "destructive_risk": True,
        },
        "http_request": {
            "action_type": ACTION_TYPE_EXTERNAL_SEND,
            "target_system": "external_system",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "connector.action.read": {
            "action_type": ACTION_TYPE_READ,
            "target_system": "external_system",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
        },
        "connector.action.write": {
            "action_type": ACTION_TYPE_EXTERNAL_SEND,
            "target_system": "external_system",
            "reversibility": True,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "connector.action.execute": {
            "action_type": ACTION_TYPE_EXTERNAL_SEND,
            "target_system": "external_system",
            "reversibility": False,
            "external_visibility": True,
            "destructive_risk": False,
        },
        "delete_files": {
            "action_type": ACTION_TYPE_DESTRUCTIVE,
            "target_system": "local_workspace",
            "reversibility": False,
            "external_visibility": False,
            "destructive_risk": True,
        },
        "delete_records": {
            "action_type": ACTION_TYPE_DESTRUCTIVE,
            "target_system": "external_system",
            "reversibility": False,
            "external_visibility": True,
            "destructive_risk": True,
        },
        "transfer_funds": {
            "action_type": ACTION_TYPE_DESTRUCTIVE,
            "target_system": "financial_system",
            "reversibility": False,
            "external_visibility": True,
            "destructive_risk": True,
        },
    }
    base = dict(
        classification_map.get(
            normalized,
            {
                "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
                "target_system": "external_system",
                "reversibility": True,
                "external_visibility": True,
                "destructive_risk": False,
            },
        )
    )
    if contract is not None:
        base["reversibility"] = bool(contract.reversible)
        base["requires_contract_approval"] = bool(contract.requires_approval)
    else:
        base["requires_contract_approval"] = False
    base["action_id"] = normalized
    base["bulk_risk"] = bool(int(count or 0) > 5)
    base["target"] = normalize_execution_target(target)
    return base


def _normalize_capability_ids(raw_items: Any) -> List[str]:
    seen: Set[str] = set()
    normalized: List[str] = []
    for item in raw_items or []:
        clean = str(item or "").strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized


def _predict_required_capabilities_from_metadata(metadata: Dict[str, Any]) -> List[str]:
    precheck = metadata.get("tool_policy_precheck") if isinstance(metadata.get("tool_policy_precheck"), dict) else {}
    if isinstance(precheck, dict):
        capability_ids = _normalize_capability_ids(precheck.get("capability_ids"))
        if capability_ids:
            return capability_ids

    pack_id = normalize_action_id(metadata.get("outcome_pack"))
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    if pack_id != LOCAL_EXECUTION_PACK_ID:
        return []

    operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
    capability_ids: List[str] = []
    if operations:
        for item in operations:
            if not isinstance(item, dict):
                continue
            capability_ids.extend(_normalize_capability_ids([item.get("capability")]))
    else:
        capability_ids.extend(_normalize_capability_ids([pack_inputs.get("capability")]))
    return _normalize_capability_ids(capability_ids)


def _local_runtime_capability_state(required_capabilities: List[str]) -> Dict[str, Any]:
    def _runtime_trust_rank(value: Any) -> int:
        normalized = str(value or "").strip().lower()
        if normalized == "verified":
            return 0
        if normalized in {"trusted", "healthy"}:
            return 1
        if normalized:
            return 2
        return 3

    def _runtime_selection_key(item: Dict[str, Any], required_set: Set[str]) -> tuple:
        capabilities = set(_normalize_capability_ids(item.get("capabilities")))
        is_busy = bool(item.get("current_run_id"))
        seconds_since_seen = item.get("seconds_since_seen")
        try:
            seen_rank = int(seconds_since_seen)
        except Exception:
            seen_rank = 999999
        extra_capabilities = len(capabilities - required_set)
        runtime_id = str(item.get("runtime_id") or item.get("worker_id") or "").strip()
        return (
            0 if not is_busy else 1,
            _runtime_trust_rank(item.get("trust_state")),
            extra_capabilities,
            seen_rank,
            runtime_id,
        )

    normalized_required = _normalize_capability_ids(required_capabilities)
    if not normalized_required:
        return {
            "required_capabilities": [],
            "matching_runtime_ids": [],
            "matching_runtime_count": 0,
            "available_runtime_ids": [],
            "available_runtime_count": 0,
            "busy_matching_runtime_ids": [],
            "busy_runtime_labels": [],
            "online_runtime_ids": [],
            "online_capabilities": [],
            "missing_capabilities": [],
            "waiting_for_runtime": False,
            "waiting_for_capacity": False,
            "preferred_runtime_id": None,
            "preferred_runtime_label": None,
            "preferred_runtime_reason": None,
            "queued_ahead_count": 0,
            "estimated_wait_band": "unknown",
        }

    from server_modules import local_queue

    payload = local_queue.handle_get_local_workers_status()
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    online_runtime_ids: List[str] = []
    matching_runtime_ids: List[str] = []
    matching_runtime_items: List[Dict[str, Any]] = []
    available_runtime_ids: List[str] = []
    busy_matching_runtime_ids: List[str] = []
    online_capability_set: Set[str] = set()
    required_set = set(normalized_required)

    for item in items:
        if not isinstance(item, dict):
            continue
        runtime_type = str(item.get("runtime_type") or "").strip().lower()
        if runtime_type not in {"local", "local_companion"}:
            continue
        if not bool(item.get("online")):
            continue
        runtime_id = str(item.get("runtime_id") or item.get("worker_id") or "").strip()
        if runtime_id:
            online_runtime_ids.append(runtime_id)
        capabilities = set(_normalize_capability_ids(item.get("capabilities")))
        online_capability_set.update(capabilities)
        if required_set.issubset(capabilities) and runtime_id:
            matching_runtime_items.append(dict(item))

    matching_runtime_items.sort(key=lambda item: _runtime_selection_key(item, required_set))
    matching_runtime_ids = [
        str(item.get("runtime_id") or item.get("worker_id") or "").strip()
        for item in matching_runtime_items
        if str(item.get("runtime_id") or item.get("worker_id") or "").strip()
    ]
    available_runtime_ids = [
        str(item.get("runtime_id") or item.get("worker_id") or "").strip()
        for item in matching_runtime_items
        if not bool(item.get("current_run_id")) and str(item.get("runtime_id") or item.get("worker_id") or "").strip()
    ]
    busy_matching_runtime_ids = [
        str(item.get("runtime_id") or item.get("worker_id") or "").strip()
        for item in matching_runtime_items
        if bool(item.get("current_run_id")) and str(item.get("runtime_id") or item.get("worker_id") or "").strip()
    ]
    busy_runtime_labels = [
        str(item.get("display_name") or item.get("runtime_id") or item.get("worker_id") or "").strip()
        for item in matching_runtime_items
        if bool(item.get("current_run_id")) and str(item.get("display_name") or item.get("runtime_id") or item.get("worker_id") or "").strip()
    ]
    preferred_runtime = matching_runtime_items[0] if matching_runtime_items else None
    preferred_runtime_id = (
        str(preferred_runtime.get("runtime_id") or preferred_runtime.get("worker_id") or "").strip()
        if isinstance(preferred_runtime, dict)
        else None
    )
    preferred_runtime_label = (
        str(preferred_runtime.get("display_name") or preferred_runtime_id or "").strip() or None
        if isinstance(preferred_runtime, dict)
        else None
    )
    preferred_runtime_reason = None
    if isinstance(preferred_runtime, dict) and preferred_runtime_id:
        is_busy = bool(preferred_runtime.get("current_run_id"))
        trust_state = str(preferred_runtime.get("trust_state") or "").strip().lower()
        if not is_busy and trust_state == "verified":
            preferred_runtime_reason = f"{preferred_runtime_label} is preferred because it is idle, verified, and already reports the required capabilities."
        elif not is_busy:
            preferred_runtime_reason = f"{preferred_runtime_label} is preferred because it is idle and reports the required capabilities."
        elif trust_state == "verified":
            preferred_runtime_reason = f"{preferred_runtime_label} is the best verified machine online for the required capabilities, but it is busy right now."
        else:
            preferred_runtime_reason = f"{preferred_runtime_label} is the best machine online for the required capabilities, but it is busy right now."

    missing_capabilities = sorted(required_set - online_capability_set)
    waiting_for_runtime = bool(normalized_required and not matching_runtime_ids)
    waiting_for_capacity = bool(normalized_required and matching_runtime_ids and not available_runtime_ids)
    with _server.LOCAL_QUEUE_LOCK:
        pending_run_ids = list(_server.LOCAL_PENDING_RUN_IDS)
    queue_pressure = local_queue._queue_pressure_for_runtime_group(
        pending_run_ids,
        matching_runtime_ids,
        preferred_runtime_id,
    )
    queued_ahead_count = int(queue_pressure.get("queued_ahead_count") or 0)
    estimated_wait_band = local_queue._capacity_wait_band(queued_ahead_count, len(busy_matching_runtime_ids)) if waiting_for_capacity else "unknown"
    return {
        "required_capabilities": normalized_required,
        "matching_runtime_ids": matching_runtime_ids,
        "matching_runtime_count": len(matching_runtime_ids),
        "available_runtime_ids": available_runtime_ids,
        "available_runtime_count": len(available_runtime_ids),
        "busy_matching_runtime_ids": busy_matching_runtime_ids,
        "busy_runtime_labels": busy_runtime_labels,
        "online_runtime_ids": online_runtime_ids,
        "online_capabilities": sorted(online_capability_set),
        "missing_capabilities": missing_capabilities,
        "waiting_for_runtime": waiting_for_runtime,
        "waiting_for_capacity": waiting_for_capacity,
        "preferred_runtime_id": preferred_runtime_id,
        "preferred_runtime_label": preferred_runtime_label,
        "preferred_runtime_reason": preferred_runtime_reason,
        "queued_ahead_count": queued_ahead_count,
        "estimated_wait_band": estimated_wait_band,
    }


def _local_runtime_pool_state() -> Dict[str, Any]:
    _init()
    from server_modules import local_queue

    def _runtime_trust_rank(value: Any) -> int:
        normalized = str(value or "").strip().lower()
        if normalized == "verified":
            return 0
        if normalized in {"trusted", "healthy"}:
            return 1
        if normalized:
            return 2
        return 3

    def _runtime_selection_key(item: Dict[str, Any]) -> tuple:
        is_busy = bool(item.get("current_run_id"))
        last_seen = str(item.get("last_seen_at") or "").strip()
        return (
            1 if is_busy else 0,
            _runtime_trust_rank(item.get("trust_state")),
            -(parse_positive_int(item.get("capability_count"), 0)),
            1 if not last_seen else 0,
            last_seen or "",
            str(item.get("display_name") or item.get("runtime_id") or item.get("worker_id") or "").strip(),
        )

    payload = local_queue.handle_get_local_workers_status()
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    online_items = [
        item for item in items
        if isinstance(item, dict)
        and bool(item.get("online"))
        and (
            normalize_execution_target(item.get("runtime_type")) == EXECUTION_TARGET_LOCAL_COMPANION
            or EXECUTION_TARGET_LOCAL_COMPANION in {
                normalize_execution_target(candidate)
                for candidate in (item.get("execution_targets") or [])
                if str(candidate or "").strip()
            }
        )
    ]
    ordered_online_items = sorted(online_items, key=_runtime_selection_key)
    available_items = [item for item in ordered_online_items if not bool(item.get("current_run_id"))]
    busy_items = [item for item in ordered_online_items if bool(item.get("current_run_id"))]
    online_runtime_ids = [
        str(item.get("runtime_id") or item.get("worker_id") or "").strip()
        for item in ordered_online_items
        if str(item.get("runtime_id") or item.get("worker_id") or "").strip()
    ]
    available_runtime_ids = [
        str(item.get("runtime_id") or item.get("worker_id") or "").strip()
        for item in available_items
        if str(item.get("runtime_id") or item.get("worker_id") or "").strip()
    ]
    busy_runtime_ids = [
        str(item.get("runtime_id") or item.get("worker_id") or "").strip()
        for item in busy_items
        if str(item.get("runtime_id") or item.get("worker_id") or "").strip()
    ]
    busy_runtime_labels = [
        str(item.get("display_name") or item.get("runtime_id") or item.get("worker_id") or "").strip()
        for item in busy_items
        if str(item.get("display_name") or item.get("runtime_id") or item.get("worker_id") or "").strip()
    ]
    preferred_runtime = ordered_online_items[0] if ordered_online_items else None
    preferred_runtime_id = (
        str(preferred_runtime.get("runtime_id") or preferred_runtime.get("worker_id") or "").strip()
        if isinstance(preferred_runtime, dict)
        else None
    )
    preferred_runtime_label = (
        str(preferred_runtime.get("display_name") or preferred_runtime_id or "").strip() or None
        if isinstance(preferred_runtime, dict)
        else None
    )
    preferred_runtime_reason = None
    if isinstance(preferred_runtime, dict) and preferred_runtime_id:
        is_busy = bool(preferred_runtime.get("current_run_id"))
        trust_state = str(preferred_runtime.get("trust_state") or "").strip().lower()
        if not is_busy and trust_state == "verified":
            preferred_runtime_reason = f"{preferred_runtime_label} is preferred because it is idle and verified."
        elif not is_busy:
            preferred_runtime_reason = f"{preferred_runtime_label} is preferred because it is idle."
        elif trust_state == "verified":
            preferred_runtime_reason = f"{preferred_runtime_label} is the best verified local machine online, but it is busy right now."
        else:
            preferred_runtime_reason = f"{preferred_runtime_label} is the best local machine online, but it is busy right now."
    with _server.LOCAL_QUEUE_LOCK:
        pending_run_ids = list(_server.LOCAL_PENDING_RUN_IDS)
    queue_pressure = local_queue._queue_pressure_for_runtime_group(
        pending_run_ids,
        online_runtime_ids,
        preferred_runtime_id,
    )
    queued_ahead_count = int(queue_pressure.get("queued_ahead_count") or 0)
    estimated_wait_band = (
        local_queue._capacity_wait_band(queued_ahead_count, len(busy_runtime_ids))
        if busy_runtime_ids and not available_runtime_ids
        else "unknown"
    )
    return {
        "online_runtime_ids": online_runtime_ids,
        "available_runtime_ids": available_runtime_ids,
        "busy_runtime_ids": busy_runtime_ids,
        "busy_runtime_labels": busy_runtime_labels,
        "online_runtime_count": len(online_runtime_ids),
        "available_runtime_count": len(available_runtime_ids),
        "busy_runtime_count": len(busy_runtime_ids),
        "preferred_runtime_id": preferred_runtime_id,
        "preferred_runtime_label": preferred_runtime_label,
        "preferred_runtime_reason": preferred_runtime_reason,
        "queued_ahead_count": queued_ahead_count,
        "estimated_wait_band": estimated_wait_band,
    }


def _metadata_has_connector_or_channel_work(metadata: Dict[str, Any]) -> bool:
    if str(metadata.get("connector_credential_id") or "").strip():
        return True
    if str(metadata.get("source_channel") or metadata.get("channel") or "").strip():
        return True
    if str(metadata.get("app_id") or "").strip():
        return True
    connected_connector_ids = metadata.get("connected_connector_ids") if isinstance(metadata.get("connected_connector_ids"), list) else []
    return any(str(item or "").strip() for item in connected_connector_ids)


def _metadata_has_local_artifact_hints(metadata: Dict[str, Any]) -> bool:
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    if any(str(pack_inputs.get(key) or "").strip() for key in ["file_path", "path", "cwd", "command"]):
        return True
    argv = pack_inputs.get("argv") if isinstance(pack_inputs.get("argv"), list) else []
    if any(str(item or "").strip() for item in argv):
        return True
    operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
    for item in operations:
        if not isinstance(item, dict):
            continue
        if any(str(item.get(key) or "").strip() for key in ["path", "file_path", "cwd", "command"]):
            return True
        nested_argv = item.get("argv") if isinstance(item.get("argv"), list) else []
        if any(str(arg or "").strip() for arg in nested_argv):
            return True
    return False


def _collect_pack_operation_tools(metadata: Dict[str, Any]) -> List[str]:
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    operations = pack_inputs.get("operations") if isinstance(pack_inputs.get("operations"), list) else []
    tools: List[str] = []
    for item in operations:
        if not isinstance(item, dict):
            continue
        token = normalize_action_id(item.get("tool") or item.get("action"))
        if token:
            tools.append(token)
    return tools


def _metadata_has_personal_local_context(metadata: Dict[str, Any]) -> bool:
    connection_mode = str(metadata.get("connection_mode") or "").strip().lower()
    if connection_mode in {"local", "local_companion"}:
        return True
    if bool(metadata.get("personal_mode")):
        return True
    browser_session_mode = str(metadata.get("browser_session_mode") or "").strip().lower()
    if browser_session_mode == "existing_session_attach":
        return True
    source_channel = str(metadata.get("source_channel") or metadata.get("channel") or "").strip().lower()
    if source_channel.startswith("personal_"):
        return True
    if _metadata_has_local_artifact_hints(metadata):
        return True
    return False


def _metadata_requests_browser_automation(metadata: Dict[str, Any]) -> bool:
    if bool(metadata.get("risky_web_automation")):
        return True
    operation_tools = _collect_pack_operation_tools(metadata)
    if "browser_automation.interactive" in operation_tools:
        browser_policy = (
            metadata.get("browser_automation_policy")
            if isinstance(metadata.get("browser_automation_policy"), dict)
            else {}
        )
        if bool(browser_policy.get("requires_approval")):
            return True
        if bool(browser_policy.get("privileged_actions")):
            return True
        if bool(metadata.get("browser_sensitive_access")):
            return True
        if bool(metadata.get("browser_reviewed_approval_required")):
            return True
        return True
    return False


def _metadata_requests_unknown_or_untrusted_web(metadata: Dict[str, Any]) -> bool:
    if bool(metadata.get("unknown_website")) or bool(metadata.get("untrusted_website")):
        return True
    if bool(metadata.get("untrusted_file")) or bool(metadata.get("untrusted_files")):
        return True
    if bool(metadata.get("scraping")) or bool(metadata.get("web_scraping")) or bool(metadata.get("scrape_job")):
        return True
    url = str(metadata.get("url") or metadata.get("target_url") or "").strip().lower()
    if url.startswith("http://"):
        return True
    if "pastebin" in url or "raw.githubusercontent.com" in url:
        return True
    return False


def _studio_runtime_options_catalog() -> List[Dict[str, Any]]:
    return [
        {
            "option_id": "managed_cloud",
            "label": "Managed Cloud",
            "runtime_supplier": "empyralis",
            "runtime_placement": "managed_cloud",
            "computer_automation_default": "disabled",
            "runtime_choice": None,
            "execution_target": EXECUTION_TARGET_CLOUD,
        },
        {
            "option_id": "hosted_hardware_pool",
            "label": "Empyralis Hosted Hardware",
            "runtime_supplier": "empyralis",
            "runtime_placement": "hosted_hardware_pool",
            "computer_automation_default": "disabled",
            "runtime_choice": None,
            "execution_target": EXECUTION_TARGET_CLOUD,
            "supports_checkpoint_resume": True,
        },
        {
            "option_id": "customer_local",
            "label": "Customer Local Runtime",
            "runtime_supplier": "customer",
            "runtime_placement": "customer_local",
            "computer_automation_default": "disabled",
            "runtime_choice": None,
            "execution_target": EXECUTION_TARGET_LOCAL_COMPANION,
        },
        {
            "option_id": "customer_hosted",
            "label": "Customer-Hosted Runtime",
            "runtime_supplier": "customer",
            "runtime_placement": "customer_hosted",
            "computer_automation_default": "disabled",
            "runtime_choice": None,
            "execution_target": EXECUTION_TARGET_CLOUD,
        },
        {
            "option_id": "computer_automation_add_on",
            "label": "Computer Automation Add-on",
            "runtime_placement": None,
            "computer_automation_default": "disabled",
            "runtime_choice": RUNTIME_CHOICE_VIRTUAL_BROWSER,
            "execution_target": EXECUTION_TARGET_CLOUD,
            "requires_explicit_enablement": True,
        },
    ]


def _studio_template_requirements(metadata: Dict[str, Any]) -> Dict[str, bool]:
    req = metadata.get("template_requirements") if isinstance(metadata.get("template_requirements"), dict) else {}
    runtime_req = (
        metadata.get("template_runtime_requirements")
        if isinstance(metadata.get("template_runtime_requirements"), dict)
        else {}
    )
    merged = {**runtime_req, **req}
    return {
        "needs_browser": bool(merged.get("needs_browser") or metadata.get("needs_browser")),
        "needs_files": bool(merged.get("needs_files") or metadata.get("needs_files")),
        "needs_website_login": bool(merged.get("needs_website_login") or metadata.get("needs_website_login")),
        "needs_payments": bool(merged.get("needs_payments") or metadata.get("needs_payments")),
        "needs_approval": bool(merged.get("needs_approval") or metadata.get("needs_approval")),
    }


def _metadata_requests_shop_assistant_virtual_browser(metadata: Dict[str, Any]) -> bool:
    template_slug = str(metadata.get("template_slug") or metadata.get("agent_template_slug") or "").strip().lower()
    agent_kind = str(metadata.get("agent_kind") or metadata.get("agent_type") or "").strip().lower()
    is_shop_assistant = template_slug == "shop-assistant" or agent_kind == "shop_assistant"
    if not is_shop_assistant:
        return False
    if bool(metadata.get("supplier_portal")) or bool(metadata.get("supplier_portal_login")):
        return True
    if bool(metadata.get("order_status_lookup")) or bool(metadata.get("order_status_workflow")):
        return True
    requirements = _studio_template_requirements(metadata)
    return bool(requirements.get("needs_browser") or requirements.get("needs_website_login"))


def _metadata_requests_code_or_data_job(metadata: Dict[str, Any]) -> bool:
    if bool(metadata.get("code_job")) or bool(metadata.get("data_job")):
        return True
    outcome_pack = normalize_action_id(metadata.get("outcome_pack"))
    if outcome_pack in {
        normalize_action_id(LOCAL_EXECUTION_PACK_ID),
        normalize_action_id(SPREADSHEET_OPS_PACK_ID),
        normalize_action_id(DOCUMENT_STUDIO_PACK_ID),
    }:
        return True
    operation_tools = _collect_pack_operation_tools(metadata)
    if any(tool in {"shell.execute", "filesystem.read_write"} for tool in operation_tools):
        return True
    return False


def _metadata_requests_enterprise_customer_workflow(metadata: Dict[str, Any]) -> bool:
    if bool(metadata.get("enterprise_workflow")) or bool(metadata.get("customer_workflow")):
        return True
    outcome_pack = normalize_action_id(metadata.get("outcome_pack"))
    if outcome_pack == normalize_action_id(CUSTOMER_OPS_PACK_ID):
        return True
    if _metadata_has_connector_or_channel_work(metadata):
        return True
    return False


def decide_runtime_choice(metadata: Dict[str, Any]) -> Dict[str, Any]:
    requested = normalize_runtime_choice(
        metadata.get("runtime_choice")
        or metadata.get("execution_runtime_choice")
    )
    def _override_warning(selected: str) -> str:
        if selected == RUNTIME_CHOICE_LOCAL and (
            _metadata_requests_browser_automation(metadata)
            or _metadata_requests_unknown_or_untrusted_web(metadata)
            or _metadata_requests_code_or_data_job(metadata)
            or _metadata_requests_enterprise_customer_workflow(metadata)
        ):
            return "Local override conflicts with virtual-first policy for risky or untrusted workloads."
        if selected != RUNTIME_CHOICE_LOCAL and _metadata_has_personal_local_context(metadata):
            return "Virtual override conflicts with local-first policy for personal device context."
        return ""
    if requested:
        warning = _override_warning(requested)
        return {
            "requested": requested,
            "selected": requested,
            "reason": "Runtime choice was explicitly requested in metadata.",
            "source": "explicit",
            "override_requested": True,
            "policy_warning": warning or None,
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": _studio_template_requirements(metadata),
        }

    if _metadata_has_personal_local_context(metadata):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_LOCAL,
            "reason": "Task uses personal apps/files/session context and stays local by default.",
            "source": "router_rule_personal_local",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": _studio_template_requirements(metadata),
        }

    template_requirements = _studio_template_requirements(metadata)
    if _metadata_requests_shop_assistant_virtual_browser(metadata):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_VIRTUAL_BROWSER,
            "reason": "Shop Assistant supplier portal/order-status workflow runs in Cloud Computer without touching the owner's machine.",
            "source": "router_rule_shop_assistant_virtual_browser",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": template_requirements,
        }
    if template_requirements.get("needs_website_login"):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_VIRTUAL_BROWSER,
            "reason": "Template needs website login; routing to an isolated Cloud Computer browser.",
            "source": "router_rule_template_website_login",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": template_requirements,
        }
    if template_requirements.get("needs_browser"):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_VIRTUAL_BROWSER,
            "reason": "Template declares browser dependency; routing to an isolated Cloud Computer browser.",
            "source": "router_rule_template_browser",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": template_requirements,
        }
    if template_requirements.get("needs_files"):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
            "reason": "Template declares files dependency; routing to a Cloud Computer code sandbox.",
            "source": "router_rule_template_files",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": template_requirements,
        }
    if template_requirements.get("needs_payments"):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_VIRTUAL_DESKTOP,
            "reason": "Template declares payments dependency; routing to a dedicated Cloud Computer desktop lane.",
            "source": "router_rule_template_payments",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": template_requirements,
        }

    if _metadata_requests_browser_automation(metadata):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_VIRTUAL_BROWSER,
            "reason": "Risky or privileged browser automation is routed to Cloud Computer isolation.",
            "source": "router_rule_virtual_browser",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": template_requirements,
        }

    if _metadata_requests_unknown_or_untrusted_web(metadata):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_VIRTUAL_BROWSER,
            "reason": "Unknown/untrusted websites, files, and scraping routes default to Cloud Computer isolation.",
            "source": "router_rule_virtual_browser_untrusted",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": template_requirements,
        }

    if _metadata_requests_code_or_data_job(metadata):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
            "reason": "Code/data style work is routed to Cloud Computer code sandbox execution.",
            "source": "router_rule_virtual_code_sandbox",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": template_requirements,
        }

    if _metadata_requests_enterprise_customer_workflow(metadata):
        return {
            "requested": "",
            "selected": RUNTIME_CHOICE_VIRTUAL_DESKTOP,
            "reason": "Enterprise/customer workflow is routed to dedicated virtual workspace class.",
            "source": "router_rule_virtual_desktop",
            "studio_runtime_options": _studio_runtime_options_catalog(),
            "studio_template_requirements": template_requirements,
        }

    return {
        "requested": "",
        "selected": RUNTIME_CHOICE_LOCAL,
        "reason": "No virtual runtime routing rule matched; defaulting to local computer.",
        "source": "router_rule_default_local",
        "studio_runtime_options": _studio_runtime_options_catalog(),
        "studio_template_requirements": template_requirements,
    }


def _auto_cloud_capacity_fallback_allowed(metadata: Dict[str, Any]) -> tuple[bool, str]:
    trust_mode = normalize_trust_mode(metadata.get("trust_mode"))
    if trust_mode != TRUST_MODE_AUTO:
        return False, "Automatic route keeps local execution for guarded or stricter trust modes."

    if _metadata_has_connector_or_channel_work(metadata):
        return False, "Automatic route keeps local execution for connector-backed or channel-bound work."

    if _metadata_has_local_artifact_hints(metadata):
        return False, "Automatic route keeps local execution for file-backed or command-backed work."

    outcome_pack = normalize_action_id(metadata.get("outcome_pack"))
    if outcome_pack in {
        normalize_action_id(LOCAL_EXECUTION_PACK_ID),
        normalize_action_id(SPREADSHEET_OPS_PACK_ID),
        normalize_action_id(DOCUMENT_STUDIO_PACK_ID),
        normalize_action_id(CUSTOMER_OPS_PACK_ID),
    }:
        return False, "Automatic route keeps local execution for this task type."

    return True, "Local machines are busy, so Hekor can use cloud runtime for this task."


def _local_fallback_risk_allows_runtime(metadata: Dict[str, Any]) -> tuple[bool, str]:
    if _metadata_requests_enterprise_customer_workflow(metadata):
        return False, "Local fallback blocked for enterprise/customer workflow route."
    if _metadata_requests_code_or_data_job(metadata):
        return False, "Local fallback blocked for sandboxed code/data route."
    if _metadata_requests_browser_automation(metadata):
        return False, "Local fallback blocked for risky browser automation route."
    if _metadata_requests_unknown_or_untrusted_web(metadata):
        return False, "Local fallback blocked for unknown/untrusted web/file route."
    return True, "Virtual runtime unavailable; local fallback is allowed for low-risk route."


def apply_execution_route_metadata(metadata: Dict[str, Any], route: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        return metadata
    metadata["execution_target_requested"] = route.get("requested")
    metadata["execution_target_selected"] = route.get("selected")
    metadata["execution_target_reason"] = route.get("reason")
    if route.get("fallback"):
        metadata["execution_target_fallback"] = route.get("fallback")
    else:
        metadata.pop("execution_target_fallback", None)

    if route.get("required_capabilities"):
        metadata["execution_target_required_capabilities"] = list(route.get("required_capabilities") or [])
    else:
        metadata.pop("execution_target_required_capabilities", None)
    if route.get("missing_capabilities"):
        metadata["execution_target_missing_capabilities"] = list(route.get("missing_capabilities") or [])
    else:
        metadata.pop("execution_target_missing_capabilities", None)
    if route.get("matching_runtime_ids"):
        metadata["execution_target_matching_runtime_ids"] = list(route.get("matching_runtime_ids") or [])
    else:
        metadata.pop("execution_target_matching_runtime_ids", None)
    if route.get("available_runtime_ids"):
        metadata["execution_target_available_runtime_ids"] = list(route.get("available_runtime_ids") or [])
    else:
        metadata.pop("execution_target_available_runtime_ids", None)
    if route.get("busy_matching_runtime_ids"):
        metadata["execution_target_busy_runtime_ids"] = list(route.get("busy_matching_runtime_ids") or [])
    else:
        metadata.pop("execution_target_busy_runtime_ids", None)
    if route.get("preferred_runtime_id"):
        metadata["execution_target_preferred_runtime_id"] = route.get("preferred_runtime_id")
    else:
        metadata.pop("execution_target_preferred_runtime_id", None)
    if route.get("preferred_runtime_label"):
        metadata["execution_target_preferred_runtime_label"] = route.get("preferred_runtime_label")
    else:
        metadata.pop("execution_target_preferred_runtime_label", None)
    if route.get("preferred_runtime_reason"):
        metadata["execution_target_preferred_runtime_reason"] = route.get("preferred_runtime_reason")
    else:
        metadata.pop("execution_target_preferred_runtime_reason", None)
    if route.get("busy_runtime_labels"):
        metadata["execution_target_busy_runtime_labels"] = list(route.get("busy_runtime_labels") or [])
    else:
        metadata.pop("execution_target_busy_runtime_labels", None)
    metadata["execution_target_queued_ahead_count"] = int(route.get("queued_ahead_count") or 0)
    metadata["execution_target_estimated_wait_band"] = str(route.get("estimated_wait_band") or "unknown")

    metadata["execution_target_waiting_for_runtime"] = bool(route.get("waiting_for_runtime"))
    metadata["execution_target_waiting_for_capacity"] = bool(route.get("waiting_for_capacity"))
    if route.get("runtime_choice_requested"):
        metadata["runtime_choice_requested"] = route.get("runtime_choice_requested")
    else:
        metadata.pop("runtime_choice_requested", None)
    metadata["runtime_choice_selected"] = str(route.get("runtime_choice_selected") or RUNTIME_CHOICE_LOCAL)
    metadata["runtime_choice_reason"] = str(route.get("runtime_choice_reason") or "")
    metadata["runtime_choice_source"] = str(route.get("runtime_choice_source") or "")
    metadata["runtime_choice_applied"] = bool(route.get("runtime_choice_applied"))
    if route.get("runtime_provider_requested"):
        metadata["runtime_provider_requested"] = str(route.get("runtime_provider_requested"))
    else:
        metadata.pop("runtime_provider_requested", None)
    metadata["runtime_choice_override_requested"] = bool(route.get("runtime_choice_override_requested"))
    if route.get("runtime_choice_policy_warning"):
        metadata["runtime_choice_policy_warning"] = str(route.get("runtime_choice_policy_warning"))
    else:
        metadata.pop("runtime_choice_policy_warning", None)
    if route.get("studio_runtime_options"):
        metadata["studio_runtime_options"] = list(route.get("studio_runtime_options") or [])
    else:
        metadata.pop("studio_runtime_options", None)
    if route.get("studio_template_requirements"):
        metadata["studio_template_requirements"] = dict(route.get("studio_template_requirements") or {})
    else:
        metadata.pop("studio_template_requirements", None)
    metadata["runtime_contract_interface"] = str(route.get("runtime_contract_interface") or RUNTIME_CONTRACT_INTERFACE_ID)
    metadata["runtime_contract_kind"] = str(
        route.get("runtime_contract_kind")
        or _runtime_contract_kind_from_choice(route.get("runtime_choice_selected"))
    )
    metadata["runtime_contract_methods"] = list(route.get("runtime_contract_methods") or RUNTIME_CONTRACT_METHODS)
    metadata["runtime_contract_states"] = list(route.get("runtime_contract_states") or RUNTIME_CONTRACT_STATES)
    return metadata


def decide_execution_target(metadata: Dict[str, Any], schedule_id: Optional[str] = None) -> Dict[str, Any]:
    _init()
    requested = normalize_execution_target(metadata.get("execution_target"))
    runtime_choice = decide_runtime_choice(metadata if isinstance(metadata, dict) else {})
    runtime_provider_requested = (
        str(metadata.get("runtime_provider_id") or metadata.get("virtual_provider_id") or "").strip() or None
    )
    runtime_choice_applied = False
    if requested == EXECUTION_TARGET_AUTO:
        requested_from_choice = runtime_choice_to_execution_target(runtime_choice.get("selected"))
        if requested_from_choice == EXECUTION_TARGET_AUTO:
            requested_from_choice = normalize_execution_target(runtime_choice.get("selected"))
        if requested_from_choice in {EXECUTION_TARGET_LOCAL_COMPANION, EXECUTION_TARGET_CLOUD}:
            requested = requested_from_choice
            runtime_choice_applied = True
    if requested == EXECUTION_TARGET_AUTO:
        # Respect explicit connection-mode intent if present.
        connection_mode = str(metadata.get("connection_mode") or "").strip().lower()
        if connection_mode in {"local_companion", "local"}:
            requested = EXECUTION_TARGET_LOCAL_COMPANION

    selected = requested
    reason = ""
    fallback = None
    capability_state = _local_runtime_capability_state(_predict_required_capabilities_from_metadata(metadata))
    required_capabilities = list(capability_state.get("required_capabilities") or [])
    missing_capabilities = list(capability_state.get("missing_capabilities") or [])
    matching_runtime_ids = list(capability_state.get("matching_runtime_ids") or [])
    available_runtime_ids = list(capability_state.get("available_runtime_ids") or [])
    busy_matching_runtime_ids = list(capability_state.get("busy_matching_runtime_ids") or [])
    waiting_for_runtime = bool(capability_state.get("waiting_for_runtime"))
    waiting_for_capacity = bool(capability_state.get("waiting_for_capacity"))
    preferred_runtime_id = str(capability_state.get("preferred_runtime_id") or "").strip() or None
    preferred_runtime_label = str(capability_state.get("preferred_runtime_label") or "").strip() or None
    preferred_runtime_reason = str(capability_state.get("preferred_runtime_reason") or "").strip() or None
    busy_runtime_labels = list(capability_state.get("busy_runtime_labels") or [])
    queued_ahead_count = int(capability_state.get("queued_ahead_count") or 0)
    estimated_wait_band = str(capability_state.get("estimated_wait_band") or "unknown")
    local_pool = _local_runtime_pool_state() if not required_capabilities else {}

    if required_capabilities:
        capability_text = ", ".join(required_capabilities)
        missing_text = ", ".join(missing_capabilities or required_capabilities)
        selected = EXECUTION_TARGET_LOCAL_COMPANION
        if available_runtime_ids:
            preferred_text = preferred_runtime_label or "a capable local runtime"
            if requested == EXECUTION_TARGET_CLOUD:
                reason = f"Run requires local machine capabilities ({capability_text}) and will use {preferred_text}."
                fallback = "Cloud runtime cannot satisfy these local capabilities; using local machine instead."
            elif requested == EXECUTION_TARGET_LOCAL_COMPANION:
                reason = f"Run requires local machine capabilities ({capability_text}) and will use {preferred_text}."
            else:
                reason = f"Run requires local machine capabilities ({capability_text}) and will use {preferred_text}."
        elif matching_runtime_ids:
            preferred_text = preferred_runtime_label or "a capable local runtime"
            if schedule_id:
                reason = f"Scheduled run requires local machine capabilities ({capability_text}) and is waiting for machine capacity on {preferred_text}."
            else:
                reason = f"Run requires local machine capabilities ({capability_text}) and is waiting for machine capacity on {preferred_text}."
            fallback = "Capable local machines are online, but they are currently busy."
            if queued_ahead_count > 0:
                fallback = f"{fallback} {queued_ahead_count} similar local run{'s are' if queued_ahead_count != 1 else ' is'} ahead in the queue."
        else:
            if schedule_id:
                reason = f"Scheduled run requires local machine capabilities ({capability_text}) and is waiting for a capable machine."
            else:
                reason = f"Run requires local machine capabilities ({capability_text}) and is waiting for a machine that reports: {missing_text}."
            fallback = f"No online local machine currently reports required capabilities: {missing_text}."
        return {
            "requested": requested,
            "selected": selected,
            "reason": reason,
            "fallback": fallback,
            "runtime_choice_requested": runtime_choice.get("requested"),
            "runtime_choice_selected": runtime_choice.get("selected"),
            "runtime_choice_reason": runtime_choice.get("reason"),
            "runtime_choice_source": runtime_choice.get("source"),
            "runtime_choice_applied": runtime_choice_applied,
            "runtime_choice_override_requested": bool(runtime_choice.get("override_requested")),
            "runtime_choice_policy_warning": str(runtime_choice.get("policy_warning") or ""),
            "studio_runtime_options": list(runtime_choice.get("studio_runtime_options") or []),
            "studio_template_requirements": dict(runtime_choice.get("studio_template_requirements") or {}),
            "runtime_provider_requested": runtime_provider_requested,
            "runtime_contract_interface": RUNTIME_CONTRACT_INTERFACE_ID,
            "runtime_contract_kind": _runtime_contract_kind_from_choice(runtime_choice.get("selected")),
            "runtime_contract_methods": list(RUNTIME_CONTRACT_METHODS),
            "runtime_contract_states": list(RUNTIME_CONTRACT_STATES),
            "local_companion_enabled": ORION_LOCAL_COMPANION_ENABLED,
            "required_capabilities": required_capabilities,
            "missing_capabilities": missing_capabilities,
            "matching_runtime_ids": matching_runtime_ids,
            "matching_runtime_count": int(capability_state.get("matching_runtime_count") or 0),
            "available_runtime_ids": available_runtime_ids,
            "available_runtime_count": int(capability_state.get("available_runtime_count") or 0),
            "busy_matching_runtime_ids": busy_matching_runtime_ids,
            "waiting_for_runtime": waiting_for_runtime,
            "waiting_for_capacity": waiting_for_capacity,
            "preferred_runtime_id": preferred_runtime_id,
            "preferred_runtime_label": preferred_runtime_label,
            "preferred_runtime_reason": preferred_runtime_reason,
            "busy_runtime_labels": busy_runtime_labels,
            "queued_ahead_count": queued_ahead_count,
            "estimated_wait_band": estimated_wait_band,
        }

    if selected == EXECUTION_TARGET_AUTO:
        if schedule_id or str(metadata.get("source") or "").strip().lower() in {"weekly_scheduler", "scheduled"}:
            selected = EXECUTION_TARGET_CLOUD
            reason = "Scheduled runs default to always-on cloud execution."
        else:
            if ORION_LOCAL_COMPANION_ENABLED:
                local_online_ids = list(local_pool.get("online_runtime_ids") or [])
                local_available_ids = list(local_pool.get("available_runtime_ids") or [])
                local_busy_ids = list(local_pool.get("busy_runtime_ids") or [])
                local_busy_labels = list(local_pool.get("busy_runtime_labels") or [])
                local_preferred_runtime_id = str(local_pool.get("preferred_runtime_id") or "").strip() or None
                local_preferred_runtime_label = str(local_pool.get("preferred_runtime_label") or "").strip() or None
                local_preferred_runtime_reason = str(local_pool.get("preferred_runtime_reason") or "").strip() or None
                local_queued_ahead_count = int(local_pool.get("queued_ahead_count") or 0)
                local_estimated_wait_band = str(local_pool.get("estimated_wait_band") or "unknown")
                if local_available_ids:
                    selected = EXECUTION_TARGET_LOCAL_COMPANION
                    reason = (
                        f"Automatic route will use {local_preferred_runtime_label or 'a local machine'} because local execution is available now."
                    )
                    matching_runtime_ids = local_online_ids
                    available_runtime_ids = local_available_ids
                    busy_matching_runtime_ids = local_busy_ids
                    waiting_for_runtime = False
                    waiting_for_capacity = False
                    preferred_runtime_id = local_preferred_runtime_id
                    preferred_runtime_label = local_preferred_runtime_label
                    preferred_runtime_reason = local_preferred_runtime_reason
                    busy_runtime_labels = local_busy_labels
                    queued_ahead_count = local_queued_ahead_count
                    estimated_wait_band = local_estimated_wait_band
                elif local_online_ids:
                    can_reroute_to_cloud, reroute_reason = _auto_cloud_capacity_fallback_allowed(metadata)
                    if can_reroute_to_cloud:
                        selected = EXECUTION_TARGET_CLOUD
                        reason = reroute_reason
                        fallback = (
                            f"{local_preferred_runtime_label or 'The local machine'} is busy right now, so Hekor is starting this in cloud runtime."
                        )
                    else:
                        selected = EXECUTION_TARGET_LOCAL_COMPANION
                        reason = (
                            f"Automatic route prefers local execution and is waiting for capacity on {local_preferred_runtime_label or 'a local machine'}."
                        )
                        fallback = reroute_reason
                        matching_runtime_ids = local_online_ids
                        available_runtime_ids = local_available_ids
                        busy_matching_runtime_ids = local_busy_ids
                        waiting_for_runtime = False
                        waiting_for_capacity = True
                        preferred_runtime_id = local_preferred_runtime_id
                        preferred_runtime_label = local_preferred_runtime_label
                        preferred_runtime_reason = local_preferred_runtime_reason
                        busy_runtime_labels = local_busy_labels
                        queued_ahead_count = local_queued_ahead_count
                        estimated_wait_band = local_estimated_wait_band
                        if queued_ahead_count > 0:
                            fallback = f"{fallback} {queued_ahead_count} similar local run{'s are' if queued_ahead_count != 1 else ' is'} ahead in the queue."
                else:
                    selected = EXECUTION_TARGET_CLOUD
                    reason = "Default runtime route is cloud because no local machine is online."
                    fallback = "Bring a local machine online if you want automatic tasks to prefer local execution."
            else:
                selected = EXECUTION_TARGET_CLOUD
                reason = "Default runtime route is cloud because Gateway is unavailable."
    elif selected == EXECUTION_TARGET_LOCAL_COMPANION:
        if schedule_id:
            selected = EXECUTION_TARGET_CLOUD
            reason = "Scheduled runs require cloud reliability."
            fallback = "Gateway route requested, but schedules run in cloud mode."
        else:
            if ORION_LOCAL_COMPANION_ENABLED:
                selected = EXECUTION_TARGET_LOCAL_COMPANION
                reason = "Run requested on Gateway."
            else:
                can_fallback_to_cloud, fallback_reason = _auto_cloud_capacity_fallback_allowed(metadata)
                if can_fallback_to_cloud:
                    selected = EXECUTION_TARGET_CLOUD
                    reason = "Gateway requested but not available; using cloud fallback."
                    fallback = "Gateway is not enabled on this workspace; using cloud fallback."
                else:
                    selected = EXECUTION_TARGET_LOCAL_COMPANION
                    reason = "Gateway requested but unavailable; fallback to cloud is blocked by policy."
                    fallback = fallback_reason
                    waiting_for_runtime = True
                    waiting_for_capacity = False
    else:
        reason = "Run requested in cloud mode."
        virtual_runtime_unavailable = bool(
            metadata.get("virtual_runtime_unavailable")
            or metadata.get("cloud_runtime_unavailable")
            or metadata.get("virtual_runtime_down")
        )
        if virtual_runtime_unavailable:
            allow_local_fallback, local_fallback_reason = _local_fallback_risk_allows_runtime(metadata)
            if allow_local_fallback:
                selected = EXECUTION_TARGET_LOCAL_COMPANION
                reason = "Virtual runtime unavailable; local fallback selected for low-risk route."
                fallback = local_fallback_reason
            else:
                selected = EXECUTION_TARGET_CLOUD
                reason = "Virtual runtime unavailable and local fallback blocked by risk policy."
                fallback = local_fallback_reason
                waiting_for_runtime = True
                waiting_for_capacity = False

    return {
        "requested": requested,
        "selected": selected,
        "reason": reason,
        "fallback": fallback,
        "runtime_choice_requested": runtime_choice.get("requested"),
        "runtime_choice_selected": runtime_choice.get("selected"),
        "runtime_choice_reason": runtime_choice.get("reason"),
        "runtime_choice_source": runtime_choice.get("source"),
        "runtime_choice_applied": runtime_choice_applied,
        "runtime_choice_override_requested": bool(runtime_choice.get("override_requested")),
        "runtime_choice_policy_warning": str(runtime_choice.get("policy_warning") or ""),
        "studio_runtime_options": list(runtime_choice.get("studio_runtime_options") or []),
        "studio_template_requirements": dict(runtime_choice.get("studio_template_requirements") or {}),
        "runtime_provider_requested": runtime_provider_requested,
        "runtime_contract_interface": RUNTIME_CONTRACT_INTERFACE_ID,
        "runtime_contract_kind": _runtime_contract_kind_from_choice(runtime_choice.get("selected")),
        "runtime_contract_methods": list(RUNTIME_CONTRACT_METHODS),
        "runtime_contract_states": list(RUNTIME_CONTRACT_STATES),
        "local_companion_enabled": ORION_LOCAL_COMPANION_ENABLED,
        "required_capabilities": required_capabilities,
        "missing_capabilities": missing_capabilities,
        "matching_runtime_ids": matching_runtime_ids,
        "matching_runtime_count": int(capability_state.get("matching_runtime_count") or 0),
        "available_runtime_ids": available_runtime_ids,
        "available_runtime_count": int(capability_state.get("available_runtime_count") or 0),
        "busy_matching_runtime_ids": busy_matching_runtime_ids,
        "waiting_for_runtime": waiting_for_runtime,
        "waiting_for_capacity": waiting_for_capacity,
        "preferred_runtime_id": preferred_runtime_id,
        "preferred_runtime_label": preferred_runtime_label,
        "preferred_runtime_reason": preferred_runtime_reason,
        "busy_runtime_labels": busy_runtime_labels,
        "queued_ahead_count": queued_ahead_count,
        "estimated_wait_band": estimated_wait_band,
    }


def parse_positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed >= 0 else default


def default_pack_next_steps(pack_id: str) -> List[str]:
    if pack_id == WEEKLY_CONTENT_PACK_ID:
        return [
            "Approve top posts and schedule publishing.",
            "Assign final edits by channel owner.",
            "Review engagement after 7 days and refine.",
        ]
    if pack_id == COMPETITOR_BRIEF_PACK_ID:
        return [
            "Prioritize one response play for each high-threat competitor.",
            "Update your top offer and landing copy this week.",
            "Re-run this brief weekly to track movement.",
        ]
    if pack_id == SPREADSHEET_OPS_PACK_ID:
        return [
            "Verify preview rows before sharing the file.",
            "Approve write operations only after checking target path.",
            "Re-run with update/append if additional rows are needed.",
        ]
    if pack_id == DOCUMENT_STUDIO_PACK_ID:
        return [
            "Open the generated Word or PowerPoint file and verify the structure.",
            "Use update mode to append new sections or slides instead of recreating the whole file.",
            "Keep OneDrive paths explicit when collaborating through Microsoft 365.",
        ]
    if pack_id == LOCAL_EXECUTION_PACK_ID:
        return [
            "Review generated artifacts before trusting the result.",
            "Keep local execution inside the configured root.",
            "Use explicit allow-actions before enabling shell commands.",
        ]
    return [
        "Review urgent inbox items first.",
        "Send follow-up drafts for hot/warm leads.",
        "Confirm pending booking slots with leads.",
    ]


def normalize_pack_result(pack_id: str, raw_result: Any) -> Dict[str, Any]:
    if not isinstance(raw_result, dict):
        raise RuntimeError("Outcome pack returned an invalid result payload.")

    result = dict(raw_result)
    outputs = result.get("outputs")
    if not isinstance(outputs, dict):
        outputs = {}
    outputs["outbound_actions"] = parse_positive_int(outputs.get("outbound_actions"), 0)
    outputs["urgent_count"] = parse_positive_int(outputs.get("urgent_count"), 0)
    result["outputs"] = outputs

    result["pack_id"] = str(result.get("pack_id") or pack_id).strip().lower() or pack_id
    result["summary"] = str(result.get("summary") or f"{pack_id} completed.").strip()
    result["generated_at"] = str(result.get("generated_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    raw_steps = result.get("next_steps")
    if isinstance(raw_steps, list):
        next_steps = [str(step).strip() for step in raw_steps if str(step).strip()]
    else:
        next_steps = []
    if not next_steps:
        next_steps = default_pack_next_steps(pack_id)
    result["next_steps"] = next_steps

    return result


def _approval_rules_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    defaults = {
        "max_outbound_auto": 0,
        "max_urgent_auto": 0,
        "sensitive_keywords": ["payment", "refund", "delete", "terminate", "password", "credential", "transfer"],
        "approve_on_connector_actions": True,
    }
    raw_rules = metadata.get("approval_rules") if isinstance(metadata.get("approval_rules"), dict) else {}
    rules = dict(defaults)
    if isinstance(raw_rules, dict):
        if "max_outbound_auto" in raw_rules:
            rules["max_outbound_auto"] = parse_positive_int(raw_rules.get("max_outbound_auto"), defaults["max_outbound_auto"])
        if "max_urgent_auto" in raw_rules:
            rules["max_urgent_auto"] = parse_positive_int(raw_rules.get("max_urgent_auto"), defaults["max_urgent_auto"])
        if "sensitive_keywords" in raw_rules and isinstance(raw_rules.get("sensitive_keywords"), list):
            keywords = []
            for item in raw_rules.get("sensitive_keywords", []):
                value = str(item or "").strip().lower()
                if value:
                    keywords.append(value)
            if keywords:
                rules["sensitive_keywords"] = keywords
        if "approve_on_connector_actions" in raw_rules:
            rules["approve_on_connector_actions"] = bool(raw_rules.get("approve_on_connector_actions"))
    return rules


def _pack_has_sensitive_signals(result_data: Dict[str, Any], sensitive_keywords: List[str]) -> bool:
    samples: List[str] = []
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    for key in ["triage", "follow_ups", "bookings", "content_plan", "briefs", "actions"]:
        value = outputs.get(key)
        if isinstance(value, list):
            for item in value[:10]:
                samples.append(json.dumps(item, ensure_ascii=True).lower())

    connector = result_data.get("connector") if isinstance(result_data.get("connector"), dict) else {}
    warnings = connector.get("warnings")
    if isinstance(warnings, list):
        for item in warnings[:20]:
            samples.append(str(item or "").strip().lower())

    blob = "\n".join(samples)
    return any(keyword in blob for keyword in sensitive_keywords)


def pack_approval_policy(trust_mode: str, result_data: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> tuple[bool, str]:
    metadata = metadata if isinstance(metadata, dict) else {}
    elevated = resolve_elevated_access(metadata)
    rules = _approval_rules_from_metadata(metadata)
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    outbound_actions = parse_positive_int(outputs.get("outbound_actions"), 0)
    urgent_count = parse_positive_int(outputs.get("urgent_count"), 0)
    connector = result_data.get("connector") if isinstance(result_data.get("connector"), dict) else {}
    connector_actions = (
        parse_positive_int(connector.get("email_sent_count"), 0)
        + parse_positive_int(connector.get("email_drafts_created", connector.get("gmail_drafts_created")), 0)
        + parse_positive_int(connector.get("calendar_events_created"), 0)
    )
    sensitive_keywords = list(rules.get("sensitive_keywords", [])) if isinstance(rules.get("sensitive_keywords"), list) else []
    has_sensitive_signals = _pack_has_sensitive_signals(result_data, [str(item).lower() for item in sensitive_keywords])
    max_outbound_auto = parse_positive_int(rules.get("max_outbound_auto"), 0)
    max_urgent_auto = parse_positive_int(rules.get("max_urgent_auto"), 0)
    approve_on_connector_actions = bool(rules.get("approve_on_connector_actions"))

    if elevated.get("mode") == ELEVATED_MODE_FULL and bool(elevated.get("active")):
        if outbound_actions > 0 or connector_actions > 0:
            return True, "Elevated mode blocks outbound or connector actions."
        return False, ""

    if trust_mode == TRUST_MODE_AUTO:
        return False, ""
    if trust_mode == TRUST_MODE_STRICT:
        return True, "Strict mode requires explicit confirmation before finalizing actions."
    if trust_mode == TRUST_MODE_COST_GUARD:
        if outbound_actions > max_outbound_auto or urgent_count > max_urgent_auto:
            return (
                True,
                (
                    f"Cost Guard triggered: outbound_actions={outbound_actions} (limit={max_outbound_auto}), "
                    f"urgent_items={urgent_count} (limit={max_urgent_auto})."
                ),
            )
        if approve_on_connector_actions and connector_actions > 0:
            return True, f"Cost Guard detected connector actions ({connector_actions}) requiring approval."
        return False, ""
    if trust_mode == TRUST_MODE_SENSITIVE_GUARD:
        if has_sensitive_signals:
            return True, "Sensitive Guard detected sensitive content in generated actions."
        if approve_on_connector_actions and connector_actions > 0:
            return True, f"Sensitive Guard detected connector actions ({connector_actions})."
        return False, ""
    if outbound_actions > 0:
        reason = f"Guarded mode detected {outbound_actions} outbound actions."
        if urgent_count > 0:
            reason += f" Urgent items: {urgent_count}."
        return True, reason
    if urgent_count > 0:
        return True, f"Guarded mode detected {urgent_count} urgent items."
    return False, ""


def derive_pack_risk_level(result_data: Dict[str, Any]) -> str:
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    outbound_actions = parse_positive_int(outputs.get("outbound_actions"), 0)
    urgent_count = parse_positive_int(outputs.get("urgent_count"), 0)
    if urgent_count > 0:
        return "high"
    if outbound_actions >= 5:
        return "medium"
    if outbound_actions > 0:
        return "low"
    return "low"


def estimate_pack_time_saved_minutes(pack_id: str, result_data: Dict[str, Any]) -> int:
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    if pack_id == CUSTOMER_OPS_PACK_ID:
        triage_count = len(outputs.get("triage", [])) if isinstance(outputs.get("triage"), list) else 0
        follow_count = len(outputs.get("follow_ups", [])) if isinstance(outputs.get("follow_ups"), list) else 0
        booking_count = len(outputs.get("bookings", [])) if isinstance(outputs.get("bookings"), list) else 0
        minutes = (triage_count * 3) + (follow_count * 5) + (booking_count * 4)
        return max(5, min(360, minutes))
    if pack_id == WEEKLY_CONTENT_PACK_ID:
        post_count = len(outputs.get("content_plan", [])) if isinstance(outputs.get("content_plan"), list) else 0
        minutes = post_count * 8
        return max(5, min(300, minutes))
    if pack_id == COMPETITOR_BRIEF_PACK_ID:
        brief_count = len(outputs.get("briefs", [])) if isinstance(outputs.get("briefs"), list) else 0
        minutes = brief_count * 10
        return max(5, min(300, minutes))
    if pack_id == SPREADSHEET_OPS_PACK_ID:
        rows_read = parse_positive_int(outputs.get("rows_read"), 0)
        rows_written = parse_positive_int(outputs.get("rows_written"), 0)
        minutes = (rows_read * 1) + (rows_written * 2)
        return max(5, min(240, minutes))
    if pack_id == DOCUMENT_STUDIO_PACK_ID:
        items = parse_positive_int(outputs.get("items_written"), 0)
        return max(8, min(180, items * 6))
    if pack_id == LOCAL_EXECUTION_PACK_ID:
        actions = len(outputs.get("actions", [])) if isinstance(outputs.get("actions"), list) else 0
        return max(5, min(120, actions * 6))
    return 10


def build_pack_execution_summary(
    pack_id: str,
    result_data: Dict[str, Any],
    policy_mode: str,
    approval_required: bool,
    approval_reason: str,
) -> Dict[str, Any]:
    next_steps = result_data.get("next_steps") if isinstance(result_data.get("next_steps"), list) else []
    next_action = str(next_steps[0]).strip() if next_steps else "Review output and proceed."
    return {
        "schema_version": 1,
        "policy_mode_applied": normalize_policy_mode(policy_mode),
        "approval_required": bool(approval_required),
        "approval_reason": approval_reason or None,
        "risk_level": derive_pack_risk_level(result_data),
        "next_action": next_action,
        "estimated_time_saved_minutes": estimate_pack_time_saved_minutes(pack_id, result_data),
    }


def friendly_runtime_error_message(exc: Exception) -> str:
    raw = str(exc or "").strip()
    if not raw:
        return "Runtime failed unexpectedly. Please try again."
    lower = raw.lower()
    if "codex oauth token" in lower and "use openai-codex provider" in lower:
        return "This workspace is using a Codex/ChatGPT OAuth token for a direct OpenAI run. Switch to OpenAI Codex or connect a direct OpenAI API key."
    if "missing_chatgpt_account_id" in lower or "codex session expired" in lower or "missing_oauth_token" in lower:
        return "OpenAI Codex session is not valid. Open Setup and reconnect your Codex/OpenAI account."
    if "claude code cli is not installed" in lower:
        return "Claude Code CLI is not installed on this machine. Install it or switch to another provider."
    if "invalid api key" in lower or "incorrect api key" in lower or "unauthorized" in lower:
        return "AI account authorization failed. Open Setup and reconnect your AI account."
    if "no credentials available" in lower or "credential" in lower:
        return "No valid AI account is connected. Open Setup and connect an account."
    if "approval timeout" in lower:
        return "Approval window timed out. Start the run again and approve when prompted."
    if "unsupported provider" in lower:
        return "Selected AI provider is not supported by this runtime."
    if "unsupported outcome pack" in lower:
        return "This outcome pack is not supported by the current runtime version."
    if "requires local companion execution" in lower:
        return "This run requires Gateway execution. Start Gateway and retry."
    if "action policy blocked" in lower:
        return "Run blocked by safety policy. Review trust/settings and retry."
    return raw


def is_non_retryable_runtime_error(exc: Exception) -> bool:
    raw = str(exc or "").strip().lower()
    if not raw:
        return False
    non_retryable_markers = [
        "action policy blocked",
        "run blocked by safety policy",
        "invalid api key",
        "incorrect api key",
        "unauthorized",
        "no credentials available",
        "codex oauth token",
        "missing_chatgpt_account_id",
        "missing_oauth_token",
        "codex session expired",
        "claude code cli is not installed",
        "api_key is required",
        "api key is required",
        "unsupported provider",
        "unsupported outcome pack",
        "requires local companion execution",
        "approval timeout",
        "stopped by human decision",
    ]
    return any(marker in raw for marker in non_retryable_markers)


def parse_text_items(raw: Any, max_items: int = 100) -> List[str]:
    if not isinstance(raw, str):
        return []
    lines = raw.replace("\r", "\n").split("\n")
    items: List[str] = []
    for line in lines:
        cleaned = line.strip().strip("-").strip()
        if cleaned:
            items.append(cleaned)
    return items[:max_items]


class ToolPolicyRegistry:
    def __init__(self):
        self._sensitive_tools: Set[str] = set()
        self._critical_tools: Set[str] = set()

    def register_sensitive(self, tool_id: str):
        self._sensitive_tools.add(tool_id)

    def register_critical(self, tool_id: str):
        self._critical_tools.add(tool_id)

    def is_sensitive(self, tool_id: str) -> bool:
        return tool_id in self._sensitive_tools

    def is_critical(self, tool_id: str) -> bool:
        return tool_id in self._critical_tools

    def sensitive_tools(self) -> List[str]:
        return sorted(self._sensitive_tools)

    def critical_tools(self) -> List[str]:
        return sorted(self._critical_tools)


TOOL_POLICY = ToolPolicyRegistry()
# Pre-register known sensitive/critical tools
TOOL_POLICY.register_sensitive("draft_email")
TOOL_POLICY.register_sensitive("create_calendar_event")
TOOL_POLICY.register_sensitive("publish_content")
TOOL_POLICY.register_sensitive("send_message")
TOOL_POLICY.register_sensitive("spreadsheet_update")
TOOL_POLICY.register_sensitive("spreadsheet_append")
TOOL_POLICY.register_sensitive("spreadsheet_create")
TOOL_POLICY.register_sensitive("document_create")
TOOL_POLICY.register_sensitive("document_update")
TOOL_POLICY.register_sensitive("presentation_create")
TOOL_POLICY.register_sensitive("presentation_update")
TOOL_POLICY.register_critical("delete_files")
TOOL_POLICY.register_critical("delete_records")
TOOL_POLICY.register_critical("transfer_funds")
for _tool_id in ("filesystem.read_write", "screenshot.capture", "computer_control", "shell.execute"):
    _contract = resolve_capability(_tool_id)
    _risk_level = str(_contract.risk_level).strip().lower() if _contract is not None else ""
    if _risk_level == "critical":
        TOOL_POLICY.register_critical(_tool_id)
    elif _contract is not None:
        TOOL_POLICY.register_sensitive(_tool_id)


def evaluate_tool_policy_decision(
    tool_id: str,
    trust_mode: str,
    target: str,
    metadata: Optional[Dict[str, Any]] = None,
    capability_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    _init()
    clean_tool_id = normalize_action_id(tool_id) or str(tool_id or "").strip().lower()
    effective_target = normalize_execution_target(target)
    metadata = metadata if isinstance(metadata, dict) else {}
    runtime_policy = resolve_runtime_policy_mode(metadata, selected_target=effective_target)
    effective_policy_mode = runtime_policy.get("policy_mode")
    policy = _action_policy_from_metadata(metadata)

    blocked_actions = policy.get("blocked_actions", set())
    block_cloud_critical = bool(policy.get("block_cloud_critical", True))

    browser_policy = metadata.get("browser_automation_policy") if isinstance(metadata.get("browser_automation_policy"), dict) else {}
    browser_requires_approval = bool(browser_policy.get("requires_approval")) if clean_tool_id == "browser_automation.interactive" else False
    browser_profile = str(browser_policy.get("profile") or "").strip().lower() if clean_tool_id == "browser_automation.interactive" else ""
    browser_privileged_actions = list(browser_policy.get("privileged_actions") or []) if clean_tool_id == "browser_automation.interactive" else []
    capability_ids = [
        str(item).strip().lower()
        for item in (capability_ids or [])
        if str(item).strip()
    ]
    capability_details = [
        detail
        for detail in (
            capability_metadata(capability_id, Path(__file__).resolve().parents[1])
            for capability_id in capability_ids
        )
        if isinstance(detail, dict)
    ]
    uses_capability_path = clean_tool_id == "shell.execute" and bool(capability_details)
    uses_raw_command_path = clean_tool_id == "shell.execute" and not uses_capability_path
    unsupported_capability = next(
        (
            detail
            for detail in capability_details
            if not bool(detail.get("platform_supported"))
        ),
        None,
    )

    known_read_only_system_probe = (
        uses_raw_command_path and _metadata_is_known_read_only_system_probe(metadata)
    )
    safe_raw_shell_command = uses_raw_command_path and _metadata_allows_safe_raw_shell(metadata)
    classification = classify_runtime_action(clean_tool_id, count=1, target=effective_target)
    if clean_tool_id == "shell.execute" and known_read_only_system_probe:
        classification = {
            **classification,
            "action_type": ACTION_TYPE_READ,
            "target_system": "local_device",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
            "requires_contract_approval": False,
            "safe_raw_shell_command": True,
            "known_read_only_system_probe": True,
        }
    elif clean_tool_id == "shell.execute" and safe_raw_shell_command:
        classification = {
            **classification,
            "action_type": ACTION_TYPE_REVERSIBLE_WRITE,
            "target_system": "local_device",
            "reversibility": True,
            "external_visibility": False,
            "destructive_risk": False,
            "safe_raw_shell_command": True,
        }
    execution_decision = "allow"
    reason = "policy_allow_default"
    runtime_decision: Optional[Dict[str, Any]] = None

    if clean_tool_id in TOOL_CONTRACTS and not is_tool_enabled(clean_tool_id):
        execution_decision = "deny"
        reason = "tool_disabled"
    elif unsupported_capability:
        execution_decision = "deny"
        reason = "blocked_unsupported_capability"
    elif uses_raw_command_path and not safe_raw_shell_command:
        execution_decision = "deny"
        reason = "blocked_raw_shell_command"
    elif clean_tool_id in blocked_actions and not uses_capability_path and not safe_raw_shell_command:
        execution_decision = "deny"
        reason = "blocked_by_action_policy"
    elif effective_target == EXECUTION_TARGET_CLOUD and TOOL_POLICY.is_critical(clean_tool_id) and block_cloud_critical:
        execution_decision = "deny"
        reason = "blocked_cloud_critical"
    else:
        runtime_decision = decide_runtime_action_execution(
            classification,
            target=effective_target,
            policy_mode=effective_policy_mode,
            metadata=metadata,
        )
        execution_decision = str(runtime_decision.get("execution_decision") or "allow").strip().lower() or "allow"
        reason = str(runtime_decision.get("reason") or "").strip() or reason

    if (
        execution_decision != "deny"
        and clean_tool_id == "browser_automation.interactive"
        and effective_target == EXECUTION_TARGET_LOCAL_COMPANION
        and browser_profile in {"authenticated_interactive", "authenticated_privileged"}
    ):
        execution_decision = "require_confirmation"
        reason = (
            "browser_authenticated_privileged_requires_reviewed_approval"
            if browser_privileged_actions
            else "browser_authenticated_interactive_requires_reviewed_approval"
        )

    if (
        execution_decision != "deny"
        and clean_tool_id == "browser_automation.interactive"
        and browser_requires_approval
        and not (
            effective_target == EXECUTION_TARGET_LOCAL_COMPANION
            and browser_profile in {"authenticated_interactive", "authenticated_privileged"}
        )
    ):
        if effective_policy_mode == POLICY_MODE_LOCAL_DEFAULT:
            execution_decision = "require_confirmation"
            reason = (
                "browser_authenticated_privileged_requires_approval"
                if browser_privileged_actions
                else "browser_authenticated_requires_approval"
            )

    return {
        "tool_id": clean_tool_id,
        "capability_ids": capability_ids or None,
        "capabilities": capability_details or None,
        "execution_decision": execution_decision,
        "decision": _legacy_decision_label(execution_decision),
        "reason": reason,
        "policy_mode": effective_policy_mode,
        "runtime_policy_source": runtime_policy.get("source"),
        "target": effective_target,
        "is_sensitive": TOOL_POLICY.is_sensitive(clean_tool_id) or browser_requires_approval,
        "is_critical": TOOL_POLICY.is_critical(clean_tool_id),
        "classification": classification,
        "uses_capability_path": uses_capability_path,
        "uses_raw_command_path": uses_raw_command_path,
        "safe_raw_shell_command": safe_raw_shell_command,
        "known_read_only_system_probe": known_read_only_system_probe,
        "unsupported_capability": unsupported_capability.get("id") if isinstance(unsupported_capability, dict) else None,
        "browser_security_profile": browser_profile or None,
        "browser_requires_approval": browser_requires_approval,
        "browser_privileged_actions": browser_privileged_actions or None,
        "runtime_trust_zone": (
            (runtime_decision.get("runtime_trust_zone") if isinstance(runtime_decision, dict) else None)
            or resolve_elevated_access(
                metadata,
                selected_target=effective_target,
                policy_mode=effective_policy_mode,
            ).get("runtime_trust_zone")
        ),
        "elevated": runtime_decision.get("elevated") if isinstance(runtime_decision, dict) else None,
        "reviewed_approval_required": bool(
            clean_tool_id == "browser_automation.interactive"
            and effective_target == EXECUTION_TARGET_LOCAL_COMPANION
            and browser_profile in {"authenticated_interactive", "authenticated_privileged"}
        ),
    }


def tool_policy_snapshot(metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _init()
    metadata = metadata if isinstance(metadata, dict) else {}
    policy = _action_policy_from_metadata(metadata)
    runtime_policy = resolve_runtime_policy_mode(metadata)
    elevated = resolve_elevated_access(
        metadata,
        selected_target=runtime_policy.get("target"),
        policy_mode=runtime_policy.get("policy_mode"),
    )
    return {
        "policy_mode": runtime_policy.get("policy_mode"),
        "runtime_trust_zone": elevated.get("runtime_trust_zone"),
        "elevated_mode": elevated.get("mode"),
        "elevated_active": bool(elevated.get("active")),
        "blocked_actions": sorted(policy.get("blocked_actions", set())),
        "approval_actions": sorted(policy.get("approval_actions", set())),
        "allow_actions": sorted(policy.get("allow_actions", set())),
        "block_cloud_critical": bool(policy.get("block_cloud_critical", True)),
        "connector_cloud_readonly": bool(policy.get("connector_cloud_readonly", True)),
        "sensitive_tools": TOOL_POLICY.sensitive_tools(),
        "critical_tools": TOOL_POLICY.critical_tools(),
    }


def enforce_tool_policy(
    tool_id: str,
    trust_mode: str,
    target: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Evaluates whether a tool call should be allowed, blocked, or require approval.
    Returns: "allow", "blocked", or "approval_required"
    """
    evaluation = evaluate_tool_policy_decision(
        tool_id=tool_id,
        trust_mode=trust_mode,
        target=target,
        metadata=metadata,
    )
    return str(evaluation.get("decision") or "allow")


# Canonical policy ownership lives in policy_service.py. Keep these bindings as
# compatibility exports for callers that still import the historical module.
from server_modules import policy_service as _canonical_policy_service

action_policy_from_app_permissions = _canonical_policy_service.action_policy_from_app_permissions
merge_action_policies = _canonical_policy_service.merge_action_policies
resolve_runtime_policy_mode = _canonical_policy_service.resolve_runtime_policy_mode
apply_execution_route_metadata = _canonical_policy_service.apply_execution_route_metadata
decide_execution_target = _canonical_policy_service.decide_execution_target
evaluate_action_policy = _canonical_policy_service.evaluate_action_policy
summarize_action_policy_eval = _canonical_policy_service.summarize_action_policy_eval
evaluate_tool_policy_decision = _canonical_policy_service.evaluate_tool_policy_decision
tool_policy_snapshot = _canonical_policy_service.tool_policy_snapshot
enforce_tool_policy = _canonical_policy_service.enforce_tool_policy
