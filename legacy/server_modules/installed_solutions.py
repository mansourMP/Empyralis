from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception:  # pragma: no cover - optional during bootstrap
    yaml = None  # type: ignore[assignment]


def solutions_root() -> Path:
    explicit = str(os.getenv("ORION_INSTALLED_SOLUTIONS_DIR", "")).strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "solutions").resolve()


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_solution_dir(root: Path, candidate: Path) -> Optional[Path]:
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


def _safe_solution_file(solution_dir: Path, filename: str) -> Optional[Path]:
    candidate = (solution_dir / filename).resolve()
    if not _path_is_within(candidate, solution_dir) or not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_structured(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    raw_text = _read_text(path)
    if not raw_text.strip():
        return {}
    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw_text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _bool_from_any(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def packaged_solutions_enabled() -> bool:
    return _bool_from_any(os.getenv("ORION_PACKAGED_SOLUTIONS_ENABLED"), False)


def _load_solution_module(solution: Dict[str, Any]):
    solution_dir = Path(str(solution.get("path") or "")).expanduser().resolve()
    module_path = _safe_solution_file(solution_dir, "solution.py")
    if module_path is None:
        return None
    module_name = f"empyralis_solution_{str(solution.get('id') or 'solution').replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_solution_hook(solution: Dict[str, Any], hook_name: str, *args: Any, **kwargs: Any) -> Any:
    module = _load_solution_module(solution)
    if module is None:
        return None
    hook = getattr(module, hook_name, None)
    if not callable(hook):
        return None
    return hook(*args, **kwargs)


def list_installed_solutions() -> List[Dict[str, Any]]:
    if not packaged_solutions_enabled():
        return []
    root = solutions_root()
    if not root.exists():
        return []
    items: List[Dict[str, Any]] = []
    solution_dirs: List[Path] = []
    for path in root.iterdir():
        safe_dir = _safe_solution_dir(root, path)
        if safe_dir is not None:
            solution_dirs.append(safe_dir)
    for solution_dir in sorted(solution_dirs, key=lambda item: item.name.lower()):
        solution_md = solution_dir / "SOLUTION.md"
        install_yaml = solution_dir / "install.yaml"
        ui_preset_path = solution_dir / "ui_preset.json"
        if not solution_md.exists():
            continue
        install = _load_structured(install_yaml)
        ui_preset = _load_structured(ui_preset_path)
        solution_id = str(install.get("id") or solution_dir.name).strip().lower() or solution_dir.name.lower()
        route_base = str(install.get("route_base") or ui_preset.get("route_base") or f"/solutions/{solution_id}").strip()
        item: Dict[str, Any] = {
            "id": solution_id[:120],
            "name": str(install.get("name") or solution_dir.name).strip() or solution_dir.name,
            "description": str(install.get("description") or "").strip()[:500],
            "enabled": _bool_from_any(install.get("enabled"), True),
            "path": str(solution_dir),
            "route_base": route_base,
            "primary_route": str(ui_preset.get("primary_route") or route_base).strip() or route_base,
            "required_skills": [str(value).strip() for value in (install.get("required_skills") if isinstance(install.get("required_skills"), list) else []) if str(value).strip()],
            "data_roots": install.get("data_roots") if isinstance(install.get("data_roots"), list) else [],
            "workflows": install.get("workflows") if isinstance(install.get("workflows"), list) else [],
            "ui_preset": ui_preset,
            "install": install,
        }
        try:
            status = _call_solution_hook(item, "build_status", item)
        except Exception as exc:
            status = {"ok": False, "error": str(exc)}
        if isinstance(status, dict):
            item["status"] = status
        items.append(item)
    return items


def active_installed_solutions() -> List[Dict[str, Any]]:
    return [item for item in list_installed_solutions() if bool(item.get("enabled"))]


def find_installed_solution(solution_id: str) -> Optional[Dict[str, Any]]:
    target = str(solution_id or "").strip().lower()
    if not target:
        return None
    for item in list_installed_solutions():
        if str(item.get("id") or "").strip().lower() == target:
            return item
    return None


def call_installed_solution_hook(solution_id: str, hook_name: str, *args: Any, **kwargs: Any) -> Any:
    solution = find_installed_solution(solution_id)
    if solution is None:
        raise RuntimeError(f"Installed solution '{solution_id}' was not found.")
    return _call_solution_hook(solution, hook_name, solution, *args, **kwargs)
