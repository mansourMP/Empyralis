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
    bridge_text = read("frontend/lib/workspace/workstation-desktop-bridge.ts")
    status_text = read("frontend/lib/workspace/workstation-desktop-status.tsx")
    settings_text = read("frontend/lib/workspace/workstation-settings-pane.tsx")
    rust_text = read("src-tauri/src/lib.rs")

    assert_contains(bridge_text, "resolveWorkstationDesktopBridge", "desktop bridge resolver")
    assert_contains(bridge_text, "candidateWindow.empyralisDesktop ?? candidateWindow.orionDesktop ?? null", "desktop bridge alias support")
    assert_contains(bridge_text, "if (!candidateWindow)", "browser-safe no-window guard")
    assert_contains(bridge_text, "if (!bridge || bridge.desktop !== true)", "explicit desktop availability guard")
    assert_contains(bridge_text, "window.open(normalized, '_blank', 'noopener,noreferrer');", "browser external fallback")
    assert_contains(bridge_text, "if (!desktopBridge || typeof desktopBridge.openPermissionSettings !== 'function')", "guarded permission settings action")
    assert_contains(bridge_text, "id === 'local_companion' || kind === 'local_companion'", "local companion runtime detection")
    assert_not_contains(bridge_text, "backend/dist/main.js", "removed backend path")
    assert_not_contains(bridge_text, "NEXT_PUBLIC_API_URL", "transport ownership override")

    assert_contains(status_text, "useWorkstationDesktopBridge()", "desktop bridge hook usage")
    assert_contains(status_text, 'data-workstation-desktop-bridge="native"', "native desktop mode surface")
    assert_contains(status_text, "desktop.localCompanion.present && !desktop.localCompanion.online", "guarded desktop-only permission action")
    assert_contains(status_text, "desktop.openPermissionSettings('accessibility')", "desktop permission action")

    assert_contains(settings_text, "import { WorkstationDesktopStatus }", "desktop status import")
    assert_contains(settings_text, "<WorkstationDesktopStatus />", "desktop status mount")

    assert_contains(rust_text, "window.empyralisDesktop = Object.assign(window.empyralisDesktop || {{}}, bridge);", "desktop bridge injection")
    assert_contains(rust_text, 'window.orionDesktop = window.empyralisDesktop;', "desktop bridge alias injection")
    assert_not_contains(rust_text, "backend/dist/main.js", "removed backend entrypoint")
    assert_not_contains(rust_text, "spawn_backend(", "removed backend sidecar launch")
    assert_not_contains(rust_text, "backend_entrypoint(", "removed backend entrypoint helper")

    print("phase106 workstation desktop bridge contract ok")


if __name__ == "__main__":
    main()
