"""Proof that the three approval operations classify as `run_approvals`
against the Rust kernel gate, matching the kernel's own authoritative
mapping.

`server_modules/run_state_repository.py`'s `_enforce_runtime_state_store_
decision` infers a `state_class` for the Rust kernel's runtime-state-store-
decision request when the caller doesn't pass one explicitly. Before this
fix it special-cased only `archive_run` -> "run_archive" and
`upsert_runtime_registration` -> "runtime_registrations"; every other
operation, including all three approval writes
(`create_or_update_approval_request`, `resolve_approval_if_pending`,
`record_approval_resolution`), fell through to the generic "live_runs"
class.

`empyralis-runtime-kernel/src/runtime_state_store.rs`'s own
`default_state_class()` is explicit that all three approval operations map
to `"run_approvals"`:

    "create_or_update_approval_request"
    | "resolve_approval_if_pending"
    | "record_approval_resolution" => "run_approvals",

so Python and Rust disagreed about what these three operations are. The
Rust kernel is authoritative (it's the actual state-store gate); this file
proves the Python side now matches it.

No live database needed: `_enforce_runtime_state_store_decision` is a
synchronous function that builds its request dict and calls straight into
`rust_runtime_kernel_client.run_runtime_kernel_enforced` BEFORE any pool
access. Patching that call to raise (same technique the pre-existing
`test_run_state_repository_approval_rust_gate.py` already uses for its own
operation/state_class assertions) lets this file inspect the exact request
dict the function built, with no Postgres involved. Not touching that
pre-existing file, per this change's collision protocol -- this is a new,
narrower file whose only job is the state_class mapping, so a future edit
to that file's other assertions can't mask a regression here.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest

from server_modules import run_state_repository


def _captured_request(operation: str, **kwargs) -> dict:
    """Patch the kernel call to raise immediately (before it can return
    anything _enforce_runtime_state_store_decision would try to read), and
    hand back the exact request dict that call was made with."""
    with patch.object(
        run_state_repository.rust_runtime_kernel_client,
        "run_runtime_kernel_enforced",
        side_effect=RuntimeError("blocked for capture"),
    ) as kernel:
        with pytest.raises(RuntimeError, match="blocked for capture"):
            run_state_repository._enforce_runtime_state_store_decision(
                operation=operation,
                run_id="run-1",
                workspace_id="workspace-1",
                tenant_id="tenant-1",
                **kwargs,
            )
    command, request = kernel.call_args.args
    assert command == "runtime-state-store-decision"
    return request


def test_create_or_update_approval_request_classifies_as_run_approvals():
    request = _captured_request("create_or_update_approval_request", status="requested")
    assert request["operation"] == "create_or_update_approval_request"
    assert request["state_class"] == "run_approvals"


def test_resolve_approval_if_pending_classifies_as_run_approvals():
    request = _captured_request("resolve_approval_if_pending", status="resolved")
    assert request["operation"] == "resolve_approval_if_pending"
    assert request["state_class"] == "run_approvals"


def test_record_approval_resolution_classifies_as_run_approvals():
    request = _captured_request("record_approval_resolution", status="resolved")
    assert request["operation"] == "record_approval_resolution"
    assert request["state_class"] == "run_approvals"


def test_archive_run_still_classifies_as_run_archive():
    """Regression guard: the fix must not disturb the two special cases
    that already worked (archive_run, upsert_runtime_registration)."""
    request = _captured_request("archive_run", status="archived")
    assert request["state_class"] == "run_archive"


def test_upsert_runtime_registration_still_classifies_as_runtime_registrations():
    request = _captured_request("upsert_runtime_registration", status="active")
    assert request["state_class"] == "runtime_registrations"


def test_an_unrelated_operation_still_falls_through_to_live_runs():
    """The fallthrough itself is correct and deliberate for operations that
    really are live-run state -- only the three approval operations were
    misclassified, not the fallthrough mechanism."""
    request = _captured_request("upsert_live_run", status="running")
    assert request["state_class"] == "live_runs"


def test_explicit_state_class_override_is_never_shadowed():
    """A caller-supplied `state_class` must still win over inference --
    the fix only changes what happens when `state_class is None`."""
    request = _captured_request(
        "create_or_update_approval_request", status="requested", state_class="custom_class",
    )
    assert request["state_class"] == "custom_class"


def test_matches_rust_kernel_default_state_class_mapping_for_approvals():
    """Direct cross-check against the Rust kernel's own source text for the
    three approval arms of `default_state_class()`
    (empyralis-runtime-kernel/src/runtime_state_store.rs) -- guards against
    Python and Rust silently drifting apart again if either side is edited
    without the other. Source-text matching rather than a compiled-kernel
    call because this repository's Python test suite has no Rust toolchain
    dependency today; this is a documentation-level guard, not a substitute
    for the kernel's own Rust unit tests."""
    kernel_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "empyralis-runtime-kernel"
        / "src"
        / "runtime_state_store.rs"
    )
    if not kernel_path.exists():
        pytest.skip(f"Rust kernel source not found at {kernel_path} — nothing to cross-check.")
    kernel_source = kernel_path.read_text()

    # Anchor on the `default_state_class` function specifically -- the
    # operation name string appears elsewhere in this file too (e.g. the
    # audit-visibility/next-action tables), and matching those would prove
    # nothing about state_class.
    function_start = kernel_source.index("fn default_state_class(")
    approvals_arm_start = kernel_source.index('"create_or_update_approval_request"', function_start)
    arrow_index = kernel_source.index("=>", approvals_arm_start)
    approvals_arm_end = kernel_source.index(",", arrow_index)
    approvals_arm = kernel_source[approvals_arm_start:approvals_arm_end]
    assert "resolve_approval_if_pending" in approvals_arm
    assert "record_approval_resolution" in approvals_arm
    assert "run_approvals" in approvals_arm

    for operation in (
        "create_or_update_approval_request",
        "resolve_approval_if_pending",
        "record_approval_resolution",
    ):
        request = _captured_request(operation, status="x")
        assert request["state_class"] == "run_approvals", (
            f"Python's _enforce_runtime_state_store_decision classifies {operation!r} as "
            f"{request['state_class']!r}, but the Rust kernel's default_state_class() maps it "
            "to 'run_approvals'. Python and Rust must agree."
        )
