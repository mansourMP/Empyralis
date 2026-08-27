import SwiftUI

/// Read-only, per the platform-wide law: observe what an agent did, never
/// converse with it here. Conversation happens in the agent's own channel.
///
/// The screen resolves the agent's most recent trace and, when that trace
/// is still running, hands off to AgentTraceView for a live SSE stream of
/// its actual work. A finished trace still renders its steps — the stream
/// endpoint replays persisted events before it starts polling, so the same
/// view serves both cases without a second code path.
struct AgentDetailView: View {
    let agent: Agent

    @EnvironmentObject private var session: SessionStore
    @Environment(\.colorScheme) private var scheme

    @State private var trace: TraceSummary?
    @State private var lookup: LookupState = .loading

    private enum LookupState: Equatable {
        case loading
        case ready
        case none          // resolved successfully, this agent has no traces
        case failed        // could not ask — a different fact from "none"
    }

    var body: some View {
        ZStack {
            Theme.bgPage(scheme).ignoresSafeArea()

            VStack(spacing: 0) {
                header
                Divider().overlay(Theme.border(scheme))
                traceSection
            }
        }
        .navigationTitle(agent.displayName)
        .navigationBarTitleDisplayMode(.inline)
        .task(id: agent.id) { await resolveLatestTrace() }
    }

    private var header: some View {
        HStack(spacing: Space.x3) {
            AgentStatusDot(agent: agent)
            VStack(alignment: .leading, spacing: 2) {
                Text(agent.statusLabel)
                    .font(.empBodyMedium)
                    .foregroundStyle(Theme.textPrimary(scheme))
                if let channel = agent.channel, !channel.isEmpty {
                    Text(channel)
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
            }
            Spacer()
        }
        .padding(Space.x4)
    }

    @ViewBuilder
    private var traceSection: some View {
        switch lookup {
        case .loading:
            // SkeletonRow, like every other first-load state in this app
            // (Inbox, My work, Projects, Agents, Documents, Settings). This
            // was a bare ProgressView — the one loading state that did not
            // match the house vocabulary. Not an honesty problem: this
            // genuinely IS a fresh, uncacheable fetch with nothing known yet,
            // which is exactly the one case a skeleton is allowed to appear.
            VStack(spacing: Space.x3) {
                ForEach(0..<4, id: \.self) { _ in SkeletonRow() }
            }
            .padding(.horizontal, Space.x4)
            .padding(.top, Space.x4)
            Spacer()

        case .ready:
            if let trace, let workspaceId = session.currentWorkspaceId {
                AgentTraceView(traceId: trace.id, workspaceId: workspaceId)
            } else {
                // Defensive: .ready without a trace shouldn't happen, and if
                // it does, say so rather than render an empty pane.
                EmptyStateView(
                    title: "Nothing to show",
                    message: "This agent's work couldn't be resolved.",
                    systemImage: "questionmark.circle"
                )
                Spacer()
            }

        case .none:
            EmptyStateView(
                title: "Hasn't run yet",
                message: "When this agent works, its steps show up here.",
                systemImage: "clock"
            )
            Spacer()

        case .failed:
            // Deliberately distinct from .none — "it has never run" and
            // "we couldn't find out" are different facts and must never
            // share one screen.
            VStack(spacing: Space.x4) {
                EmptyStateView(
                    title: "Couldn't load its work",
                    message: "Check your connection and try again.",
                    systemImage: "wifi.exclamationmark"
                )
                // PrimaryButtonStyle, matching AgentTraceView's own
                // whole-screen failure. This is the same situation one level
                // up — nothing loaded, and retry is the only action — so it
                // gets the same weight. It was SecondaryButtonStyle, which
                // left this screen with no primary action at all.
                Button("Try again") {
                    Task { await resolveLatestTrace() }
                }
                .buttonStyle(PrimaryButtonStyle())
                .padding(.horizontal, Space.x8)
            }
            Spacer()
        }
    }

    /// Newest trace wins — the list route returns them most-recent-first,
    /// which is what "what is this agent doing" means.
    private func resolveLatestTrace() async {
        guard let workspaceId = session.currentWorkspaceId else {
            lookup = .failed
            return
        }
        lookup = .loading
        do {
            let response: TraceListResponse = try await APIClient.shared.get(
                "/agent-traces",
                query: [
                    "workspace_id": workspaceId,
                    // THE PREFIX IS LOAD-BEARING — DO NOT "SIMPLIFY" IT AWAY.
                    // agent_traces.root_agent_id is never a bare install id.
                    // Two independent writers stamp it, and both produce the
                    // prefixed form: agent_turn._trace_root_agent_id and
                    // run_service (both `f"specialist:{install_id}"`).
                    // Querying with the bare id compiles, runs, returns 200
                    // and matches ZERO rows — so every agent would read
                    // "hasn't run yet" forever with nothing reporting why.
                    "root_agent_id": "specialist:\(agent.id)",
                    "limit": "1",
                ]
            )
            if let newest = response.items.first {
                trace = newest
                lookup = .ready
            } else {
                lookup = .none
            }
        } catch APIError.unauthorized {
            await session.handleUnauthorized()
            lookup = .failed
        } catch {
            lookup = .failed
        }
    }
}
