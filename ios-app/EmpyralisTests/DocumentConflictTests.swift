import XCTest
@testable import Empyralis

/// Every assertion here is a direct port of `document-conflict.test.ts` —
/// same cases, same reasoning, so a divergence between the two products is a
/// test failure on at least one side rather than something only a person
/// staring at both screens would notice. See DocumentConflict.swift's own
/// header for the property that actually matters: NEITHER SIDE'S TEXT IS
/// EVER THROWN AWAY BY THE PRODUCT.
final class DocumentConflictTests: XCTestCase {

    // MARK: - The actor

    func testResolvedActorIsNamed() {
        XCTAssertEqual(DocumentConflict.actorLabel("Support Bot"), "Support Bot")
    }

    func testUnresolvedActorReadsAsSomeoneElse() {
        XCTAssertEqual(DocumentConflict.actorLabel(nil), "Someone else")
        XCTAssertEqual(DocumentConflict.actorLabel(""), "Someone else")
        XCTAssertEqual(DocumentConflict.actorLabel("   "), "Someone else")
    }

    // MARK: - The plan

    func testDifferentContentNeedsADecision() {
        let plan = DocumentConflict.plan(
            mine: .init(title: "Runbook", body: "mine\n"),
            theirs: .init(title: "Runbook", body: "theirs\n"),
            actorName: "Support Bot"
        )
        XCTAssertTrue(plan.needsResolution)
        XCTAssertTrue(plan.headline.contains("Support Bot"), "the headline names who changed it — 'this changed' with no actor is half a fact")
        XCTAssertTrue(plan.reassurance.contains("have not been saved"), "the person is told their draft is intact AND unsaved")
        XCTAssertEqual(plan.actions.count, 2, "exactly two ways out, both explicit")
        XCTAssertEqual(plan.actions.filter { $0.accent }.count, 1, "one accent action only")
    }

    func testEveryActionStatesItsOwnConsequence() {
        let plan = DocumentConflict.plan(
            mine: .init(title: "Runbook", body: "mine\n"),
            theirs: .init(title: "Runbook", body: "theirs\n"),
            actorName: "Support Bot"
        )
        for action in plan.actions {
            XCTAssertFalse(action.consequence.trimmingCharacters(in: .whitespaces).isEmpty)
        }
    }

    func testAccentIsOnTheOptionThatLosesNothing() {
        let plan = DocumentConflict.plan(
            mine: .init(title: "Runbook", body: "mine\n"),
            theirs: .init(title: "Runbook", body: "theirs\n"),
            actorName: "Support Bot"
        )
        let keepMine = plan.actions.first { $0.key == .keepMine }!
        let takeTheirs = plan.actions.first { $0.key == .takeTheirs }!
        XCTAssertTrue(keepMine.accent)
        XCTAssertFalse(takeTheirs.accent)
        XCTAssertTrue(
            keepMine.consequence.range(of: "history", options: .caseInsensitive) != nil,
            "keeping mine must say the other version survives in history"
        )
        XCTAssertTrue(takeTheirs.consequence.range(of: "discard", options: .caseInsensitive) != nil)
        XCTAssertTrue(takeTheirs.consequence.range(of: "cannot be recovered", options: .caseInsensitive) != nil)
        XCTAssertNil(
            takeTheirs.label.range(of: "keep", options: .caseInsensitive),
            "the destructive option is never worded as if it keeps something"
        )
    }

    func testIdenticalContentIsNotAConflict() {
        let plan = DocumentConflict.plan(
            mine: .init(title: "Runbook", body: "same bytes\n"),
            theirs: .init(title: "Runbook", body: "same bytes\n"),
            actorName: "Support Bot"
        )
        XCTAssertFalse(plan.needsResolution, "a rewrite that produced identical text is not a conflict")
        XCTAssertEqual(plan.actions.count, 0)
    }

    func testTitleOnlyDivergenceStillNeedsADecision() {
        let plan = DocumentConflict.plan(
            mine: .init(title: "Runbook", body: "same\n"),
            theirs: .init(title: "Deploy Runbook", body: "same\n")
        )
        XCTAssertTrue(plan.needsResolution, "a stale save reverts a rename just as silently as a paragraph")
    }

    // MARK: - The diff

    func testTheDiffShowsMineAsRemovedAndTheirsAsAdded() {
        let d = DocumentConflict.diffBodies("one\ntwo\nthree\n", "one\nTWO\nthree\n")
        XCTAssertTrue(d.lines.contains { $0.kind == .remove && $0.text == "two" }, "the person's own line shows as removed")
        XCTAssertTrue(d.lines.contains { $0.kind == .add && $0.text == "TWO" }, "the incoming line shows as added")
        XCTAssertFalse(
            d.lines.contains { $0.kind != .context && $0.text == "one" },
            "an unchanged line is never reported as changed"
        )
    }

    func testIdenticalBodiesProduceNoChanges() {
        let noChange = DocumentConflict.diffBodies("same\n", "same\n")
        XCTAssertTrue(noChange.lines.allSatisfy { $0.kind == .context })
    }

    func testALongRewriteIsBoundedAndSaysSo() {
        let longMine = (0..<200).map { "mine \($0)" }.joined(separator: "\n")
        let longTheirs = (0..<200).map { "theirs \($0)" }.joined(separator: "\n")
        let bounded = DocumentConflict.diffBodies(longMine, longTheirs, maxLines: 40)
        XCTAssertLessThanOrEqual(bounded.lines.count, 40)
        XCTAssertTrue(bounded.truncated)
    }

    func testLongRunsOfUnchangedLinesAreCollapsed() {
        let mine = ["a", "b", "c", "d", "e", "f", "g", "CHANGED", "h", "i", "j"].joined(separator: "\n")
        let theirs = ["a", "b", "c", "d", "e", "f", "g", "changed", "h", "i", "j"].joined(separator: "\n")
        let d = DocumentConflict.diffBodies(mine, theirs)
        XCTAssertLessThan(d.lines.count, 11, "long runs of unchanged lines are collapsed")
        XCTAssertTrue(d.lines.contains { $0.kind == .remove && $0.text == "CHANGED" })
        XCTAssertTrue(d.lines.contains { $0.kind == .add && $0.text == "changed" })
    }

    func testAPathologicalPasteDegradesToASummary() {
        let huge = (0..<2500).map { "line \($0)" }.joined(separator: "\n")
        let hugeOther = (0..<2500).map { "other \($0)" }.joined(separator: "\n")
        let degraded = DocumentConflict.diffBodies(huge, hugeOther)
        XCTAssertTrue(degraded.truncated)
        XCTAssertLessThanOrEqual(degraded.lines.count, 4)
    }
}
