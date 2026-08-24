import SwiftUI

/// Watching an agent work, live.
///
/// Streams `GET /agent-traces/{trace_id}/stream?workspace_id=...` (SSE) and
/// renders each event with the same vocabulary of steps the web app's
/// WorkTab.tsx uses, so a run reads identically in both products.
///
/// HONESTY, which is the hard part rather than the streaming:
///  - "connected, nothing has happened yet" and "couldn't connect" are
///    DIFFERENT facts and never share a message or a visual state.
///  - The connecting spinner is only ever shown over an EMPTY list. Once a
///    single step exists, a reconnect or an error is reported beside the
///    content, never on top of it.
///  - A field that is absent renders nothing. Nothing is invented to fill a
///    row out.
@MainActor
struct AgentTraceView: View {
    let traceId: String
    let workspaceId: String

    @Environment(\.colorScheme) private var scheme
    @StateObject private var model = AgentTraceModel()

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if model.steps.isEmpty {
                        emptyState
                            .padding(.top, Space.x10)
                    } else {
                        ForEach(model.steps) { step in
                            TraceStepRow(step: step)
                                .id(step.id)
                        }
                    }

                    footer
                        .id(Self.bottomAnchor)
                }
                .padding(Space.x4)
                .background(
                    // A zero-height probe that reports where the content
                    // bottom currently sits, so "is the reader at the bottom"
                    // is a measurement rather than a guess.
                    GeometryReader { geo in
                        Color.clear.preference(
                            key: ContentBottomKey.self,
                            value: geo.frame(in: .named(Self.scrollSpace)).maxY
                        )
                    }
                )
            }
            .coordinateSpace(name: Self.scrollSpace)
            .background(Theme.bgPage(scheme))
            .onPreferenceChange(ContentBottomKey.self) { bottom in
                Task { @MainActor in model.contentBottom = bottom }
            }
            .background(
                GeometryReader { geo in
                    Color.clear
                        .onAppear { model.viewportHeight = geo.size.height }
                        .onChange(of: geo.size.height) { _, new in model.viewportHeight = new }
                }
            )
            .onChange(of: model.steps.count) { _, _ in
                // Auto-follow ONLY while the reader is already at the bottom.
                // Never yank someone who scrolled up to read an earlier step.
                guard model.isPinnedToBottom else { return }
                withAnimation(.easeOut(duration: 0.15)) {
                    proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                }
            }
        }
        .task(id: traceId + workspaceId) {
            await model.stream(traceId: traceId, workspaceId: workspaceId)
        }
    }

    // MARK: - Empty state — three distinct facts, never collapsed

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: Space.x3) {
            switch model.phase {
            case .connecting:
                ProgressView()
                Text("Connecting…")
                    .font(.empSecondary)
                    .foregroundStyle(Theme.textMuted(scheme))
            case .live:
                Image(systemName: "clock")
                    .font(.system(size: 22))
                    .foregroundStyle(Theme.textMuted(scheme))
                Text("Connected. Nothing has happened yet.")
                    .font(.empBody)
                    .foregroundStyle(Theme.textSecondary(scheme))
                Text("Steps appear here as the agent takes them.")
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
            case .finished:
                Image(systemName: "checkmark.circle")
                    .font(.system(size: 22))
                    .foregroundStyle(Theme.online(scheme))
                Text("This run finished without recording any steps.")
                    .font(.empBody)
                    .foregroundStyle(Theme.textSecondary(scheme))
            case .failed(let message):
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 22))
                    .foregroundStyle(Theme.offline(scheme))
                Text("Couldn't connect to this run.")
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                Text(message)
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
                    .multilineTextAlignment(.center)
                Button("Try again") { model.retry() }
                    .font(.empBodyMedium)
                    .foregroundStyle(Theme.accentContrast)
                    .padding(.horizontal, Space.x4)
                    .padding(.vertical, Space.x2)
                    .background(Theme.accent(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
                    .padding(.top, Space.x2)
            }
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Footer — the run's own state, beside content and never over it

    @ViewBuilder
    private var footer: some View {
        if !model.steps.isEmpty {
            HStack(spacing: Space.x2) {
                switch model.phase {
                case .connecting:
                    Circle().fill(Theme.textMuted(scheme)).frame(width: 6, height: 6)
                    Text("Connecting…")
                case .live:
                    Circle().fill(Theme.warning(scheme)).frame(width: 6, height: 6)
                    Text("Working")
                case .finished:
                    Circle().fill(Theme.online(scheme)).frame(width: 6, height: 6)
                    Text(model.finishedNote ?? "Run finished")
                case .failed(let message):
                    Circle().fill(Theme.offline(scheme)).frame(width: 6, height: 6)
                    Text("Stopped streaming — \(message)")
                    Button("Try again") { model.retry() }
                        .font(.empCaptionMedium)
                        .foregroundStyle(Theme.textPrimary(scheme))
                }
                Spacer(minLength: 0)
            }
            .font(.empCaption)
            .foregroundStyle(Theme.textMuted(scheme))
            .padding(.top, Space.x4)
        }
    }

    private static let bottomAnchor = "emp-trace-bottom"
    private static let scrollSpace = "emp-trace-scroll"
}

// MARK: - Row

private struct TraceStepRow: View {
    let step: TraceStep
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HStack(alignment: .top, spacing: Space.x3) {
            Image(systemName: step.symbol)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(toneColor)
                .frame(width: 18, height: 18)
                .padding(.top, 1)

            VStack(alignment: .leading, spacing: Space.x1) {
                Text(step.title)
                    .font(.empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))

                if let detail = step.detail, !detail.isEmpty {
                    Text(detail)
                        .font(.empMono)
                        .foregroundStyle(Theme.textSecondary(scheme))
                        .lineLimit(3)
                        .padding(.horizontal, Space.x2)
                        .padding(.vertical, Space.x1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.bgInset(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
                }

                if let stamp = Self.timeLabel(step.ts) {
                    Text(stamp)
                        .font(.empCaption)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
            }
            Spacer(minLength: 0)
        }
        .padding(Space.x3)
        .background(Theme.bgCard(scheme), in: RoundedRectangle(cornerRadius: Radius.card))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.card)
                .stroke(Theme.border(scheme), lineWidth: 1)
        )
        .padding(.bottom, Space.x2)
    }

    // Status is the semantic vocabulary. The accent NEVER appears here.
    private var toneColor: Color {
        switch step.tone {
        case .normal: return Theme.textSecondary(scheme)
        case .muted: return Theme.textMuted(scheme)
        case .success: return Theme.online(scheme)
        case .warning: return Theme.warning(scheme)
        case .danger: return Theme.offline(scheme)
        }
    }

    private static let parser = ISO8601DateFormatter()
    private static let display: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    /// A timestamp we cannot parse renders NOTHING rather than a guess.
    static func timeLabel(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty else { return nil }
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = parser.date(from: raw) { return display.string(from: date) }
        parser.formatOptions = [.withInternetDateTime]
        if let date = parser.date(from: raw) { return display.string(from: date) }
        return nil
    }
}

private struct ContentBottomKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

// MARK: - Model

@MainActor
final class AgentTraceModel: ObservableObject {
    enum Phase: Equatable {
        case connecting
        case live
        case finished
        case failed(String)
    }

    @Published private(set) var steps: [TraceStep] = []
    @Published private(set) var phase: Phase = .connecting
    @Published private(set) var finishedNote: String?

    /// Set from the view's geometry probes; together they answer whether the
    /// reader is parked at the bottom.
    var contentBottom: CGFloat = 0
    var viewportHeight: CGFloat = 0

    var isPinnedToBottom: Bool {
        guard viewportHeight > 0, contentBottom > 0 else { return true }
        return contentBottom - viewportHeight < 48
    }

    private var indexByKey: [String: Int] = [:]
    private var seenSeq: Set<Int> = []
    private var task: Task<Void, Never>?
    private var traceId = ""
    private var workspaceId = ""

    func stream(traceId: String, workspaceId: String) async {
        self.traceId = traceId
        self.workspaceId = workspaceId
        task?.cancel()
        await run()
    }

    func retry() {
        task?.cancel()
        task = Task { [weak self] in await self?.run() }
    }

    private func run() async {
        guard !traceId.isEmpty, !workspaceId.isEmpty else {
            phase = .failed("This run has no id to stream.")
            return
        }
        phase = steps.isEmpty ? .connecting : .live

        let stream = SSEClient.records(
            path: "agent-traces/\(traceId)/stream",
            query: ["workspace_id": workspaceId]
        )

        do {
            var sawTerminal = false
            for try await record in stream {
                if Task.isCancelled { return }
                phase = .live
                guard let payload = record.data.data(using: .utf8),
                      let event = try? JSONDecoder().decode(TraceEvent.self, from: payload)
                else { continue }
                apply(event)
                if event.isTerminal {
                    sawTerminal = true
                    finishedNote = event.normalizedType == "trace.failed"
                        ? "Run ended with an error"
                        : "Run finished"
                    phase = .finished
                    break
                }
            }
            // The stream closed. Without a terminal event we genuinely do not
            // know the run's outcome, so we say the honest thing rather than
            // claiming it finished.
            if !sawTerminal, !Task.isCancelled, phase != .finished {
                phase = .failed("The connection closed before the run reported an outcome.")
            }
        } catch {
            if Task.isCancelled { return }
            let message = (error as? LocalizedError)?.errorDescription
                ?? error.localizedDescription
            phase = .failed(message)
        }
    }

    /// Idempotent by `seq` — the backend replays persisted events on connect,
    /// so a reconnect must not duplicate the steps already on screen.
    private func apply(_ event: TraceEvent) {
        if event.seq > 0 {
            guard !seenSeq.contains(event.seq) else { return }
            seenSeq.insert(event.seq)
        }

        if let target = TraceStep.resolutionTarget(event),
           let index = indexByKey[target] {
            steps[index] = steps[index].resolved(by: event)
            return
        }
        guard let step = TraceStep.from(event) else { return }
        if let existing = indexByKey[step.id] {
            steps[existing] = step
            return
        }
        if let key = step.resolutionKey {
            indexByKey[key] = steps.count
        }
        steps.append(step)
    }

    deinit { task?.cancel() }
}
