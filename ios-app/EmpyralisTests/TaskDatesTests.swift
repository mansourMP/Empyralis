import XCTest
@testable import Empyralis

/// THE STRINGS BELOW ARE CAPTURED FROM THE LIVE ROUTE, not composed here.
/// `GET /api/w/{ws}/fleet/tasks` emits TWO different timestamp shapes on the
/// same object, and only one of them is ISO 8601:
///
///   created_at / updated_at / due_at   "2026-08-25 07:38:07.933865+00:00"
///                                       ^ SPACE separator — not ISO 8601
///   metadata.activity[].timestamp      "2026-08-25T07:38:07.935913+00:00"
///                                       ^ T separator, six fractional digits
///
/// `ISO8601DateFormatter` rejects the space form outright, so it fell all
/// the way through the parse cascade to the date-only branch, which reads
/// the first ten characters and yields MIDNIGHT UTC. Every "created 8 hr
/// ago" on a task created 35 minutes earlier came from that.
final class TaskDatesTests: XCTestCase {

    private let spaceSeparated = "2026-08-25 07:38:07.933865+00:00"
    private let tSeparated = "2026-08-25T07:38:07.935913+00:00"
    private let noFraction = "2026-08-20 17:00:00+00:00"
    private let dateOnly = "2026-08-20"

    private func components(_ date: Date) -> DateComponents {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(secondsFromGMT: 0)!
        return cal.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
    }

    func testParsesTheSpaceSeparatedShapeWithItsTimeOfDay() throws {
        let date = try XCTUnwrap(TaskDates.parse(spaceSeparated))
        let c = components(date)
        XCTAssertEqual(c.year, 2026); XCTAssertEqual(c.month, 8); XCTAssertEqual(c.day, 25)
        // The whole point: NOT midnight.
        XCTAssertEqual(c.hour, 7, "space-separated stamps were collapsing to midnight UTC")
        XCTAssertEqual(c.minute, 38)
        XCTAssertEqual(c.second, 7)
    }

    func testParsesTheTSeparatedActivityShape() throws {
        let date = try XCTUnwrap(TaskDates.parse(tSeparated))
        let c = components(date)
        XCTAssertEqual(c.hour, 7)
        XCTAssertEqual(c.minute, 38)
    }

    func testParsesASpaceSeparatedStampWithNoFractionalSeconds() throws {
        let date = try XCTUnwrap(TaskDates.parse(noFraction))
        let c = components(date)
        XCTAssertEqual(c.day, 20)
        XCTAssertEqual(c.hour, 17)
    }

    /// The date-only contract shape must keep working — `due_at` is
    /// documented as date-only and some writers still send it that way.
    func testStillParsesADateOnlyValue() throws {
        let date = try XCTUnwrap(TaskDates.parse(dateOnly))
        let c = components(date)
        XCTAssertEqual(c.year, 2026); XCTAssertEqual(c.month, 8); XCTAssertEqual(c.day, 20)
    }

    func testUnparseableStaysNil() {
        XCTAssertNil(TaskDates.parse("not a date"))
        XCTAssertNil(TaskDates.parse(""))
        XCTAssertNil(TaskDates.parse(nil))
    }

    /// A stamp a few minutes old must not read as hours old. This is the
    /// user-visible half of the bug.
    func testRecentStampReadsAsMinutesNotHours() {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss.SSSSSSXXXXX"
        f.timeZone = TimeZone(secondsFromGMT: 0)
        f.locale = Locale(identifier: "en_US_POSIX")
        let thirtyFiveMinutesAgo = f.string(from: Date().addingTimeInterval(-35 * 60))

        let rendered = TaskDates.timeAgo(thirtyFiveMinutesAgo)
        XCTAssertFalse(rendered.isEmpty)
        XCTAssertFalse(
            rendered.localizedCaseInsensitiveContains("hr"),
            "a 35-minute-old stamp rendered as \(rendered) — the date-only fallback ate the time"
        )
    }

    /// THE ORDERING BUG, at the function that actually ranks the Inbox.
    ///
    /// `inboxSortMillis` returned 0 for every task timestamp, so every stuck
    /// task tied at 0 and the founder's oldest-first ranking degraded to
    /// whatever order the API returned. The 29 existing InboxNeedsYou tests
    /// stayed GREEN through all of it, because every fixture in them is
    /// hand-written with a `T` separator — a shape `fleet/tasks` does not
    /// emit. This test uses the shape it does emit.
    func testInboxSortMillisReadsTheShapeTheRouteActuallySends() {
        let older = inboxSortMillis("2026-08-20 09:15:00.100000+00:00")
        let newer = inboxSortMillis("2026-08-25 07:38:07.933865+00:00")

        XCTAssertGreaterThan(older, 0, "space-separated stamps were all sorting as 0")
        XCTAssertGreaterThan(newer, 0)
        XCTAssertLessThan(older, newer, "oldest-first ranking cannot work if both sides tie")
    }

    /// Two stamps on the SAME DAY must still be orderable. Under the
    /// date-only fallback they collapsed to the same midnight and tied,
    /// which is the case that actually occurs in a busy workspace.
    func testTwoStampsOnTheSameDayDoNotTie() {
        let morning = inboxSortMillis("2026-08-25 07:38:07.933865+00:00")
        let evening = inboxSortMillis("2026-08-25 19:04:11.020000+00:00")
        XCTAssertNotEqual(morning, evening)
        XCTAssertLessThan(morning, evening)
    }

    func testUnparseableStillSortsAsZero() {
        XCTAssertEqual(inboxSortMillis("not a date"), 0)
        XCTAssertEqual(inboxSortMillis(nil), 0)
    }

    /// Overdue is compared against the start of today, so a stamp earlier
    /// today is NOT overdue while yesterday is. Unchanged by the fix, and
    /// worth pinning so the parse change cannot move it.
    func testOverdueBoundary() {
        XCTAssertTrue(TaskDates.isOverdue(noFraction), "2026-08-20 is in the past")
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss.SSSSSSXXXXX"
        f.timeZone = TimeZone(secondsFromGMT: 0)
        f.locale = Locale(identifier: "en_US_POSIX")
        let tomorrow = f.string(from: Date().addingTimeInterval(36 * 3600))
        XCTAssertFalse(TaskDates.isOverdue(tomorrow))
    }
}
