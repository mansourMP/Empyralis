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
    streams_text = read("frontend/lib/workspace/workstation-stream-manager.ts")
    services_text = read("frontend/lib/workspace/workspace-services.tsx")
    feature_text = read("frontend/lib/workspace/workspace-feature-surface.tsx")
    shell_text = read("frontend/lib/workspace/workstation-kernel-shell.tsx")
    chat_text = read("frontend/lib/workspace/workstation-chat-pane.tsx")
    roster_text = read("frontend/lib/workspace/workstation-roster-pane.tsx")
    stage_text = read("frontend/lib/workspace/workstation-stage-pane.tsx")

    assert_contains(streams_text, "class WorkstationStreamManager", "stream manager class")
    assert_contains(streams_text, "openNotificationsStream", "notifications stream usage")
    assert_contains(streams_text, "openChannelEventsStream", "channel stream usage")
    assert_contains(streams_text, "sinceId: this.state.notifications.cursor.id ?? undefined", "notifications cursor resume")
    assert_contains(streams_text, "sinceTs: this.state.notifications.cursor.ts ?? undefined", "notifications ts resume")
    assert_contains(streams_text, "includeBacklog = !this.state.notifications.cursor.id && !this.state.notifications.cursor.ts", "notifications backlog semantics")
    assert_contains(streams_text, "sinceId: this.state.activity.cursor.id ?? undefined", "activity cursor resume")
    assert_contains(streams_text, "sinceTs: this.state.activity.cursor.ts ?? undefined", "activity ts resume")
    assert_contains(streams_text, "source.addEventListener('notification'", "notification listener")
    assert_contains(streams_text, "source.addEventListener('inbox_event'", "activity listener")
    assert_contains(streams_text, "source.addEventListener('done'", "done reconnect handler")
    assert_contains(streams_text, "source.addEventListener('heartbeat'", "heartbeat handler")
    assert_contains(streams_text, "source.onerror = () => {", "error reconnect handler")

    assert_contains(services_text, "streams: WorkstationStreamManager;", "streams service contract")
    assert_contains(services_text, "const streams = createWorkstationStreamManager({", "stream manager creation")
    assert_contains(services_text, "services.streams.start();", "kernel-owned stream start")
    assert_contains(services_text, "streams.dispose();", "kernel stream disposal")
    assert_contains(services_text, "export function useWorkstationStreamState(): WorkstationStreamState", "stream state hook")

    assert_not_contains(feature_text, "registerPoller(() =>", "old notifications poller")
    assert_contains(feature_text, "const streamState = useWorkstationStreamState();", "feature surface stream hook")
    assert_contains(feature_text, "streamSnapshot: streamState", "feature surface stream snapshot")

    assert_contains(shell_text, "const streamState = useWorkstationStreamState();", "shell live stream hook")
    assert_contains(chat_text, "const streamState = useWorkstationStreamState();", "chat live stream hook")
    assert_contains(roster_text, "const streamState = useWorkstationStreamState();", "roster live stream hook")
    assert_contains(stage_text, "const streamState = useWorkstationStreamState();", "stage live stream hook")

    assert_not_contains(shell_text, "new EventSource", "shell-owned event source")
    assert_not_contains(chat_text, "new EventSource", "chat-owned event source")
    assert_not_contains(roster_text, "new EventSource", "roster-owned event source")
    assert_not_contains(stage_text, "new EventSource", "stage-owned event source")
    assert_not_contains(feature_text, "new EventSource", "feature-owned event source")
    assert_not_contains(shell_text, "new WebSocket", "shell-owned websocket")
    assert_not_contains(chat_text, "new WebSocket", "chat-owned websocket")
    assert_not_contains(roster_text, "new WebSocket", "roster-owned websocket")
    assert_not_contains(stage_text, "new WebSocket", "stage-owned websocket")
    assert_not_contains(feature_text, "new WebSocket", "feature-owned websocket")

    print("phase105 workstation stream manager contract ok")


if __name__ == "__main__":
    main()
