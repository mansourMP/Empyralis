import Foundation

/// The two Inbox sources that are NOT already in `WorkspaceStore` — the
/// caller's own notification feed, and the blocked/failed agent runs from
/// the activity ledger. Both are traced from the producing Python, not from
/// a docstring:
///
///   task_notification_service._row_to_notification        (the feed row)
///   activity_ledger_service._project_timeline_item        (the ledger row)
///
/// Same defensive-decoding posture as `TaskModels.swift`: a missing key, a
/// null, and a value of an unexpected type all resolve to the fallback
/// rather than failing the whole row. These are cached to disk, so the shape
/// also has to survive a payload written by an older build of this app.

/// `try? container.decodeIfPresent(...)` produces a DOUBLE optional — `T??` —
/// because the `try?` and the `decodeIfPresent` each contribute one level.
/// Collapsed explicitly here rather than left to implicit flattening.
/// Deliberately `fileprivate`: `TaskModels.swift` carries its own copy at
/// the same visibility, and two same-named extensions on
/// `KeyedDecodingContainer` at wider scope would be ambiguous at every call
/// site in both files.
fileprivate extension KeyedDecodingContainer {
    func lenient<T: Decodable>(_ type: T.Type, _ key: Key, default fallback: T) -> T {
        (try? decodeIfPresent(type, forKey: key)).flatMap { $0 } ?? fallback
    }

    func lenient<T: Decodable>(_ type: T.Type, _ key: Key) -> T? {
        (try? decodeIfPresent(type, forKey: key)).flatMap { $0 }
    }
}

// MARK: - Per-user notifications

/// One row of `GET /api/w/{workspace_id}/fleet/notifications`.
///
/// RECIPIENT-SCOPED SERVER-SIDE, and that is the whole security story for
/// this surface: the route resolves `recipient_user_id` from the
/// authenticated caller and puts it in the WHERE clause — it is never a
/// parameter this app could supply, so there is no id to get wrong here and
/// no client-side filter that could be forgotten.
struct FleetNotification: Codable, Identifiable, Equatable, InboxNotificationShape {
    let id: String
    let sourceEventType: String?
    let taskId: String?
    let commentId: String?
    let actorType: String?
    let actorId: String?
    let body: String?
    let deepLink: String?
    let readAt: String?
    let isRead: Bool
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, body
        case sourceEventType = "source_event_type"
        case taskId = "task_id"
        case commentId = "comment_id"
        case actorType = "actor_type"
        case actorId = "actor_id"
        case deepLink = "deep_link"
        case readAt = "read_at"
        case isRead = "is_read"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        sourceEventType = c.lenient(String.self, .sourceEventType)
        taskId = c.lenient(String.self, .taskId)
        commentId = c.lenient(String.self, .commentId)
        actorType = c.lenient(String.self, .actorType)
        actorId = c.lenient(String.self, .actorId)
        body = c.lenient(String.self, .body)
        deepLink = c.lenient(String.self, .deepLink)
        readAt = c.lenient(String.self, .readAt)
        // `is_read` is derived server-side as `bool(read_at)`; deriving it
        // again from readAt would be a second opinion about the same fact.
        isRead = c.lenient(Bool.self, .isRead, default: false)
        createdAt = c.lenient(String.self, .createdAt)
    }

    init(
        id: String,
        sourceEventType: String?,
        taskId: String?,
        commentId: String? = nil,
        actorType: String? = nil,
        actorId: String? = nil,
        body: String?,
        deepLink: String? = nil,
        readAt: String? = nil,
        isRead: Bool = false,
        createdAt: String?
    ) {
        self.id = id
        self.sourceEventType = sourceEventType
        self.taskId = taskId
        self.commentId = commentId
        self.actorType = actorType
        self.actorId = actorId
        self.body = body
        self.deepLink = deepLink
        self.readAt = readAt
        self.isRead = isRead
        self.createdAt = createdAt
    }
}

/// The route answers HTTP 200 with `ok:false` and an EMPTY list when the
/// read itself failed, so the status code alone is not the answer — "no
/// notifications" and "couldn't ask" are different facts and the caller must
/// check `ok` before rendering an empty state.
struct NotificationsResponse: Decodable {
    let ok: Bool
    let notifications: [FleetNotification]
    let error: String?
}

struct NotificationReadAck: Decodable {
    let ok: Bool
    let error: String?
}

// MARK: - Activity ledger

/// One row of `GET /api/activity/timeline`. Note the path: it is NOT under
/// `/w/{id}/fleet` like everything else this app reads — it is a top-level
/// route that takes `workspace_id` as a QUERY parameter and enforces access
/// with `enforce_workspace_access(..., minimum_role="viewer")` inside.
///
/// Only the `blocked_action` class is fetched for the Inbox, but the type is
/// the full row so a future Activity surface needs no second model.
struct WorkspaceActivityEvent: Codable, Equatable, InboxBlockedRunShape {
    let id: String?
    let title: String?
    let summary: String?
    let eventClass: String?
    let action: String?
    let status: String?
    let createdAt: String?
    let traceId: String?
    let installId: String?
    let channel: String?
    let actorType: String?
    let actorId: String?
    let reviewRequired: Bool

    enum CodingKeys: String, CodingKey {
        case id, title, summary, action, status, channel
        case eventClass = "event_class"
        case createdAt = "created_at"
        case traceId = "trace_id"
        case installId = "install_id"
        case actorType = "actor_type"
        case actorId = "actor_id"
        case reviewRequired = "review_required"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.lenient(String.self, .id)
        title = c.lenient(String.self, .title)
        summary = c.lenient(String.self, .summary)
        eventClass = c.lenient(String.self, .eventClass)
        action = c.lenient(String.self, .action)
        status = c.lenient(String.self, .status)
        createdAt = c.lenient(String.self, .createdAt)
        traceId = c.lenient(String.self, .traceId)
        installId = c.lenient(String.self, .installId)
        channel = c.lenient(String.self, .channel)
        actorType = c.lenient(String.self, .actorType)
        actorId = c.lenient(String.self, .actorId)
        reviewRequired = c.lenient(Bool.self, .reviewRequired, default: false)
    }

    init(
        id: String?,
        title: String?,
        eventClass: String?,
        installId: String?,
        createdAt: String?,
        summary: String? = nil,
        action: String? = nil,
        status: String? = nil,
        traceId: String? = nil,
        channel: String? = nil,
        actorType: String? = nil,
        actorId: String? = nil,
        reviewRequired: Bool = false
    ) {
        self.id = id
        self.title = title
        self.summary = summary
        self.eventClass = eventClass
        self.action = action
        self.status = status
        self.createdAt = createdAt
        self.traceId = traceId
        self.installId = installId
        self.channel = channel
        self.actorType = actorType
        self.actorId = actorId
        self.reviewRequired = reviewRequired
    }
}

/// `ApiActivityListResponse` — `items` plus counts this app does not read.
/// There is no `ok` flag on this one; a failure is an HTTP status, which
/// `APIClient` already turns into a thrown `APIError`.
struct ActivityTimelineResponse: Decodable {
    let items: [WorkspaceActivityEvent]
}

// MARK: - Conformances for the pure rules

/// Declared here rather than in `TaskModels.swift` so the wire model stays
/// untouched by this surface — `EmpTask` already exposes every property both
/// rules need, under exactly these names.
extension EmpTask: InboxTaskShape, MyWorkTaskShape {}
