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
    chat_text = read("frontend/lib/workspace/workstation-chat-pane.tsx")
    feature_text = read("frontend/lib/workspace/workspace-feature-surface.tsx")

    assert_contains(chat_text, "services.client.submitTurnWithSessionRetry", "canonical turn retry path")
    assert_contains(chat_text, "services.client.getThread", "canonical thread load path")
    assert_contains(chat_text, "services.client.listRuns", "canonical runs load path")
    assert_contains(chat_text, "services.client.listApprovals", "canonical approvals load path")
    assert_contains(chat_text, "const [activeThreadId, setActiveThreadId] = useState<string>(", "active thread state")
    assert_contains(chat_text, "const loadThread = async (requestedThreadId = activeThreadId)", "explicit thread reload helper")
    assert_contains(chat_text, "services.queryClient.set(ACTIVE_THREAD_QUERY_KEY, nextThread.threadId);", "ephemeral thread pointer")
    assert_contains(chat_text, "message.approvals.length > 0", "approval surfaces")
    assert_contains(chat_text, "message.interventions.length > 0", "intervention surfaces")
    assert_contains(chat_text, "message.runId ? (", "run chip")

    assert_not_contains(chat_text, "services.persistence", "chat persistence writes")
    assert_not_contains(chat_text, "localStorage", "local storage usage")
    assert_not_contains(chat_text, "sessionStorage", "session storage usage")
    assert_not_contains(chat_text, "/api/", "direct endpoint literals")

    assert_contains(feature_text, "import { WorkstationChatPane }", "chat pane import")
    assert_contains(feature_text, "return <WorkstationChatPane />;", "chat pane handoff")
    assert_not_contains(feature_text, "draftPersistenceKey", "old chat persistence key")
    assert_not_contains(feature_text, "threadPersistenceKey", "old chat persistence key")
    assert_not_contains(feature_text, "sessionPersistenceKey", "old chat persistence key")

    print("phase103 workstation chat pane contract ok")


if __name__ == "__main__":
    main()
