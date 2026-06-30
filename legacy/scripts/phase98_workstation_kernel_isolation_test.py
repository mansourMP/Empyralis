from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_kernel_key_includes_full_scope() -> None:
    source = _read("frontend/lib/workspace/workspace-bootstrap.ts")

    _assert("export function createWorkstationKernelKey" in source, "kernel key helper is missing")
    _assert(
        "`${accountId}:${tenantId}:${workspaceId}:${membershipVersion}:${shellProfileId}`" in source,
        "kernel key does not include full workstation scope",
    )


def test_boundary_uses_full_kernel_key_and_provider() -> None:
    source = _read("frontend/lib/workspace/workspace-boundary.tsx")

    _assert("createWorkstationKernelKey({" in source, "workspace boundary does not construct the workstation kernel key")
    _assert("accountId: bootstrap.account.id" in source, "boundary key is missing accountId")
    _assert("tenantId: bootstrap.workspace.tenantId" in source, "boundary key is missing tenantId")
    _assert("membershipVersion: bootstrap.membership.version" in source, "boundary key is missing membershipVersion")
    _assert("shellProfileId," in source, "boundary key is missing shellProfileId")
    _assert("<WorkstationKernelProvider" in source, "workspace boundary does not mount the workstation kernel provider")
    _assert("key={boundaryKey}" in source, "workspace boundary does not remount on kernel key changes")
    _assert("data-workstation-kernel={kernelKey}" in source, "kernel diagnostics marker is missing")


def test_sensitive_workspace_persistence_is_blocked_centrally() -> None:
    source = _read("frontend/lib/workspace/workspace-services.tsx")

    _assert(
        "export function isWorkstationPreferencePersistenceKey" in source,
        "workspace persistence whitelist helper is missing",
    )
    _assert(
        "feature:[^:]+:surface" in source,
        "feature surface preferences are not explicitly whitelisted",
    )
    _assert(
        "if (!this.isAllowedKey(key)) {\n      this.dropKey(key);\n      return null;\n    }" in source,
        "disallowed persistence reads are not blocked",
    )
    _assert(
        "if (!this.isAllowedKey(key)) {\n      this.dropKey(key);\n      return;\n    }" in source,
        "disallowed persistence writes are not blocked",
    )
    _assert(
        "empyralis.workspace.ui.v3" in source,
        "workspace persistence namespace was not migrated to the UI-only storage prefix",
    )
    _assert(
        "empyralis.workspace.v2" in source,
        "legacy workspace persistence cleanup is missing",
    )


def test_kernel_scope_and_query_cache_are_kernel_scoped() -> None:
    source = _read("frontend/lib/workspace/workspace-services.tsx")

    _assert("const scope: WorkstationKernelScope" in source, "kernel scope object is missing")
    _assert("membershipVersion: bootstrap.membership.version" in source, "kernel scope is missing membershipVersion")
    _assert("shellProfileId," in source, "kernel scope is missing shellProfileId")
    _assert("const scopeKey = kernelKey" in source, "scope key is not bound to the kernel key")
    _assert("new WorkspaceQueryClient(\n    kernelKey," in source, "query client is not kernel scoped")
    _assert("queryClient.dispose();" in source, "kernel dispose path does not clear query client state")
    _assert("transport.dispose();" in source, "kernel dispose path does not clear transport state")
    _assert("realtime.dispose();" in source, "kernel dispose path does not clear realtime state")
    _assert("stores.dispose();" in source, "kernel dispose path does not clear store state")
    _assert("disposables.dispose();" in source, "kernel dispose path does not clear disposable state")
    _assert("services.streams.start();" in source, "kernel provider does not start streams on mount")
    _assert("return () => {" in source and "services.dispose();" in source, "kernel provider cleanup hook is missing")


def main() -> None:
    test_kernel_key_includes_full_scope()
    test_boundary_uses_full_kernel_key_and_provider()
    test_sensitive_workspace_persistence_is_blocked_centrally()
    test_kernel_scope_and_query_cache_are_kernel_scoped()
    print("phase98_workstation_kernel_isolation_test: ok")


if __name__ == "__main__":
    main()
