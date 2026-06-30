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
    client_text = read("frontend/lib/workspace/workstation-client.ts")
    services_text = read("frontend/lib/workspace/workspace-services.tsx")
    feature_text = read("frontend/lib/workspace/workspace-feature-surface.tsx")
    chat_text = read("frontend/lib/workspace/workstation-chat-pane.tsx")
    mobile_shared_text = read("mobile/src/lib/surfaces/shared.js")

    assert_contains(client_text, "buildWorkstationApiPaths", "endpoint map builder")
    assert_contains(client_text, "workspaceBootstrap", "bootstrap path")
    assert_contains(client_text, "sessionCreate", "session path")
    assert_contains(client_text, "turnSubmit", "turn path")
    assert_contains(client_text, "approvalResolve", "approval resolve path")
    assert_contains(client_text, "notificationsStream", "notification stream path")
    assert_contains(client_text, "channelEventsStream", "channel event stream path")
    assert_contains(client_text, "workspace_id", "explicit workspace scope")
    assert_contains(client_text, "submitTurnWithSessionRetry", "normalized turn retry helper")
    assert_contains(client_text, "error.status !== 409", "409 retry guard")
    assert_contains(client_text, "new WorkstationClientError", "normalized client error")

    assert_contains(services_text, "createWorkstationClient(", "kernel client construction")
    assert_contains(services_text, "client: WorkstationClient", "services client type")
    assert_contains(services_text, "client: client.snapshot()", "client snapshot")

    assert_contains(feature_text, "<WorkstationChatPane />", "chat pane delegation")
    assert_not_contains(feature_text, "requestWorkspaceJson", "local request helper")
    assert_not_contains(feature_text, "services.transport.request", "direct transport request")
    assert_not_contains(feature_text, "/api/", "pane endpoint literals")

    assert_contains(chat_text, "services.client.getThread", "thread client usage")
    assert_contains(chat_text, "services.client.listRuns", "runs client usage")
    assert_contains(chat_text, "services.client.listApprovals", "approvals client usage")
    assert_contains(chat_text, "services.client.submitTurnWithSessionRetry", "turn retry client usage")
    assert_not_contains(chat_text, "fetch(", "direct fetch in chat pane")

    assert_contains(mobile_shared_text, "/api/notifications?workspace_id=", "canonical mobile notifications path")
    assert_contains(mobile_shared_text, "/api/artifacts?workspace_id=", "canonical mobile artifacts path")
    assert_not_contains(mobile_shared_text, "/api/workspaces/${workspaceId}/notifications", "stale mobile notifications path")
    assert_not_contains(mobile_shared_text, "/api/workspaces/${workspaceId}/artifacts", "stale mobile artifacts path")

    print("phase100 workstation client contract ok")


if __name__ == "__main__":
    main()
