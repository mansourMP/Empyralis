"""Shell tool — run a command via subprocess. 30s timeout. Home dir.

Following MAN-15: no command blocking. Agent uses its own reasoning."""

import os
import subprocess
import time

TIMEOUT = 30  # seconds
WORKDIR = os.path.expanduser("~")

TOOL_DEF = {
    "name": "shell",
    "description": (
        "Run a shell command in the user's home directory. "
        "Returns stdout, stderr, and exit code. "
        f"Commands are killed after {TIMEOUT}s timeout. "
        "Use this for file operations, system commands, and data processing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute. "
                               "Prefer non-interactive, idempotent commands.",
            },
        },
        "required": ["command"],
    },
}


def run(command: str) -> str:
    """Execute a shell command, return stdout + stderr + exit code summary."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=WORKDIR,
        )
        elapsed = time.monotonic() - started
        out = proc.stdout
        if proc.stderr:
            out += f"\n[stderr]\n{proc.stderr}"
        out += f"\n[exit:{proc.returncode} | {elapsed:.1f}s]"
        return out
    except subprocess.TimeoutExpired:
        return f"Timed out after {TIMEOUT}s (killed)"
    except Exception as exc:
        return f"Shell error: {exc}"
