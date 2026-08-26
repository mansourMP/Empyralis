import XCTest
@testable import Empyralis

final class TaskAuthoringTests: XCTestCase {

    // MARK: - canSaveEdit

    func testSaveDisabledWhenNothingChanged() {
        XCTAssertFalse(TaskAuthoring.canSaveEdit(draft: "Ship it", initial: "Ship it", requiresNonEmpty: true))
    }

    func testSaveDisabledWhenOnlyWhitespaceDiffers() {
        // Trimmed comparison — a trailing space the person didn't mean to
        // type must not read as a real edit.
        XCTAssertFalse(TaskAuthoring.canSaveEdit(draft: "Ship it  ", initial: "Ship it", requiresNonEmpty: true))
    }

    func testSaveEnabledOnARealChange() {
        XCTAssertTrue(TaskAuthoring.canSaveEdit(draft: "Ship it faster", initial: "Ship it", requiresNonEmpty: true))
    }

    /// The whole reason `requiresNonEmpty` exists: the backend's own
    /// `COALESCE(NULLIF($4, ''), title)` silently no-ops an empty title
    /// rather than clearing it, so this must refuse before that request is
    /// ever sent — a "Save" that quietly changed nothing would be a lie.
    func testTitleCannotBeSavedBlank() {
        XCTAssertFalse(TaskAuthoring.canSaveEdit(draft: "   ", initial: "Ship it", requiresNonEmpty: true))
    }

    /// Descriptions have no such floor — an empty save is how one is
    /// deliberately CLEARED, and the backend applies it.
    func testDescriptionCanBeSavedBlankToClearIt() {
        XCTAssertTrue(TaskAuthoring.canSaveEdit(draft: "", initial: "Some notes", requiresNonEmpty: false))
    }

    func testEmptyToEmptyIsNotAChange() {
        XCTAssertFalse(TaskAuthoring.canSaveEdit(draft: "  ", initial: "", requiresNonEmpty: false))
    }

    // MARK: - canCreateTask

    func testCreateDisabledOnBlankTitle() {
        XCTAssertFalse(TaskAuthoring.canCreateTask(title: "   ", isCreating: false))
    }

    func testCreateDisabledWhileAlreadyCreating() {
        XCTAssertFalse(TaskAuthoring.canCreateTask(title: "Fix the crash", isCreating: true))
    }

    func testCreateEnabledWithARealTitle() {
        XCTAssertTrue(TaskAuthoring.canCreateTask(title: "Fix the crash", isCreating: false))
    }

    // MARK: - subtaskTotal

    func testSubtaskTotalPrefersTheLargerCount() {
        // The existing case: the server's rollup already knows about more
        // than have synced locally.
        XCTAssertEqual(TaskAuthoring.subtaskTotal(rollup: 3, synced: 1), 3)
        // The new case this feature introduces: a sub-task was just created
        // locally and the rollup has not caught up yet.
        XCTAssertEqual(TaskAuthoring.subtaskTotal(rollup: 0, synced: 1), 1)
    }

    func testSubtaskTotalWhenBothAgree() {
        XCTAssertEqual(TaskAuthoring.subtaskTotal(rollup: 2, synced: 2), 2)
    }
}
