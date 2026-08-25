import Foundation

/// Mirrors `workspace_search_service._row_to_task_hit` /
/// `_row_to_document_hit` as returned by GET /api/w/{workspace_id}/search.
///
/// ONE STRUCT FOR BOTH KINDS. A task and a document are genuinely different
/// things, but a search result is a ROW IN A LIST — and the row needs the
/// same five facts either way (what it is, what it's called, a line of
/// context, where it lives, how to open it). Two structs would mean two
/// row views and two decode paths for one screen; the fields that apply to
/// only one kind (`displayId`, `status` for a task; `path` for a document)
/// are optional and the server sends null for the other.
///
/// `kind` is decoded as a STRING, not as an enum. A `Kind` enum would make
/// the whole response fail to decode the day the server learns a third kind
/// — one unknown value silently costing a person their task results. The
/// typed view is `resolvedKind`, which has an `.other` case that this
/// screen simply does not group.
struct SearchHit: Codable, Identifiable, Equatable {
    let id: String
    let kind: String
    let title: String
    let snippet: String?
    let projectId: String?
    /// "GEN-12" — the task's own quotable identifier, and nil rather than a
    /// uuid fragment when the project has no key yet. The server refuses to
    /// invent one (see `workspace_search_service._display_id`), so a nil
    /// here means "there isn't one", never "we couldn't be bothered".
    let displayId: String?
    let status: String?
    /// A document's address ("specs/invoice.md"). Its identifier, in the
    /// place `displayId` occupies for a task.
    let path: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, kind, title, snippet, status, path
        case projectId = "project_id"
        case displayId = "display_id"
        case updatedAt = "updated_at"
    }

    enum Kind: String {
        case task
        case document
        case other
    }

    var resolvedKind: Kind {
        Kind(rawValue: kind) ?? .other
    }

    /// The one-line context under the title. Empty and nil are the same
    /// thing to a reader, so both collapse to nil and the row draws nothing
    /// rather than an empty line that reads as a rendering bug.
    var contextLine: String? {
        guard let snippet, !snippet.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return snippet
    }

    /// The identity a document detail screen needs, rebuilt from the hit.
    ///
    /// SAFE, and specifically not a fabricated document: `body` is nil here
    /// exactly as it is on every row from the documents LIST route (which
    /// omits bodies on purpose), and `DocumentDetailView` fetches the real
    /// body itself from `/fleet/documents/{id}` using this `id`. So opening
    /// a search result shows the true document, never the snippet dressed
    /// up as its contents.
    var asDocument: EmpDocument {
        EmpDocument(
            id: id,
            title: title,
            path: path ?? "",
            projectId: projectId,
            body: nil,
            createdBy: nil,
            updatedBy: nil,
            createdAt: nil,
            updatedAt: updatedAt,
            stateSha256: nil
        )
    }
}

/// The route answers 200 with `ok:false` and an EMPTY results array when the
/// search could not be RUN — so "nothing matched" and "we never searched"
/// are two different facts and a caller must check `ok`/`error` before
/// rendering an empty state. Same contract as `DocumentsResponse`.
struct SearchResponse: Decodable {
    let ok: Bool
    let query: String?
    let results: [SearchHit]
    let error: String?
    let counts: SearchCounts?
}

struct SearchCounts: Decodable, Equatable {
    let tasks: Int
    let documents: Int
}
