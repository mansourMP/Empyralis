from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request as urlrequest

from server_modules.skill_scanner import scan_skill_dir

_log = logging.getLogger(__name__)

from server_modules.installed_skills import (
    bundled_skills_root,
    get_installed_skill_registry_entry,
    list_installed_skills,
    normalize_skill_id,
    remove_installed_skill_registry_entry,
    save_installed_skill_registry,
    upsert_installed_skill_registry_entry,
    workspace_skills_root,
    load_installed_skill_registry,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_REGISTRY_FILE = (REPO_ROOT / "marketplace" / "registry.json").resolve()
MARKETPLACE_PUBLISH_DIR = (REPO_ROOT / "marketplace" / "published").resolve()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


class SkillsRegistryRustGateError(RuntimeError):
    pass


def _enforce_skills_registry_file_write(*, path: Path, kind: str, payload_bytes: int) -> None:
    from server_modules import rust_runtime_kernel_client

    payload = {
        "path": str(path),
        "kind": str(kind or "file").strip() or "file",
        "payload_bytes": max(0, int(payload_bytes or 0)),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_skills_registry_file",
            state_class="skills_registry_files",
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=int(payload["payload_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        decision = rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        if str(decision.get("next_action") or "").strip() != "write_skills_registry_file":
            raise SkillsRegistryRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise SkillsRegistryRustGateError(exc.reason) from exc


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    _enforce_skills_registry_file_write(
        path=path,
        kind="json",
        payload_bytes=len(serialized.encode("utf-8")),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")


def _strip_frontmatter(text: str) -> str:
    raw = str(text or "")
    if not raw.startswith("---\n"):
        return raw.strip()
    marker = raw.find("\n---", 4)
    if marker == -1:
        return raw.strip()
    return raw[marker + 4 :].strip()


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    raw = str(text or "")
    if not raw.startswith("---\n"):
        return {}
    marker = raw.find("\n---", 4)
    if marker == -1:
        return {}
    body = raw[4:marker]
    out: Dict[str, Any] = {}
    current_list_key: Optional[str] = None
    for line in body.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        if stripped.startswith("  - ") or stripped.startswith("- "):
            if current_list_key:
                value = stripped.split("- ", 1)[1].strip()
                bucket = out.setdefault(current_list_key, [])
                if isinstance(bucket, list) and value:
                    bucket.append(value)
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        token = key.strip()
        raw_value = value.strip()
        if raw_value == "":
            out[token] = []
            current_list_key = token
            continue
        current_list_key = None
        if raw_value.lower() in {"true", "false"}:
            out[token] = raw_value.lower() == "true"
        else:
            out[token] = raw_value
    return out


def _list_from_any(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _validate_manifest(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("skill.json must contain an object.")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("skill.json.name is required.")
    version = str(payload.get("version") or "").strip() or "1.0.0"
    description = str(payload.get("description") or "").strip()
    if not description:
        raise ValueError("skill.json.description is required.")
    author = str(payload.get("author") or "").strip()
    if not author:
        raise ValueError("skill.json.author is required.")
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    return {
        "name": name[:160],
        "id": normalize_skill_id(name)[:120],
        "version": version[:40],
        "description": description[:500],
        "author": author[:120],
        "tools": _list_from_any(payload.get("tools"))[:60],
        "slash_commands": _list_from_any(payload.get("slash_commands"))[:30],
        "requires": _list_from_any(payload.get("requires"))[:60],
        "homepage": str(payload.get("homepage") or "").strip()[:1000],
        "source": str(payload.get("source") or "").strip()[:1000],
        "runtime": runtime,
    }


_DANGEROUS_CODE_PATTERNS = [
    "subprocess",
    "os.system",
    "eval(",
    "exec(",
    "__import__",
    "socket.",
    "requests.",
    "urllib.",
    "http.client",
]


def _scanner_flags_dangerous(findings: List[Dict[str, Any]]) -> bool:
    for f in findings:
        if f.get("severity") in ("critical", "high"):
            return True
    return False


def _validate_manifest_against_scanner(manifest: Dict[str, Any], scan_result: Dict[str, Any]) -> List[str]:
    """Cross-check manifest-declared permissions against scanner findings.

    Returns a list of blocking reasons (empty = ok).
    """
    reasons: List[str] = []
    declared_action_class = str(manifest.get("runtime", {}).get("action_class") or "").strip().lower()
    declared_scopes = _list_from_any(manifest.get("runtime", {}).get("connector_scopes"))
    findings = scan_result.get("findings") or []

    has_dangerous = _scanner_flags_dangerous(findings)
    if has_dangerous and declared_action_class in ("read", ""):
        reasons.append(
            "Manifest declares low-risk action_class but scanner found dangerous code patterns. "
            "Review the skill and either fix the code or update the manifest to reflect actual risk."
        )
    return reasons


def _legacy_manifest_from_dir(skill_dir: Path) -> Dict[str, Any]:
    skill_md = skill_dir / "SKILL.md"
    raw = _read_text(skill_md)
    frontmatter = _parse_frontmatter(raw)
    readme = _strip_frontmatter(raw)
    payload = {
        "name": frontmatter.get("name") or skill_dir.name,
        "version": frontmatter.get("version") or "1.0.0",
        "description": frontmatter.get("description") or next(
            (line.strip() for line in readme.splitlines() if line.strip() and not line.startswith("#")),
            "",
        ),
        "author": frontmatter.get("author") or "Empyralis",
        "tools": frontmatter.get("tools") or [],
        "slash_commands": frontmatter.get("slash_commands") or [],
        "requires": frontmatter.get("requires") or frontmatter.get("required_bins") or [],
        "homepage": frontmatter.get("homepage") or "",
        "source": frontmatter.get("source") or "",
        "runtime": {
            key: value
            for key, value in frontmatter.items()
            if key in {
                "skill_class",
                "permission_label",
                "execution_mode",
                "action_class",
                "connector_scopes",
                "trigger_terms",
                "allowed_runtime_modes",
                "requires_approval",
                "execution_adapter",
            }
        },
    }
    return _validate_manifest(payload)


def _manifest_from_dir(skill_dir: Path) -> Dict[str, Any]:
    skill_json = skill_dir / "skill.json"
    if skill_json.exists():
        return _validate_manifest(_read_json(skill_json))
    return _legacy_manifest_from_dir(skill_dir)


def _readme_from_dir(skill_dir: Path) -> str:
    readme = _read_text(skill_dir / "README.md")
    if readme.strip():
        return readme.strip()
    skill_md = _read_text(skill_dir / "SKILL.md")
    return _strip_frontmatter(skill_md)


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _normalize_skill_tree(skill_dir: Path) -> Dict[str, Any]:
    manifest = _manifest_from_dir(skill_dir)
    skill_json = skill_dir / "skill.json"
    if not skill_json.exists():
        _write_json(skill_json, {
            "name": manifest["name"],
            "version": manifest["version"],
            "description": manifest["description"],
            "author": manifest["author"],
            "tools": manifest["tools"],
            "slash_commands": manifest["slash_commands"],
            "requires": manifest["requires"],
            "homepage": manifest["homepage"],
            "source": manifest["source"],
            "runtime": manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {},
        })
    readme_path = skill_dir / "README.md"
    if not readme_path.exists():
        readme_body = _readme_from_dir(skill_dir)
        if readme_body.strip():
            readme_payload = readme_body + "\n"
            _enforce_skills_registry_file_write(
                path=readme_path,
                kind="readme",
                payload_bytes=len(readme_payload.encode("utf-8")),
            )
            readme_path.write_text(readme_payload, encoding="utf-8")
    handler_path = skill_dir / "handler.py"
    query_handler = skill_dir / "query_handler.py"
    if not handler_path.exists() and query_handler.exists():
        handler_payload = _read_text(query_handler)
        _enforce_skills_registry_file_write(
            path=handler_path,
            kind="handler",
            payload_bytes=len(handler_payload.encode("utf-8")),
        )
        handler_path.write_text(handler_payload, encoding="utf-8")
    return manifest


def _discover_skill_dir(root: Path) -> Path:
    direct_candidates = [root, *(child for child in root.iterdir() if child.is_dir())] if root.exists() else []
    for candidate in direct_candidates:
        if (candidate / "skill.json").exists():
            return candidate
    walk_hits: List[Path] = []
    for path in root.rglob("skill.json"):
        if path.parent.name.startswith("."):
            continue
        walk_hits.append(path.parent)
        if len(walk_hits) > 1:
            break
    if len(walk_hits) == 1:
        return walk_hits[0]
    legacy_hits: List[Path] = []
    for path in root.rglob("SKILL.md"):
        if path.parent.name.startswith("."):
            continue
        legacy_hits.append(path.parent)
        if len(legacy_hits) > 1:
            break
    if len(legacy_hits) == 1:
        return legacy_hits[0]
    raise ValueError("Could not locate a skill.json or SKILL.md root in the provided source.")


def _clone_git_source(source_url: str, target_dir: Path) -> None:
    completed = subprocess.run(
        ["git", "clone", "--depth", "1", source_url, str(target_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or "git clone failed"
        raise RuntimeError(detail[:2000])


def _install_requirements(requirements: List[str]) -> None:
    if not requirements:
        return
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "install", *requirements],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or "pip install failed"
        raise RuntimeError(detail[:2000])


def _resolve_local_source(source_url: str) -> Path:
    token = str(source_url or "").strip()
    if token.startswith("local:bundled/"):
        skill_name = normalize_skill_id(token.split("/", 1)[1])
        path = bundled_skills_root() / skill_name
        if not path.exists():
            raise FileNotFoundError(f"Bundled skill '{skill_name}' does not exist.")
        return path
    raise ValueError(f"Unsupported local source '{source_url}'.")


def _prepare_source_tree(*, source_url: Optional[str], zip_path: Optional[str], temp_root: Path) -> Path:
    if zip_path:
        archive = Path(zip_path).expanduser().resolve()
        if not archive.exists() or not archive.is_file():
            raise FileNotFoundError(f"Zip archive '{zip_path}' does not exist.")
        extracted = temp_root / "unzipped"
        with zipfile.ZipFile(archive, "r") as handle:
            handle.extractall(extracted)
        return _discover_skill_dir(extracted)
    if not source_url:
        raise ValueError("Either source_url or zip_path is required.")
    if source_url.startswith("local:"):
        return _resolve_local_source(source_url)
    cloned = temp_root / "clone"
    _clone_git_source(source_url, cloned)
    return _discover_skill_dir(cloned)


def _public_skill_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    registry = item.get("registry") if isinstance(item.get("registry"), dict) else {}
    return {
        "id": str(item.get("id") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "version": str(item.get("version") or "").strip(),
        "description": str(item.get("description") or "").strip(),
        "author": str(item.get("author") or "").strip(),
        "tools": item.get("tools") if isinstance(item.get("tools"), list) else [],
        "slash_commands": item.get("slash_commands") if isinstance(item.get("slash_commands"), list) else [],
        "requires": item.get("requires") if isinstance(item.get("requires"), list) else [],
        "homepage": str(item.get("homepage") or "").strip(),
        "source": str(item.get("source_url") or "").strip(),
        "enabled": bool(item.get("enabled")),
        "available": bool(item.get("available")),
        "format": str(item.get("format") or "").strip(),
        "source_kind": str(item.get("source") or "").strip(),
        "installed_path": str(item.get("path") or "").strip(),
        "skill_class": str(((item.get("runtime_metadata") or {}) if isinstance(item.get("runtime_metadata"), dict) else {}).get("skill_class") or "").strip(),
        "action_class": str(((item.get("runtime_metadata") or {}) if isinstance(item.get("runtime_metadata"), dict) else {}).get("action_class") or "").strip(),
        "execution_adapter": str(((item.get("runtime_metadata") or {}) if isinstance(item.get("runtime_metadata"), dict) else {}).get("execution_adapter") or "").strip(),
        "readme": str(item.get("readme") or item.get("skill_body") or "").strip(),
        "install_source": str(registry.get("install_source") or "").strip(),
        "installed_at": str(registry.get("installed_at") or "").strip(),
        "uninstallable": str(item.get("source") or "").strip() == "workspace",
    }


def list_marketplace_skills() -> Dict[str, Any]:
    items = [_public_skill_payload(item) for item in list_installed_skills()]
    return {
        "ok": True,
        "items": items,
        "count": len(items),
    }


def _registry_items_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = payload.get("skills") or payload.get("items") or []
    else:
        raw_items = []
    items: List[Dict[str, Any]] = []
    installed_ids = {str(item.get("id") or "").strip() for item in list_installed_skills()}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            manifest = _validate_manifest(raw)
        except ValueError:
            continue
        readme = str(raw.get("readme") or "").strip()
        items.append({
            **manifest,
            "skill_class": str(((manifest.get("runtime") or {}) if isinstance(manifest.get("runtime"), dict) else {}).get("skill_class") or "").strip(),
            "action_class": str(((manifest.get("runtime") or {}) if isinstance(manifest.get("runtime"), dict) else {}).get("action_class") or "").strip(),
            "execution_adapter": str(((manifest.get("runtime") or {}) if isinstance(manifest.get("runtime"), dict) else {}).get("execution_adapter") or "").strip(),
            "readme": readme,
            "installed": manifest["id"] in installed_ids,
        })
    return items


def fetch_marketplace_registry(registry_url: Optional[str] = None) -> Dict[str, Any]:
    source = str(registry_url or "").strip()
    if not source:
        payload: Any = _read_json(MARKETPLACE_REGISTRY_FILE)
        if not payload:
            try:
                payload = json.loads(MARKETPLACE_REGISTRY_FILE.read_text(encoding="utf-8"))
            except Exception:
                payload = {"skills": []}
        return {"ok": True, "items": _registry_items_from_payload(payload)}
    with urlrequest.urlopen(source, timeout=15) as response:  # noqa: S310
        raw = response.read().decode("utf-8", "ignore")
    payload = json.loads(raw)
    return {"ok": True, "items": _registry_items_from_payload(payload), "source": source}


def get_marketplace_skill(skill_name: str) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_name)
    for item in list_installed_skills():
        if str(item.get("id") or "").strip() == normalized:
            return {"ok": True, "item": _public_skill_payload(item)}
    raise FileNotFoundError(f"Skill '{skill_name}' is not installed.")


def install_marketplace_skill(*, source_url: Optional[str] = None, zip_path: Optional[str] = None, allow_unsafe: bool = False) -> Dict[str, Any]:
    install_root = workspace_skills_root()
    install_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="empyralis-skill-install-") as temp_dir_raw:
        temp_root = Path(temp_dir_raw)
        source_dir = _prepare_source_tree(source_url=source_url, zip_path=zip_path, temp_root=temp_root)

        scan_result = scan_skill_dir(source_dir)
        if scan_result.get("blocked"):
            if not allow_unsafe:
                _log.warning(
                    "Skill install blocked by scanner: %s critical finding(s) in %s",
                    scan_result.get("summary", {}).get("critical", 0),
                    source_dir,
                )
                raise RuntimeError(
                    f"Skill contains unsafe code patterns and cannot be installed. "
                    f"Findings: {len(scan_result.get('findings') or [])} issue(s). "
                    f"Use allow_unsafe=True to bypass this check."
                )
            _log.warning(
                "Allowing install of skill with scanner block (allow_unsafe=True): %s",
                source_dir,
            )

        manifest = _manifest_from_dir(source_dir)
        perm_reasons = _validate_manifest_against_scanner(manifest, scan_result)
        if perm_reasons and not allow_unsafe:
            raise RuntimeError(
                f"Skill manifest permissions do not match scanned code behavior: {'; '.join(perm_reasons)}"
            )
        if perm_reasons:
            _log.warning(
                "Allowing install despite manifest/scanner mismatch (allow_unsafe=True): %s",
                perm_reasons,
            )

        destination = install_root / manifest["id"]
        _copy_tree(source_dir, destination)
        manifest = _normalize_skill_tree(destination)
        _install_requirements(_list_from_any(manifest.get("requires")))
    upsert_installed_skill_registry_entry(
        manifest["id"],
        {
            "enabled": True,
            "install_source": str(source_url or zip_path or "unknown").strip(),
            "installed_at": _utc_now_iso(),
            "format": "skill_json",
        },
    )
    return {
        "ok": True,
        "item": get_marketplace_skill(manifest["id"])["item"],
    }


def set_marketplace_skill_enabled(skill_name: str, enabled: bool) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_name)
    existing = get_marketplace_skill(normalized)["item"]
    patch = get_installed_skill_registry_entry(normalized)
    patch["enabled"] = bool(enabled)
    if not patch.get("installed_at"):
        patch["installed_at"] = _utc_now_iso()
    upsert_installed_skill_registry_entry(normalized, patch)
    updated = get_marketplace_skill(normalized)["item"]
    return {"ok": True, "item": updated, "previous_enabled": existing.get("enabled")}


def uninstall_marketplace_skill(skill_name: str) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_name)
    install_root = workspace_skills_root().resolve()
    target = (install_root / normalized).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Workspace skill '{skill_name}' is not installed.")
    if target == install_root or install_root not in target.parents:
        raise ValueError("Refusing to uninstall an unsafe skill path.")
    shutil.rmtree(target)
    remove_installed_skill_registry_entry(normalized)
    return {"ok": True, "removed": normalized}


def publish_marketplace_skill(skill_name: str, output_path: Optional[str] = None) -> Dict[str, Any]:
    normalized = normalize_skill_id(skill_name)
    skill_path: Optional[Path] = None
    manifest: Optional[Dict[str, Any]] = None
    for item in list_installed_skills():
        if str(item.get("id") or "").strip() == normalized:
            skill_path = Path(str(item.get("path") or "")).expanduser().resolve()
            manifest = {
                "name": item.get("name"),
                "version": item.get("version"),
                "description": item.get("description"),
                "author": item.get("author") or "Empyralis",
                "tools": item.get("tools") or [],
                "slash_commands": item.get("slash_commands") or [],
                "requires": item.get("requires") or [],
                "homepage": item.get("homepage") or "",
                "source": item.get("source_url") or "",
                "runtime": ((item.get("runtime_metadata") or {}) if isinstance(item.get("runtime_metadata"), dict) else {}),
            }
            break
    if skill_path is None or manifest is None:
        raise FileNotFoundError(f"Skill '{skill_name}' is not installed.")
    destination = Path(output_path).expanduser().resolve() if output_path else (MARKETPLACE_PUBLISH_DIR / f"{normalized}-{manifest['version']}.zip").resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="empyralis-skill-publish-") as temp_dir_raw:
        temp_root = Path(temp_dir_raw)
        working_dir = temp_root / normalized
        _copy_tree(skill_path, working_dir)
        _normalize_skill_tree(working_dir)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(working_dir.rglob("*")):
                if not file_path.is_file():
                    continue
                archive.write(file_path, arcname=str(Path(normalized) / file_path.relative_to(working_dir)))
    return {
        "ok": True,
        "path": str(destination),
        "item": get_marketplace_skill(normalized)["item"],
    }


def ensure_registry_seeded() -> None:
    if MARKETPLACE_REGISTRY_FILE.exists():
        return
    MARKETPLACE_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "skills": []}
    for item in list_installed_skills():
        if str(item.get("source") or "").strip() != "bundled":
            continue
        payload["skills"].append(
            {
                "name": item.get("name"),
                "version": item.get("version") or "1.0.0",
                "description": item.get("description") or "",
                "author": item.get("author") or "Empyralis",
                "tools": item.get("tools") or [],
                "slash_commands": item.get("slash_commands") or [],
                "requires": item.get("requires") or [],
                "homepage": item.get("homepage") or "",
                "source": f"local:bundled/{item.get('id')}",
                "runtime": ((item.get("runtime_metadata") or {}) if isinstance(item.get("runtime_metadata"), dict) else {}),
                "readme": item.get("readme") or item.get("skill_body") or "",
            }
        )
    _write_json(MARKETPLACE_REGISTRY_FILE, payload)


ensure_registry_seeded()
