import SwiftUI

/// A PROJECT'S documents, ordered by path — a repository tree rendered as a
/// list, which is exactly what the backend returns.
///
/// THIS USED TO BE A TOP-LEVEL TAB, and that contradicted a decision the web
/// app had already made: a cross-project document LENS does not earn a
/// permanent rail slot. Documents are a project's accumulated knowledge, so
/// they are reached through the project that owns them — see
/// `ProjectDetailView`, whose two sections mirror `PROJECT_TAB_VIEWS`
/// exactly.
///
/// SCOPED SERVER-SIDE, NOT FILTERED HERE. `GET /fleet/documents` takes an
/// optional `project_id`, and the two modes are NOT the same ACL: passing it
/// runs `enforce_project_access` on that project (a caller with no
/// membership row gets a 404, not an empty list that would still confirm the
/// project exists), while omitting it filters to `_visible_project_ids`.
/// Fetching everything and filtering on the phone would silently take the
/// weaker of the two.
///
/// The list route omits every body on purpose, so a row here can only show
/// identity (title + folder). The body arrives when a document is opened.
struct ProjectDocumentsView: View {
    let project: Project

    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var documents: [EmpDocument] = []
    @State private var hasLoadedOnce = false
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if !hasLoadedOnce {
                if let errorMessage {
                    ScrollView {
                        EmptyStateView(
                            title: "Couldn't load documents",
                            message: errorMessage,
                            systemImage: "exclamationmark.triangle"
                        )
                        .padding(.top, Space.x10)
                    }
                    .refreshable { await load() }
                } else {
                    List {
                        ForEach(0..<4, id: \.self) { _ in
                            SkeletonRow().listRowBackground(Theme.bgPage(scheme))
                        }
                    }
                    .listStyle(.plain)
                }
            } else if documents.isEmpty, let errorMessage {
                // "empty" and "could not load" are different facts and never
                // share one screen.
                ScrollView {
                    EmptyStateView(
                        title: "Couldn't load documents",
                        message: errorMessage,
                        systemImage: "exclamationmark.triangle"
                    )
                    .padding(.top, Space.x10)
                }
                .refreshable { await load() }
            } else if documents.isEmpty {
                ScrollView {
                    EmptyStateView(
                        title: "No documents yet",
                        message: "This project has nothing written down yet.",
                        systemImage: "doc.text"
                    )
                    .padding(.top, Space.x10)
                }
                .refreshable { await load() }
            } else {
                List {
                    ForEach(documents) { document in
                        NavigationLink {
                            DocumentDetailView(document: document)
                        } label: {
                            row(document)
                        }
                        .listRowBackground(Theme.bgPage(scheme))
                    }
                }
                .listStyle(.plain)
                .refreshable { await load() }
            }
        }
        .task(id: project.id) { await load() }
    }

    private func row(_ document: EmpDocument) -> some View {
        VStack(alignment: .leading, spacing: Space.x1) {
            Text(document.displayTitle)
                .font(.empBodyMedium)
                .foregroundStyle(Theme.textPrimary(scheme))
                .lineLimit(1)
            if let folder = document.folder {
                Text(folder)
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
                    .lineLimit(1)
            }
        }
        .padding(.vertical, Space.x1)
    }

    /// Documents are NOT in `WorkspaceStore` — they are fetched per project,
    /// on demand, because the list is potentially large and is only ever read
    /// one project at a time. That makes this the one list in the app that
    /// legitimately starts empty; `hasLoadedOnce` keeps the skeleton honest
    /// exactly the way the store's own flag does, and a failed refresh keeps
    /// the rows already on screen.
    private func load() async {
        guard let workspaceId = session.currentWorkspaceId else {
            errorMessage = "No workspace."
            hasLoadedOnce = true
            return
        }
        do {
            let response: DocumentsResponse = try await APIClient.shared.get(
                "/w/\(workspaceId)/fleet/documents",
                query: ["project_id": project.id]
            )
            // The route answers 200 with ok:false and an empty list when the
            // read itself failed, so the status code alone is not the answer.
            if response.ok {
                documents = response.documents
                errorMessage = nil
            } else {
                errorMessage = response.error ?? "Pull to retry."
            }
            hasLoadedOnce = true
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch APIError.server(let message) {
            errorMessage = message
            hasLoadedOnce = true
        } catch {
            errorMessage = "Pull to retry."
            hasLoadedOnce = true
        }
    }
}
