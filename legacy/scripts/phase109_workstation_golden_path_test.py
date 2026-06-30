#!/usr/bin/env python3

import re
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


def assert_no_direct_fetch_call(text: str, label: str) -> None:
    if re.search(r"(?<!pre)fetch\(", text):
        raise AssertionError(f"unexpected {label}: direct fetch(")


def main() -> None:
    layout_text = read("frontend/app/(account)/w/[workspaceId]/layout.tsx")
    switcher_text = read("frontend/app/(account)/AccountTenantSwitcher.tsx")
    roster_text = read("frontend/lib/workspace/workstation-roster-pane.tsx")
    stage_intent_text = read("frontend/lib/workspace/workstation-stage-intent.tsx")
    chat_text = read("frontend/lib/workspace/workstation-chat-pane.tsx")
    stage_text = read("frontend/lib/workspace/workstation-stage-pane.tsx")
    shell_text = read("frontend/lib/workspace/workstation-kernel-shell.tsx")
    desktop_text = read("frontend/lib/workspace/workstation-desktop-status.tsx")
    diagnostics_text = read("frontend/lib/workspace/workstation-diagnostics.tsx")
    stream_text = read("frontend/lib/workspace/workstation-stream-manager.ts")

    assert_contains(layout_text, "<WorkstationShellFrame", "shell frame mount")
    assert_contains(layout_text, "<WorkspaceBoundary workspaceId={workspaceId} bootstrap={bootstrap}>", "workspace boundary mount")
    assert_contains(layout_text, "<WorkstationKernelShell>", "kernel shell mount")

    assert_contains(switcher_text, "<Link", "route-only switcher link")
    assert_contains(switcher_text, "router.prefetch(targetHref);", "switcher prefetch path")
    assert_not_contains(switcher_text, "useWorkstationKernel", "switcher kernel mutation")
    assert_no_direct_fetch_call(switcher_text, "switcher direct fetch")

    assert_contains(roster_text, "setIntent({", "roster stage selection")
    assert_contains(stage_intent_text, "services.stores.createStore<WorkstationStageIntent | null>('workstation:stage-intent', null)", "kernel-scoped stage intent store")
    assert_not_contains(roster_text, "submitTurn", "roster chat coupling")
    assert_no_direct_fetch_call(roster_text, "roster direct fetch")

    assert_contains(chat_text, "services.client.submitTurnWithSessionRetry", "chat canonical turn path")
    assert_contains(chat_text, "services.client.getThread", "chat thread reload path")
    assert_contains(chat_text, "services.client.listRuns", "chat runs overview path")
    assert_contains(chat_text, "services.client.listApprovals", "chat approvals overview path")
    assert_not_contains(chat_text, "services.persistence", "chat persistence")
    assert_no_direct_fetch_call(chat_text, "chat direct fetch")

    assert_contains(stage_text, "services.client.listRuns", "stage run detail path")
    assert_contains(stage_text, "services.client.listApprovals", "stage approval detail path")
    assert_contains(stage_text, "services.client.listArtifacts", "stage artifact detail path")
    assert_contains(stage_text, "resolveRouteIdFromHref", "stage route reconstruction")
    assert_contains(stage_text, "data-stage-scope-guard=\"blocked\"", "stage scope guard")
    assert_not_contains(stage_text, "services.persistence", "stage persistence")
    assert_no_direct_fetch_call(stage_text, "stage direct fetch")

    assert_contains(stream_text, "openNotificationsStream", "notifications stream manager")
    assert_contains(stream_text, "openChannelEventsStream", "activity stream manager")
    assert_contains(stream_text, "sinceId: this.state.notifications.cursor.id ?? undefined", "notification cursor resume")
    assert_contains(stream_text, "sinceId: this.state.activity.cursor.id ?? undefined", "activity cursor resume")

    assert_contains(shell_text, "<WorkstationDesktopStatus />", "desktop status mount")
    assert_contains(shell_text, "<WorkstationDiagnostics />", "diagnostics mount")
    assert_contains(shell_text, "<WorkstationRosterPane />", "roster pane mount")
    assert_contains(shell_text, "{children}", "chat viewport mount")
    assert_contains(shell_text, "<WorkstationStagePane />", "stage pane mount")

    assert_contains(desktop_text, "data-workstation-desktop-bridge={desktop.available ? 'desktop' : 'browser'}", "desktop/browser fallback contract")
    assert_contains(desktop_text, "desktop.openExternal(window.location.href)", "desktop external-open action")

    assert_contains(diagnostics_text, "data-workstation-diagnostics=\"surface\"", "diagnostics surface marker")
    assert_contains(diagnostics_text, "kernel: kernel.snapshot()", "kernel diagnostics payload")
    assert_contains(diagnostics_text, "streams: streamState", "stream diagnostics payload")

    print("phase109 workstation golden path contract ok")


if __name__ == "__main__":
    main()
