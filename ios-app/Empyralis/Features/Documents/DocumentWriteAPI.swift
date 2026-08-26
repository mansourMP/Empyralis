import Foundation

/// A DELIBERATE duplicate of `APIClient`'s request pipeline — same reasoning
/// `TaskWriteAPI.swift`'s own header already states for the identical shape:
/// same bearer header, same non-2xx-before-decode ordering, same typed
/// `.unauthorized` case so `SessionStore` keeps making the refresh-or-sign-out
/// decision. FOLD THIS INTO `APIClient` once that file settles.
///
/// It exists here, and not as a thin wrapper over `APIClient.patch`, for one
/// reason: `fleet_patch_document`'s 409 refusal carries a STRUCTURED conflict
/// payload (`error.details.document`, the FULL current document — see
/// routes_fleet.py's own docstring: "the current state is attached so the
/// person can be shown what they would have overwritten") that
/// `APIClient`'s generic `{detail}`-only error decode cannot see. Routing
/// this write through `APIClient.patch` would collapse a 409 into the exact
/// same bare string as a 500 — and the whole point of this feature is that a
/// conflict is NOT just another failure (CLAUDE.md: "a conflict message with
/// no way to see the other side is a dead end, not a choice").
///
/// WIRE SHAPE, verified live against the real seeded backend (2026-08-26),
/// not read off the route's docstring alone — curled a real create, a real
/// concurrent edit, and a real stale PATCH to capture the byte-exact 409
/// body (see DocumentWriteAPITests.swift's fixture, captured verbatim):
///
///   200 {"ok": true,  "document": {...full row, body + state_sha256...}}
///   200 {"ok": false, "error": "..."}                    ordinary business
///                                                          failure (e.g. not
///                                                          found)
///   401                                                   handled before
///                                                          either shape below
///   409 {"detail": "...", "error": {"code": "document_conflict",
///        "message": "...", "details": {"conflict": true,
///        "document": {...current server state...}}}}     REFUSED, nothing
///                                                          written
///   4xx/5xx (other) {"detail": "...", "error": {"message": "...", ...}}
///                                                          the same platform
///                                                          error envelope
///                                                          every HTTPException
///                                                          in this codebase
///                                                          produces
enum DocumentWriteError: Error {
    case unauthorized
    /// A refused stale write. Carries the CURRENT server state (body
    /// included) so the caller can show the person what they would have
    /// overwritten instead of only telling them that something changed.
    case conflict(current: EmpDocument, message: String)
    case server(String)
    /// 2xx, but the reply body could not be decoded. `APIClient` only ever
    /// throws its own `.decoding` AFTER its 2xx status guard passes, so the
    /// same is true here: the write LANDED, only the confirmation was
    /// unreadable. See DocumentEditSheet's own handling for why this is
    /// deliberately NOT treated as a failure.
    case decoding
}

enum DocumentWriteAPI {

    /// PATCH /w/{workspaceId}/fleet/documents/{documentId}. Title and body
    /// are always sent together — mirroring the web's own `runSave`, which
    /// always PATCHes both regardless of which field was actually edited,
    /// because the precondition token (`base_sha256`) covers the WHOLE
    /// document state, not one field.
    ///
    /// `baseSha256` nil means an UNCONDITIONAL overwrite — greppable and
    /// deliberate, matching `update_document`'s own "a caller has to TYPE
    /// None to ask for this" posture. Every call site in this app passes a
    /// real value; there is no reason for this app's own document editor to
    /// ever send an unguarded write.
    static func patchDocument(
        workspaceId: String,
        documentId: String,
        title: String,
        body: String,
        baseSha256: String?
    ) async throws -> EmpDocument {
        let path = "/w/\(workspaceId)/fleet/documents/\(documentId)"
        var req = URLRequest(url: APIConfig.baseURL.appendingPathComponent(path))
        req.httpMethod = "PATCH"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = APIClient.shared.bearerToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        var payload: [String: Any] = ["title": title, "body": body]
        if let baseSha256 { payload["base_sha256"] = baseSha256 }
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)

        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw DocumentWriteError.server("No response")
        }
        if http.statusCode == 401 {
            throw DocumentWriteError.unauthorized
        }
        if http.statusCode == 409 {
            let envelope = try? JSONDecoder().decode(PlatformErrorEnvelope.self, from: data)
            if envelope?.error?.code == "document_conflict", let current = envelope?.error?.details?.document {
                throw DocumentWriteError.conflict(
                    current: current,
                    message: envelope?.error?.message ?? envelope?.detail
                        ?? "This document changed while you were editing."
                )
            }
            // A 409 that does not carry the shape this route promises is
            // still a refusal, just not one this app can render as a
            // conflict — surfaced as an ordinary failure rather than
            // crashing on a force-unwrap, matching the web's own fallback
            // (`documentsRequest`: the conflict branch is checked first,
            // anything that fails it falls through to a plain Error).
            throw DocumentWriteError.server(envelope?.error?.message ?? envelope?.detail ?? "Request failed (409)")
        }
        guard (200..<300).contains(http.statusCode) else {
            let envelope = try? JSONDecoder().decode(PlatformErrorEnvelope.self, from: data)
            throw DocumentWriteError.server(envelope?.error?.message ?? envelope?.detail ?? "Request failed (\(http.statusCode))")
        }

        let ack: DocumentResponse
        do {
            ack = try JSONDecoder().decode(DocumentResponse.self, from: data)
        } catch {
            throw DocumentWriteError.decoding
        }
        guard ack.ok, let document = ack.document else {
            throw DocumentWriteError.server(ack.error ?? "Couldn't save this document.")
        }
        return document
    }
}

// MARK: - The platform error envelope

/// Mirrors `error_response_service.serialize_http_error_envelope` — every
/// `HTTPException` raised anywhere in this backend answers in this exact
/// shape, `{"detail": "<message>", "error": {"code", "message", "details",
/// ...}}`. `APIClient`'s own `ServerErrorBody` only ever reads `detail`,
/// which is enough for an ordinary refusal but not for this route's
/// structured 409 — see this file's own header.
///
/// Internal, not `private` — DocumentWriteAPITests.swift decodes a real
/// captured 409 body straight into THESE types (`@testable import`), rather
/// than a hand-copied duplicate struct in the test file. A duplicate would
/// only ever prove that two independently-written decoders agree with each
/// other, never that either one still matches the wire.
struct PlatformErrorEnvelope: Decodable {
    let detail: String?
    let error: PlatformErrorBody?
}

struct PlatformErrorBody: Decodable {
    let code: String?
    let message: String?
    let details: PlatformErrorDetails?
}

struct PlatformErrorDetails: Decodable {
    /// `EmpDocument` decodes this directly — the 409's embedded document is
    /// the identical shape `get_document` always returns (full body +
    /// `state_sha256`; `_row_to_document`'s `include_body=True` default),
    /// which is what `DocumentPreconditionFailed.current_document` is built
    /// from. Verified byte-for-byte against a real captured 409 response,
    /// not assumed from the route's docstring — see
    /// DocumentWriteAPITests.swift.
    let document: EmpDocument?
}
