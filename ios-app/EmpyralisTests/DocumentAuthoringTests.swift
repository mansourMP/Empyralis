import XCTest
@testable import Empyralis

final class DocumentAuthoringTests: XCTestCase {

    // MARK: - canWrite

    private func member(_ userId: String, role: String) -> WorkspaceMember {
        // WorkspaceMember has no public memberwise init exposed by this file
        // alone, so decode it — the same shape `MembersResponse` produces,
        // matching this codebase's own "build the fixture from the producer"
        // discipline rather than hand-assembling a struct literal.
        let json = """
        {"user_id": "\(userId)", "email": null, "display_name": null, "role": "\(role)"}
        """.data(using: .utf8)!
        return try! JSONDecoder().decode(WorkspaceMember.self, from: json)
    }

    func testOwnerCanWrite() {
        let members = [member("u1", role: "owner")]
        XCTAssertTrue(DocumentAuthoring.canWrite(members: members, ownUserId: "u1"))
    }

    func testMemberCanWrite() {
        let members = [member("u1", role: "member")]
        XCTAssertTrue(DocumentAuthoring.canWrite(members: members, ownUserId: "u1"))
    }

    func testViewerCannotWrite() {
        let members = [member("u1", role: "viewer")]
        XCTAssertFalse(DocumentAuthoring.canWrite(members: members, ownUserId: "u1"))
    }

    func testUnresolvedOwnRoleCannotWrite() {
        // The caller's own row is not in the list at all — e.g. the members
        // fetch failed and nothing is cached. Reads as false, never a guess
        // towards showing a control that might not work.
        let members = [member("someone-else", role: "owner")]
        XCTAssertFalse(DocumentAuthoring.canWrite(members: members, ownUserId: "u1"))
    }

    func testNoOwnUserIdCannotWrite() {
        XCTAssertFalse(DocumentAuthoring.canWrite(members: [], ownUserId: nil))
        XCTAssertFalse(DocumentAuthoring.canWrite(members: [], ownUserId: ""))
    }

    // MARK: - resolvedTitle

    func testResolvedTitleTrims() {
        XCTAssertEqual(DocumentAuthoring.resolvedTitle("  Runbook  "), "Runbook")
    }

    func testResolvedTitleFallsBackWhenBlank() {
        XCTAssertEqual(DocumentAuthoring.resolvedTitle(""), "Untitled document")
        XCTAssertEqual(DocumentAuthoring.resolvedTitle("   "), "Untitled document")
    }

    // MARK: - canSave

    func testCanSaveFalseWhenNothingChanged() {
        XCTAssertFalse(DocumentAuthoring.canSave(
            draftTitle: "Runbook", draftBody: "same\n",
            lastSavedTitle: "Runbook", lastSavedBody: "same\n"
        ))
    }

    func testCanSaveFalseWhenTitleOnlyDiffersByWhitespace() {
        XCTAssertFalse(DocumentAuthoring.canSave(
            draftTitle: "  Runbook  ", draftBody: "same\n",
            lastSavedTitle: "Runbook", lastSavedBody: "same\n"
        ))
    }

    func testCanSaveTrueOnARealTitleChange() {
        XCTAssertTrue(DocumentAuthoring.canSave(
            draftTitle: "New title", draftBody: "same\n",
            lastSavedTitle: "Runbook", lastSavedBody: "same\n"
        ))
    }

    func testCanSaveTrueOnARealBodyChange() {
        XCTAssertTrue(DocumentAuthoring.canSave(
            draftTitle: "Runbook", draftBody: "new body\n",
            lastSavedTitle: "Runbook", lastSavedBody: "same\n"
        ))
    }

    /// Body is compared RAW, never trimmed — a document's body is markdown,
    /// where trailing whitespace can be real content (inside a code fence, a
    /// deliberate blank line). Only the title gets the whitespace-forgiving
    /// treatment.
    func testCanSaveTrueWhenBodyOnlyDiffersByTrailingWhitespace() {
        XCTAssertTrue(DocumentAuthoring.canSave(
            draftTitle: "Runbook", draftBody: "same\n  ",
            lastSavedTitle: "Runbook", lastSavedBody: "same\n"
        ))
    }

    func testCanSaveTrueWhenBlankTitleWouldResolveToADifferentSavedTitle() {
        // Blank title resolves to "Untitled document", which differs from
        // whatever was last saved — a real, savable change.
        XCTAssertTrue(DocumentAuthoring.canSave(
            draftTitle: "   ", draftBody: "same\n",
            lastSavedTitle: "Runbook", lastSavedBody: "same\n"
        ))
    }
}
