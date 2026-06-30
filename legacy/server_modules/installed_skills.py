from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from server_modules import rust_runtime_kernel_client
from server_modules.config_loader import load_runtime_config
from server_modules.skill_scanner import scan_skill_dir

try:
    import yaml
except Exception:  # pragma: no cover - optional during bootstrap
    yaml = None  # type: ignore[assignment]


_REPO_ROOT = Path(__file__).resolve().parent.parent


def installed_skill_registry_file() -> Path:
    return (workspace_skills_root() / ".registry.json").resolve()


def bundled_skills_root() -> Path:
    return (_REPO_ROOT / "skills").resolve()


def workspace_skills_root() -> Path:
    return (_REPO_ROOT / ".orion-stack" / "skills").resolve()


def global_skills_root() -> Path:
    explicit = str(os.getenv("ORION_INSTALLED_SKILLS_DIR", "")).strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path.home() / ".orion-stack" / "skills").expanduser().resolve()


def skills_root() -> Path:
    return bundled_skills_root()


def skill_roots() -> List[tuple[str, Path]]:
    return [
        ("workspace", workspace_skills_root()),
        ("global", global_skills_root()),
        ("bundled", bundled_skills_root()),
    ]


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_skill_dir(root: Path, candidate: Path) -> Optional[Path]:
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            return None
    except Exception:
        return None
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if not _path_is_within(resolved_candidate, resolved_root):
        return None
    return resolved_candidate


def _safe_skill_file(skill_dir: Path, filename: str) -> Optional[Path]:
    candidate = (skill_dir / filename).resolve()
    if not _path_is_within(candidate, skill_dir) or not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class InstalledSkillsRustGateError(RuntimeError):
    pass


def _enforce_installed_skill_registry_state_decision(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    try:
        payload_bytes = len(
            json.dumps(
                normalized_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="save_installed_skill_registry",
            state_class="installed_skill_registry",
            actor_id="system",
            status="active",
            payload=normalized_payload,
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "save_installed_skill_registry":
            raise InstalledSkillsRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise InstalledSkillsRustGateError(exc.reason) from exc


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    raw_text = path.read_text(encoding="utf-8")
    if yaml is None:
        try:
            parsed = json.loads(raw_text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    try:
        parsed = yaml.safe_load(raw_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _strip_skill_frontmatter(text: str) -> str:
    raw = str(text or "")
    if not raw.startswith("---\n"):
        return raw.strip()
    marker = raw.find("\n---", 4)
    if marker == -1:
        return raw.strip()
    return raw[marker + 4 :].strip()


def _parse_skill_frontmatter(text: str) -> Dict[str, Any]:
    raw = str(text or "")
    if yaml is None or not raw.startswith("---\n"):
        return {}
    marker = raw.find("\n---", 4)
    if marker == -1:
        return {}
    try:
        parsed = yaml.safe_load(raw[4:marker])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _bool_from_any(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if not token:
        return default
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def _list_from_any(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _string_keys_from_any(value: Any) -> List[str]:
    if isinstance(value, dict):
        return [str(key).strip() for key in value.keys() if str(key).strip()]
    return _list_from_any(value)


def normalize_skill_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-_")


def _default_installed_skill_registry() -> Dict[str, Any]:
    return {
        "version": 2,
        "items": {},
        "workspace_overrides": {},
        "updated_at": None,
    }


def load_installed_skill_registry() -> Dict[str, Any]:
    payload = _read_json(installed_skill_registry_file())
    items = payload.get("items") if isinstance(payload.get("items"), dict) else {}
    workspace_overrides = payload.get("workspace_overrides") if isinstance(payload.get("workspace_overrides"), dict) else {}
    return {
        "version": int(payload.get("version") or 2),
        "items": items,
        "workspace_overrides": workspace_overrides,
        "updated_at": payload.get("updated_at"),
    }


def save_installed_skill_registry(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _default_installed_skill_registry()
    data["items"] = payload.get("items") if isinstance(payload.get("items"), dict) else {}
    data["workspace_overrides"] = (
        payload.get("workspace_overrides") if isinstance(payload.get("workspace_overrides"), dict) else {}
    )
    data["updated_at"] = payload.get("updated_at")
    _enforce_installed_skill_registry_state_decision(data)
    _write_json(installed_skill_registry_file(), data)
    return data


def get_installed_skill_registry_entry(skill_id: str) -> Dict[str, Any]:
    registry = load_installed_skill_registry()
    item = registry["items"].get(skill_id)
    return dict(item) if isinstance(item, dict) else {}


def _normalize_workspace_key(workspace_id: Any) -> str:
    return str(workspace_id or "").strip()


def get_workspace_installed_skill_registry_entry(skill_id: str, workspace_id: Any) -> Dict[str, Any]:
    normalized_skill = normalize_skill_id(skill_id)
    normalized_workspace = _normalize_workspace_key(workspace_id)
    if not normalized_skill or not normalized_workspace:
        return {}
    registry = load_installed_skill_registry()
    workspace_overrides = registry.get("workspace_overrides") if isinstance(registry.get("workspace_overrides"), dict) else {}
    workspace_items = workspace_overrides.get(normalized_workspace) if isinstance(workspace_overrides.get(normalized_workspace), dict) else {}
    item = workspace_items.get(normalized_skill)
    return dict(item) if isinstance(item, dict) else {}


def upsert_installed_skill_registry_entry(skill_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        return {}
    registry = load_installed_skill_registry()
    current = registry["items"].get(normalized)
    entry = dict(current) if isinstance(current, dict) else {}
    entry.update(patch)
    registry["items"][normalized] = entry
    registry["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    save_installed_skill_registry(registry)
    return entry


def upsert_workspace_installed_skill_registry_entry(skill_id: str, workspace_id: Any, patch: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_id)
    normalized_workspace = _normalize_workspace_key(workspace_id)
    if not normalized or not normalized_workspace:
        return {}
    registry = load_installed_skill_registry()
    workspace_overrides = registry.get("workspace_overrides") if isinstance(registry.get("workspace_overrides"), dict) else {}
    workspace_items = workspace_overrides.get(normalized_workspace)
    item_bucket = dict(workspace_items) if isinstance(workspace_items, dict) else {}
    current = item_bucket.get(normalized)
    entry = dict(current) if isinstance(current, dict) else {}
    entry.update(patch)
    item_bucket[normalized] = entry
    workspace_overrides[normalized_workspace] = item_bucket
    registry["workspace_overrides"] = workspace_overrides
    registry["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    save_installed_skill_registry(registry)
    return entry


def set_workspace_installed_skill_enabled(*, skill_id: str, workspace_id: Any, enabled: bool) -> Dict[str, Any]:
    return upsert_workspace_installed_skill_registry_entry(
        skill_id,
        workspace_id,
        {"enabled": bool(enabled)},
    )


def remove_installed_skill_registry_entry(skill_id: str) -> None:
    normalized = normalize_skill_id(skill_id)
    if not normalized:
        return
    registry = load_installed_skill_registry()
    if normalized in registry["items"]:
        registry["items"].pop(normalized, None)
        registry["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        save_installed_skill_registry(registry)


def _first_defined(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, dict) and not value:
            continue
        return value
    return None


def _runtime_section(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    runtime = value.get("runtime")
    return dict(runtime) if isinstance(runtime, dict) else {}


def _normalize_runtime_metadata(*, raw_manifest: Dict[str, Any], frontmatter: Dict[str, Any], has_query_handler: bool) -> Dict[str, Any]:
    runtime_manifest = _runtime_section(raw_manifest)
    metadata_source = runtime_manifest or raw_manifest
    trigger_terms = _list_from_any(
        _first_defined(
            runtime_manifest.get("trigger_terms"),
            raw_manifest.get("trigger_terms"),
            frontmatter.get("trigger_terms"),
        )
    )
    connector_scopes = _list_from_any(
        _first_defined(
            runtime_manifest.get("connector_scopes"),
            raw_manifest.get("connector_scopes"),
            frontmatter.get("connector_scopes"),
        )
    )
    allowed_runtime_modes = _list_from_any(
        _first_defined(
            runtime_manifest.get("allowed_runtime_modes"),
            raw_manifest.get("allowed_runtime_modes"),
            frontmatter.get("allowed_runtime_modes"),
        )
    )
    execution_adapter = str(
        _first_defined(
            runtime_manifest.get("execution_adapter"),
            raw_manifest.get("execution_adapter"),
            frontmatter.get("execution_adapter"),
            "handler" if has_query_handler else "",
        )
        or ""
    ).strip().lower()
    return {
        "skill_class": str(
            _first_defined(
                runtime_manifest.get("skill_class"),
                raw_manifest.get("skill_class"),
                frontmatter.get("skill_class"),
            )
            or ""
        ).strip().lower(),
        "permission_label": str(
            _first_defined(
                runtime_manifest.get("permission_label"),
                raw_manifest.get("permission_label"),
                frontmatter.get("permission_label"),
            )
            or ""
        ).strip(),
        "execution_mode": str(
            _first_defined(
                runtime_manifest.get("execution_mode"),
                raw_manifest.get("execution_mode"),
                frontmatter.get("execution_mode"),
            )
            or ""
        ).strip().lower(),
        "action_class": str(
            _first_defined(
                runtime_manifest.get("action_class"),
                raw_manifest.get("action_class"),
                frontmatter.get("action_class"),
            )
            or ""
        ).strip().lower(),
        "connector_scopes": connector_scopes,
        "trigger_terms": trigger_terms,
        "allowed_runtime_modes": allowed_runtime_modes,
        "requires_approval": _bool_from_any(
            _first_defined(
                runtime_manifest.get("requires_approval"),
                raw_manifest.get("requires_approval"),
                frontmatter.get("requires_approval"),
            ),
            False,
        ),
        "execution_adapter": execution_adapter,
        "metadata_source": metadata_source,
    }


def _runtime_skill_configs() -> Dict[str, Any]:
    payload = load_runtime_config()
    skills = payload.get("skills") if isinstance(payload, dict) else {}
    return skills if isinstance(skills, dict) else {}


def _runtime_skill_config(skill_id: str) -> Dict[str, Any]:
    payload = _runtime_skill_configs()
    item = payload.get(skill_id)
    return dict(item) if isinstance(item, dict) else {}


def _manifest_description(value: Any, readme_text: str) -> str:
    explicit = str(value or "").strip()
    if explicit:
        return explicit
    for line in str(readme_text or "").splitlines():
        token = line.strip()
        if not token or token.startswith("#") or token.startswith("---"):
            continue
        return token[:500]
    return ""


def _standardize_manifest(payload: Dict[str, Any], *, fallback_name: str, readme_text: str) -> Dict[str, Any]:
    name = str(payload.get("name") or fallback_name).strip() or fallback_name
    skill_id = normalize_skill_id(name) or normalize_skill_id(fallback_name) or fallback_name.lower()
    tools = _list_from_any(payload.get("tools"))
    slash_commands = _list_from_any(payload.get("slash_commands"))
    requires = _list_from_any(payload.get("requires"))
    description = _manifest_description(payload.get("description"), readme_text)
    return {
        "name": name[:160],
        "id": skill_id[:120],
        "version": str(payload.get("version") or "1.0.0").strip()[:40] or "1.0.0",
        "description": description[:500],
        "author": str(payload.get("author") or "").strip()[:120],
        "tools": tools[:60],
        "slash_commands": slash_commands[:30],
        "requires": requires[:60],
        "homepage": str(payload.get("homepage") or "").strip()[:500],
        "source": str(payload.get("source") or "").strip()[:1000],
    }


def _load_skill_manifest(skill_dir: Path) -> Dict[str, Any]:
    skill_json = _safe_skill_file(skill_dir, "skill.json")
    if skill_json is not None:
        payload = _read_json(skill_json)
        if payload:
            return _standardize_manifest(payload, fallback_name=skill_dir.name, readme_text=_read_text(skill_dir / "README.md"))
    skill_md = _safe_skill_file(skill_dir, "SKILL.md")
    skill_text = _read_text(skill_md) if skill_md is not None else ""
    frontmatter = _parse_skill_frontmatter(skill_text)
    readme_text = _read_text(skill_dir / "README.md") or skill_text
    payload: Dict[str, Any] = {
        "name": frontmatter.get("name") or skill_dir.name,
        "version": frontmatter.get("version") or "1.0.0",
        "description": frontmatter.get("description") or "",
        "author": frontmatter.get("author") or "Empyralis",
        "tools": frontmatter.get("tools") or [],
        "slash_commands": frontmatter.get("slash_commands") or [],
        "requires": frontmatter.get("requires") or frontmatter.get("required_bins") or [],
        "homepage": frontmatter.get("homepage") or "",
        "source": frontmatter.get("source") or "",
    }
    return _standardize_manifest(payload, fallback_name=skill_dir.name, readme_text=readme_text)


def _required_bins(
    raw_manifest: Dict[str, Any],
    frontmatter: Dict[str, Any],
    config: Dict[str, Any],
    runtime_config: Dict[str, Any],
) -> List[str]:
    required: List[str] = []
    runtime_manifest = _runtime_section(raw_manifest)
    for candidate in (
        runtime_config.get("required_bins"),
        runtime_manifest.get("required_bins"),
        config.get("required_bins"),
        frontmatter.get("required_bins"),
    ):
        for token in _list_from_any(candidate):
            if token not in required:
                required.append(token)
    return required


def _missing_bins(required_bins: List[str]) -> List[str]:
    return [token for token in required_bins if not shutil.which(token)]


def _required_env_vars(
    raw_manifest: Dict[str, Any],
    frontmatter: Dict[str, Any],
    config: Dict[str, Any],
    runtime_config: Dict[str, Any],
) -> List[str]:
    required: List[str] = []
    runtime_manifest = _runtime_section(raw_manifest)
    for candidate in (
        runtime_config.get("required_env"),
        runtime_config.get("required_env_vars"),
        runtime_manifest.get("required_env"),
        runtime_manifest.get("required_env_vars"),
        config.get("required_env"),
        config.get("required_env_vars"),
        frontmatter.get("required_env"),
        frontmatter.get("required_env_vars"),
    ):
        for token in _string_keys_from_any(candidate):
            if token not in required:
                required.append(token)
    return required


def _missing_env_vars(required_env_vars: List[str]) -> List[str]:
    missing: List[str] = []
    for token in required_env_vars:
        if not str(os.getenv(token, "")).strip():
            missing.append(token)
    return missing


def _required_python_packages(
    raw_manifest: Dict[str, Any],
    frontmatter: Dict[str, Any],
    config: Dict[str, Any],
    runtime_config: Dict[str, Any],
) -> List[str]:
    required: List[str] = []
    runtime_manifest = _runtime_section(raw_manifest)
    for candidate in (
        runtime_config.get("required_python_packages"),
        runtime_config.get("required_packages"),
        runtime_config.get("required_modules"),
        runtime_manifest.get("required_python_packages"),
        runtime_manifest.get("required_packages"),
        runtime_manifest.get("required_modules"),
        config.get("required_python_packages"),
        config.get("required_packages"),
        config.get("required_modules"),
        frontmatter.get("required_python_packages"),
        frontmatter.get("required_packages"),
        frontmatter.get("required_modules"),
    ):
        for token in _list_from_any(candidate):
            if token not in required:
                required.append(token)
    return required


def _missing_python_packages(required_python_packages: List[str]) -> List[str]:
    missing: List[str] = []
    for token in required_python_packages:
        module_name = str(token or "").strip().replace("-", "_")
        if not module_name:
            continue
        if importlib.util.find_spec(module_name) is None:
            missing.append(str(token).strip())
    return missing


def _supported_os(
    raw_manifest: Dict[str, Any],
    frontmatter: Dict[str, Any],
    config: Dict[str, Any],
    runtime_config: Dict[str, Any],
) -> List[str]:
    supported: List[str] = []
    runtime_manifest = _runtime_section(raw_manifest)
    for candidate in (
        runtime_config.get("supported_os"),
        runtime_config.get("allowed_os"),
        runtime_manifest.get("supported_os"),
        runtime_manifest.get("allowed_os"),
        config.get("supported_os"),
        config.get("allowed_os"),
        frontmatter.get("supported_os"),
        frontmatter.get("allowed_os"),
    ):
        for token in _list_from_any(candidate):
            normalized = str(token or "").strip().lower()
            if normalized and normalized not in supported:
                supported.append(normalized)
    return supported


def _current_os_aliases() -> set[str]:
    platform = sys.platform.lower()
    aliases = {platform}
    if platform.startswith("darwin"):
        aliases.update({"macos", "mac", "osx", "unix", "posix"})
    elif platform.startswith("linux"):
        aliases.update({"linux", "unix", "posix"})
    elif platform.startswith("win"):
        aliases.update({"windows", "win", "win32"})
    return aliases


def _os_available(supported_os: List[str]) -> bool:
    if not supported_os:
        return True
    aliases = _current_os_aliases()
    return any(token in aliases for token in supported_os)


def current_device_os_aliases() -> List[str]:
    return sorted(_current_os_aliases())


def current_device_os_label() -> str:
    aliases = _current_os_aliases()
    if "macos" in aliases:
        return "macOS"
    if "linux" in aliases:
        return "Linux"
    if "windows" in aliases:
        return "Windows"
    for token in sorted(aliases):
        if token not in {"posix", "unix"}:
            return token
    return "this device"


def _availability_reasons(
    *,
    supported_os: List[str],
    required_bins: List[str],
    missing_bins: List[str],
    required_env_vars: List[str],
    missing_env_vars: List[str],
    required_python_packages: List[str],
    missing_python_packages: List[str],
) -> List[str]:
    reasons: List[str] = []
    if supported_os and not _os_available(supported_os):
        reasons.append(f"Only available on: {', '.join(supported_os)}")
    if missing_bins:
        reasons.append(f"Missing runtime dependencies: {', '.join(missing_bins)}")
    if missing_env_vars:
        reasons.append(f"Missing environment variables: {', '.join(missing_env_vars)}")
    if missing_python_packages:
        reasons.append(f"Missing Python packages: {', '.join(missing_python_packages)}")
    return reasons


def skill_availability_state(item: Dict[str, Any]) -> str:
    enabled = bool(item.get("enabled"))
    if not enabled:
        return "disabled_policy"
    supported_os = [
        str(token or "").strip().lower()
        for token in list(item.get("supported_os") or [])
        if str(token or "").strip()
    ]
    if supported_os and not _os_available(supported_os):
        return "unsupported_device"
    if bool(item.get("available")):
        return "ready"
    return "needs_setup"


def list_installed_skills(*, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    registry = load_installed_skill_registry()
    registry_items = registry.get("items") if isinstance(registry.get("items"), dict) else {}
    normalized_workspace_id = _normalize_workspace_key(workspace_id)
    for source, root in skill_roots():
        if not root.exists():
            continue
        skill_dirs: List[Path] = []
        for path in root.iterdir():
            safe_dir = _safe_skill_dir(root, path)
            if safe_dir is not None:
                skill_dirs.append(safe_dir)
        for skill_dir in sorted(skill_dirs, key=lambda item: item.name.lower()):
            has_skill_md = (skill_dir / "SKILL.md").exists()
            has_skill_json = (skill_dir / "skill.json").exists()
            if not has_skill_md and not has_skill_json:
                continue
            manifest = _load_skill_manifest(skill_dir)
            skill_text = _read_text(skill_dir / "SKILL.md")
            frontmatter = _parse_skill_frontmatter(skill_text)
            raw_manifest = _read_json(skill_dir / "skill.json") if has_skill_json else {}
            config = _load_yaml(skill_dir / "config.yaml")
            skill_id = normalize_skill_id(manifest.get("id") or frontmatter.get("name") or skill_dir.name) or skill_dir.name.lower()
            if not skill_id or skill_id in seen_ids:
                continue
            seen_ids.add(skill_id)
            registry_entry = registry_items.get(skill_id) if isinstance(registry_items.get(skill_id), dict) else {}
            workspace_registry_entry = (
                get_workspace_installed_skill_registry_entry(skill_id, normalized_workspace_id)
                if normalized_workspace_id
                else {}
            )
            description = str(manifest.get("description") or frontmatter.get("description") or "").strip()
            runtime_config = _runtime_skill_config(skill_id)
            enabled_default = True if has_skill_json else source == "bundled"
            enabled_fallback = _bool_from_any(
                runtime_config.get("enabled"),
                _bool_from_any(config.get("enabled"), _bool_from_any(frontmatter.get("enabled"), enabled_default)),
            )
            enabled = _bool_from_any(
                workspace_registry_entry.get("enabled") if isinstance(workspace_registry_entry, dict) else None,
                _bool_from_any(
                    registry_entry.get("enabled") if isinstance(registry_entry, dict) else None,
                    enabled_fallback,
                ),
            )
            runtime_metadata = _normalize_runtime_metadata(
                raw_manifest=raw_manifest,
                frontmatter=frontmatter,
                has_query_handler=(skill_dir / "query_handler.py").exists() or (skill_dir / "handler.py").exists(),
            )
            required_bins = _required_bins(raw_manifest, frontmatter, config, runtime_config)
            missing_bins = _missing_bins(required_bins)
            required_env_vars = _required_env_vars(raw_manifest, frontmatter, config, runtime_config)
            missing_env_vars = _missing_env_vars(required_env_vars)
            required_python_packages = _required_python_packages(raw_manifest, frontmatter, config, runtime_config)
            missing_python_packages = _missing_python_packages(required_python_packages)
            supported_os = _supported_os(raw_manifest, frontmatter, config, runtime_config)
            availability_reasons = _availability_reasons(
                supported_os=supported_os,
                required_bins=required_bins,
                missing_bins=missing_bins,
                required_env_vars=required_env_vars,
                missing_env_vars=missing_env_vars,
                required_python_packages=required_python_packages,
                missing_python_packages=missing_python_packages,
            )
            readme_text = _read_text(skill_dir / "README.md")
            security_scan = scan_skill_dir(skill_dir)
            if bool(security_scan.get("blocked")):
                critical_titles = [
                    str(item.get("title") or item.get("code") or "critical finding").strip()
                    for item in list(security_scan.get("findings") or [])
                    if str(item.get("severity") or "").strip() == "critical"
                ]
                suffix = f": {', '.join(critical_titles[:3])}" if critical_titles else ""
                availability_reasons.append(f"Security scan blocked skill{suffix}")
            items.append(
                {
                    "id": skill_id[:120],
                    "name": str(manifest.get("name") or frontmatter.get("name") or skill_dir.name).strip() or skill_dir.name,
                    "version": str(manifest.get("version") or "1.0.0").strip()[:40] or "1.0.0",
                    "description": description[:500],
                    "author": str(manifest.get("author") or "").strip()[:120],
                    "tools": _list_from_any(manifest.get("tools")),
                    "slash_commands": _list_from_any(manifest.get("slash_commands")),
                    "requires": _list_from_any(manifest.get("requires")),
                    "homepage": str(manifest.get("homepage") or "").strip()[:500],
                    "source_url": str(manifest.get("source") or "").strip()[:1000],
                    "enabled": enabled,
                    "available": not availability_reasons,
                    "availability_reasons": availability_reasons,
                    "security_scan": security_scan,
                    "supported_os": supported_os,
                    "missing_bins": missing_bins,
                    "required_bins": required_bins,
                    "missing_env_vars": missing_env_vars,
                    "required_env_vars": required_env_vars,
                    "missing_python_packages": missing_python_packages,
                    "required_python_packages": required_python_packages,
                    "source": source,
                    "format": "skill_json" if has_skill_json else "legacy",
                    "path": str(skill_dir),
                    "config_path": str(skill_dir / "config.yaml"),
                    "has_query_handler": (skill_dir / "query_handler.py").exists() or (skill_dir / "handler.py").exists(),
                    "has_snapshot_worker": (skill_dir / "worker.py").exists(),
                    "config": config,
                    "runtime_config": runtime_config,
                    "runtime_metadata": runtime_metadata,
                    "skill_body": _strip_skill_frontmatter(skill_text) or readme_text.strip(),
                    "readme": readme_text.strip() or _strip_skill_frontmatter(skill_text),
                    "registry": registry_entry if isinstance(registry_entry, dict) else {},
                    "workspace_registry": workspace_registry_entry if isinstance(workspace_registry_entry, dict) else {},
                    "workspace_id": normalized_workspace_id or None,
                }
            )
    return items


def active_installed_skills(*, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return [
        item
        for item in list_installed_skills(workspace_id=workspace_id)
        if bool(item.get("enabled")) and bool(item.get("available"))
    ]


def merge_skill_prompt_append(existing: str, extra: str, *, max_chars: int = 12000) -> str:
    left = str(existing or "").strip()
    right = str(extra or "").strip()
    if not left:
        return right[:max_chars]
    if not right:
        return left[:max_chars]
    merged = f"{left}\n\n{right}".strip()
    return merged[:max_chars]


def build_active_skill_prompt_append(*, workspace_id: Optional[str] = None, max_chars: int = 12000) -> str:
    active = active_installed_skills(workspace_id=workspace_id)
    if not active:
        return ""
    chunks: List[str] = ["Installed skill instructions (active for this deployment):"]
    for item in active:
        body = str(item.get("skill_body") or "").strip()
        name = str(item.get("name") or item.get("id") or "Skill").strip()
        description = str(item.get("description") or "").strip()
        runtime_config = item.get("runtime_config") if isinstance(item.get("runtime_config"), dict) else {}
        if description:
            chunks.append(f"\n{name} — {description}")
        else:
            chunks.append(f"\n{name}")
        if body:
            chunks.append(body)
        if runtime_config:
            chunks.append("Runtime config:")
            chunks.append(json.dumps(runtime_config, ensure_ascii=False, sort_keys=True, indent=2)[:1000])
    return "\n".join(chunks).strip()[:max_chars]


def active_installed_skill_ids(*, workspace_id: Optional[str] = None) -> List[str]:
    return [
        str(item.get("id") or "").strip()
        for item in active_installed_skills(workspace_id=workspace_id)
        if str(item.get("id") or "").strip()
    ]


def _run_skill_query_handler(skill: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    skill_path = Path(str(skill.get("path") or "")).expanduser().resolve()
    handler_path = _safe_skill_file(skill_path, "handler.py") or _safe_skill_file(skill_path, "query_handler.py")
    if handler_path is None:
        return {}
    try:
        completed = subprocess.run(
            [sys.executable, str(handler_path)],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(skill_path),
            timeout=8,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "skill_id": skill.get("id")}
    raw = (completed.stdout or b"").decode("utf-8", "ignore").strip()
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "ignore").strip() or raw or f"exit_{completed.returncode}"
        return {"ok": False, "error": detail[:1000], "skill_id": skill.get("id")}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {"ok": False, "error": raw[:1000], "skill_id": skill.get("id")}


def query_active_installed_skills(
    *,
    query: str,
    channel: Optional[str] = None,
    workspace_id: Optional[str] = None,
    connector_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    session_key: Optional[str] = None,
) -> Dict[str, Any]:
    active = active_installed_skills(workspace_id=workspace_id)
    combined_prompt = ""
    combined_errors: List[Dict[str, Any]] = []
    first_handled: Optional[Dict[str, Any]] = None
    payload = {
        "query": str(query or "").strip(),
        "channel": str(channel or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "connector_id": str(connector_id or "").strip(),
        "chat_id": str(chat_id or "").strip(),
        "session_key": str(session_key or "").strip(),
        "skills_roots": [str(path) for _source, path in skill_roots()],
    }
    for skill in active:
        result = _run_skill_query_handler(skill, payload)
        if not result:
            continue
        prompt_append = str(result.get("prompt_append") or "").strip()
        if prompt_append:
            combined_prompt = merge_skill_prompt_append(combined_prompt, prompt_append)
        if result.get("ok") is False and result.get("error"):
            combined_errors.append(
                {
                    "skill_id": str(skill.get("id") or "").strip(),
                    "error": str(result.get("error") or "").strip()[:1000],
                }
            )
        handled = _bool_from_any(result.get("handled"), False)
        response = str(result.get("response") or "").strip()
        if handled and response and first_handled is None:
            first_handled = {
                "skill_id": str(skill.get("id") or "").strip(),
                "response": response[:4000],
                "metadata": result.get("metadata") if isinstance(result.get("metadata"), dict) else {},
            }
    return {
        "handled": bool(first_handled),
        "response": str((first_handled or {}).get("response") or "").strip(),
        "skill_id": str((first_handled or {}).get("skill_id") or "").strip(),
        "metadata": (first_handled or {}).get("metadata") if isinstance((first_handled or {}).get("metadata"), dict) else {},
        "prompt_append": combined_prompt[:12000],
        "active_skill_ids": [str(item.get("id") or "").strip() for item in active if str(item.get("id") or "").strip()],
        "errors": combined_errors,
    }
