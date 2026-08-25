import SwiftUI

/// Find a task or a document by what it SAYS.
///
/// Network-backed rather than a filter over `WorkspaceStore`, deliberately.
/// The store holds tasks only, and holds them as identity (title, status) —
/// it has never carried a description, and it carries no documents at all.
/// A local filter would therefore answer "not found" for a task whose words
/// are in its description and for every document in the workspace, which is
/// a worse lie than a spinner. The server owns the index; this screen owns
/// saying honestly what came back.
///
/// FOUR STATES, AND THEY ARE A TYPE — see `SearchState`. Collapsing "nothing
/// matched" into "couldn't search" is CLAUDE.md's outcome-honesty law, and a
/// search box is where it costs most: a person told "No results for
/// invoice" concludes their task is gone, closes the app, and refiles it.
struct SearchView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var query = ""
    @State private var state: SearchState = .idle
    /// Bumped by "Try again". It is part of the task id below, so a retry
    /// re-runs the identical query — which `.task(id:)` would otherwise
    /// consider unchanged and skip.
    @State private var retryToken = 0
    @State private var path = NavigationPath()

    /// The four states, made unrepresentable in combination. Two booleans
    /// (`isSearching`, `errorMessage`) plus an array is the shape that lets
    /// a failed request render as an empty result — this cannot.
    private enum SearchState: Equatable {
        case idle
        case searching
        case results([SearchHit])
        case noResults(query: String)
        case failed(message: String)
    }

    /// What a run is keyed on. Equatable so `.task(id:)` cancels the
    /// in-flight request the moment the next keystroke arrives — which is
    /// half the debounce, and the half that also stops a slow earlier
    /// response from overwriting a newer one.
    private struct Attempt: Equatable {
        let query: String
        let token: Int
    }

    private var attempt: Attempt {
        Attempt(
            query: query.trimmingCharacters(in: .whitespacesAndNewlines),
            token: retryToken
        )
    }

    var body: some View {
        NavigationStack(path: $path) {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()
                content
            }
            .navigationTitle("Search")
            .navigationDestination(for: TaskRoute.self) { route in
                TaskDetailView(taskId: route.taskId)
            }
        }
        .searchable(
            text: $query,
            placement: .navigationBarDrawer(displayMode: .always),
            prompt: "Search tasks and documents"
        )
        .task(id: attempt) { await run(attempt) }
    }

    // MARK: - States

    @ViewBuilder
    private var content: some View {
        switch state {
        case .idle:
            // Nothing typed. Not an error, not a zero-result search — the
            // screen says what it can do and asks for nothing else.
            EmptyStateView(
                title: "Search this workspace",
                message: "Find a task or a document by its title or its text.",
                systemImage: "magnifyingglass"
            )

        case .searching:
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .noResults(let searched):
            // Names what was searched for, so it is obvious this is an
            // answer about THAT query and not a broken screen.
            EmptyStateView(
                title: "No results for \u{201C}\(searched)\u{201D}",
                message: "Nothing in this workspace matches those words.",
                systemImage: "text.magnifyingglass"
            )

        case .failed(let message):
            failure(message)

        case .results(let hits):
            resultsList(hits)
        }
    }

    /// "Couldn't search" — and never the same screen as "no results". The
    /// distinction is the whole point: one means look elsewhere, the other
    /// means try again.
    private func failure(_ message: String) -> some View {
        VStack(spacing: Space.x4) {
            EmptyStateView(
                title: "Couldn't search",
                message: message,
                systemImage: "wifi.exclamationmark"
            )
            // THE ONLY ACCENT ON THIS SCREEN. One primary action, in one
            // state — results, section headers, identifiers and selection
            // are all neutral (CLAUDE.md: purple only on primary buttons).
            Button("Try again") { retryToken += 1 }
                .buttonStyle(PrimaryButtonStyle())
                .frame(maxWidth: 240)
        }
        .padding(.horizontal, Space.x6)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func resultsList(_ hits: [SearchHit]) -> some View {
        let tasks = hits.filter { $0.resolvedKind == .task }
        let documents = hits.filter { $0.resolvedKind == .document }

        return List {
            if !tasks.isEmpty {
                Section {
                    ForEach(tasks) { hit in
                        // Value-based, through the SAME TaskRoute the Inbox
                        // and every deep link already push — two mechanisms
                        // for "open a task" would drift.
                        NavigationLink(value: TaskRoute(taskId: hit.id)) {
                            taskRow(hit)
                        }
                        .listRowBackground(Theme.bgPage(scheme))
                    }
                } header: {
                    sectionHeader("Tasks", count: tasks.count)
                }
            }

            if !documents.isEmpty {
                Section {
                    ForEach(documents) { hit in
                        NavigationLink {
                            DocumentDetailView(document: hit.asDocument)
                        } label: {
                            documentRow(hit)
                        }
                        .listRowBackground(Theme.bgPage(scheme))
                    }
                } header: {
                    sectionHeader("Documents", count: documents.count)
                }
            }
        }
        .listStyle(.plain)
    }

    private func sectionHeader(_ title: String, count: Int) -> some View {
        HStack(spacing: Space.x2) {
            Text(title)
            Text("\(count)")
                .font(.empMono)
        }
        .font(.empSectionHeader)
        .foregroundStyle(Theme.textMuted(scheme))
    }

    // MARK: - Rows

    private func taskRow(_ hit: SearchHit) -> some View {
        HStack(alignment: .top, spacing: Space.x3) {
            StatusDot(status: hit.status ?? "open")
                .padding(.top, 5)

            VStack(alignment: .leading, spacing: Space.x1) {
                Text(hit.title)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                    .lineLimit(2)

                if let context = hit.contextLine {
                    Text(context)
                        .font(.empSecondary)
                        .foregroundStyle(Theme.textSecondary(scheme))
                        .lineLimit(2)
                }

                if let displayId = hit.displayId, !displayId.isEmpty {
                    Text(displayId)
                        .font(.empMono)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
            }
        }
        .padding(.vertical, Space.x1)
    }

    private func documentRow(_ hit: SearchHit) -> some View {
        VStack(alignment: .leading, spacing: Space.x1) {
            Text(hit.title.isEmpty ? (hit.path ?? "Untitled") : hit.title)
                .font(.empBodyMedium)
                .foregroundStyle(Theme.textPrimary(scheme))
                .lineLimit(2)

            if let context = hit.contextLine {
                Text(context)
                    .font(.empSecondary)
                    .foregroundStyle(Theme.textSecondary(scheme))
                    .lineLimit(2)
            }

            if let path = hit.path, !path.isEmpty {
                Text(path)
                    .font(.empMono)
                    .foregroundStyle(Theme.textMuted(scheme))
                    .lineLimit(1)
            }
        }
        .padding(.vertical, Space.x1)
    }

    // MARK: - Loading

    /// Debounce, then search. `.task(id:)` has already cancelled whatever
    /// was in flight for the previous keystroke, so the sleep below is the
    /// only thing standing between a fast typist and one request per
    /// character — and a cancelled sleep throws, which is the early return.
    private func run(_ attempt: Attempt) async {
        let text = attempt.query
        guard !text.isEmpty else {
            state = .idle
            return
        }

        do {
            try await Task.sleep(for: .milliseconds(250))
        } catch {
            return  // superseded by a newer keystroke; that run owns the state
        }
        guard !Task.isCancelled else { return }

        guard let workspaceId = session.currentWorkspaceId else {
            // Not "no results" — nothing was searched.
            state = .failed(message: "No workspace is open yet.")
            return
        }

        state = .searching
        do {
            let response: SearchResponse = try await APIClient.shared.get(
                "/w/\(workspaceId)/search",
                query: ["q": text]
            )
            guard !Task.isCancelled else { return }

            // The route answers 200 with ok:false when the search itself
            // could not run, so the status code alone is not the answer.
            guard response.ok else {
                let reported = response.error?.trimmingCharacters(in: .whitespacesAndNewlines)
                state = .failed(message: (reported?.isEmpty == false ? reported! : "The search didn't run."))
                return
            }
            state = response.results.isEmpty
                ? .noResults(query: text)
                : .results(response.results)
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
            guard !Task.isCancelled else { return }
            // Deliberately "couldn't confirm", not "signed out": the refresh
            // above may well have succeeded, in which case Try again works.
            // If it failed, this view is about to disappear anyway. What is
            // NOT acceptable is leaving the spinner up forever.
            state = .failed(message: "Couldn't confirm your session. Try again.")
        } catch {
            guard !Task.isCancelled else { return }
            state = .failed(message: "Couldn't reach the workspace.")
        }
    }
}
