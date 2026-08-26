import XCTest
@testable import Empyralis

/// THE FIXTURES ARE CAPTURED FROM THE PRODUCER, NOT WRITTEN FROM THE MODEL —
/// same discipline `AgentDecodingTests.swift` establishes, for the same
/// reason: a hand-written fixture only ever encodes what the author already
/// believes the shape is.
///
/// Captured live against the seeded disposable backend (2026-08-26) by
/// driving the REAL route end to end: signed up a throwaway curl-only
/// account (`POST /auth/signup`), created a real project and document,
/// PATCHed it once with a valid `base_sha256` (a stand-in for a concurrent
/// agent edit), then PATCHed it AGAIN with the now-stale original
/// `base_sha256` to trigger a genuine 409 from `fleet_patch_document`. The
/// bytes below are that exact response, reformatted only for line width —
/// nothing added, nothing removed.
final class DocumentWriteAPITests: XCTestCase {

    /// Verbatim from the live route's 409. Two real Postgres rows changed to
    /// produce this: the document was created with body "mine", concurrently
    /// edited to body "theirs" (simulating an agent's write), and then this
    /// is what coming back with the ORIGINAL, now-stale `base_sha256`
    /// produces.
    private let live409ConflictPayload = """
    {"detail":"This document changed since you last read it, so nothing was \
    written. Re-read it and reapply your change on top of the current \
    version.","error":{"code":"document_conflict","message":"This document \
    changed since you last read it, so nothing was written. Re-read it and \
    reapply your change on top of the current version.","class":"user_input_error",\
    "retryable":true,"status_code":409,"request_id":null,"trace_id":null,\
    "details":{"conflict":true,"document":{"id":"doc_5ee40e458b0e4f04",\
    "tenant_id":"tenant_3d7f1a5dbd08","workspace_id":"ws_b2369681de20",\
    "project_id":"project_dbddb2fd20394186","title":"Runbook","path":"runbook.md",\
    "created_by":"5badcccb-add2-4ff6-a542-ee523562cac4",\
    "updated_by":"5badcccb-add2-4ff6-a542-ee523562cac4","metadata":{},\
    "created_at":"2026-08-26 10:20:53.009734+00:00",\
    "updated_at":"2026-08-26 10:21:18.418175+00:00","body":"theirs",\
    "state_sha256":"c916dea4113dab3c85e4a0c519d5f90e565933614fb3281ed0f63538bb70830c"}}}
    """.data(using: .utf8)!

    /// Verbatim from a real 200 PATCH acknowledging a successful write.
    private let live200OkPayload = """
    {"ok":true,"document":{"id":"doc_5ee40e458b0e4f04","tenant_id":"tenant_3d7f1a5dbd08",\
    "workspace_id":"ws_b2369681de20","project_id":"project_dbddb2fd20394186",\
    "title":"Runbook","path":"runbook.md",\
    "created_by":"5badcccb-add2-4ff6-a542-ee523562cac4",\
    "updated_by":"5badcccb-add2-4ff6-a542-ee523562cac4","metadata":{},\
    "created_at":"2026-08-26 10:20:53.009734+00:00",\
    "updated_at":"2026-08-26 10:21:18.418175+00:00","body":"theirs",\
    "state_sha256":"c916dea4113dab3c85e4a0c519d5f90e565933614fb3281ed0f63538bb70830c",\
    "revision_recorded":true}}
    """.data(using: .utf8)!

    /// Verbatim from a real 200 PATCH against a document id that does not
    /// exist — an "ordinary business failure" carried at 200, never a
    /// conflict and never an HTTP-layer error.
    private let live200NotFoundPayload = """
    {"ok":false,"error":"Document not found."}
    """.data(using: .utf8)!

    /// Verbatim from a real 404 (`GET /api/healthz`, a plain HTTPException
    /// with no route-specific `details`) — confirms the generic platform
    /// error envelope this route also produces for anything other than the
    /// structured 409 decodes without a force-unwrap.
    private let liveGenericErrorPayload = """
    {"detail":"Not Found","error":{"code":"not_found","message":"Not Found",\
    "class":"user_input_error","retryable":false,"status_code":404,\
    "request_id":null,"trace_id":null,"details":{}}}
    """.data(using: .utf8)!

    // MARK: - The 200/ok:true shape (DocumentResponse, shared with the read routes)

    func testDecodesARealSuccessfulPatch() throws {
        let decoded = try JSONDecoder().decode(DocumentResponse.self, from: live200OkPayload)
        XCTAssertTrue(decoded.ok)
        XCTAssertEqual(decoded.document?.id, "doc_5ee40e458b0e4f04")
        XCTAssertEqual(decoded.document?.body, "theirs")
        XCTAssertEqual(decoded.document?.stateSha256, "c916dea4113dab3c85e4a0c519d5f90e565933614fb3281ed0f63538bb70830c")
    }

    func testDecodesARealNotFoundBusinessFailure() throws {
        let decoded = try JSONDecoder().decode(DocumentResponse.self, from: live200NotFoundPayload)
        XCTAssertFalse(decoded.ok)
        XCTAssertNil(decoded.document)
        XCTAssertEqual(decoded.error, "Document not found.")
    }

    // MARK: - The 409 conflict shape

    /// Decodes straight into `DocumentWriteAPI`'s OWN types
    /// (`PlatformErrorEnvelope` and friends, internal for exactly this
    /// reason) rather than a duplicate struct — so a field-name drift in the
    /// production decoder fails THIS test instead of two independently
    /// hand-written decoders quietly agreeing with each other.
    func testDecodesARealConflictResponse() throws {
        let envelope = try JSONDecoder().decode(PlatformErrorEnvelope.self, from: live409ConflictPayload)
        XCTAssertEqual(envelope.error?.code, "document_conflict")

        let current = try XCTUnwrap(envelope.error?.details?.document)
        XCTAssertEqual(current.id, "doc_5ee40e458b0e4f04")
        XCTAssertEqual(current.title, "Runbook")
        XCTAssertEqual(current.body, "theirs")
        XCTAssertEqual(current.updatedBy, "5badcccb-add2-4ff6-a542-ee523562cac4")
        XCTAssertEqual(current.stateSha256, "c916dea4113dab3c85e4a0c519d5f90e565933614fb3281ed0f63538bb70830c")
    }

    /// The generic envelope (no `document` under `details`) must decode
    /// cleanly too — this is what every OTHER refusal on this route
    /// (403 role-gated, 404, 500) looks like, and `document` must come back
    /// nil rather than throw.
    func testGenericErrorEnvelopeDecodesWithNoDocument() throws {
        let envelope = try JSONDecoder().decode(PlatformErrorEnvelope.self, from: liveGenericErrorPayload)
        XCTAssertEqual(envelope.error?.code, "not_found")
        XCTAssertEqual(envelope.detail, "Not Found")
        XCTAssertNil(envelope.error?.details?.document)
    }
}
