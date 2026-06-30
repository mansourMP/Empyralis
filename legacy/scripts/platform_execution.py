from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class PlatformContext:
    key: str
    is_windows: bool
    is_macos: bool
    is_linux: bool
    path_sep: str
    home_dir: Path
    temp_dir: Path


@dataclass(frozen=True)
class CommandSpec:
    argv: Tuple[str, ...]
    display: str


def current_platform_context() -> PlatformContext:
    is_windows = sys.platform == "win32"
    is_macos = sys.platform == "darwin"
    key = "windows" if is_windows else "macos" if is_macos else "linux"
    return PlatformContext(
        key=key,
        is_windows=is_windows,
        is_macos=is_macos,
        is_linux=not is_windows and not is_macos,
        path_sep=os.sep,
        home_dir=Path.home(),
        temp_dir=Path(os.getenv("TMPDIR") or os.getenv("TEMP") or "/tmp").expanduser(),
    )


CAPABILITY_CATALOG: Tuple[Dict[str, Any], ...] = (
    {
        "id": "stack.start",
        "tool_id": "shell.execute",
        "category": "runtime",
        "title": "Start local stack",
        "summary": "Start the local Empyralis runtime stack.",
        "platforms": ("macos", "windows", "linux"),
    },
    {
        "id": "stack.status",
        "tool_id": "shell.execute",
        "category": "runtime",
        "title": "Check local stack status",
        "summary": "Inspect the local Empyralis runtime stack status.",
        "platforms": ("macos", "windows", "linux"),
    },
    {
        "id": "stack.logs",
        "tool_id": "shell.execute",
        "category": "runtime",
        "title": "Open local stack logs",
        "summary": "Read local Empyralis runtime stack logs.",
        "platforms": ("macos", "windows", "linux"),
    },
    {
        "id": "device.sleep",
        "tool_id": "shell.execute",
        "category": "device",
        "title": "Sleep computer",
        "summary": "Put the computer into sleep mode.",
        "platforms": ("macos",),
    },
    {
        "id": "device.display_sleep",
        "tool_id": "shell.execute",
        "category": "device",
        "title": "Sleep display",
        "summary": "Turn off the display without full system sleep.",
        "platforms": ("macos",),
    },
    {
        "id": "device.lock_screen",
        "tool_id": "shell.execute",
        "category": "device",
        "title": "Lock screen",
        "summary": "Lock the current desktop session.",
        "platforms": ("macos",),
    },
    {
        "id": "screenshot.capture",
        "tool_id": "screenshot.capture",
        "category": "media",
        "title": "Capture screenshot",
        "summary": "Capture the current screen into the local artifact space.",
        "platforms": ("macos", "linux"),
    },
)


def _pick_existing(paths: Sequence[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path.resolve()
    return None


def _quote_windows_arg(arg: str) -> str:
    text = str(arg or "")
    if not text or any(char.isspace() for char in text) or any(char in text for char in '"&()[]{}^=;!+,`~'):
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text


def format_command(argv: Sequence[str]) -> str:
    if current_platform_context().is_windows:
        return " ".join(_quote_windows_arg(part) for part in argv)
    return " ".join(shlex.quote(str(part)) for part in argv)


def command_spec_from_operation(command: str = "", argv: Optional[Sequence[object]] = None) -> CommandSpec:
    if isinstance(argv, Sequence) and not isinstance(argv, (str, bytes)):
        clean_argv = tuple(str(part) for part in argv if str(part or "").strip())
        if clean_argv:
            return CommandSpec(argv=clean_argv, display=format_command(clean_argv))
    tokens = tokenize_command(command)
    if not tokens:
        raise RuntimeError("Command is required.")
    return CommandSpec(argv=tuple(tokens), display=str(command or "").strip() or format_command(tokens))


def _stack_script_command(script_path: Path) -> List[str]:
    if script_path.suffix.lower() == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
    return ["bash", str(script_path)]


def stack_start_script(root_dir: Path) -> Optional[Path]:
    root = Path(root_dir).resolve()
    context = current_platform_context()
    candidates: List[Path] = []
    if context.is_windows:
        candidates.extend(
            [
                root / "scripts" / "start_empyralis_local_stack.ps1",
                root / "scripts" / "start_orion_local_stack.ps1",
                root / "scripts" / "start_empyralis_local_stack.sh",
                root / "scripts" / "start_orion_local_stack.sh",
            ]
        )
    else:
        candidates.extend(
            [
                root / "scripts" / "start_empyralis_local_stack.sh",
                root / "scripts" / "start_orion_local_stack.sh",
                root / "scripts" / "start_empyralis_local_stack.ps1",
                root / "scripts" / "start_orion_local_stack.ps1",
            ]
        )
    return _pick_existing(candidates)


def stack_status_script(root_dir: Path) -> Optional[Path]:
    root = Path(root_dir).resolve()
    context = current_platform_context()
    candidates: List[Path] = []
    if context.is_windows:
        candidates.extend(
            [
                root / "scripts" / "status_empyralis_local_stack.ps1",
                root / "scripts" / "status_orion_local_stack.ps1",
                root / "scripts" / "status_empyralis_local_stack.sh",
                root / "scripts" / "status_orion_local_stack.sh",
            ]
        )
    else:
        candidates.extend(
            [
                root / "scripts" / "status_empyralis_local_stack.sh",
                root / "scripts" / "status_orion_local_stack.sh",
                root / "scripts" / "status_empyralis_local_stack.ps1",
                root / "scripts" / "status_orion_local_stack.ps1",
            ]
        )
    return _pick_existing(candidates)


def stack_logs_script(root_dir: Path) -> Optional[Path]:
    root = Path(root_dir).resolve()
    context = current_platform_context()
    candidates: List[Path] = []
    if context.is_windows:
        candidates.extend(
            [
                root / "scripts" / "logs_empyralis_local_stack.ps1",
                root / "scripts" / "logs_orion_local_stack.ps1",
                root / "scripts" / "logs_empyralis_local_stack.sh",
                root / "scripts" / "logs_orion_local_stack.sh",
            ]
        )
    else:
        candidates.extend(
            [
                root / "scripts" / "logs_empyralis_local_stack.sh",
                root / "scripts" / "logs_orion_local_stack.sh",
                root / "scripts" / "logs_empyralis_local_stack.ps1",
                root / "scripts" / "logs_orion_local_stack.ps1",
            ]
        )
    return _pick_existing(candidates)


def stack_start_command(root_dir: Path) -> Optional[List[str]]:
    script_path = stack_start_script(root_dir)
    if script_path is None:
        return None
    return _stack_script_command(script_path)


def stack_status_command(root_dir: Path) -> Optional[List[str]]:
    script_path = stack_status_script(root_dir)
    if script_path is None:
        return None
    return _stack_script_command(script_path)


def stack_logs_command(root_dir: Path) -> Optional[List[str]]:
    script_path = stack_logs_script(root_dir)
    if script_path is None:
        return None
    return _stack_script_command(script_path)


def stack_start_command_hint(root_dir: Path) -> str:
    command = stack_start_command(root_dir)
    return format_command(command) if command else "start the local stack"


def _clean_split_token(token: str) -> str:
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    return text


def tokenize_command(command: str) -> List[str]:
    cleaned = str(command or "").strip()
    if not cleaned:
        return []
    posix = not current_platform_context().is_windows
    return [_clean_split_token(part) for part in shlex.split(cleaned, posix=posix)]


def _normalized_match_token(token: str) -> str:
    text = _clean_split_token(token)
    if current_platform_context().is_windows:
        return text.replace("\\", "/").lower()
    return text


def match_allowed_command(command: str, prefixes: Sequence[str]) -> Tuple[List[str], str]:
    spec = command_spec_from_operation(command=command)
    return match_allowed_argv(spec.argv, prefixes)


def match_allowed_argv(argv: Sequence[str], prefixes: Sequence[str]) -> Tuple[List[str], str]:
    tokens = [str(part) for part in argv if str(part or "").strip()]
    if not tokens:
        raise RuntimeError("Command is required.")
    normalized_tokens = [_normalized_match_token(part) for part in tokens]
    for prefix in prefixes:
        try:
            prefix_tokens = tokenize_command(prefix)
        except Exception:
            continue
        normalized_prefix = [_normalized_match_token(part) for part in prefix_tokens]
        if normalized_prefix and normalized_tokens[: len(normalized_prefix)] == normalized_prefix:
            return tokens, prefix
    raise RuntimeError("Command is not on the local companion allowlist.")


def default_local_companion_allow_prefixes(project_root: Path) -> List[str]:
    root = Path(project_root).resolve()
    common = [
        "pwd",
        "git status",
        "git diff",
        "git log --oneline",
    ]
    context = current_platform_context()
    prefixes = list(common)
    if context.is_windows:
        prefixes.extend(
            [
                "echo",
                "dir",
                "type",
                "findstr",
                "python",
                "py -3",
                "pip",
                "python -m unittest",
                "py -3 -m unittest",
                ".venv\\Scripts\\python.exe -m unittest",
            ]
        )
        status_command = stack_status_command(root)
        if status_command:
            prefixes.append(format_command(status_command))
    else:
        prefixes.extend(
            [
                "echo",
                "ls",
                "ls -la",
                "cat",
                "pwd",
                "grep",
                "python",
                "python3",
                "pip",
                "pip3",
                "python3 -m unittest",
                ".venv/bin/python -m unittest",
                "./bin/empyralis status",
            ]
        )
        status_command = stack_status_command(root)
        if status_command:
            prefixes.append(format_command(status_command))
        logs_command = stack_logs_command(root)
        if logs_command:
            prefixes.append(format_command(logs_command))
        prefixes.extend(supported_device_actions().values())
    return prefixes


def supported_device_actions() -> Dict[str, str]:
    if current_platform_context().is_macos:
        return {
            "sleep": "pmset sleepnow",
            "display_sleep": "pmset displaysleepnow",
            "lock_screen": '"/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession" -suspend',
        }
    return {}


def capability_command(capability: str, project_root: Path) -> Optional[List[str]]:
    clean = str(capability or "").strip().lower()
    if not clean:
        return None
    if clean.startswith("device."):
        action = clean.split(".", 1)[1]
        command = supported_device_actions().get(action)
        if not command:
            return None
        return tokenize_command(command)
    if clean == "stack.start":
        return stack_start_command(project_root)
    if clean == "stack.status":
        return stack_status_command(project_root)
    if clean == "stack.logs":
        return stack_logs_command(project_root)
    return None


def capability_from_command(command: str, project_root: Path) -> Optional[str]:
    try:
        command_tokens = tokenize_command(command)
    except Exception:
        return None
    if not command_tokens:
        return None
    known_capabilities = [
        "stack.start",
        "stack.status",
        "stack.logs",
        "device.sleep",
        "device.display_sleep",
        "device.lock_screen",
    ]
    for capability in known_capabilities:
        candidate_commands: List[List[str]] = []
        resolved = capability_command(capability, project_root)
        if resolved:
            candidate_commands.append(list(resolved))
        if capability == "stack.start":
            candidate_commands.extend(
                [
                    ["bash", "scripts/start_orion_local_stack.sh"],
                    ["bash", "scripts/start_empyralis_local_stack.sh"],
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/start_orion_local_stack.ps1"],
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/start_empyralis_local_stack.ps1"],
                ]
            )
        elif capability == "stack.status":
            candidate_commands.extend(
                [
                    ["bash", "scripts/status_orion_local_stack.sh"],
                    ["bash", "scripts/status_empyralis_local_stack.sh"],
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/status_orion_local_stack.ps1"],
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/status_empyralis_local_stack.ps1"],
                ]
            )
        elif capability == "stack.logs":
            candidate_commands.extend(
                [
                    ["bash", "scripts/logs_orion_local_stack.sh"],
                    ["bash", "scripts/logs_empyralis_local_stack.sh"],
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/logs_orion_local_stack.ps1"],
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/logs_empyralis_local_stack.ps1"],
                ]
            )
        if any(candidate == command_tokens for candidate in candidate_commands):
            return capability
    return None


def supports_capability(capability: str, project_root: Path) -> bool:
    clean = str(capability or "").strip().lower()
    if not clean:
        return False
    if clean == "screenshot.capture":
        try:
            screenshot_command(Path(project_root).resolve() / ".orion-artifacts" / "capability-probe.png")
            return True
        except Exception:
            return False
    return capability_command(clean, project_root) is not None


def capability_tool_id(capability: str) -> Optional[str]:
    clean = str(capability or "").strip().lower()
    if not clean:
        return None
    if clean == "screenshot.capture":
        return "screenshot.capture"
    if clean.startswith("device.") or clean.startswith("stack."):
        return "shell.execute"
    return None


def capability_metadata(capability: str, project_root: Path) -> Optional[Dict[str, Any]]:
    clean = str(capability or "").strip().lower()
    if not clean:
        return None
    context = current_platform_context()
    for item in CAPABILITY_CATALOG:
        if str(item.get("id") or "").strip().lower() != clean:
            continue
        platforms = [str(platform) for platform in item.get("platforms") or [] if str(platform).strip()]
        supported = supports_capability(clean, project_root)
        return {
            "id": clean,
            "tool_id": str(item.get("tool_id") or capability_tool_id(clean) or "").strip(),
            "category": str(item.get("category") or "").strip(),
            "title": str(item.get("title") or "").strip(),
            "summary": str(item.get("summary") or "").strip(),
            "platforms": platforms,
            "platform_supported": supported,
            "current_platform": context.key,
        }
    tool_id = capability_tool_id(clean)
    if not tool_id:
        return None
    return {
        "id": clean,
        "tool_id": tool_id,
        "category": "custom",
        "title": clean,
        "summary": "",
        "platforms": [],
        "platform_supported": supports_capability(clean, project_root),
        "current_platform": context.key,
    }


def supported_capability_catalog(project_root: Path) -> List[Dict[str, Any]]:
    root = Path(project_root).resolve()
    items: List[Dict[str, Any]] = []
    for entry in CAPABILITY_CATALOG:
        metadata = capability_metadata(str(entry.get("id") or ""), root)
        if metadata:
            items.append(metadata)
    return items


def screenshot_command(target: Path) -> List[str]:
    context = current_platform_context()
    if context.is_macos:
        return ["screencapture", "-x", str(target)]
    if context.is_windows:
        raise RuntimeError("Screenshot capture is not implemented on Windows yet.")
    if shutil.which("gnome-screenshot"):
        return ["gnome-screenshot", "-f", str(target)]
    if shutil.which("scrot"):
        return ["scrot", str(target)]
    raise RuntimeError("No supported screenshot command found on this machine.")


def electron_command(project_root: Path) -> List[str]:
    configured = (
        str(os.getenv("EMPYRALIS_DESKTOP_ELECTRON_BIN") or "").strip()
        or str(os.getenv("ORION_DESKTOP_ELECTRON_BIN") or "").strip()
    )
    context = current_platform_context()
    candidates: List[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    root = Path(project_root).resolve()
    if context.is_windows:
        candidates.extend(
            [
                root / "desktop" / "node_modules" / "electron" / "dist" / "electron.exe",
                root / "desktop" / "node_modules" / ".bin" / "electron.cmd",
            ]
        )
    else:
        candidates.append(root / "desktop" / "node_modules" / ".bin" / "electron")
    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if context.is_windows and resolved.suffix.lower() == ".cmd":
            return ["cmd.exe", "/c", str(resolved)]
        return [str(resolved)]
    fallback = shutil.which("electron.exe" if context.is_windows else "electron")
    if fallback:
        return [fallback]
    raise RuntimeError(
        "Browser runtime not found for browser capture. Install the required local browser capture runtime "
        "or set EMPYRALIS_DESKTOP_ELECTRON_BIN."
    )
