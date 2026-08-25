import XCTest
@testable import Empyralis

/// "My work" bucketing, proven against the REAL rule `MyWork.swift` exports
/// rather than a re-typed copy of it. Every assertion here is a port of one
/// in the web's own `my-work.test.ts`, so the two products cannot drift.
///
/// THE ASSERTIONS THAT MATTER ARE THE NEGATIVE ONES: this surface's whole
/// risk is over-inclusion — a teammate's work, or unclaimed work, quietly
/// filed under the reader's name — and an over-inclusive implementation
/// satisfies every "my task is listed" assertion perfectly.
final class MyWorkTests: XCTestCase {

    private let me = "user_me"
    private let other = "user_other"
    private let agentId = "agent_install_1"

    private struct TaskFixture: MyWorkTaskShape {
        var id: String = "t"
        var status: String = "todo"
        var assigneeUserId: String?
        var assigneeAgentId: String?
        var createdBy: String?
    }

    // MARK: - Bucket "mine": assigned to me as a person

    func testATaskAssignedToMeIsMine() {
        XCTAssertEqual(myWorkBucket(TaskFixture(assigneeUserId: me), userId: me), .mine)
    }

    func testATaskAssignedToAnotherPersonIsNotMine() {
        XCTAssertNil(myWorkBucket(TaskFixture(assigneeUserId: other), userId: me))
    }

    // MARK: - Bucket "agent": work I handed over

    func testATaskIFiledAndHandedToAnAgentIsInTheAgentBucket() {
        XCTAssertEqual(
            myWorkBucket(TaskFixture(assigneeAgentId: agentId, createdBy: me), userId: me),
            .agent
        )
    }

    /// THE OVER-INCLUSION THE `createdBy` CLAUSE EXISTS TO STOP. Drop it and
    /// this becomes "every agent-assigned task in every project I can see",
    /// i.e. a teammate's agents' work filed under my name.
    func testATaskSOMEBODYELSEHandedToAnAgentIsNotMyWork() {
        XCTAssertNil(myWorkBucket(TaskFixture(assigneeAgentId: agentId, createdBy: other), userId: me))
    }

    func testAnAgentTaskWithNoKnownAuthorIsNotClaimedForMe() {
        XCTAssertNil(myWorkBucket(TaskFixture(assigneeAgentId: agentId, createdBy: nil), userId: me))
    }

    // MARK: - Everything else is out

    /// My work is an ownership list, not a job board — unclaimed work is
    /// browsed on the project's own list.
    func testAnUnassignedTaskICreatedIsNotMyWork() {
        XCTAssertNil(myWorkBucket(TaskFixture(createdBy: me), userId: me))
    }

    func testATaskICreatedAndHandedToAnotherPersonIsTheirs() {
        XCTAssertNil(myWorkBucket(TaskFixture(assigneeUserId: other, createdBy: me), userId: me))
    }

    func testWithNoResolvedIdentityNothingIsClaimed() {
        let task = TaskFixture(assigneeUserId: me)
        XCTAssertNil(myWorkBucket(task, userId: nil), "never a fallback that guesses")
        XCTAssertNil(myWorkBucket(task, userId: ""))
        XCTAssertNil(myWorkBucket(task, userId: "  "), "a blank id is no identity, not a matchable value")
    }

    // MARK: - The two buckets never overlap

    /// `project_tasks_single_assignee_check` guarantees a task cannot carry
    /// both assignee columns. This asserts the human column wins if one ever
    /// arrives that way, rather than the task appearing in both lists.
    func testIfBothAssigneeColumnsWereSetTheHumanOneDecides() {
        XCTAssertEqual(
            myWorkBucket(TaskFixture(assigneeUserId: me, assigneeAgentId: agentId, createdBy: me), userId: me),
            .mine
        )
    }

    // MARK: - selectMyWork partitions, preserving order

    private var rows: [TaskFixture] {
        [
            TaskFixture(id: "a", assigneeUserId: me),
            TaskFixture(id: "b", assigneeUserId: other),
            TaskFixture(id: "c", assigneeAgentId: agentId, createdBy: me),
            TaskFixture(id: "d", assigneeAgentId: agentId, createdBy: other),
            TaskFixture(id: "e", status: "done", assigneeUserId: me),
        ]
    }

    func testSelectMyWorkPartitionsInInputOrder() {
        let split = selectMyWork(rows, userId: me)
        XCTAssertEqual(split.mine.map(\.id), ["a", "e"])
        XCTAssertEqual(split.agent.map(\.id), ["c"])
    }

    /// It is a FILTER, not a regrouping — rows that are nobody's business
    /// here are dropped entirely.
    func testPartitioningDropsRowsThatAreNotMine() {
        let split = selectMyWork(rows, userId: me)
        XCTAssertLessThan(split.mine.count + split.agent.count, rows.count)
    }

    func testTwoReadersOfTheSameListShareNoRows() {
        let mineSplit = selectMyWork(rows, userId: me)
        let otherSplit = selectMyWork(rows, userId: other)
        let mineIds = Set((mineSplit.mine + mineSplit.agent).map(\.id))
        let otherIds = Set((otherSplit.mine + otherSplit.agent).map(\.id))

        XCTAssertEqual(mineIds, ["a", "c", "e"])
        XCTAssertEqual(otherIds, ["b", "d"])
        XCTAssertTrue(mineIds.isDisjoint(with: otherIds), "the split is per-identity, not a shared view")
    }

    // MARK: - Open-ness: only `done` is terminal

    func testEveryNonDoneStatusIsStillOpenWork() {
        for status in ["backlog", "todo", "in_progress", "awaiting_input", "blocked", "in_review"] {
            XCTAssertTrue(isOpenMyWork(TaskFixture(status: status)), "\(status) is still open work")
        }
    }

    func testDoneIsTheOnlyTerminalStatusAndTheCheckIsCaseInsensitive() {
        XCTAssertFalse(isOpenMyWork(TaskFixture(status: "done")))
        XCTAssertFalse(isOpenMyWork(TaskFixture(status: "DONE")))
        XCTAssertFalse(isOpenMyWork(TaskFixture(status: " done ")))
    }

    // MARK: - The count reads BOTH buckets, open only

    func testCountSpansBothBucketsAndCountsOpenOnly() {
        XCTAssertEqual(myWorkBadgeCount(rows, userId: me), 2, "a + c; `e` is done")
    }

    func testAnotherReadersCountIsTheirOwnRows() {
        XCTAssertEqual(myWorkBadgeCount(rows, userId: other), 2, "b + d")
    }

    func testNoIdentityResolvesToZeroNeverToEverything() {
        XCTAssertEqual(myWorkBadgeCount(rows, userId: nil), 0)
        XCTAssertEqual(myWorkBadgeCount(rows, userId: ""), 0)
    }

    func testAnEmptyWorkspaceCountsZero() {
        XCTAssertEqual(myWorkBadgeCount([], userId: me), 0, "a zero badge is noise; 0 is how the rule says so")
    }

    /// THE COUNT IS NOT A SECOND RULE. Any badge and what the screen actually
    /// lists as open must agree, or the number quietly understates exactly
    /// the thing the founder asked to be shown.
    func testTheCountEqualsWhatTheScreenListsAsOpen() {
        let split = selectMyWork(rows, userId: me)
        let openOnScreen = (split.mine + split.agent).filter(isOpenMyWork).count
        XCTAssertEqual(openOnScreen, myWorkBadgeCount(rows, userId: me))
    }

    // MARK: - The rule is wired to the app's own wire model

    /// `EmpTask` is what `WorkspaceStore` actually holds, so the screen only
    /// works if the real decoded row satisfies the rule. A field rename on
    /// either side breaks here rather than silently emptying the screen.
    func testEmpTaskSatisfiesBothBuckets() throws {
        let mineJSON = """
        {"id":"t1","title":"Mine","status":"todo","assignee_user_id":"\(me)"}
        """.data(using: .utf8)!
        let agentJSON = """
        {"id":"t2","title":"Handed over","status":"in_progress","assignee_agent_id":"\(agentId)","created_by":"\(me)"}
        """.data(using: .utf8)!
        let theirsJSON = """
        {"id":"t3","title":"Theirs","status":"todo","assignee_agent_id":"\(agentId)","created_by":"\(other)"}
        """.data(using: .utf8)!

        let decoder = JSONDecoder()
        let mine = try decoder.decode(EmpTask.self, from: mineJSON)
        let handed = try decoder.decode(EmpTask.self, from: agentJSON)
        let theirs = try decoder.decode(EmpTask.self, from: theirsJSON)

        XCTAssertEqual(myWorkBucket(mine, userId: me), .mine)
        XCTAssertEqual(myWorkBucket(handed, userId: me), .agent)
        XCTAssertNil(myWorkBucket(theirs, userId: me))

        let split = selectMyWork([mine, handed, theirs], userId: me)
        XCTAssertEqual(split.mine.map(\.id), ["t1"])
        XCTAssertEqual(split.agent.map(\.id), ["t2"])
        XCTAssertEqual(myWorkBadgeCount([mine, handed, theirs], userId: me), 2)
    }
}
