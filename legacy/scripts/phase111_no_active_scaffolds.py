from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_SHELL = REPO_ROOT / "frontend" / "lib" / "workspace" / "workspace-shell.ts"
SURFACE_PAGE = REPO_ROOT / "frontend" / "app" / "(account)" / "w" / "[workspaceId]" / "WorkspaceSurfacePage.tsx"
SCAFFOLD_SURFACE = REPO_ROOT / "frontend" / "lib" / "workspace" / "workspace-feature-surface.tsx"

EXPECTED_ROUTE_TOKENS = [
    "case 'chat':",
    "case 'workstation':",
    "case 'runs':",
    "case 'approvals':",
    "case 'artifacts':",
    "case 'notifications':",
    "case 'applications':",
    "case 'agents':",
    "case 'activity':",
    "case 'admin/billing':",
    "case 'admin/routing':",
    "case 'admin/members':",
    "case 'admin/policies':",
    "surface === 'integrations'",
    "surface === 'settings'",
    "surface === 'admin'",
]


def main() -> None:
    workspace_shell_text = WORKSPACE_SHELL.read_text()
    surface_page_text = SURFACE_PAGE.read_text()

    assert "workspace-feature-surface" not in surface_page_text, "WorkspaceSurfacePage still imports scaffold surface."
    assert not SCAFFOLD_SURFACE.exists(), "workspace-feature-surface.tsx must be deleted."

    for token in EXPECTED_ROUTE_TOKENS:
        assert token in surface_page_text, f"WorkspaceSurfacePage is missing explicit handling for {token}"

    for route_id in [
        "chat",
        "workstation",
        "runs",
        "approvals",
        "artifacts",
        "notifications",
        "applications",
        "agents",
        "activity",
        "integrations",
        "settings",
        "admin",
        "admin/billing",
        "admin/routing",
        "admin/members",
        "admin/policies",
    ]:
        assert f"| '{route_id}'" in workspace_shell_text or f"id: '{route_id}'" in workspace_shell_text, f"Expected active route {route_id} missing from workspace shell manifest."

    print("phase111 no active workstation scaffolds ok")


if __name__ == "__main__":
    main()
