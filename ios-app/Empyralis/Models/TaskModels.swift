import Foundation

/// `try? container.decodeIfPresent(...)` produces a DOUBLE optional — `T??` —
/// because the `try?` and the `decodeIfPresent` each contribute one level.
/// Written inline it flattens implicitly, which compiles, warns, and hides
/// which of the two nils a caller is actually looking at. These two helpers
/// collapse it explicitly instead.
///
/// The leniency is deliberate and matches `_row_to_task`'s own posture: a
/// missing key, a null, and a value of an unexpected type all resolve to the
/// fallback rather than failing the whole row. The backend adds task columns
/// progressively and answers reads from databases that have not yet had the
/// matching migration applied — and the same shape protects an on-disk cache
/// written by an older build of this app. `id` is the one field that stays
/// strict, because a task without one is not a task.
private extension KeyedDecodingContainer {
    func lenient<T: Decodable>(_ type: T.Type, _ key: Key, default fallback: T) -> T {
        (try? decodeIfPresent(type, forKey: key)).flatMap { $0 } ?? fallback
    }

    func lenient<T: Decodable>(_ type: T.Type, _ key: Key) -> T? {
        (try? decodeIfPresent(type, forKey: key)).flatMap { $0 }
    }
}

/// Mirrors project_tasks_service._row_to_task as returned by
/// GET /api/w/{workspace_id}/fleet/tasks. Traced from that function directly,
/// not from a docstring — routes_fleet's own `fleet_list_tasks` docstring
/// still describes the PRE-vocabulary status set ("open | in_progress |
/// blocked | awaiting_input | done"), which the service has not spoken since
/// migrations/add_task_status_vocabulary.sql. The SERVICE is the authority.
///
/// ── THE BUG THIS FILE'S PREVIOUS VERSION SHIPPED ────────────────────────
/// `priority` was typed `String?`. The backend has always sent an INTEGER
/// (`_row_to_task` -> `_normalize_priority`, which defaults to 0 and never
/// returns None), so the synthesized `decodeIfPresent(String.self)` threw
/// `DecodingError.typeMismatch` on EVERY task row. `TasksResponse` therefore
/// failed to decode, `try?` in WorkspaceStore.refresh swallowed it, and the
/// app reported "Couldn't reach Empyralis" — a false statement about a
/// server it had reached and successfully talked to. Verified by decoding
/// `{"id":"t1","priority":0}` into the old shape, which throws.
///
/// `display_id` was the same class of error pointed the other way: a key the
/// backend has never sent. The real identifier is `project_task_key` +
/// `number` composed ("GEN-12"), which is what the web's own `taskDisplayId`
/// does — so `displayId` is now COMPUTED and InboxView's id line, which has
/// silently rendered nothing, starts working without that file being touched.
///
/// Decoding is deliberately defensive on every field the backend adds
/// progressively (labels, priority, parent, assignee, completion, wake) —
/// the same deploy-before-migrate posture `_row_to_task` itself takes, and it
/// doubles as protection for an on-disk cache written by an older build.
struct EmpTask: Codable, Identifiable, Equatable {
    let id: String
    var title: String
    var status: String
    let projectId: String?
    var priority: Int
    var description: String?
    var dueAt: String?
    var labels: [TaskLabel]

    /// Exactly one of these is ever set — project_tasks_single_assignee_check
    /// backstops it at the storage layer. `assigneeType` is the backend's own
    /// derived convenience field; never re-derive it here.
    var assigneeAgentId: String?
    var assigneeUserId: String?
    var assigneeType: String?

    let createdBy: String?
    var completedByUserId: String?
    var completedByAgentId: String?
    var completedAt: String?

    let parentTaskId: String?
    let subtaskCount: Int
    let subtaskDoneCount: Int

    /// MAN-294. A task can read `in_progress` while still carrying a pending
    /// wake — that combination is precisely "assigned, but the assignee has
    /// not actually started yet", and it is the one thing on this row that
    /// exists to stop the status from being a lie.
    var pendingWakeDueAt: String?
    var pendingWakeDelayReason: String?

    let number: Int?
    let projectTaskKey: String?
    let createdAt: String?
    var updatedAt: String?

    /// Comments and the structured activity log both live under the task's
    /// free-form `metadata` JSONB — there is no task_comments table and no
    /// comments endpoint to GET (see add_task_comment's own docstring).
    var metadata: TaskMetadata

    enum CodingKeys: String, CodingKey {
        case id, title, status, priority, description, labels, metadata, number
        case projectId = "project_id"
        case dueAt = "due_at"
        case assigneeAgentId = "assignee_agent_id"
        case assigneeUserId = "assignee_user_id"
        case assigneeType = "assignee_type"
        case createdBy = "created_by"
        case completedByUserId = "completed_by_user_id"
        case completedByAgentId = "completed_by_agent_id"
        case completedAt = "completed_at"
        case parentTaskId = "parent_task_id"
        case subtaskCount = "subtask_count"
        case subtaskDoneCount = "subtask_done_count"
        case pendingWakeDueAt = "pending_wake_due_at"
        case pendingWakeDelayReason = "pending_wake_delay_reason"
        case projectTaskKey = "project_task_key"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenient(String.self, .title, default: "")
        status = c.lenient(String.self, .status, default: "todo")
        projectId = c.lenient(String.self, .projectId)
        priority = c.lenient(Int.self, .priority, default: 0)
        description = c.lenient(String.self, .description)
        dueAt = c.lenient(String.self, .dueAt)
        labels = c.lenient([TaskLabel].self, .labels, default: [])
        assigneeAgentId = c.lenient(String.self, .assigneeAgentId)
        assigneeUserId = c.lenient(String.self, .assigneeUserId)
        assigneeType = c.lenient(String.self, .assigneeType)
        createdBy = c.lenient(String.self, .createdBy)
        completedByUserId = c.lenient(String.self, .completedByUserId)
        completedByAgentId = c.lenient(String.self, .completedByAgentId)
        completedAt = c.lenient(String.self, .completedAt)
        parentTaskId = c.lenient(String.self, .parentTaskId)
        subtaskCount = c.lenient(Int.self, .subtaskCount, default: 0)
        subtaskDoneCount = c.lenient(Int.self, .subtaskDoneCount, default: 0)
        pendingWakeDueAt = c.lenient(String.self, .pendingWakeDueAt)
        pendingWakeDelayReason = c.lenient(String.self, .pendingWakeDelayReason)
        number = c.lenient(Int.self, .number)
        projectTaskKey = c.lenient(String.self, .projectTaskKey)
        createdAt = c.lenient(String.self, .createdAt)
        updatedAt = c.lenient(String.self, .updatedAt)
        metadata = c.lenient(TaskMetadata.self, .metadata, default: TaskMetadata())
    }

    /// Only the fields this app renders are written back. Round-tripping
    /// through the disk cache is therefore LOSSY for any metadata key we do
    /// not model (`plan`, future keys) — harmless because nothing here reads
    /// them, and the next refresh restores the server's full copy, but worth
    /// knowing before something starts depending on a key that isn't listed.
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(title, forKey: .title)
        try c.encode(status, forKey: .status)
        try c.encodeIfPresent(projectId, forKey: .projectId)
        try c.encode(priority, forKey: .priority)
        try c.encodeIfPresent(description, forKey: .description)
        try c.encodeIfPresent(dueAt, forKey: .dueAt)
        try c.encode(labels, forKey: .labels)
        try c.encodeIfPresent(assigneeAgentId, forKey: .assigneeAgentId)
        try c.encodeIfPresent(assigneeUserId, forKey: .assigneeUserId)
        try c.encodeIfPresent(assigneeType, forKey: .assigneeType)
        try c.encodeIfPresent(createdBy, forKey: .createdBy)
        try c.encodeIfPresent(completedByUserId, forKey: .completedByUserId)
        try c.encodeIfPresent(completedByAgentId, forKey: .completedByAgentId)
        try c.encodeIfPresent(completedAt, forKey: .completedAt)
        try c.encodeIfPresent(parentTaskId, forKey: .parentTaskId)
        try c.encode(subtaskCount, forKey: .subtaskCount)
        try c.encode(subtaskDoneCount, forKey: .subtaskDoneCount)
        try c.encodeIfPresent(pendingWakeDueAt, forKey: .pendingWakeDueAt)
        try c.encodeIfPresent(pendingWakeDelayReason, forKey: .pendingWakeDelayReason)
        try c.encodeIfPresent(number, forKey: .number)
        try c.encodeIfPresent(projectTaskKey, forKey: .projectTaskKey)
        try c.encodeIfPresent(createdAt, forKey: .createdAt)
        try c.encodeIfPresent(updatedAt, forKey: .updatedAt)
        try c.encode(metadata, forKey: .metadata)
    }

    // MARK: - Derived

    /// "GEN-12". Composed exactly the way the web's `taskDisplayId` composes
    /// it — from the project's own key plus this task's per-project sequence
    /// number. Deliberately nil rather than a uuid slice when either half is
    /// missing: "task 69D656" is a hex fragment wearing an identifier's
    /// clothes, and a caller that shows nothing is more honest than one that
    /// shows that.
    var displayId: String? {
        guard let key = projectTaskKey, !key.isEmpty, let number else { return nil }
        return "\(key)-\(number)"
    }

    /// `_normalize_status`'s one alias, applied on this side too so the
    /// status picker marks the right row for a value the live API cannot
    /// send but a stale cache can.
    var normalizedStatus: String { status == "open" ? "todo" : status }

    /// Anything outside TASK_STATUS_ORDER renders raw rather than being
    /// coerced into a guess — with ONE exception, `open`, which is not an
    /// unknown value but the vocabulary's documented legacy alias for `todo`
    /// (TASK_STATUS_ALIASES). `_normalize_status` maps it away on read so the
    /// live backend cannot hand it back, but a stale cache or an un-migrated
    /// deployment can, and "Open" beside a picker whose matching row says
    /// "Todo" reads as two different states rather than one.
    var statusLabel: String {
        if status == "open" { return TaskStatusOption.todo.label }
        return TaskStatusOption(rawValue: status)?.label
            ?? status.replacingOccurrences(of: "_", with: " ").capitalized
    }

    var priorityValue: TaskPriority { TaskPriority(rawValue: priority) ?? .none }

    var isDone: Bool { status == "done" }

    /// True when this task claims to be underway but the wake behind that
    /// claim is still sitting in the scheduler — the dishonest-status case
    /// MAN-294 added `pending_wake_due_at` to make visible.
    var hasPendingWake: Bool {
        !(pendingWakeDueAt ?? "").isEmpty
    }
}

// MARK: - Labels

/// `{id, name, color}` — `_coerce_labels` guarantees all three are present
/// and non-null, with `color` defaulting to "grey" server-side.
struct TaskLabel: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let color: String?

    var colorToken: String { (color?.isEmpty == false ? color! : "grey") }
}

struct LabelsResponse: Decodable {
    let ok: Bool
    let labels: [TaskLabel]
}

// MARK: - Metadata (comments + activity)

struct TaskMetadata: Codable, Equatable {
    var comments: [TaskComment]
    var activity: [TaskActivityEvent]

    init(comments: [TaskComment] = [], activity: [TaskActivityEvent] = []) {
        self.comments = comments
        self.activity = activity
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        comments = c.lenient([TaskComment].self, .comments, default: [])
        activity = c.lenient([TaskActivityEvent].self, .activity, default: [])
    }
}

/// One entry of `task.metadata.comments`, written by add_task_comment.
///
/// `mentions` is deliberately NOT modelled. The web renders each resolved
/// mention as an inline chip using its stored character offsets; this app
/// renders the comment body as plain text, and the body ALREADY contains the
/// literal "@name" the offsets point at — so nothing is lost, only the chip
/// styling. Modelling offsets we do not use would be dead weight.
struct TaskComment: Codable, Identifiable, Equatable {
    let id: String
    let authorType: String
    let authorId: String
    let body: String
    let createdAt: String
    let authorDisplayName: String?

    enum CodingKeys: String, CodingKey {
        case id, body
        case authorType = "author_type"
        case authorId = "author_id"
        case createdAt = "created_at"
        case authorDisplayName = "author_display_name"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.lenient(String.self, .id, default: UUID().uuidString)
        body = c.lenient(String.self, .body, default: "")
        authorType = c.lenient(String.self, .authorType, default: "unknown")
        authorId = c.lenient(String.self, .authorId, default: "unknown")
        createdAt = c.lenient(String.self, .createdAt, default: "")
        authorDisplayName = c.lenient(String.self, .authorDisplayName)
    }

    private init(id: String, authorType: String, authorId: String, body: String, createdAt: String, authorDisplayName: String?) {
        self.id = id
        self.authorType = authorType
        self.authorId = authorId
        self.body = body
        self.createdAt = createdAt
        self.authorDisplayName = authorDisplayName
    }

    /// The optimistic stand-in shown while POST .../comments is in flight.
    /// Its id is local and is replaced by the server's `comment_<hex12>` on
    /// the next refresh; `author_type: "human"` matches what
    /// add_human_task_comment writes, so it renders identically before and
    /// after the swap rather than visibly changing shape under the reader.
    static func local(body: String, authorUserId: String?) -> TaskComment {
        TaskComment(
            id: "local_\(UUID().uuidString)",
            authorType: "human",
            authorId: authorUserId ?? "",
            body: body,
            createdAt: ISO8601DateFormatter().string(from: Date()),
            authorDisplayName: nil
        )
    }

    var isPending: Bool { id.hasPrefix("local_") }
}

/// One entry of `task.metadata.activity`, written by `_record_task_activity`.
/// Keys are `type` / `actor_type` / `actor_id` / `timestamp`, with optional
/// `actor_name` and `details`.
struct TaskActivityEvent: Codable, Equatable {
    let type: String
    let actorType: String?
    let actorId: String?
    let timestamp: String
    let actorName: String?
    let details: ActivityDetails?

    enum CodingKeys: String, CodingKey {
        case type, timestamp, details
        case actorType = "actor_type"
        case actorId = "actor_id"
        case actorName = "actor_name"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = c.lenient(String.self, .type, default: "unknown")
        timestamp = c.lenient(String.self, .timestamp, default: "")
        actorType = c.lenient(String.self, .actorType)
        actorId = c.lenient(String.self, .actorId)
        actorName = c.lenient(String.self, .actorName)
        details = c.lenient(ActivityDetails.self, .details)
    }

    /// Mirrors the web's ACTIVITY_LABELS + describeActivity, including its
    /// from/to expansion for status and priority changes.
    var summary: String {
        let label: String
        switch type {
        case "status_changed": label = "changed status"
        case "priority_changed": label = "changed priority"
        case "title_edited": label = "edited the title"
        case "description_edited": label = "edited the description"
        case "due_date_changed": label = "changed the due date"
        case "assigned": label = "assigned this task"
        case "created": label = "created this task"
        default: label = type.replacingOccurrences(of: "_", with: " ")
        }

        guard let details else { return label }

        if type == "status_changed" {
            let from = details.from?.stringValue.map { TaskStatusOption(rawValue: $0)?.label ?? $0 }
            let to = details.to?.stringValue.map { TaskStatusOption(rawValue: $0)?.label ?? $0 }
            if let from, let to { return "\(label) from \(from) to \(to)" }
            if let to { return "\(label) to \(to)" }
        }
        if type == "priority_changed" {
            let from = details.from?.intValue.map { TaskPriority(rawValue: $0)?.label ?? String($0) }
            let to = details.to?.intValue.map { TaskPriority(rawValue: $0)?.label ?? String($0) }
            if let from, let to { return "\(label) from \(from) to \(to)" }
        }
        if type == "assigned", let kind = details.assigneeType {
            return "\(label) to \(kind == "agent" ? "an agent" : "a person")"
        }
        return label
    }
}

/// `details.from`/`details.to` are a String for a status change and an Int
/// for a priority change — one key, two real types, so it needs a container
/// that can hold either rather than a guess about which one wins.
struct ActivityDetails: Codable, Equatable {
    let from: ActivityValue?
    let to: ActivityValue?
    let assigneeType: String?

    enum CodingKeys: String, CodingKey {
        case from, to
        case assigneeType = "assignee_type"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        from = c.lenient(ActivityValue.self, .from)
        to = c.lenient(ActivityValue.self, .to)
        assigneeType = c.lenient(String.self, .assigneeType)
    }
}

enum ActivityValue: Codable, Equatable {
    case string(String)
    case int(Int)

    var stringValue: String? { if case .string(let s) = self { return s }; return nil }
    var intValue: Int? { if case .int(let i) = self { return i }; return nil }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let i = try? c.decode(Int.self) { self = .int(i); return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        throw DecodingError.typeMismatch(
            ActivityValue.self,
            .init(codingPath: decoder.codingPath, debugDescription: "Not a string or int")
        )
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .int(let i): try c.encode(i)
        }
    }
}

// MARK: - Vocabularies (project_tasks_service is the authority for both)

/// TASK_STATUS_ORDER, in the backend's own hand-ordered sequence. Display
/// strings are the web's STATUS_VISUALS labels verbatim, so the two products
/// name the same state the same way.
///
/// `open` is absent on purpose: it is an INPUT-ONLY legacy alias that
/// `_normalize_status` maps to `todo` on read, so the backend can never hand
/// it back. The previous version of this file offered it as a selectable
/// option while omitting `backlog` and `todo` entirely — two of the seven
/// real states were unreachable from this app.
enum TaskStatusOption: String, CaseIterable, Identifiable {
    case backlog
    case todo
    case inProgress = "in_progress"
    case awaitingInput = "awaiting_input"
    case blocked
    case inReview = "in_review"
    case done

    var id: String { rawValue }

    var label: String {
        switch self {
        case .backlog: return "Backlog"
        case .todo: return "Todo"
        case .inProgress: return "In progress"
        case .awaitingInput: return "Needs input"
        case .blocked: return "Blocked"
        case .inReview: return "In review"
        case .done: return "Done"
        }
    }
}

/// Linear's five-level scale, inversion included: 1 is the MOST urgent and 0
/// means untriaged. Stored as the integer the backend stores.
enum TaskPriority: Int, CaseIterable, Identifiable {
    case none = 0
    case urgent = 1
    case high = 2
    case medium = 3
    case low = 4

    var id: Int { rawValue }

    /// TASK_PRIORITY_ORDER — urgent first, "No priority" last. NOT numeric
    /// order, because 0 belongs at the bottom rather than the top.
    static let pickerOrder: [TaskPriority] = [.urgent, .high, .medium, .low, .none]

    /// TASK_PRIORITY_LABELS as the web renders them.
    var label: String {
        switch self {
        case .none: return "No priority"
        case .urgent: return "Urgent"
        case .high: return "High"
        case .medium: return "Medium"
        case .low: return "Low"
        }
    }

    /// How many of the three bars are lit — the web's PRIORITY_BARS.
    var litBars: Int {
        switch self {
        case .none: return 0
        case .urgent, .high: return 3
        case .medium: return 2
        case .low: return 1
        }
    }
}

// MARK: - Responses

struct TasksResponse: Decodable {
    let ok: Bool
    let tasks: [EmpTask]
}

struct TaskWriteAck: Decodable {
    let ok: Bool
    let error: String?
    let task: EmpTask?
}

struct Project: Codable, Identifiable, Equatable {
    let id: String
    let name: String
}

struct ProjectsResponse: Decodable {
    let ok: Bool
    let projects: [Project]
}

// MARK: - Workspace members (valid HUMAN assignees, and comment-author names)

/// GET /api/workspaces/{workspace_id}/members — note this is NOT under the
/// `/w/{id}/fleet` namespace every other call on this screen uses, and it
/// answers `{"items": [...]}` rather than the `{"ok": ..., "<plural>": [...]}`
/// envelope the fleet routes share.
struct WorkspaceMember: Codable, Identifiable, Equatable {
    let userId: String
    let email: String?
    let displayName: String?
    let role: String?

    var id: String { userId }

    enum CodingKeys: String, CodingKey {
        case email, role
        case userId = "user_id"
        case displayName = "display_name"
    }

    /// Name first, then the email's local part, then the raw id. Never blank.
    var name: String {
        if let n = displayName, !n.isEmpty { return n }
        if let e = email, !e.isEmpty { return e.split(separator: "@").first.map(String.init) ?? e }
        return userId
    }

    var initial: String { String(name.prefix(1)).uppercased() }
}

struct MembersResponse: Decodable {
    let items: [WorkspaceMember]
}
