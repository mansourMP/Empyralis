import Foundation

/// WHAT LANDS IN THE INBOX — a port of the web's
/// `frontend/lib/workspace/fleet/inbox-needs-you.ts`, kept as a pure module
/// with its own plain XCTest for the same reason that one is: the expected
/// set and the actual set must never come from one place.
///
/// THE PROBLEM THIS REPLACES, on this platform specifically. iOS `Inbox`
/// listed every open task in the workspace under a name the web already
/// uses for a different, narrower question. The web version exists BECAUSE a
/// raw list was measured useless: 50 sampled ledger rows were almost
/// entirely `Configured`/`Created`/"<Agent> chat completed", none of which
/// needs a human, while a real task sat in `Needs input`, assigned to the
/// founder, for 17 days and was never surfaced once. Two products showing
/// different things under one word is the same class of lie this codebase
/// records elsewhere for delivery outcomes — so the RULE is ported rather
/// than a phone-side approximation invented.
///
/// THREE SOURCES, never a fourth invented here — each already exists and is
/// correctly scoped server-side; this module only composes and ranks what
/// its caller fetched:
///
///   1. Real per-user notifications (task_notification_service.py via
///      `GET /api/w/{ws}/fleet/notifications`) — mentions, "assigned to
///      you", "commented on a task you own". Recipient-scoped server-side
///      (an explicit `recipient_user_id` WHERE clause, never left to RLS);
///      this module trusts what the caller passes rather than re-filtering
///      on `isRead`, so a future "show read too" view isn't silently
///      dropped here.
///
///   2. The caller's OWN tasks stuck in `blocked` / `awaiting_input` — the
///      exact status pair that sat unseen for 17 days. Deliberately
///      narrower than `myWorkBucket(.mine)`: every open task assigned to you
///      is "yours", but only a STUCK one needs you to do something about it
///      right now. `in_progress`/`todo`/`backlog` belong on My work, not on
///      a "needs you" surface.
///
///   3. Blocked/failed agent runs — activity_ledger_service.py's own
///      `blocked_action` event class (run_failed, machine_revoked,
///      machine_enrollment_failed). Filtered on the STRUCTURAL `event_class`
///      field, never on title/action text — CLAUDE.md's own "stale string
///      matching" failure mode (an error bucket matched "ai limit", the
///      message was reworded, and users got a generic failure for five
///      weeks).
///
/// RANKING IS PER-GROUP, NOT ONE CHRONOLOGICAL MERGE, and that split is
/// deliberate. Stuck tasks sort OLDEST-first: the founder's own complaint
/// was a task sitting for 17 days, and a newest-first merge would keep
/// burying exactly that item under whatever moved most recently — the
/// opposite of what a "needs you" surface is for. Notifications and runs
/// sort newest-first, the ordinary "what just happened" reading.
///
/// ── THE TWO DELIBERATE TRANSLATIONS FROM THE TS ─────────────────────────
///
///   href → InboxDestination?   The web returns a URL string or `null`;
///                              a phone has no URLs, it has navigation
///                              values. The MEANING is preserved exactly:
///                              nil still means "this item's real home
///                              can't be resolved", and such a row renders
///                              as plain, untappable text rather than a
///                              link to nowhere.
///
///   Date.parse → inboxSortMillis   see that function's own note; Foundation
///                              has no single lenient ISO parser and the
///                              backend emits MICROSECOND precision, which
///                              the obvious one rejects.

// MARK: - The shapes this module reads

/// Structural on purpose (a protocol, not a concrete type) so this module
/// depends on nothing — the real `EmpTask` satisfies it, and so does a test
/// fixture, which is what keeps the test's inputs independent of the app's
/// own wire model.
protocol InboxTaskShape {
    var id: String { get }
    var title: String { get }
    var status: String { get }
    var assigneeUserId: String? { get }
    var updatedAt: String? { get }
    var createdAt: String? { get }
}

protocol InboxNotificationShape {
    var id: String { get }
    var sourceEventType: String? { get }
    var body: String? { get }
    var taskId: String? { get }
    var createdAt: String? { get }
}

protocol InboxBlockedRunShape {
    var id: String? { get }
    var title: String? { get }
    var eventClass: String? { get }
    var installId: String? { get }
    var createdAt: String? { get }
}

// MARK: - The rules

/// The two statuses that mean "this needs a human, right now" — a subset of
/// project_tasks_service.TASK_STATUS_ORDER, never a re-derivation of it.
let INBOX_STUCK_STATUSES: [String] = ["blocked", "awaiting_input"]

/// Assigned to this person AND currently stuck. Both conditions matter:
/// dropping the assignee check would surface a teammate's stuck task under
/// my name; dropping the status check would turn this into "everything I
/// own", which is My work's job, not Inbox's.
func isMyStuckTask(_ task: any InboxTaskShape, userId: String?) -> Bool {
    let me = (userId ?? "").trimmingCharacters(in: .whitespaces)
    guard !me.isEmpty else { return false }
    guard (task.assigneeUserId ?? "").trimmingCharacters(in: .whitespaces) == me else { return false }
    return INBOX_STUCK_STATUSES.contains(task.status.trimmingCharacters(in: .whitespaces))
}

private let inboxNotificationTitles: [String: String] = [
    "task_mention": "Mentioned you",
    "task_assigned": "Assigned to you",
    "task_comment": "Commented on your task",
]

func notificationTitle(_ notification: any InboxNotificationShape) -> String {
    let key = (notification.sourceEventType ?? "").trimmingCharacters(in: .whitespaces)
    return inboxNotificationTitles[key] ?? "Notification"
}

/// Structural, not string-matched: activity_ledger_service.py's
/// `record_notification_activity` classifies run_failed/machine_revoked/
/// machine_enrollment_failed into event_class "blocked_action" server-side —
/// this reads that enum field, never the human-readable title, which changes
/// wording without notice.
func isBlockedRunEvent(_ event: any InboxBlockedRunShape) -> Bool {
    (event.eventClass ?? "").trimmingCharacters(in: .whitespaces) == "blocked_action"
}

// MARK: - The composed result

/// Where a row opens. The iOS stand-in for the web's `href`, and it carries
/// the same three-valued meaning: a task's own page, an agent's own page, or
/// nil for "no resolvable home". Never a second place the underlying thing
/// is edited.
enum InboxDestination: Equatable, Hashable {
    case task(taskId: String)
    case agent(agentId: String)
}

enum InboxNeedsYouKind: String, Equatable {
    case task
    case notification
    case run
}

struct InboxNeedsYouItem: Identifiable, Equatable {
    let kind: InboxNeedsYouKind
    /// Stable across polls — prefixed by kind so a task id and a
    /// notification id can never collide in a merged key space.
    let id: String
    let title: String
    let detail: String?
    let timestamp: String?
    let destination: InboxDestination?
}

struct InboxNeedsYouGroups: Equatable {
    var tasks: [InboxNeedsYouItem]
    var notifications: [InboxNeedsYouItem]
    var runs: [InboxNeedsYouItem]

    static let empty = InboxNeedsYouGroups(tasks: [], notifications: [], runs: [])
}

func planInboxNeedsYou(
    stuckTasks: [any InboxTaskShape],
    notifications: [any InboxNotificationShape],
    blockedRuns: [any InboxBlockedRunShape],
    userId: String?,
    taskDestinationFor: (String?) -> InboxDestination?,
    agentDestinationFor: (String?) -> InboxDestination?
) -> InboxNeedsYouGroups {
    let taskItems: [InboxNeedsYouItem] = stuckTasks
        .filter { isMyStuckTask($0, userId: userId) }
        .sorted { inboxSortMillis($0.updatedAt ?? $0.createdAt) < inboxSortMillis($1.updatedAt ?? $1.createdAt) }
        .map { task in
            InboxNeedsYouItem(
                kind: .task,
                id: "task:\(task.id)",
                title: task.title.isEmpty ? "Untitled task" : task.title,
                detail: task.status.trimmingCharacters(in: .whitespaces) == "blocked" ? "Blocked" : "Needs your input",
                timestamp: task.updatedAt ?? task.createdAt,
                destination: taskDestinationFor(task.id)
            )
        }

    let notificationItems: [InboxNeedsYouItem] = notifications
        .sorted { inboxSortMillis($0.createdAt) > inboxSortMillis($1.createdAt) }
        .map { notification in
            InboxNeedsYouItem(
                kind: .notification,
                id: "notification:\(notification.id)",
                title: notificationTitle(notification),
                detail: inboxNonEmpty(notification.body),
                timestamp: notification.createdAt,
                destination: taskDestinationFor(notification.taskId)
            )
        }

    let runItems: [InboxNeedsYouItem] = blockedRuns
        .filter(isBlockedRunEvent)
        .sorted { inboxSortMillis($0.createdAt) > inboxSortMillis($1.createdAt) }
        .map { event in
            InboxNeedsYouItem(
                kind: .run,
                id: "run:\(inboxNonEmpty(event.id) ?? inboxNonEmpty(event.createdAt) ?? "")",
                title: inboxNonEmpty(event.title) ?? "An agent run failed",
                detail: nil,
                timestamp: event.createdAt,
                destination: agentDestinationFor(event.installId)
            )
        }

    return InboxNeedsYouGroups(tasks: taskItems, notifications: notificationItems, runs: runItems)
}

/// Total across all three groups — the single number the screen's own
/// empty-state check and any badge must agree on, so they are never computed
/// from two independently-drifting expressions.
func inboxNeedsYouCount(_ groups: InboxNeedsYouGroups) -> Int {
    groups.tasks.count + groups.notifications.count + groups.runs.count
}

/// Blocked-run events newer than `since` — "unseen since last visit" is the
/// right question for a feed with no per-row read state. A nil/unparseable
/// `since` means everything counts as unseen, which is the honest answer for
/// a reader who has never opened the surface.
func countUnseenBlockedRuns(_ events: [any InboxBlockedRunShape], since: String?) -> Int {
    let sinceMillis = inboxSortMillis(since)
    return events.filter { event in
        guard isBlockedRunEvent(event) else { return false }
        guard sinceMillis > 0 else { return true }
        return inboxSortMillis(event.createdAt) > sinceMillis
    }.count
}

// MARK: - Helpers

/// JS's `""` is falsy, so `e.id || e.created_at` in the TS silently skips an
/// empty string. Swift's `??` does not, so the fallthrough has to be spelled
/// out or an empty id would win over a real timestamp.
func inboxNonEmpty(_ value: String?) -> String? {
    guard let value, !value.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
    return value
}

/// The port of the TS's `sortMillis`, and it is NOT a one-liner for a real
/// reason. `Date.parse` accepts every shape this backend emits; Foundation's
/// `ISO8601DateFormatter` accepts either zero or exactly-three fractional
/// digits, and `activity_ledger_service._iso_ts` emits Python's
/// `datetime.isoformat()` — SIX. So a naive single formatter returns nil on
/// every real timestamp, every item sorts as 0, and the ordering this whole
/// module exists to get right silently degrades to input order.
///
/// Unparseable resolves to 0, exactly as the TS's non-finite branch does, so
/// a row with a broken timestamp sinks rather than throwing the list away.
/// A value carrying no timezone is read as UTC — every producer here emits
/// UTC, and being explicit beats JS's own "treat as local" surprise.
func inboxSortMillis(_ iso: String?) -> Double {
    guard let raw = inboxNonEmpty(iso)?.trimmingCharacters(in: .whitespaces) else { return 0 }

    if let date = InboxDateParsing.fractional.date(from: raw) { return date.timeIntervalSince1970 * 1000 }
    if let date = InboxDateParsing.plain.date(from: raw) { return date.timeIntervalSince1970 * 1000 }

    let normalized = InboxDateParsing.normalize(raw)
    if normalized != raw {
        if let date = InboxDateParsing.fractional.date(from: normalized) { return date.timeIntervalSince1970 * 1000 }
        if let date = InboxDateParsing.plain.date(from: normalized) { return date.timeIntervalSince1970 * 1000 }
    }
    return 0
}

enum InboxDateParsing {
    static let fractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    /// Turns what the backend really sends into something
    /// `ISO8601DateFormatter` will actually accept. THREE shapes, and the
    /// first one is the one that was missing:
    ///
    ///   "2026-08-25 07:38:07.933865+00:00"   SPACE separator — NOT ISO 8601
    ///   "2026-08-25T07:38:07.933865+00:00"   microseconds
    ///   "2026-08-25T07:38:07"                no timezone
    ///
    /// `created_at`, `updated_at` and `due_at` all arrive space-separated on
    /// every task (Postgres renders them with `str()`, not `isoformat()`),
    /// and both ISO parsers reject that outright. The measured consequence:
    /// `inboxSortMillis` returned 0 for EVERY stuck task, so the Inbox's
    /// oldest-first ranking — the whole reason that surface exists —
    /// silently degraded to whatever order the API happened to return.
    static func normalize(_ raw: String) -> String {
        var value = raw

        // Only the date/time separator, and only at the position where a
        // date/time separator can be. A blanket space-strip would corrupt a
        // value that is not a timestamp at all.
        if value.count > 10 {
            let sep = value.index(value.startIndex, offsetBy: 10)
            if value[sep] == " " { value.replaceSubrange(sep...sep, with: "T") }
        }

        if let dot = value.firstIndex(of: "."), dot > value.startIndex {
            var end = value.index(after: dot)
            while end < value.endIndex, value[end].isNumber { end = value.index(after: end) }
            let digits = value.distance(from: value.index(after: dot), to: end)
            if digits > 3 {
                let keepUntil = value.index(dot, offsetBy: 4)
                value.removeSubrange(keepUntil..<end)
            } else if digits == 0 {
                value.remove(at: dot)
            }
        }

        let hasZone = value.hasSuffix("Z") || value.hasSuffix("z")
            || value.dropFirst(11).contains("+")
            || value.dropFirst(11).contains("-")
        if !hasZone { value += "Z" }

        return value
    }
}
