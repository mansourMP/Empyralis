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
    roster_text = read("frontend/lib/workspace/workstation-roster-pane.tsx")
    stage_text = read("frontend/lib/workspace/workstation-stage-intent.tsx")
    shell_text = read("frontend/lib/workspace/workstation-kernel-shell.tsx")
    stage_pane_text = read("frontend/lib/workspace/workstation-stage-pane.tsx")

    assert_contains(roster_text, "WORKSTATION_ROSTER_UI_KEY = 'pane:roster:v1'", "roster UI persistence key")
    assert_contains(roster_text, "bootstrap.runtime.runtimeTargets", "bootstrap runtime target source")
    assert_contains(roster_text, "routeManifest.navGroups", "canonical application source")
    assert_contains(roster_text, "workspaceTraits['documentHeavy']", "bootstrap-driven agent source")
    assert_contains(roster_text, "workspaceTraits['adminHeavy']", "bootstrap-driven agent source")
    assert_contains(roster_text, "services.queryClient.set('workstation:roster:sections', sections);", "kernel-scoped roster cache")
    assert_contains(roster_text, "setIntent({", "stage intent mutation")
    assert_contains(roster_text, "clearIntent();", "stage intent clear action")
    assert_contains(roster_text, "showOfflineRuntimeTargets", "roster filter state")
    assert_not_contains(roster_text, "fetch(", "direct fetch")
    assert_not_contains(roster_text, "services.transport", "direct transport")
    assert_not_contains(roster_text, "submitTurn", "chat coupling")

    assert_contains(stage_text, "services.stores.createStore<WorkstationStageIntent | null>('workstation:stage-intent', null)", "kernel stage-intent store")
    assert_not_contains(stage_text, "persistence", "stage intent persistence")

    assert_contains(shell_text, "<WorkstationRosterPane />", "pane-2 roster mount")
    assert_contains(shell_text, "<WorkstationStagePane />", "pane-4 stage mount")
    assert_contains(stage_pane_text, "const { intent } = useWorkstationStageIntentState();", "pane-4 intent reader")
    assert_contains(stage_pane_text, "label: intent.label,", "stage active selection label")
    assert_contains(stage_pane_text, "kind: intent.kind,", "stage active selection kind")

    print("phase102 agent roster pane contract ok")


if __name__ == "__main__":
    main()
