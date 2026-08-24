import SwiftUI

/// The workspace's documents, ordered by path — a repository tree rendered
/// as a list, which is exactly what the backend returns and the shape the
/// web app's Context view uses.
///
/// The list route omits every body on purpose, so a row here can only show
/// identity (title + folder). The body arrives when a document is opened.
struct DocumentsView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var documents: [EmpDocument] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            List {
                ForEach(documents) { document in
                    NavigationLink {
                        DocumentDetailView(document: document)
                    } label: {
                        row(document)
                    }
                }
            }
            .listStyle(.plain)
            .navigationTitle("Documents")
            .refreshable { await load() }
            .task { await load() }
            .overlay {
                if isLoading && documents.isEmpty {
                    ProgressView()
                } else if let errorMessage {
                    // "empty" and "could not load" are different facts and
                    // never share one screen.
                    ContentUnavailableView(
                        "Couldn't load documents",
                        systemImage: "exclamationmark.triangle",
                        description: Text(errorMessage)
                    )
                } else if documents.isEmpty {
                    ContentUnavailableView("No documents yet", systemImage: "doc.text")
                }
            }
        }
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

    private func load() async {
        guard let workspaceId = session.currentWorkspaceId else {
            isLoading = false
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            let response: DocumentsResponse = try await APIClient.shared.get(
                "/w/\(workspaceId)/fleet/documents",
                query: [:]
            )
            documents = response.documents
            // The route answers 200 with ok:false and an empty list when the
            // read itself failed, so the status code alone is not the answer.
            errorMessage = response.ok ? nil : (response.error ?? "Please pull to retry.")
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
        } catch {
            errorMessage = "Pull to retry."
        }
    }
}
