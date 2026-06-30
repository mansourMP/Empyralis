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
    layout_text = read("frontend/app/(account)/w/[workspaceId]/layout.tsx")
    surface_text = read("frontend/app/(account)/w/[workspaceId]/WorkspaceSurfacePage.tsx")
    frame_text = read("frontend/lib/workspace/workstation-shell-frame.tsx")
    kernel_text = read("frontend/lib/workspace/workstation-kernel-shell.tsx")

    assert_contains(layout_text, "AccountTenantSwitcher", "pane-1 switcher import")
    assert_contains(layout_text, "WorkstationShellFrame", "shell frame import")
    assert_contains(layout_text, "WorkstationKernelShell", "kernel shell import")
    assert_contains(layout_text, "switcherPane={<AccountTenantSwitcher />}", "pane-1 shell mount")
    assert_contains(layout_text, "kernelPane={(", "kernel pane mount")
    assert_contains(layout_text, "<WorkspaceBoundary workspaceId={workspaceId} bootstrap={bootstrap}>", "kernel boundary mount")
    assert_contains(layout_text, "<WorkstationKernelShell>", "kernel shell mount")

    assert_contains(frame_text, 'data-workstation-pane="1"', "pane-1 marker")
    assert_contains(frame_text, 'data-workstation-shell="frame"', "outer shell marker")
    assert_contains(frame_text, 'data-workstation-shell="kernel-host"', "kernel host marker")

    assert_contains(kernel_text, 'paneId="2"', "pane-2 declaration")
    assert_contains(kernel_text, 'paneId="3"', "pane-3 declaration")
    assert_contains(kernel_text, 'paneId="4"', "pane-4 declaration")
    assert_contains(kernel_text, "layout:workstation-shell:v1", "whitelisted layout persistence key")
    assert_contains(
        kernel_text,
        "kernel.persistence.setJson(WORKSTATION_SHELL_LAYOUT_KEY, layout);",
        "kernel layout persistence",
    )

    assert_contains(surface_text, "WorkstationRouteFallback", "route fallback mount")
    assert_contains(surface_text, "WorkstationSurfaceViewport", "surface viewport mount")
    assert_not_contains(surface_text, "minHeight: '100vh'", "page-height fallback")

    print("phase99 workstation shell layout contract ok")


if __name__ == "__main__":
    main()
