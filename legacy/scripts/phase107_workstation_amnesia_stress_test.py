#!/usr/bin/env python3

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


def build_kernel_key(
    *,
    account_id: str,
    tenant_id: str,
    workspace_id: str,
    membership_version: str,
    shell_profile_id: str,
) -> str:
    return f"{account_id}:{tenant_id}:{workspace_id}:{membership_version}:{shell_profile_id}"


def test_switch_sequence_changes_kernel_every_time() -> None:
    variants = [
        {
            "account_id": "acct-alpha",
            "tenant_id": "tenant-law-a",
            "workspace_id": "workspace-briefs",
            "membership_version": "m1",
            "shell_profile_id": "document_workstation_shell",
        },
        {
            "account_id": "acct-alpha",
            "tenant_id": "tenant-law-b",
            "workspace_id": "workspace-litigation",
            "membership_version": "m7",
            "shell_profile_id": "document_workstation_shell",
        },
        {
            "account_id": "acct-alpha",
            "tenant_id": "tenant-law-a",
            "workspace_id": "workspace-ops",
            "membership_version": "m4",
            "shell_profile_id": "operations_admin_shell",
        },
        {
            "account_id": "acct-alpha",
            "tenant_id": "tenant-law-a",
            "workspace_id": "workspace-briefs",
            "membership_version": "m2",
            "shell_profile_id": "document_workstation_shell",
        },
    ]

    keys = [
        build_kernel_key(**variants[index % len(variants)])
        for index in range(100)
    ]

    if len(keys) != 100:
        raise AssertionError("switch stress sequence did not generate 100 switches")

    for index in range(1, len(keys)):
        if keys[index] == keys[index - 1]:
            raise AssertionError(f"kernel key did not change on switch {index}")


def test_boundary_and_provider_remount_contract() -> None:
    boundary_text = read("frontend/lib/workspace/workspace-boundary.tsx")
    bootstrap_text = read("frontend/lib/workspace/workspace-bootstrap.ts")

    assert_contains(
        bootstrap_text,
        "`${accountId}:${tenantId}:${workspaceId}:${membershipVersion}:${shellProfileId}`",
        "full-scope kernel key",
    )
    assert_contains(boundary_text, "const boundaryKey = kernelKey;", "boundary key alias")
    assert_contains(boundary_text, "key={boundaryKey}", "react remount key")
    assert_contains(boundary_text, "tenantId: bootstrap.workspace.tenantId", "tenant scope in boundary key")
    assert_contains(boundary_text, "membershipVersion: bootstrap.membership.version", "membership scope in boundary key")
    assert_contains(boundary_text, "data-workstation-kernel={kernelKey}", "kernel diagnostics marker")


def test_kernel_cleanup_contract() -> None:
    services_text = read("frontend/lib/workspace/workspace-services.tsx")

    assert_contains(services_text, "services.streams.start();", "stream manager startup")
    assert_contains(services_text, "services.dispose();", "kernel cleanup hook")
    assert_contains(services_text, "streams.dispose();", "stream disposal")
    assert_contains(services_text, "queryClient.dispose();", "query client disposal")
    assert_contains(services_text, "transport.dispose();", "transport disposal")
    assert_contains(services_text, "realtime.dispose();", "realtime disposal")
    assert_contains(services_text, "stores.dispose();", "store disposal")
    assert_contains(services_text, "disposables.dispose();", "disposable registry disposal")


def main() -> None:
    test_switch_sequence_changes_kernel_every_time()
    test_boundary_and_provider_remount_contract()
    test_kernel_cleanup_contract()
    print("phase107 workstation amnesia stress ok")


if __name__ == "__main__":
    main()
