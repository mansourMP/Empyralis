"""
Architecture guard — verifies that the channel plane is cleanly separated
from the execution plane (Step 3 contract).

Channel files may import:
  - channel_adapter, sage_turn_adapter, sage_command_dispatcher, channel_gateway_bridge
  - gateway_protocol_service (for dispatch_channel_outbound — channel delivery)
  - gateway_execution_service (for channel configuration — pairing/QR setup)

Channel files MUST NOT import:
  - hardware_action_broker_service
  - hardware_runtime_adapters/*
  - hardware_runtime_target_resolver
  - virtual_computer_runtime
"""

from __future__ import annotations

import ast
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent  # server_modules/ (up from tests/)

CHANNEL_FILES = {
    "routes_wechat.py",
    "routes_imessage.py",
    "routes_slack.py",
    "routes_sage_telegram_hosted.py",
    "agent_channel_router.py",          # Path B — allowed gateway_execution_service
    "personal_channels_service.py",     # Path B bridge — allowed gateway_execution_service
    "channel_adapter.py",
    "sage_telegram_hosted_service.py",
    "sage_turn_adapter.py",
    "channel_gateway_bridge.py",
    "channel_blocking_policy_service.py",
    "channel_concurrency_service.py",
    "channel_execution_service.py",
    "channel_activity_service.py",
    "channel_event_journal_service.py",
}

# These channel files have a GRANDFATHERED exception for gateway communication
# (channel delivery + configuration only — NOT hardware dispatch).
GRANDFATHERED_GATEWAY_IMPORTS = {
    "agent_channel_router.py",
    "personal_channels_service.py",
    "channel_concurrency_service.py",
    "channel_gateway_bridge.py",
    "channel_execution_service.py",
}

# Execution modules that channel files MUST NOT import.
FORBIDDEN_EXECUTION_MODULES = {
    "hardware_action_broker_service",
    "hardware_runtime_target_resolver",
    "virtual_computer_runtime",
}


def _extract_imports(filepath: Path) -> set[str]:
    """Extract all imported module names from a Python file."""
    imports: set[str] = set()
    try:
        tree = ast.parse(filepath.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    base = node.module.split(".")[0]
                    if base == "server_modules" and node.module.count(".") >= 1:
                        # Extract the actual module name: server_modules.foo → foo
                        parts = node.module.split(".")
                        imports.add(parts[1])
                    else:
                        imports.add(base)
    except SyntaxError:
        pass
    return imports


def test_channel_execution_separation() -> None:
    """Verify channel files don't import forbidden execution modules."""
    violations: list[str] = []

    for filename in CHANNEL_FILES:
        filepath = ROOT / filename
        if not filepath.exists():
            continue

        imports = _extract_imports(filepath)

        if filename in GRANDFATHERED_GATEWAY_IMPORTS:
            # Only check for the truly forbidden modules
            forbidden_hits = imports & FORBIDDEN_EXECUTION_MODULES
        else:
            # Check for any execution module
            forbidden_hits = imports & (FORBIDDEN_EXECUTION_MODULES | {
                "gateway_execution_service",
                "gateway_protocol_service",
            })

        for mod in sorted(forbidden_hits):
            violations.append(f"{filename} imports {mod} (FORBIDDEN)")

    if violations:
        msg = "Channel plane / execution plane separation violated:\n" + "\n".join(
            f"  ❌ {v}" for v in violations
        )
        raise AssertionError(msg)

    print(f"✅ Architecture guard passed — {len(CHANNEL_FILES)} channel files, 0 violations")


def test_path_a_channels_are_pure() -> None:
    """Path A and D channels (routes_*) must NOT import any gateway module."""
    pure_channel_files = [
        "routes_wechat.py",
        "routes_imessage.py",
        "routes_slack.py",
        "routes_sage_telegram_hosted.py",
        "channel_adapter.py",
        "sage_telegram_hosted_service.py",
        "sage_turn_adapter.py",
    ]
    violations = []
    for fn in pure_channel_files:
        fp = ROOT / fn
        if not fp.exists():
            continue
        imports = _extract_imports(fp)
        gateway_hits = imports & {
            "gateway_protocol_service",
            "gateway_execution_service",
            "hardware_action_broker_service",
            "gateway_approval_service",
        }
        for mod in sorted(gateway_hits):
            violations.append(f"{fn} imports {mod}")

    if violations:
        msg = "Path A/D channels must not import gateway modules:\n" + "\n".join(
            f"  ❌ {v}" for v in violations
        )
        raise AssertionError(msg)

    print(f"✅ Path A/D channels are pure — {len(pure_channel_files)} files, 0 gateway imports")


if __name__ == "__main__":
    test_channel_execution_separation()
    test_path_a_channels_are_pure()
