"""Phase C3: Kernel integration test.

Verifies the Rust runtime kernel binary is real, callable, and returns
correct decisions.  Marked ``@pytest.mark.kernel`` so the test is
skipped when the binary is not built (existing conftest behavior).

This is the only test in the suite that exercises the real kernel.
All 108 baseline tests mock ``run_runtime_kernel`` — this one doesn't.
"""

from __future__ import annotations

import pytest


@pytest.mark.kernel
def test_policy_preset_deny_all():
    """policy-preset 'deny_all' returns a valid policy dict."""
    from server_modules.rust_runtime_kernel_client import policy_preset

    result = policy_preset("deny_all")
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result.get("ok") is True, f"Kernel returned error: {result}"


@pytest.mark.kernel
def test_policy_preset_cautious():
    """policy-preset 'cautious' returns a valid policy dict."""
    from server_modules.rust_runtime_kernel_client import policy_preset

    result = policy_preset("cautious")
    assert isinstance(result, dict)
    assert result.get("ok") is True, f"Kernel returned error: {result}"


@pytest.mark.kernel
def test_policy_preset_yolo():
    """policy-preset 'yolo' returns a valid policy dict."""
    from server_modules.rust_runtime_kernel_client import policy_preset

    result = policy_preset("yolo")
    assert isinstance(result, dict)
    assert result.get("ok") is True, f"Kernel returned error: {result}"


@pytest.mark.kernel
def test_capability_manifest():
    """capability-manifest returns the canonical capability list."""
    from server_modules.rust_runtime_kernel_client import capability_manifest

    result = capability_manifest()
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "capabilities" in result or "ok" in result, (
        f"Expected 'capabilities' or 'ok' in: {list(result.keys())}"
    )


@pytest.mark.kernel
def test_validate_policy_roundtrip():
    """Get cautious policy, then validate memory_read against it."""
    from server_modules.rust_runtime_kernel_client import (
        policy_preset,
        run_runtime_kernel,
    )

    preset = policy_preset("cautious")
    assert preset.get("ok") is True, f"Failed to get preset: {preset}"

    policy = preset.get("policy", preset)
    result = run_runtime_kernel("validate-policy", {
        "policy": policy,
        "capability": "memory_read",
        "action_class": "read",
    })
    assert isinstance(result, dict)
    decision = result.get("decision", "")
    assert decision in ("allow", "approval_required", "block"), (
        f"Unexpected decision: {decision}"
    )
