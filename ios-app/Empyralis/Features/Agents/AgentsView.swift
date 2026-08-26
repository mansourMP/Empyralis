import SwiftUI

/// The Operator ("Sage", agent_kind == "master") is filtered out by the
/// store — same rule as the web fleet grid's "real agent" count. It exists
/// in every workspace and is not something anyone created.
struct AgentsView: View {
    /// Owned by MainTabView, same reason InboxView takes one: an `.agent`
    /// deep link (a channel message's own link, or the Inbox's "Failed
    /// runs" rows) needs somewhere to push a destination FROM OUTSIDE this
    /// view — this tab used to have no such path at all, so a deep link to
    /// a specific agent could only ever land on the tab's generic list.
    @Binding var path: NavigationPath

    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        NavigationStack(path: $path) {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()

                if !store.hasLoadedOnce {
                    if let error = store.loadError {
                        // Nothing known AND the read failed. This is
                        // distinct from `agentsLookupFailed` below, which
                        // fires once we DO have a workspace but the agents
                        // lookup specifically came back empty/undecodable —
                        // here we have nothing at all yet.
                        ScrollView {
                            EmptyStateView(
                                title: "Couldn't load agents",
                                message: error,
                                systemImage: "wifi.exclamationmark"
                            )
                            .padding(.top, Space.x10)
                        }
                        .refreshable { await store.refresh() }
                    } else {
                        // The ONLY state that may show a skeleton: nothing
                        // known yet, and no failure to report either.
                        List {
                            ForEach(0..<4, id: \.self) { _ in
                                SkeletonRow().listRowBackground(Theme.bgPage(scheme))
                            }
                        }
                        .listStyle(.plain)
                    }
                } else if store.realAgents.isEmpty {
                    ScrollView {
                        // "None" and "couldn't find out" are different facts.
                        // This screen used to say the first while meaning the
                        // second, for months, because a decode failure became
                        // an empty array on the way here.
                        if store.agentsLookupFailed {
                            EmptyStateView(
                                title: "Couldn't load agents",
                                message: "The list didn't come back. Pull down to try again.",
                                systemImage: "exclamationmark.triangle"
                            )
                            .padding(.top, Space.x10)
                        } else {
                            EmptyStateView(
                                title: "No agents yet",
                                message: "Create one from the web app to see it here.",
                                systemImage: "cpu"
                            )
                            .padding(.top, Space.x10)
                        }
                    }
                    .refreshable { await store.refresh() }
                } else {
                    List {
                        ForEach(store.realAgents) { agent in
                            NavigationLink {
                                AgentDetailView(agent: agent)
                            } label: {
                                AgentRow(agent: agent)
                            }
                            .listRowBackground(Theme.bgPage(scheme))
                        }
                    }
                    .listStyle(.plain)
                    .refreshable { await store.refresh() }
                }
            }
            .navigationTitle("Agents")
            // Reuses InboxView.swift's own AgentDestinationView — the SAME
            // id-to-agent resolution the Inbox's "Failed runs" rows already
            // push through, rather than a second copy of that lookup here.
            .navigationDestination(for: AgentRoute.self) { route in
                AgentDestinationView(agentId: route.agentId)
            }
        }
    }
}

struct AgentRow: View {
    let agent: Agent
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HStack(spacing: Space.x3) {
            AgentStatusDot(agent: agent)

            VStack(alignment: .leading, spacing: 2) {
                Text(agent.displayName)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                Text(agent.statusLabel)
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
            }
        }
        .padding(.vertical, Space.x1)
    }
}
