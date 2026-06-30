#!/usr/bin/env python3

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


def assert_not_contains(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise AssertionError(f"unexpected {label}: {needle}")


def main() -> None:
    switcher_text = read("frontend/app/(account)/AccountTenantSwitcher.tsx")

    assert_contains(switcher_text, "extractRouteWorkspaceId", "route workspace helper")
    assert_contains(switcher_text, "const routeWorkspaceId = extractRouteWorkspaceId(pathname);", "route-derived active workspace")
    assert_contains(switcher_text, "const isActive = routeWorkspaceId === workspaceId;", "route-derived highlight")
    assert_contains(switcher_text, "resolveSafeWorkspaceHref", "safe route helper")
    assert_contains(switcher_text, "sanitizeWorkspaceRoute(", "route sanitization")
    assert_contains(switcher_text, "const targetHref = resolveSafeWorkspaceHref(membership, rememberedRoute);", "safe target route")
    assert_contains(switcher_text, "href={targetHref}", "route-only link target")
    assert_contains(switcher_text, "router.prefetch(targetHref);", "prefetch safe route")
    assert_contains(switcher_text, "shellProfileLabel", "shell profile display helper")
    assert_contains(switcher_text, "invalid remembered route fell back safely", "fallback status text")
    assert_contains(switcher_text, "Runs", "badge slot label")
    assert_contains(switcher_text, "Approvals", "badge slot label")
    assert_contains(switcher_text, "Activity", "badge slot label")
    assert_contains(switcher_text, "Route-only switching. The rail never mutates kernel state directly.", "route-only rail copy")

    assert_not_contains(switcher_text, "useWorkspaceBoundary", "workspace boundary import")
    assert_not_contains(switcher_text, "useWorkstationKernel", "kernel import")
    assert_not_contains(switcher_text, "router.push(", "imperative navigation")
    assert_not_contains(switcher_text, "router.replace(", "imperative replacement")
    assert_not_contains(switcher_text, "await fetch(", "direct fetch")
    assert_not_contains(switcher_text, "localStorage", "sensitive persistence")
    assert_not_contains(switcher_text, "sessionStorage", "sensitive persistence")

    print("phase101 workspace switcher rail contract ok")


if __name__ == "__main__":
    main()
