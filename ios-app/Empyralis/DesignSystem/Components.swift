import SwiftUI
import UIKit

/// A task's status, drawn the way the web app draws it: a filled disc in
/// the status colour, plus a check inside `done` so the two adjacent good
/// states (`in_review`, `done`) separate without relying on colour alone.
struct StatusDot: View {
    let status: String
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ZStack {
            Circle()
                .fill(Theme.taskStatus(status, scheme))
                .frame(width: 10, height: 10)
            if status == "done" {
                Image(systemName: "checkmark")
                    .font(.system(size: 6, weight: .bold))
                    .foregroundStyle(.white)
            }
        }
        .accessibilityLabel(status.replacingOccurrences(of: "_", with: " "))
    }
}

/// An agent's live state. Same three-way vocabulary as AgentRow, extracted
/// so the list and the detail header can never disagree about a colour.
struct AgentStatusDot: View {
    let agent: Agent
    @Environment(\.colorScheme) private var scheme

    private var color: Color {
        if agent.isStopped { return Theme.textMuted(scheme) }
        if agent.isWorking { return Theme.warning(scheme) }
        return Theme.online(scheme)
    }

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 8, height: 8)
    }
}

/// The ONE place the violet accent is allowed to appear — a view's single
/// primary action. Every other button in this app is neutral by weight.
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.empBodyMedium)
            // A DISABLED PRIMARY IS NEUTRAL, NOT A FADED ACCENT. A washed-out
            // violet reads as a broken accent — as if the colour failed to
            // load — rather than as "not available yet". Dropping to the
            // neutral surface says the real thing: this control exists, and
            // it isn't ready.
            .foregroundStyle(isEnabled ? Theme.accentContrast : Theme.textMuted(scheme))
            .frame(maxWidth: .infinity)
            .frame(height: 44)
            .background(
                isEnabled
                    ? Theme.accent(scheme).opacity(configuration.isPressed ? 0.85 : 1)
                    : Theme.bgInset(scheme),
                in: RoundedRectangle(cornerRadius: Radius.control)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Radius.control)
                    .stroke(isEnabled ? Color.clear : Theme.border(scheme), lineWidth: 1)
            )
            // 100ms, state-change only — the web app's motion rule ported.
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}

/// A neutral secondary action. Emphatic by weight and edge, never by hue.
struct SecondaryButtonStyle: ButtonStyle {
    @Environment(\.colorScheme) private var scheme

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.empBodyMedium)
            .foregroundStyle(Theme.textPrimary(scheme))
            .frame(maxWidth: .infinity)
            .frame(height: 44)
            .background(Theme.bgCard(scheme), in: RoundedRectangle(cornerRadius: Radius.control))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.control)
                    .stroke(Theme.borderStrong(scheme), lineWidth: 1)
            )
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

/// Empty states that TEACH are the one place prose is allowed — they have
/// nothing else to show. Everywhere else, the product labels rather than
/// lectures.
struct EmptyStateView: View {
    let title: String
    let message: String
    let systemImage: String
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: Space.x3) {
            Image(systemName: systemImage)
                .font(.system(size: 32, weight: .light))
                .foregroundStyle(Theme.textMuted(scheme))
            Text(title)
                .font(.empBodyMedium)
                .foregroundStyle(Theme.textPrimary(scheme))
            Text(message)
                .font(.empSecondary)
                .foregroundStyle(Theme.textMuted(scheme))
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(Space.x8)
    }
}

/// A skeleton row. Shown ONLY when nothing is known yet — never over
/// content already on screen. See WorkspaceStore's hasLoadedOnce.
struct SkeletonRow: View {
    @Environment(\.colorScheme) private var scheme
    @State private var shimmer = false

    var body: some View {
        VStack(alignment: .leading, spacing: Space.x2) {
            RoundedRectangle(cornerRadius: 4)
                .fill(Theme.bgInset(scheme))
                .frame(width: 60, height: 10)
            RoundedRectangle(cornerRadius: 4)
                .fill(Theme.bgInset(scheme))
                .frame(maxWidth: .infinity)
                .frame(height: 14)
        }
        .opacity(shimmer ? 0.45 : 0.85)
        .animation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true), value: shimmer)
        .onAppear { shimmer = true }
    }
}

extension View {
    /// Haptic feedback on a real state change. Native apps confirm actions
    /// physically; a web view cannot, and its absence is a large part of
    /// what makes a wrapped web app feel wrong.
    func hapticOnChange<V: Equatable>(of value: V, style: UIImpactFeedbackGenerator.FeedbackStyle = .light) -> some View {
        onChange(of: value) { _, _ in
            UIImpactFeedbackGenerator(style: style).impactOccurred()
        }
    }
}

// MARK: - Auth pills
//
// The entry screens use TALL, FULLY-ROUNDED, FULL-WIDTH buttons rather than
// the 6px-radius controls the rest of the app uses. That is deliberate and
// scoped: inside the product, tight radii read as an operator tool and are
// correct. The entry screens are a different job — one decision per row,
// nothing else on screen — and a 52pt pill is the shape that reads as
// "press this" on a phone with no chrome around it.
//
// Do NOT spread this shape into the product's own surfaces.

/// The single accent-filled option. Exactly one of these per screen — the
/// accent law is unchanged here.
struct AuthPrimaryPillStyle: ButtonStyle {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 16, weight: .medium))
            .foregroundStyle(isEnabled ? Theme.accentContrast : Theme.textMuted(scheme))
            .frame(maxWidth: .infinity)
            .frame(height: 52)
            .background(
                isEnabled
                    ? Theme.accent(scheme).opacity(configuration.isPressed ? 0.86 : 1)
                    : Theme.bgInset(scheme),
                in: Capsule()
            )
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}

/// Every other option. Neutral by weight — never a second accent.
struct AuthSecondaryPillStyle: ButtonStyle {
    @Environment(\.colorScheme) private var scheme

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 16, weight: .regular))
            .foregroundStyle(Theme.textPrimary(scheme))
            .frame(maxWidth: .infinity)
            .frame(height: 52)
            .background(
                scheme == .dark ? Color(hex: 0x1C1C1E) : Color(hex: 0xF2F2F4),
                in: Capsule()
            )
            .overlay(
                Capsule().stroke(Theme.border(scheme), lineWidth: 1)
            )
            .opacity(configuration.isPressed ? 0.75 : 1)
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}
