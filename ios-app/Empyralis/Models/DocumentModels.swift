import Foundation

/// Mirrors `project_documents_repository._row_to_document` as returned by
/// GET /api/w/{workspace_id}/fleet/documents (list, NO body) and
/// GET /api/w/{workspace_id}/fleet/documents/{document_id} (one, WITH body).
///
/// ONE STRUCT FOR BOTH SHAPES, and `body` is optional for that reason: the
/// list route omits it deliberately (include_body=False — a repository tree,
/// not a feed), so a required `body` would fail to decode every list row.
/// `stateSha256` is likewise present only on a body-carrying read; the
/// repository refuses to emit a precondition token that covers a body it did
/// not return.
///
/// Codable (not just Decodable) and Equatable because these get cached to
/// disk — same discipline as EmpTask.
struct EmpDocument: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let path: String
    let projectId: String?
    let body: String?
    let createdBy: String?
    let updatedBy: String?
    let createdAt: String?
    let updatedAt: String?
    let stateSha256: String?

    enum CodingKeys: String, CodingKey {
        case id, title, path, body
        case projectId = "project_id"
        case createdBy = "created_by"
        case updatedBy = "updated_by"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case stateSha256 = "state_sha256"
    }

    /// A title is `.strip()`ed server-side and can legitimately be empty;
    /// the path is the document's real address, so it is the honest fallback
    /// rather than the word "Untitled" pretending to be a name.
    var displayTitle: String {
        title.isEmpty ? path : title
    }

    /// The folder part of the path, or nil at the root. Used as the row's
    /// secondary line — a tree rendered as a list.
    var folder: String? {
        guard let slash = path.lastIndex(of: "/") else { return nil }
        let prefix = String(path[path.startIndex..<slash])
        return prefix.isEmpty ? nil : prefix
    }
}

/// The list route answers {"ok", "documents"} and, on failure, carries
/// "error" beside an EMPTY documents array rather than raising — so "no
/// documents" and "could not load" are two different facts and the caller
/// must check `error` before rendering an empty state.
struct DocumentsResponse: Decodable {
    let ok: Bool
    let documents: [EmpDocument]
    let error: String?
}

/// The single-document route answers {"ok", "document"} — and on a
/// not-found returns ok:false with NO document key at all, which is why
/// `document` is optional here.
struct DocumentResponse: Decodable {
    let ok: Bool
    let document: EmpDocument?
    let error: String?
}
