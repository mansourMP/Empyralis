import XCTest
@testable import Empyralis

/// DeepLink.parse decides what an EXTERNALLY-SUPPLIED URL is allowed to
/// open inside a signed-in session, so the negative cases below matter more
/// than the positive ones. A permissive matcher here is how a link from
/// somewhere else ends up opening somebody's task.
final class DeepLinkTests: XCTestCase {

    // MARK: - The shapes the backend actually emits

    func testParsesTaskURL() {
        let url = URL(string: "https://empyralis.ai/w/ws_abc123/projects/proj_1/tasks/task_9")!
        XCTAssertEqual(DeepLink.parse(url), .task(workspaceId: "ws_abc123", taskId: "task_9"))
    }

    func testParsesDocumentURL() {
        let url = URL(string: "https://empyralis.ai/w/ws_abc123/projects/proj_1/documents/doc_5")!
        XCTAssertEqual(DeepLink.parse(url), .document(workspaceId: "ws_abc123", documentId: "doc_5"))
    }

    func testParsesProjectURL() {
        let url = URL(string: "https://empyralis.ai/w/ws_abc123/projects/proj_1")!
        XCTAssertEqual(DeepLink.parse(url), .project(workspaceId: "ws_abc123", projectId: "proj_1"))
    }

    func testParsesAgentURL() {
        let url = URL(string: "https://empyralis.ai/w/ws_abc123/agents/ainstall_7")!
        XCTAssertEqual(DeepLink.parse(url), .agent(workspaceId: "ws_abc123", agentId: "ainstall_7"))
    }

    func testTrailingQueryAndFragmentAreIgnored() {
        let url = URL(string: "https://empyralis.ai/w/ws_1/projects/p/tasks/t?utm=x#frag")!
        XCTAssertEqual(DeepLink.parse(url), .task(workspaceId: "ws_1", taskId: "t"))
    }

    func testWWWHostIsAccepted() {
        let url = URL(string: "https://www.empyralis.ai/w/ws_1/projects/p/tasks/t")!
        XCTAssertEqual(DeepLink.parse(url), .task(workspaceId: "ws_1", taskId: "t"))
    }

    // MARK: - The cases that must be REFUSED

    /// The important one. A hostile or merely unrelated site using our exact
    /// path shape must not open anything.
    func testForeignHostIsRefused() {
        let url = URL(string: "https://evil.example.com/w/ws_1/projects/p/tasks/t")!
        XCTAssertNil(DeepLink.parse(url))
    }

    /// A lookalike host that merely CONTAINS our domain is a different site.
    func testLookalikeHostIsRefused() {
        let url = URL(string: "https://empyralis.ai.evil.com/w/ws_1/projects/p/tasks/t")!
        XCTAssertNil(DeepLink.parse(url))
    }

    func testUnknownSubPathIsRefused() {
        let url = URL(string: "https://empyralis.ai/w/ws_1/projects/p/billing/x")!
        XCTAssertNil(DeepLink.parse(url))
    }

    func testMissingWorkspaceIsRefused() {
        XCTAssertNil(DeepLink.parse(URL(string: "https://empyralis.ai/w/")!))
        XCTAssertNil(DeepLink.parse(URL(string: "https://empyralis.ai/")!))
    }

    func testNonWorkspaceRootIsRefused() {
        let url = URL(string: "https://empyralis.ai/settings/account")!
        XCTAssertNil(DeepLink.parse(url))
    }

    // MARK: - Notification payloads

    func testNotificationPayloadResolvesTask() {
        let info: [AnyHashable: Any] = ["workspace_id": "ws_1", "task_id": "task_2"]
        XCTAssertEqual(
            DeepLink.parse(notificationUserInfo: info),
            .task(workspaceId: "ws_1", taskId: "task_2")
        )
    }

    /// A payload with a workspace but nothing to open is not a destination.
    /// Returning the workspace root instead would send someone who tapped a
    /// specific notification to a generic screen with no explanation.
    func testNotificationPayloadWithoutTargetIsNil() {
        XCTAssertNil(DeepLink.parse(notificationUserInfo: ["workspace_id": "ws_1"]))
    }

    func testNotificationPayloadWithoutWorkspaceIsNil() {
        XCTAssertNil(DeepLink.parse(notificationUserInfo: ["task_id": "task_2"]))
    }

    /// A `url` key in a push payload goes through the SAME host check as a
    /// universal link — a notification must not be a way around it.
    func testNotificationURLKeyStillEnforcesHost() {
        let info: [AnyHashable: Any] = ["url": "https://evil.example.com/w/ws_1/projects/p/tasks/t"]
        XCTAssertNil(DeepLink.parse(notificationUserInfo: info))
    }
}
