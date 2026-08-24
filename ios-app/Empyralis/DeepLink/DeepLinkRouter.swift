import SwiftUI

/// Routes an incoming URL to a destination. The URL shapes come from the
/// backend's own deep_link_service, which already emits these links into
/// channel messages — so a task link an agent posts to Telegram opens
/// here rather than in Safari.
///
///   /w/{ws}/projects/{p}/tasks/{t}
///   /w/{ws}/projects/{p}/documents/{d}
///   /w/{ws}/projects/{p}
///   /w/{ws}/agents/{a}
///
/// Parsing is POSITIONAL against those known shapes rather than a loose
/// "find something that looks like an id" scan — a permissive matcher on
/// an externally-supplied URL is how an unrelated link ends up opening
/// somebody's task.
enum DeepLink: Equatable {
    case task(workspaceId: String, taskId: String)
    case document(workspaceId: String, documentId: String)
    case project(workspaceId: String, projectId: String)
    case agent(workspaceId: String, agentId: String)

    var workspaceId: String {
        switch self {
        case .task(let w, _), .document(let w, _), .project(let w, _), .agent(let w, _):
            return w
        }
    }

    static func parse(_ url: URL) -> DeepLink? {
        // Only our own origin. A link from anywhere else is not ours to
        // interpret, even if its path happens to match.
        guard let host = url.host?.lowercased(),
              host == "empyralis.ai" || host == "www.empyralis.ai" else { return nil }

        let parts = url.pathComponents.filter { $0 != "/" }
        guard parts.count >= 2, parts[0] == "w" else { return nil }
        let workspaceId = parts[1]
        guard !workspaceId.isEmpty else { return nil }

        // /w/{ws}/agents/{a}
        if parts.count >= 4, parts[2] == "agents" {
            return .agent(workspaceId: workspaceId, agentId: parts[3])
        }

        guard parts.count >= 4, parts[2] == "projects" else { return nil }
        let projectId = parts[3]

        if parts.count >= 6 {
            switch parts[4] {
            case "tasks":     return .task(workspaceId: workspaceId, taskId: parts[5])
            case "documents": return .document(workspaceId: workspaceId, documentId: parts[5])
            default:          return nil
            }
        }
        return .project(workspaceId: workspaceId, projectId: projectId)
    }

    /// A push payload carries the same destination as structured fields
    /// rather than a URL, so it needs no parsing and cannot be spoofed by
    /// a malformed string. Falls back to a `url` key when the server sends
    /// one instead.
    static func parse(notificationUserInfo info: [AnyHashable: Any]) -> DeepLink? {
        if let urlString = info["url"] as? String, let url = URL(string: urlString) {
            return parse(url)
        }
        guard let workspaceId = info["workspace_id"] as? String, !workspaceId.isEmpty else { return nil }
        if let taskId = info["task_id"] as? String, !taskId.isEmpty {
            return .task(workspaceId: workspaceId, taskId: taskId)
        }
        if let documentId = info["document_id"] as? String, !documentId.isEmpty {
            return .document(workspaceId: workspaceId, documentId: documentId)
        }
        if let agentId = info["agent_id"] as? String, !agentId.isEmpty {
            return .agent(workspaceId: workspaceId, agentId: agentId)
        }
        if let projectId = info["project_id"] as? String, !projectId.isEmpty {
            return .project(workspaceId: workspaceId, projectId: projectId)
        }
        return nil
    }
}

/// Holds the pending destination. A link can arrive while the app is
/// signed out or still restoring its session — dropping it in that window
/// is the difference between "tapping the link worked" and "tapping the
/// link opened the app and did nothing", which is the more common bug.
@MainActor
final class DeepLinkRouter: ObservableObject {
    @Published var pending: DeepLink?

    func handle(_ url: URL) {
        guard let link = DeepLink.parse(url) else { return }
        pending = link
    }

    func handle(notificationUserInfo info: [AnyHashable: Any]) {
        guard let link = DeepLink.parse(notificationUserInfo: info) else { return }
        pending = link
    }

    func consume() -> DeepLink? {
        defer { pending = nil }
        return pending
    }
}
