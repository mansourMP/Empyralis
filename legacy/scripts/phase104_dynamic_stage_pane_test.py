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
    stage_text = read("frontend/lib/workspace/workstation-stage-pane.tsx")
    router_text = read("frontend/lib/workspace/workstation-stage-router.ts")
    shell_text = read("frontend/lib/workspace/workstation-kernel-shell.tsx")
    client_text = read("frontend/lib/workspace/workstation-client.ts")

    assert_contains(stage_text, "resolveRouteIdFromHref", "route-derived stage selection")
    assert_contains(stage_text, "useWorkstationStageIntentState()", "kernel intent source")
    assert_contains(stage_text, "services.client.listRuns", "canonical run load")
    assert_contains(stage_text, "services.client.listApprovals", "canonical approval load")
    assert_contains(stage_text, "services.client.listArtifacts", "canonical artifact load")
    assert_contains(stage_text, "routeId === 'runs'", "run route support")
    assert_contains(stage_text, "routeId === 'approvals'", "approval route support")
    assert_contains(stage_text, "routeId === 'artifacts'", "artifact route support")
    assert_contains(stage_text, "routeId === 'agents'", "agent route support")
    assert_contains(stage_text, "routeId === 'applications'", "application route support")
    assert_contains(stage_text, "router.replace(nextHref, { scroll: false })", "route-safe stage sync")
    assert_contains(stage_text, "data-stage-scope-guard=\"blocked\"", "scope guard contract")
    assert_contains(stage_text, "The requested", "blocked mismatch message")

    assert_not_contains(stage_text, "services.persistence", "stage persistence writes")
    assert_not_contains(stage_text, "localStorage", "local storage usage")
    assert_not_contains(stage_text, "sessionStorage", "session storage usage")
    assert_not_contains(stage_text, "/api/", "direct endpoint literals")

    assert_contains(router_text, "type WorkstationStageRouteKind =", "stage route model")
    assert_contains(router_text, "'run_detail'", "run detail view kind")
    assert_contains(router_text, "'approval_detail'", "approval detail view kind")
    assert_contains(router_text, "'artifact_document'", "artifact/document view kind")
    assert_contains(router_text, "'agent_detail'", "agent detail view kind")
    assert_contains(router_text, "'application_detail'", "application detail view kind")
    assert_contains(router_text, "readWorkstationStageRouteState", "stage query reader")
    assert_contains(router_text, "writeWorkstationStageRouteState", "stage query writer")
    assert_contains(router_text, "clearWorkstationStageRouteState", "stage query clearer")

    assert_contains(client_text, "listArtifacts: (options?: { limit?: number }) => Promise<Record<string, unknown>>;", "artifact client contract")
    assert_contains(client_text, "listArtifacts: ({ limit = 80 } = {}) =>", "artifact client implementation")

    assert_contains(shell_text, "import { WorkstationStagePane }", "stage pane import")
    assert_contains(shell_text, "<WorkstationStagePane />", "stage pane mount")
    assert_not_contains(shell_text, "Stage reconstruction and live detail panels land in W5.7", "old placeholder copy")

    print("phase104 dynamic stage pane contract ok")


if __name__ == "__main__":
    main()
