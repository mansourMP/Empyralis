#!/usr/bin/env python3

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

ACTIVE_WORKSTATION_FILES = [
    "frontend/app/(account)/AccountTenantSwitcher.tsx",
    "frontend/app/(account)/w/[workspaceId]/layout.tsx",
    "frontend/app/(account)/w/[workspaceId]/WorkspaceSurfacePage.tsx",
    "frontend/lib/workspace/workspace-bootstrap.ts",
    "frontend/lib/workspace/workspace-boundary.tsx",
    "frontend/lib/workspace/workspace-shell.ts",
    "frontend/lib/workspace/workspace-services.tsx",
    "frontend/lib/workspace/workstation-shell-frame.tsx",
    "frontend/lib/workspace/workstation-kernel-shell.tsx",
    "frontend/lib/workspace/workstation-client.ts",
    "frontend/lib/workspace/workstation-chat-pane.tsx",
    "frontend/lib/workspace/workstation-stage-intent.tsx",
    "frontend/lib/workspace/workstation-stage-router.ts",
    "frontend/lib/workspace/workstation-stage-pane.tsx",
    "frontend/lib/workspace/workstation-stream-manager.ts",
    "frontend/lib/workspace/workstation-desktop-bridge.ts",
    "frontend/lib/workspace/workstation-desktop-status.tsx",
    "frontend/lib/workspace/workstation-diagnostics.tsx",
    "src-tauri/src/lib.rs",
]

ALLOWED_PERSISTENCE_CALLERS = {
    "frontend/lib/workspace/workstation-kernel-shell.tsx": "WORKSTATION_SHELL_LAYOUT_KEY",
}

ALLOWED_DIRECT_STORAGE_CALLERS = {
    "frontend/lib/workspace/workstation-kernel-shell.tsx": "activeThreadStorageKey",
    "frontend/lib/workspace/workstation-chat-pane.tsx": "sage-small-ollama-model-warning",
}


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def assert_condition(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def audit_sensitive_persistence() -> None:
    for relative_path in ACTIVE_WORKSTATION_FILES:
      if not (REPO_ROOT / relative_path).exists():
          continue
      text = read(relative_path)

      if relative_path != "frontend/lib/workspace/workspace-services.tsx":
          direct_storage_marker = ALLOWED_DIRECT_STORAGE_CALLERS.get(relative_path)
          if direct_storage_marker is not None:
              assert_condition(
                  direct_storage_marker in text,
                  f"missing allowed direct storage key marker in {relative_path}",
              )
          else:
              assert_condition("localStorage" not in text, f"unexpected localStorage usage in {relative_path}")
              assert_condition("sessionStorage" not in text, f"unexpected sessionStorage usage in {relative_path}")

      if "services.persistence.setJson" in text or "services.persistence.getJson" in text:
          allowed_marker = ALLOWED_PERSISTENCE_CALLERS.get(relative_path)
          assert_condition(
              allowed_marker is not None,
              f"unexpected persistence usage in {relative_path}",
          )
          assert_condition(
              allowed_marker in text,
              f"missing allowed persistence key marker in {relative_path}",
          )

      assert_condition("draftPersistenceKey" not in text, f"legacy draft persistence key in {relative_path}")
      assert_condition("threadPersistenceKey" not in text, f"legacy thread persistence key in {relative_path}")
      assert_condition("sessionPersistenceKey" not in text, f"legacy session persistence key in {relative_path}")


def audit_legacy_backend_references() -> None:
    for relative_path in ACTIVE_WORKSTATION_FILES:
        if not (REPO_ROOT / relative_path).exists():
            continue
        text = read(relative_path)
        assert_condition("backend/dist/main.js" not in text, f"legacy backend entrypoint reference in {relative_path}")
        assert_condition("spawn_backend(" not in text, f"legacy backend launch reference in {relative_path}")
        assert_condition("backend_entrypoint(" not in text, f"legacy backend helper reference in {relative_path}")
        assert_condition("NestJS" not in text, f"legacy NestJS reference in {relative_path}")


def main() -> None:
    audit_sensitive_persistence()
    audit_legacy_backend_references()
    print("phase108 workstation static audit ok")


if __name__ == "__main__":
    main()
