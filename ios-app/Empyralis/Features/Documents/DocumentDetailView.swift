import SwiftUI

/// One document, read-only. The list row it was opened from carries no body
/// (the list route omits it), so the body is fetched here — and the row's
/// own title is shown immediately rather than behind a spinner, because the
/// identity is already known and only the content is outstanding.
///
/// NO EDITING IN THIS PASS. The stale-write precondition the backend
/// enforces (`state_sha256`) is decoded and carried on the model so an edit
/// pass has it, but nothing here writes.
struct DocumentDetailView: View {
    let document: EmpDocument

    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var body_: String?
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.x4) {
                header

                if let body_ {
                    if body_.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        Text("This document is empty.")
                            .font(.empSecondary)
                            .foregroundStyle(Theme.textMuted(scheme))
                    } else {
                        MarkdownRenderer(markdown: body_)
                    }
                } else if let errorMessage {
                    Text(errorMessage)
                        .font(.empSecondary)
                        .foregroundStyle(Theme.textSecondary(scheme))
                } else if isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, Space.x6)
                }
            }
            .padding(Space.x4)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Theme.bgPage(scheme))
        // NO NAVIGATION TITLE — the body owns the title, exactly as
        // TaskDetailView does. Setting one here put the same string on
        // screen THREE times at once: the navigation bar, this view's own
        // header, and the document's leading `# H1`, which every real
        // markdown document has (all four seeded documents do). The
        // navigation-bar copy was also the one that truncated first, since
        // it is the narrowest of the three.
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .refreshable { await load() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Space.x2) {
            Text(document.displayTitle)
                .font(.empTitle)
                .foregroundStyle(Theme.textPrimary(scheme))
            Text(document.path)
                .font(.empCaption)
                .foregroundStyle(Theme.textMuted(scheme))
            Divider()
                .overlay(Theme.border(scheme))
        }
    }

    private func load() async {
        guard let workspaceId = session.currentWorkspaceId else {
            isLoading = false
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            let response: DocumentResponse = try await APIClient.shared.get(
                "/w/\(workspaceId)/fleet/documents/\(document.id)",
                query: [:]
            )
            if let fetched = response.document {
                body_ = fetched.body ?? ""
                errorMessage = nil
            } else {
                errorMessage = response.error ?? "This document couldn't be opened."
            }
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch {
            errorMessage = "Couldn't load this document. Pull to retry."
        }
    }
}
