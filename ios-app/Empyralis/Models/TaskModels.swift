import Foundation

/// Mirrors project_tasks_service.list_tasks row shape as returned by
/// GET /api/w/{workspace_id}/fleet/tasks. Deliberately narrow — only the
/// fields this app's surfaces render. Extend when a screen needs more,
/// never speculatively.
///
/// Codable (not just Decodable) because the local-first store writes these
/// straight back to disk — see WorkspaceStore.
struct EmpTask: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let status: String
    let projectId: String?
    let priority: String?
    let displayId: String?
    let createdAt: String?
    let description: String?

    enum CodingKeys: String, CodingKey {
        case id, title, status, priority, description
        case projectId = "project_id"
        case displayId = "display_id"
        case createdAt = "created_at"
    }

    /// Used by the optimistic write path — returns a copy with a new status
    /// so the UI can update before the server has confirmed anything.
    func withStatus(_ newStatus: String) -> EmpTask {
        EmpTask(
            id: id,
            title: title,
            status: newStatus,
            projectId: projectId,
            priority: priority,
            displayId: displayId,
            createdAt: createdAt,
            description: description
        )
    }

    /// The vocabulary is project_tasks_service.TASK_STATUS_ORDER. Anything
    /// outside it renders raw rather than being coerced into a guess.
    var statusLabel: String {
        switch status {
        case "open": return "Todo"
        case "in_progress": return "In Progress"
        case "blocked": return "Blocked"
        case "awaiting_input": return "Needs Input"
        case "in_review": return "In Review"
        case "done": return "Done"
        default: return status.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    var isDone: Bool { status == "done" }
}

struct TasksResponse: Decodable {
    let ok: Bool
    let tasks: [EmpTask]
}

struct Project: Codable, Identifiable, Equatable {
    let id: String
    let name: String
}

struct ProjectsResponse: Decodable {
    let ok: Bool
    let projects: [Project]
}

/// The statuses a person may move a task to from this app. Mirrors the
/// backend vocabulary; `in_review` is deliberately included because the
/// PATCH endpoint accepts it as a plain field update.
enum TaskStatusOption: String, CaseIterable {
    case open
    case inProgress = "in_progress"
    case blocked
    case awaitingInput = "awaiting_input"
    case inReview = "in_review"
    case done

    var label: String {
        switch self {
        case .open: return "Todo"
        case .inProgress: return "In Progress"
        case .blocked: return "Blocked"
        case .awaitingInput: return "Needs Input"
        case .inReview: return "In Review"
        case .done: return "Done"
        }
    }
}
