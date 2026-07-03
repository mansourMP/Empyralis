from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER_MODULES_DIR = ROOT / "server_modules"
# ARCHIVED (Phase U1): supervisor_client.py and computer_control.py moved to _archive/.
# No production code should import supervisor_client — the Rust empyralis-supervisor
# daemon is no longer part of the Empyralis product.
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
        text = path.read_text(encoding="utf-8")
        if any(snippet in text for snippet in DISALLOWED_IMPORT_SNIPPETS):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], (
        f"Phase U1 violation: {len(offenders)} file(s) still import supervisor_client "
        f"or computer_control. These modules are archived and desktop control is OUT of scope.\n"
        + "\n".join(offenders)
    )
