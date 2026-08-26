import Foundation

/// Local-disk safety net for an in-progress document edit, layered UNDER the
/// explicit-Save contract DocumentEditSheet enforces against the server —
/// see that file's own header for the autosave-vs-explicit-Save reasoning.
/// This store never talks to the network; it exists purely so that typing
/// survives the app being backgrounded (or killed while backgrounded) before
/// the person taps Save.
///
/// Persisted through `DiskCache`, the same mechanism `WorkspaceStore` already
/// uses for its own local-first cache — so a draft is automatically wiped on
/// sign-out by `DiskCache.clearAll()` along with everything else, with no
/// separate cleanup path to remember.
struct DocumentDraftSnapshot: Codable, Equatable {
    let documentId: String
    let title: String
    let body: String
    /// The precondition token this draft was ACTUALLY composed against —
    /// not whatever is freshest when the draft is later restored. Carrying
    /// this forward is what makes a restored draft safe to save: if the
    /// document moved on while the app was away, Save still correctly hits
    /// the same 409 conflict path a same-session edit would, rather than
    /// silently overwriting an intervening change.
    let baseSha256: String?
}

enum DocumentDraftStore {
    private static func key(for documentId: String) -> String {
        "document-draft-\(documentId)"
    }

    static func load(for documentId: String) -> DocumentDraftSnapshot? {
        DiskCache.load(DocumentDraftSnapshot.self, from: key(for: documentId))
    }

    static func save(_ snapshot: DocumentDraftSnapshot) {
        DiskCache.save(snapshot, as: key(for: snapshot.documentId))
    }

    /// Called on a successful save, an explicit discard, or "use theirs
    /// instead" — every path where the draft this represents is no longer
    /// something to protect.
    static func clear(for documentId: String) {
        DiskCache.remove(key(for: documentId))
    }
}
