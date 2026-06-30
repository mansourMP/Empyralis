from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULES_DIR = ROOT / "server_modules"
ALLOWED_DIRECT_FILES = {
    SERVER_MODULES_DIR / "computer_control.py",
    SERVER_MODULES_DIR / "supervisor_client.py",
}
DISALLOWED_IMPORT_SNIPPETS = (
    "from . import supervisor_client",
    "import supervisor_client",
    "from server_modules import supervisor_client",
    "from . import computer_control",
    "import computer_control",
    "from server_modules import computer_control",
)


def test_server_modules_production_code_does_not_import_direct_supervisor_loopback_modules() -> None:
    offenders: list[str] = []
    for path in SERVER_MODULES_DIR.rglob("*.py"):
        if "/tests/" in path.as_posix():
            continue
        if path in ALLOWED_DIRECT_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if any(snippet in text for snippet in DISALLOWED_IMPORT_SNIPPETS):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
