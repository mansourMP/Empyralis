from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, NamedTuple, Optional


_SCRIPT_EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".sh", ".bash", ".zsh"}
_TOP_LEVEL_SCRIPT_NAMES = {"handler.py", "query_handler.py", "worker.py"}
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", "dist", "build"}
_MAX_FILE_BYTES = 512_000


class _Source(NamedTuple):
    path: Path
    relative_path: str
    text: str
    line_starts: List[int]


def _line_starts(text: str) -> List[int]:
    starts = [0]
    for match in re.finditer(r"\n", text):
        starts.append(match.end())
    return starts


def _line_for_offset(source: _Source, offset: int) -> int:
    line = 1
    for index, start in enumerate(source.line_starts, start=1):
        if start > offset:
            break
        line = index
    return line


def _offset_for_ast_node(source: _Source, node: ast.AST) -> int:
    lineno = max(int(getattr(node, "lineno", 1) or 1), 1)
    col_offset = max(int(getattr(node, "col_offset", 0) or 0), 0)
    if lineno <= len(source.line_starts):
        return source.line_starts[lineno - 1] + col_offset
    return 0


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _strip_markdown_examples(text: str) -> str:
    lines = str(text or "").splitlines(keepends=True)
    filtered: List[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            filtered.append("\n")
            continue
        if in_fence or stripped.startswith(">"):
            filtered.append("\n")
            continue
        filtered.append(re.sub(r"`[^`\n]+`", "", line))
    return "".join(filtered)


def _source_for_file(skill_dir: Path, path: Path) -> Optional[_Source]:
    text = _read_text(path)
    if not text:
        return None
    if path.name == "SKILL.md":
        text = _strip_markdown_examples(text)
    try:
        relative_path = str(path.relative_to(skill_dir))
    except ValueError:
        relative_path = path.name
    return _Source(path=path, relative_path=relative_path, text=text, line_starts=_line_starts(text))


def _iter_scan_files(skill_dir: Path) -> Iterable[Path]:
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file():
        yield skill_md
    for name in sorted(_TOP_LEVEL_SCRIPT_NAMES):
        candidate = skill_dir / name
        if candidate.is_file():
            yield candidate
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        return
    for path in sorted(scripts_dir.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in _SCRIPT_EXTENSIONS:
            yield path


def _snippet(source: _Source, offset: int, length: int = 120) -> str:
    start = max(0, offset - 40)
    end = min(len(source.text), offset + length)
    return " ".join(source.text[start:end].split())[:240]


def _finding(
    *,
    severity: str,
    code: str,
    title: str,
    source: _Source,
    offset: int,
    detail: str,
) -> Dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "title": title,
        "path": source.relative_path,
        "line": _line_for_offset(source, offset),
        "detail": detail,
        "evidence": _snippet(source, offset),
    }


def _add_regex_findings(
    findings: List[Dict[str, Any]],
    source: _Source,
    *,
    pattern: str,
    severity: str,
    code: str,
    title: str,
    detail: str,
    flags: int = re.IGNORECASE,
) -> None:
    for match in re.finditer(pattern, source.text, flags):
        findings.append(
            _finding(
                severity=severity,
                code=code,
                title=title,
                source=source,
                offset=match.start(),
                detail=detail,
            )
        )


def _has(pattern: str, text: str, flags: int = re.IGNORECASE) -> bool:
    return re.search(pattern, text, flags) is not None


def _first_offset(pattern: str, text: str, flags: int = re.IGNORECASE) -> int:
    match = re.search(pattern, text, flags)
    return match.start() if match else 0


def _literal_string(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _python_call_name(node: ast.AST, aliases: Dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _python_call_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        call_name = _python_call_name(node.func, aliases)
        if call_name == "__import__":
            module_name = _literal_string(node.args[0] if node.args else None)
            return module_name
    return ""


def _python_call_has_shell_true(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg != "shell":
            continue
        if isinstance(keyword.value, ast.Constant):
            return bool(keyword.value.value)
        return True
    return False


def _scan_python_ast(source: _Source) -> List[Dict[str, Any]]:
    if source.path.suffix.lower() != ".py":
        return []
    try:
        tree = ast.parse(source.text)
    except SyntaxError:
        return []

    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = str(alias.name or "")
                if name in {"os", "subprocess", "builtins", "importlib"}:
                    aliases[str(alias.asname or name)] = name
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            if module in {"os", "subprocess", "builtins", "importlib"}:
                for alias in node.names:
                    imported_name = str(alias.name or "")
                    local_name = str(alias.asname or imported_name)
                    aliases[local_name] = f"{module}.{imported_name}"

    findings: List[Dict[str, Any]] = []
    subprocess_calls = {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _python_call_name(node.func, aliases)
        offset = _offset_for_ast_node(source, node)

        if call_name in {"eval", "builtins.eval"}:
            findings.append(
                _finding(
                    severity="critical",
                    code="dynamic_code_eval",
                    title="Dynamic code evaluation",
                    source=source,
                    offset=offset,
                    detail="Python AST found eval usage, which can execute untrusted code even when imported or aliased.",
                )
            )
            continue
        if call_name in {"exec", "builtins.exec"}:
            findings.append(
                _finding(
                    severity="critical",
                    code="python_exec",
                    title="Python exec usage",
                    source=source,
                    offset=offset,
                    detail="Python AST found exec usage, which can execute dynamic code even when imported or aliased.",
                )
            )
            continue
        if call_name == "os.system":
            findings.append(
                _finding(
                    severity="critical",
                    code="dangerous_os_system",
                    title="Shell command execution",
                    source=source,
                    offset=offset,
                    detail="Python AST found os.system usage, including imported or aliased forms.",
                )
            )
            continue
        if call_name in subprocess_calls:
            findings.append(
                _finding(
                    severity="critical",
                    code="dangerous_subprocess_shell" if _python_call_has_shell_true(node) else "dangerous_subprocess_exec",
                    title="Subprocess execution",
                    source=source,
                    offset=offset,
                    detail=(
                        "Python AST found subprocess execution with shell=True."
                        if _python_call_has_shell_true(node)
                        else "Python AST found subprocess execution, including imported or aliased forms."
                    ),
                )
            )
            continue
        if call_name == "getattr":
            findings.append(
                _finding(
                    severity="critical",
                    code="dynamic_getattr",
                    title="Dynamic attribute lookup",
                    source=source,
                    offset=offset,
                    detail="Python AST found dynamic getattr usage, which can hide dangerous function resolution.",
                )
            )
            continue
        if call_name in {"__import__", "importlib.import_module"}:
            findings.append(
                _finding(
                    severity="critical",
                    code="dynamic_import",
                    title="Dynamic import",
                    source=source,
                    offset=offset,
                    detail="Python AST found dynamic import usage, which can hide dependency and execution intent.",
                )
            )
            continue

    return findings


_JS_TS_EXTENSIONS = {".js", ".mjs", ".cjs", ".ts", ".tsx"}


def _js_token_view(text: str) -> str:
    token_view = str(text or "")
    token_view = re.sub(r"/\*.*?\*/", " ", token_view, flags=re.S)
    token_view = re.sub(r"//[^\n\r]*", " ", token_view)
    # Iteratively fold adjacent string literals to defeat multi-part concatenation.
    _STRING_CONCAT_RE = re.compile(
        r"(['\"])([A-Za-z0-9_:@./-]{1,80})\1\s*\+\s*(['\"])([A-Za-z0-9_:@./-]{1,80})\3"
    )
    for _ in range(32):
        next_view = _STRING_CONCAT_RE.sub(
            lambda match: f'"{match.group(2)}{match.group(4)}"', token_view
        )
        if next_view == token_view:
            break
        token_view = next_view
    return token_view


def _scan_js_ts_conservative(source: _Source) -> List[Dict[str, Any]]:
    if source.path.suffix.lower() not in _JS_TS_EXTENSIONS:
        return []
    text = _js_token_view(source.text)
    findings: List[Dict[str, Any]] = []

    high_risk_patterns: tuple[tuple[str, str, str, str], ...] = (
        (
            r"\brequire\s*\(\s*['\"](?:node:)?child_process['\"]\s*\)|\bfrom\s+['\"](?:node:)?child_process['\"]",
            "js_child_process_import",
            "Node child_process import",
            "JS/TS skill imports child_process, which can execute local commands.",
        ),
        (
            r"\bimport\s*\(\s*['\"](?:node:)?child_process['\"]\s*\)|\bmodule\.constructor\._load\s*\(\s*['\"](?:node:)?child_process['\"]",
            "js_dynamic_child_process_import",
            "Dynamic child_process import",
            "JS/TS skill dynamically imports child_process, which can hide command execution intent.",
        ),
        (
            r"\bprocess\.mainModule\.require\s*\(\s*['\"](?:node:)?child_process['\"]\s*\)",
            "js_process_mainmodule_require",
            "process.mainModule require",
            "JS/TS skill uses process.mainModule.require to bypass normal import scanning.",
        ),
        (
            r"\bprocess\.mainModule\.require\b|\bmodule\.constructor\._load\b",
            "js_process_mainmodule_require",
            "Dynamic module loader",
            "JS/TS skill uses a dynamic Node module loader that can hide dangerous imports.",
        ),
        (
            r"\b(?:eval|globalThis\s*\[\s*['\"]eval['\"]\s*|window\s*\[\s*['\"]eval['\"]\s*)\s*\(",
            "dynamic_code_eval",
            "Dynamic code evaluation",
            "JS/TS skill invokes eval directly or through a global alias.",
        ),
        (
            r"\b(?:Function|globalThis\s*\[\s*['\"]Function['\"]\s*\]|window\s*\[\s*['\"]Function['\"]\s*\])\s*\(",
            "dynamic_function_constructor",
            "Dynamic Function constructor",
            "JS/TS skill constructs executable code dynamically.",
        ),
        (
            r"\b(?:net|node:net|tls|node:tls)\s*\.\s*(connect|createConnection)\s*\(|\bdgram\s*\.\s*createSocket\s*\(",
            "js_raw_network_socket",
            "Raw network socket",
            "JS/TS skill opens raw sockets, which can bypass normal connector and egress controls.",
        ),
        (
            r"\bnew\s+WebSocket\s*\(",
            "js_websocket_tunnel",
            "WebSocket tunnel",
            "JS/TS skill opens a WebSocket connection that can be used as a command or exfiltration channel.",
        ),
        (
            r"\(\s*[^)]*,\s*eval\s*\)\s*\(|\beval\s*\(\s*['\"]",
            "js_indirect_eval",
            "Indirect eval invocation",
            "JS/TS skill invokes eval directly or through an indirect reference, which can execute untrusted code.",
        ),
        (
            r"\bsetTimeout\s*\(\s*['\"]",
            "js_timer_code_injection",
            "Timer-based code injection",
            "JS/TS skill passes a string to setTimeout, which evaluates it as code.",
        ),
        (
            r"\bsetInterval\s*\(\s*['\"]",
            "js_timer_code_injection",
            "Timer-based code injection",
            "JS/TS skill passes a string to setInterval, which evaluates it as code.",
        ),
        (
            r"\bReflect\.construct\s*\(",
            "reflect_construct",
            "Reflect.construct evasion",
            "JS/TS skill uses Reflect.construct to bypass direct Function constructor detection.",
        ),
        (
            r"\bnew\s+Worker\s*\(\s*['\"]data:",
            "worker_data_url",
            "Worker data URL",
            "JS/TS skill creates a Worker from a data URL, which can execute arbitrary code in a separate thread.",
        ),
        (
            r"\bimportScripts\s*\(",
            "importscripts_url",
            "importScripts usage",
            "JS/TS skill uses importScripts to load external scripts dynamically.",
        ),
    )
    for pattern, code, title, detail in high_risk_patterns:
        _add_regex_findings(
            findings,
            source,
            pattern=pattern,
            severity="critical",
            code=code,
            title=title,
            detail=detail,
            flags=re.IGNORECASE | re.DOTALL,
        )

    if _has(r"child_process", text) and _has(r"\b(exec|execSync|spawn|spawnSync|execFile|execFileSync)\s*\(", text):
        findings.append(
            _finding(
                severity="critical",
                code="dangerous_process_exec",
                title="Shell process execution",
                source=source,
                offset=_first_offset(r"\b(exec|execSync|spawn|spawnSync|execFile|execFileSync)\s*\(", text),
                detail="JS/TS skill resolves child_process and executes a process, including obfuscated module strings.",
            )
        )

    if _has(r"\b(fs|node:fs)\s*\.\s*(readFileSync|readFile|promises\.readFile)\b|\brequire\s*\(\s*['\"](?:node:)?fs['\"]\s*\)", text) and _has(
        r"\b(fetch|XMLHttpRequest|axios\.(post|put|request)|https?\.request|new\s+WebSocket)\b|wss?://",
        text,
    ):
        findings.append(
            _finding(
                severity="critical",
                code="js_file_network_exfiltration",
                title="File read with network send",
                source=source,
                offset=_first_offset(r"\b(fs|node:fs)\b|\breadFile", text),
                detail="JS/TS skill reads local files and sends data over a network path.",
            )
        )
    if _has(r"\bprocess\.env\b|\bdotenv\b", text) and _has(
        r"\b(fetch|XMLHttpRequest|axios\.(post|put|request)|https?\.request|new\s+WebSocket)\b|wss?://",
        text,
    ):
        findings.append(
            _finding(
                severity="critical",
                code="env_network_send",
                title="Environment harvesting with network send",
                source=source,
                offset=_first_offset(r"\bprocess\.env\b|\bdotenv\b", text),
                detail="JS/TS skill reads environment variables and sends data over a network path.",
            )
        )

    # Detect dynamic import() where the argument is not a string literal (e.g., computed module name)
    if _has(r"\bimport\s*\(", text) and not _has(r"\bimport\s*\(\s*['\"]", text):
        findings.append(
            _finding(
                severity="critical",
                code="js_dynamic_import_nonliteral",
                title="Dynamic import with non-literal specifier",
                source=source,
                offset=_first_offset(r"\bimport\s*\(", text),
                detail="JS/TS skill uses dynamic import with a computed module specifier, which can hide malicious dependency loading.",
            )
        )

    return findings


def _scan_source(source: _Source) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    text = source.text
    findings.extend(_scan_python_ast(source))
    findings.extend(_scan_js_ts_conservative(source))

    _add_regex_findings(
        findings,
        source,
        pattern=r"(?<![\w$.])eval\s*\(|\bnew\s+Function\s*\(",
        severity="critical",
        code="dynamic_code_eval",
        title="Dynamic code evaluation",
        detail="Skill content invokes eval or new Function, which can execute untrusted code.",
    )
    _add_regex_findings(
        findings,
        source,
        pattern=r"(?<![\w.])exec\s*\(",
        severity="critical",
        code="python_exec",
        title="Python exec usage",
        detail="Skill content invokes Python exec, which can execute dynamic code.",
    )

    child_process_present = _has(r"child_process|node:child_process|from\s+['\"]child_process['\"]", text)
    if child_process_present:
        _add_regex_findings(
            findings,
            source,
            pattern=r"\b(exec|execSync|spawn|spawnSync|execFile|execFileSync)\s*\(",
            severity="critical",
            code="dangerous_process_exec",
            title="Shell process execution",
            detail="Node child_process execution is blocked for skills unless explicitly audited.",
        )
    _add_regex_findings(
        findings,
        source,
        pattern=r"\bspawn(?:Sync)?\s*\([^;\n]{0,500}(shell\s*:\s*true|/bin/sh|cmd\.exe|powershell|bash\s+-[lc])",
        severity="critical",
        code="dangerous_process_spawn",
        title="Dangerous spawned process context",
        detail="Spawned process uses a shell or shell interpreter context.",
        flags=re.IGNORECASE | re.DOTALL,
    )
    _add_regex_findings(
        findings,
        source,
        pattern=r"subprocess\.(run|Popen|call|check_call|check_output)\s*\([^;\n]{0,500}shell\s*=\s*True",
        severity="critical",
        code="dangerous_subprocess_shell",
        title="Dangerous subprocess shell context",
        detail="Python subprocess uses shell=True, which can execute shell-expanded input.",
        flags=re.IGNORECASE | re.DOTALL,
    )
    _add_regex_findings(
        findings,
        source,
        pattern=r"\bos\.system\s*\(",
        severity="critical",
        code="dangerous_os_system",
        title="Shell command execution",
        detail="os.system executes through a shell and is blocked for skills.",
    )

    _add_regex_findings(
        findings,
        source,
        pattern=r"\b(xmrig|stratum\+tcp|cryptonight|minergate|monero|xmrpool|nanopool|nicehash)\b",
        severity="critical",
        code="crypto_mining_marker",
        title="Crypto-mining marker",
        detail="Skill content contains crypto-mining pool, miner, or currency markers.",
    )

    env_pattern = r"\b(process\.env|os\.environ|os\.getenv|dotenv|env\s*=|environ\b)"
    network_send_pattern = (
        r"\b(fetch|axios\.(post|put|request)|requests\.(post|put|request)|urllib\.request|"
        r"https?\.request|WebSocket|curl)\b|wss?://"
    )
    file_read_pattern = (
        r"\b(fs\.readFileSync|fs\.readFile|read_text\s*\(|read_bytes\s*\(|open\s*\(|"
        r"Path\s*\([^)]*\)\.read_text|cat\s+)"
    )
    if _has(env_pattern, text) and _has(network_send_pattern, text):
        findings.append(
            _finding(
                severity="critical",
                code="env_network_send",
                title="Environment harvesting with network send",
                source=source,
                offset=_first_offset(env_pattern, text),
                detail="Skill content reads environment variables and sends data over a network path.",
            )
        )
    if _has(file_read_pattern, text) and _has(network_send_pattern, text):
        findings.append(
            _finding(
                severity="critical" if source.path.suffix.lower() in _JS_TS_EXTENSIONS else "warning",
                code="file_read_network_send",
                title="File read with network send",
                source=source,
                offset=_first_offset(file_read_pattern, text),
                detail="Skill content reads local files and also sends data over a network path.",
            )
        )

    _add_regex_findings(
        findings,
        source,
        pattern=(
            r"\b(atob|unescape|String\.fromCharCode|charCodeAt|base64\.b64decode)\s*\(|"
            r"Buffer\.from\s*\([^;\n]{0,160}['\"]base64['\"]"
        ),
        severity="warning",
        code="obfuscation_marker",
        title="Obfuscation marker",
        detail="Skill content contains common string or payload obfuscation markers.",
        flags=re.IGNORECASE | re.DOTALL,
    )
    _add_regex_findings(
        findings,
        source,
        pattern=r"wss?://[^'\"\s)]+:(4444|4445|5555|6666|6667|6697|7000|7777|8081|9001|9050|31337)\b",
        severity="warning",
        code="suspicious_websocket_port",
        title="Suspicious websocket port",
        detail="Skill content opens a websocket on a port commonly associated with tunnels, IRC, or control channels.",
    )
    return findings


def scan_skill_dir(skill_dir: Path | str) -> Dict[str, Any]:
    root = Path(skill_dir).expanduser().resolve()
    findings: List[Dict[str, Any]] = []
    scanned_files: List[str] = []
    if not root.exists() or not root.is_dir():
        return {
            "ok": False,
            "blocked": True,
            "summary": {"critical": 1, "warning": 0},
            "findings": [
                {
                    "severity": "critical",
                    "code": "missing_skill_dir",
                    "title": "Missing skill directory",
                    "path": str(root),
                    "line": 1,
                    "detail": "Skill directory does not exist or is not a directory.",
                    "evidence": "",
                }
            ],
            "scanned_files": [],
        }
    for path in _iter_scan_files(root):
        source = _source_for_file(root, path)
        if source is None:
            continue
        scanned_files.append(source.relative_path)
        findings.extend(_scan_source(source))

    critical = sum(1 for item in findings if item.get("severity") == "critical")
    warning = sum(1 for item in findings if item.get("severity") == "warning")
    return {
        "ok": critical == 0,
        "blocked": critical > 0,
        "summary": {"critical": critical, "warning": warning},
        "findings": findings,
        "scanned_files": scanned_files,
    }
