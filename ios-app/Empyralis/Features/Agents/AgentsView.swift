import SwiftUI

/// The Operator ("Sage", agent_kind == "master") is filtered out by the
/// store — same rule as the web fleet grid's "real agent" count. It exists
/// in every workspace and is not something anyone created.
struct AgentsView: View {
    @EnvironmentObject private var store: WorkspaceStore
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()

                if !store.hasLoadedOnce {
                    List {
                        ForEach(0..<4, id: \.self) { _ in
                            SkeletonRow().listRowBackground(Theme.bgPage(scheme))
                        }
                    }
                    .listStyle(.plain)
                } else if store.realAgents.isEmpty {
                    ScrollView {
                        EmptyStateView(
                            title: "No agents yet",
                            message: "Create one from the web app to see it here.",
                            systemImage: "cpu"
                        )
                        .padding(.top, Space.x10)
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
