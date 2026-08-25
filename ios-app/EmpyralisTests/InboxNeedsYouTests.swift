import XCTest
@testable import Empyralis

/// "Needs you" scoping, proven against the REAL rule `InboxNeedsYou.swift`
/// exports rather than a re-typed copy of it — the same discipline the web's
/// own `inbox-needs-you.test.ts` applies, and every assertion below is a port
/// of one of its assertions so the two products cannot drift apart silently.
///
/// THE ASSERTIONS THAT MATTER ARE THE NEGATIVE ONES: this surface's whole
/// risk is showing something that does NOT need the reader (a teammate's
/// stuck task, a routine chat completion dressed up as a "run"), which is
/// exactly the failure the list-of-open-tasks Inbox had. An over-inclusive
/// implementation satisfies every "my item is listed" assertion perfectly.
///
/// Fixtures are declared here rather than reusing `EmpTask`/`FleetNotification`
/// on purpose: the rule reads structural protocols, so the test's inputs stay
/// independent of the app's own wire models — the expected set and the actual
/// set never come from one place.
final class InboxNeedsYouTests: XCTestCase {

    private let me = "user_me"
    private let other = "user_other"

    // MARK: - Fixtures

    private struct TaskFixture: InboxTaskShape {
        var id: String = "t1"
        var title: String = "A task"
        var status: String = "todo"
        var assigneeUserId: String?
        var updatedAt: String?
        var createdAt: String?
    }

    private struct NotificationFixture: InboxNotificationShape {
        var id: String
        var sourceEventType: String?
        var body: String?
        var taskId: String?
        var createdAt: String?
    }

    private struct RunFixture: InboxBlockedRunShape {
        var id: String?
        var title: String?
        var eventClass: String?
        var installId: String?
        var createdAt: String?
    }

    private func taskDestination(_ id: String?) -> InboxDestination? {
        guard let id, !id.isEmpty else { return nil }
        return .task(taskId: id)
    }

    private func agentDestination(_ id: String?) -> InboxDestination? {
        guard let id, !id.isEmpty else { return nil }
        return .agent(agentId: id)
    }

    // MARK: - isMyStuckTask

    func testMyBlockedTaskNeedsMe() {
        XCTAssertTrue(isMyStuckTask(TaskFixture(status: "blocked", assigneeUserId: me), userId: me))
    }

    func testMyAwaitingInputTaskNeedsMe() {
        XCTAssertTrue(isMyStuckTask(TaskFixture(status: "awaiting_input", assigneeUserId: me), userId: me))
    }

    /// Not every open task belongs here — that is My work's question.
    func testMyInProgressTaskDoesNotNeedMe() {
        XCTAssertFalse(isMyStuckTask(TaskFixture(status: "in_progress", assigneeUserId: me), userId: me))
    }

    /// The over-inclusion risk this whole module exists to avoid.
    func testATeammatesStuckTaskIsNeverMine() {
        XCTAssertFalse(isMyStuckTask(TaskFixture(status: "blocked", assigneeUserId: other), userId: me))
    }

    func testAnUnassignedStuckTaskNeedsNoOneSpecific() {
        XCTAssertFalse(isMyStuckTask(TaskFixture(status: "blocked", assigneeUserId: nil), userId: me))
    }

    func testNoViewerMeansNothingIsTheirs() {
        let task = TaskFixture(status: "blocked", assigneeUserId: me)
        XCTAssertFalse(isMyStuckTask(task, userId: nil))
        XCTAssertFalse(isMyStuckTask(task, userId: ""))
        XCTAssertFalse(isMyStuckTask(task, userId: "  "))
    }

    // MARK: - notificationTitle

    func testNotificationTitlesAreNamedNotRawEnums() {
        XCTAssertEqual(
            notificationTitle(NotificationFixture(id: "n1", sourceEventType: "task_mention", body: nil, taskId: nil, createdAt: nil)),
            "Mentioned you"
        )
        XCTAssertEqual(
            notificationTitle(NotificationFixture(id: "n2", sourceEventType: "task_assigned", body: nil, taskId: nil, createdAt: nil)),
            "Assigned to you"
        )
        XCTAssertEqual(
            notificationTitle(NotificationFixture(id: "n3", sourceEventType: "task_comment", body: nil, taskId: nil, createdAt: nil)),
            "Commented on your task"
        )
        // An unrecognized event type falls back rather than leaking a raw
        // enum value onto the screen.
        XCTAssertEqual(
            notificationTitle(NotificationFixture(id: "n4", sourceEventType: "bogus", body: nil, taskId: nil, createdAt: nil)),
            "Notification"
        )
        XCTAssertEqual(
            notificationTitle(NotificationFixture(id: "n5", sourceEventType: nil, body: nil, taskId: nil, createdAt: nil)),
            "Notification"
        )
    }

    // MARK: - isBlockedRunEvent: STRUCTURAL, never string-matched

    func testBlockedActionIsTheRealSignal() {
        XCTAssertTrue(isBlockedRunEvent(RunFixture(id: "e", title: "Run failed", eventClass: "blocked_action", installId: nil, createdAt: nil)))
    }

    /// The exact "stale string matching" failure mode this avoids: the word
    /// "failed" in an unrelated title must never promote a routine row.
    func testTheWordFailedInATitleDoesNotTriggerIt() {
        XCTAssertFalse(
            isBlockedRunEvent(RunFixture(
                id: "e",
                title: "run failed to do something",
                eventClass: "system_activity",
                installId: nil,
                createdAt: nil
            ))
        )
    }

    func testARoutineChatCompletionIsNeverABlockedRun() {
        XCTAssertFalse(
            isBlockedRunEvent(RunFixture(id: "e", title: "Agent chat completed", eventClass: "sage_activity", installId: nil, createdAt: nil))
        )
    }

    // MARK: - planInboxNeedsYou: composition + ranking

    private func standardGroups() -> InboxNeedsYouGroups {
        let stuckTasks: [any InboxTaskShape] = [
            TaskFixture(id: "old", status: "blocked", assigneeUserId: me, updatedAt: "2026-08-01T00:00:00Z"),
            TaskFixture(id: "new", status: "awaiting_input", assigneeUserId: me, updatedAt: "2026-08-18T00:00:00Z"),
            TaskFixture(id: "not-mine", status: "blocked", assigneeUserId: other, updatedAt: "2026-08-19T00:00:00Z"),
            TaskFixture(id: "not-stuck", status: "todo", assigneeUserId: me, updatedAt: "2026-08-19T00:00:00Z"),
        ]
        let notifications: [any InboxNotificationShape] = [
            NotificationFixture(id: "n1", sourceEventType: "task_mention", body: "@you check this", taskId: "t1", createdAt: "2026-08-10T00:00:00Z"),
            NotificationFixture(id: "n2", sourceEventType: "task_assigned", body: nil, taskId: "t2", createdAt: "2026-08-15T00:00:00Z"),
        ]
        let blockedRuns: [any InboxBlockedRunShape] = [
            RunFixture(id: "e1", title: "Run failed", eventClass: "blocked_action", installId: "agent_1", createdAt: "2026-08-12T00:00:00Z"),
            RunFixture(id: "e2", title: "Configured", eventClass: "system_activity", installId: "agent_1", createdAt: "2026-08-19T00:00:00Z"),
        ]
        return planInboxNeedsYou(
            stuckTasks: stuckTasks,
            notifications: notifications,
            blockedRuns: blockedRuns,
            userId: me,
            taskDestinationFor: taskDestination,
            agentDestinationFor: agentDestination
        )
    }

    func testOnlyMyStuckTasksAreIncluded() {
        let groups = standardGroups()
        XCTAssertEqual(groups.tasks.count, 2, "the teammate's row and the not-stuck row are both excluded")
    }

    /// THE FOUNDER'S OWN 17-DAY COMPLAINT, encoded. A newest-first merge
    /// would keep burying exactly the item this surface exists to surface.
    func testOldestStuckTaskSortsFirst() {
        let groups = standardGroups()
        XCTAssertEqual(groups.tasks.first?.id, "task:old")
        XCTAssertEqual(groups.tasks.last?.id, "task:new")
    }

    func testTaskRowsCarryTheirStuckReason() {
        let groups = standardGroups()
        XCTAssertEqual(groups.tasks.first?.detail, "Blocked")
        XCTAssertEqual(groups.tasks.last?.detail, "Needs your input")
    }

    func testTaskDestinationResolvesThroughTheCallersOwnResolver() {
        let groups = standardGroups()
        XCTAssertEqual(groups.tasks.first?.destination, .task(taskId: "old"))
    }

    /// This module trusts the caller's own scoping — the route is
    /// recipient-scoped server-side, so re-filtering here would silently drop
    /// a future "show read too" view.
    func testBothNotificationsPassThroughNewestFirst() {
        let groups = standardGroups()
        XCTAssertEqual(groups.notifications.map(\.id), ["notification:n2", "notification:n1"])
    }

    func testOnlyTheBlockedActionEventIsARun() {
        let groups = standardGroups()
        XCTAssertEqual(groups.runs.count, 1, "the system_activity noise row is excluded")
        XCTAssertEqual(groups.runs.first?.id, "run:e1")
        XCTAssertEqual(groups.runs.first?.destination, .agent(agentId: "agent_1"))
    }

    func testCountIsTheSumOfAllThreeGroups() {
        XCTAssertEqual(inboxNeedsYouCount(standardGroups()), 2 + 2 + 1)
    }

    func testGenuinelyNothingCountsAsZero() {
        let empty = planInboxNeedsYou(
            stuckTasks: [],
            notifications: [],
            blockedRuns: [],
            userId: me,
            taskDestinationFor: taskDestination,
            agentDestinationFor: agentDestination
        )
        XCTAssertEqual(inboxNeedsYouCount(empty), 0)
    }

    /// An item with no resolvable home renders as plain, untappable text —
    /// never a dead control that navigates nowhere.
    func testUnresolvableDestinationIsNilNotABrokenRoute() {
        let groups = planInboxNeedsYou(
            stuckTasks: [TaskFixture(id: "t9", status: "blocked", assigneeUserId: me)],
            notifications: [],
            blockedRuns: [],
            userId: me,
            taskDestinationFor: { _ in nil },
            agentDestinationFor: agentDestination
        )
        XCTAssertNil(groups.tasks.first?.destination)
        XCTAssertEqual(groups.tasks.count, 1, "it is still SHOWN — it is a real thing that needs the reader")
    }

    /// JS's `""` is falsy so `e.id || e.created_at` skips an empty id; Swift's
    /// `??` does not. Without the explicit fallthrough an empty id would win
    /// over a real timestamp and every such row would share one key.
    func testAnEmptyRunIdFallsThroughToTheTimestamp() {
        let groups = planInboxNeedsYou(
            stuckTasks: [],
            notifications: [],
            blockedRuns: [RunFixture(id: "", title: nil, eventClass: "blocked_action", installId: nil, createdAt: "2026-08-12T00:00:00Z")],
            userId: me,
            taskDestinationFor: taskDestination,
            agentDestinationFor: agentDestination
        )
        XCTAssertEqual(groups.runs.first?.id, "run:2026-08-12T00:00:00Z")
        XCTAssertEqual(groups.runs.first?.title, "An agent run failed", "a titleless run still says what it is")
    }

    // MARK: - countUnseenBlockedRuns

    private var runsForBadge: [any InboxBlockedRunShape] {
        [
            RunFixture(id: "r1", title: nil, eventClass: "blocked_action", installId: nil, createdAt: "2026-08-01T00:00:00Z"),
            RunFixture(id: "r2", title: nil, eventClass: "blocked_action", installId: nil, createdAt: "2026-08-19T00:00:00Z"),
            RunFixture(id: "r3", title: nil, eventClass: "system_activity", installId: nil, createdAt: "2026-08-19T00:00:00Z"),
        ]
    }

    func testOnlyBlockedActionsAfterTheSinceStampCount() {
        XCTAssertEqual(countUnseenBlockedRuns(runsForBadge, since: "2026-08-10T00:00:00Z"), 1)
    }

    func testWithNoLastSeenEveryBlockedActionIsUnseen() {
        XCTAssertEqual(countUnseenBlockedRuns(runsForBadge, since: nil), 2)
    }

    func testNoEventsNoUnseenCount() {
        XCTAssertEqual(countUnseenBlockedRuns([], since: "2026-08-01T00:00:00Z"), 0)
    }

    // MARK: - Timestamp parsing (the Swift-only trap)

    /// `activity_ledger_service._iso_ts` emits Python's `datetime.isoformat()`
    /// — SIX fractional digits. `ISO8601DateFormatter` accepts zero or three,
    /// so the obvious single-formatter port returns nil on every real ledger
    /// timestamp, every item sorts as 0, and the ranking this module exists
    /// for silently degrades to input order with nothing reporting it.
    func testMicrosecondPrecisionTimestampsStillSort() {
        let micro = inboxSortMillis("2026-08-19T12:00:00.123456Z")
        let milli = inboxSortMillis("2026-08-19T12:00:00.123Z")
        let plain = inboxSortMillis("2026-08-19T12:00:00Z")

        XCTAssertGreaterThan(micro, 0, "a microsecond-precision stamp must parse, not fall back to 0")
        XCTAssertGreaterThan(milli, 0)
        XCTAssertGreaterThan(plain, 0)
        XCTAssertEqual(micro, milli, accuracy: 1.0, "microseconds truncate to the same millisecond")
        XCTAssertEqual(micro - plain, 123, accuracy: 1.0)
    }

    func testMissingTimezoneIsReadAsUTC() {
        XCTAssertEqual(
            inboxSortMillis("2026-08-19T12:00:00"),
            inboxSortMillis("2026-08-19T12:00:00Z"),
            accuracy: 1.0
        )
    }

    /// Unparseable resolves to 0 exactly as the TS's non-finite branch does,
    /// so a row with a broken timestamp sinks rather than throwing the list
    /// away.
    func testUnparseableTimestampsSinkRatherThanThrow() {
        XCTAssertEqual(inboxSortMillis(nil), 0)
        XCTAssertEqual(inboxSortMillis(""), 0)
        XCTAssertEqual(inboxSortMillis("   "), 0)
        XCTAssertEqual(inboxSortMillis("not a date"), 0)
    }

    /// The ordering rule has to hold on the shape the ledger really sends,
    /// not just on the tidy one the fixtures above use.
    func testRunsSortNewestFirstWithMicrosecondStamps() {
        let groups = planInboxNeedsYou(
            stuckTasks: [],
            notifications: [],
            blockedRuns: [
                RunFixture(id: "older", title: nil, eventClass: "blocked_action", installId: nil, createdAt: "2026-08-12T09:00:00.000123Z"),
                RunFixture(id: "newer", title: nil, eventClass: "blocked_action", installId: nil, createdAt: "2026-08-19T09:00:00.654321Z"),
            ],
            userId: me,
            taskDestinationFor: taskDestination,
            agentDestinationFor: agentDestination
        )
        XCTAssertEqual(groups.runs.map(\.id), ["run:newer", "run:older"])
    }

    // MARK: - The rule is wired to the app's own wire models

    /// A pure rule nothing conforms to is the "built, tested, and never
    /// wired" shape. These assert the real types this app decodes actually
    /// satisfy the protocols the rule reads — a rename on either side breaks
    /// here rather than silently producing an empty Inbox.
    func testRealModelsConformToTheShapesTheRuleReads() {
        let notification = FleetNotification(
            id: "n1",
            sourceEventType: "task_mention",
            taskId: "t1",
            body: "hello",
            createdAt: "2026-08-19T00:00:00Z"
        )
        let run = WorkspaceActivityEvent(
            id: "e1",
            title: "Run failed",
            eventClass: "blocked_action",
            installId: "agent_1",
            createdAt: "2026-08-19T00:00:00Z"
        )

        XCTAssertEqual(notificationTitle(notification), "Mentioned you")
        XCTAssertTrue(isBlockedRunEvent(run))

        let groups = planInboxNeedsYou(
            stuckTasks: [],
            notifications: [notification],
            blockedRuns: [run],
            userId: me,
            taskDestinationFor: taskDestination,
            agentDestinationFor: agentDestination
        )
        XCTAssertEqual(inboxNeedsYouCount(groups), 2)
    }

    /// `EmpTask` is what the store actually holds, so the task group has to
    /// compose from it — not only from the fixture above.
    func testEmpTaskSatisfiesTheStuckTaskRule() throws {
        let json = """
        {"id":"t1","title":"Stuck one","status":"awaiting_input","assignee_user_id":"\(me)","updated_at":"2026-08-01T00:00:00Z"}
        """.data(using: .utf8)!
        let task = try JSONDecoder().decode(EmpTask.self, from: json)

        XCTAssertTrue(isMyStuckTask(task, userId: me))
        XCTAssertFalse(isMyStuckTask(task, userId: other))

        let groups = planInboxNeedsYou(
            stuckTasks: [task],
            notifications: [],
            blockedRuns: [],
            userId: me,
            taskDestinationFor: taskDestination,
            agentDestinationFor: agentDestination
        )
        XCTAssertEqual(groups.tasks.first?.title, "Stuck one")
        XCTAssertEqual(groups.tasks.first?.detail, "Needs your input")
    }
}
