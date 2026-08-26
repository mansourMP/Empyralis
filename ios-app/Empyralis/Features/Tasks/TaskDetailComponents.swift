import SwiftUI

// MARK: - Priority glyph

/// The web's TaskPriorityIcon, ported: three bars whose lit count encodes
/// the level, and ONE priority — urgent — that gets colour. `No priority`
/// draws three muted dashes, a placeholder rather than a fourth colour, so
/// an untriaged task reads as untriaged instead of as a low one.
///
/// Colour here is semantic status, the same separate vocabulary StatusDot
/// and Theme.taskStatus already live in. It is never `Theme.accent`.
struct PriorityGlyph: View {
    let priority: TaskPriority
    @Environment(\.colorScheme) private var scheme

    private var urgentColor: Color {
        scheme == .dark ? Color(hex: 0xEF6B6B) : Color(hex: 0xC0392B)
    }

    var body: some View {
        if priority == .urgent {
            Image(systemName: "exclamationmark.square.fill")
                .font(.system(size: 14))
                .foregroundStyle(urgentColor)
                .frame(width: 16, height: 16)
        } else if priority == .none {
            HStack(spacing: 1.5) {
                ForEach(0..<3, id: \.self) { _ in
                    RoundedRectangle(cornerRadius: 0.75)
                        .fill(Theme.textMuted(scheme).opacity(0.45))
                        .frame(width: 3, height: 1.5)
                }
            }
            .frame(width: 16, height: 16)
        } else {
            HStack(alignment: .bottom, spacing: 1.5) {
                ForEach(0..<3, id: \.self) { i in
                    RoundedRectangle(cornerRadius: 0.75)
                        .fill(i < priority.litBars
                              ? Theme.textSecondary(scheme)
                              : Theme.textMuted(scheme).opacity(0.35))
                        .frame(width: 3, height: CGFloat(4 + i * 3))
                }
            }
            .frame(width: 16, height: 16, alignment: .bottom)
        }
    }
}

// MARK: - Labels

/// `color` on a label is a palette TOKEN NAME from the server
/// (workspace_labels_service.LABEL_COLOR_ORDER), never a hex — the theme owns
/// what each token looks like, exactly as it already does for task statuses.
/// These ten are fleet-theme.css's own values, mid-tones chosen to hold up on
/// both surfaces, so there is no per-theme fork. An unrecognised token falls
/// through to grey rather than rendering nothing.
enum LabelPalette {
    static func color(_ token: String) -> Color {
        switch token {
        case "red": return Color(hex: 0xE5534B)
        case "orange": return Color(hex: 0xE8853A)
        case "amber": return Color(hex: 0xCF9A22)
        case "green": return Color(hex: 0x3FA863)
        case "teal": return Color(hex: 0x2EA89A)
        case "blue": return Color(hex: 0x4A8FF0)
        case "indigo": return Color(hex: 0x6A6CE0)
        case "violet": return Color(hex: 0x9B6AE0)
        case "pink": return Color(hex: 0xE05A9B)
        default: return Color(hex: 0x8B8B94)
        }
    }
}

/// Display only, deliberately: there is no per-chip "x". Attaching and
/// detaching both happen in the Labels sheet, which is one surface with one
/// rule, rather than two ways to remove a label that have to agree. A chip
/// with a remove button would also be a tap target inside a row that already
/// has its own tap action.
struct LabelChip: View {
    let label: TaskLabel
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HStack(spacing: Space.x2) {
            Circle()
                .fill(LabelPalette.color(label.colorToken))
                .frame(width: 8, height: 8)
            Text(label.name)
                .font(.empCaption)
                .foregroundStyle(Theme.textSecondary(scheme))
        }
        .padding(.horizontal, Space.x2)
        .padding(.vertical, 5)
        .background(Theme.bgInset(scheme), in: Capsule())
        .overlay(Capsule().stroke(Theme.border(scheme), lineWidth: 1))
    }
}

// MARK: - Identity

/// Who an opaque id belongs to. The `unresolvable` case is the whole reason
/// this is an enum rather than an optional string: a person, an agent,
/// "nobody", and "I could not find out" are four facts, and the last two
/// collapsing into one is exactly the bug the web hit live on 2026-08-13,
/// where a task's own creator rendered as anonymous moments after creating it.
enum ResolvedActor: Equatable {
    case person(name: String, initial: String)
    case agent(name: String)
    case external(name: String)
    case unknown(shortId: String)
    case lookupFailed

    var name: String {
        switch self {
        case .person(let n, _): return n
        case .agent(let n): return n
        case .external(let n): return n
        case .unknown(let id): return id
        case .lookupFailed: return "Couldn't load who"
        }
    }

    var isAgent: Bool {
        if case .agent = self { return true }
        if case .external = self { return true }
        return false
    }
}

enum ActorResolver {
    /// Resolution order matters: an agent install id and a user id are drawn
    /// from different namespaces, so a match in either is authoritative, and
    /// only a genuine miss falls through.
    ///
    /// `displayNameSnapshot` is the name written alongside the id by
    /// add_task_comment for the one author kind that resolves against neither
    /// list — an EXTERNAL agent (`ext_agent_<hex>`), whose name lives in the
    /// MCP roster this app deliberately does not fetch (see the report note).
    /// The snapshot is the honest fallback, and a short id is the honest
    /// fallback to THAT.
    static func resolve(
        id: String?,
        agents: [Agent],
        members: [WorkspaceMember],
        identityLookupFailed: Bool,
        displayNameSnapshot: String? = nil
    ) -> ResolvedActor? {
        let trimmed = (id ?? "").trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return nil }

        if let agent = agents.first(where: { $0.id == trimmed }) {
            return .agent(name: agent.displayName)
        }
        if let member = members.first(where: { $0.userId == trimmed }) {
            return .person(name: member.name, initial: member.initial)
        }
        if let snapshot = displayNameSnapshot, !snapshot.isEmpty {
            return .external(name: snapshot)
        }
        // A failed lookup must never present as a resolved-but-anonymous
        // actor. Checked AFTER the direct matches so a cached hit still wins.
        if identityLookupFailed {
            return .lookupFailed
        }
        if trimmed.hasPrefix("ext_agent_") {
            return .external(name: "External agent \(String(trimmed.suffix(6)))")
        }
        return .unknown(shortId: String(trimmed.suffix(6)))
    }
}

/// A person gets a circle, an agent gets a rounded square. Shape, not hue,
/// is what separates them — the same rule selection follows everywhere else
/// in this app.
struct ActorAvatar: View {
    let actor: ResolvedActor
    var size: CGFloat = 22
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Group {
            if actor.isAgent {
                RoundedRectangle(cornerRadius: size * 0.28)
                    .fill(Theme.bgInset(scheme))
                    .overlay(
                        RoundedRectangle(cornerRadius: size * 0.28)
                            .stroke(Theme.border(scheme), lineWidth: 1)
                    )
                    .overlay(
                        Image(systemName: "cpu")
                            .font(.system(size: size * 0.46, weight: .medium))
                            .foregroundStyle(Theme.textSecondary(scheme))
                    )
            } else if case .person(_, let initial) = actor {
                Circle()
                    .fill(Theme.bgInset(scheme))
                    .overlay(Circle().stroke(Theme.border(scheme), lineWidth: 1))
                    .overlay(
                        Text(initial)
                            .font(.system(size: size * 0.44, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary(scheme))
                    )
            } else {
                Circle()
                    .fill(Theme.bgInset(scheme))
                    .overlay(Circle().stroke(Theme.border(scheme), lineWidth: 1))
                    .overlay(
                        Image(systemName: "questionmark")
                            .font(.system(size: size * 0.44, weight: .medium))
                            .foregroundStyle(Theme.textMuted(scheme))
                    )
            }
        }
        .frame(width: size, height: size)
    }
}

// MARK: - Layout primitives

/// The card every section on the detail screen sits in. One definition, so
/// the surface/border/radius cannot drift between eight call sites.
struct DetailCard<Content: View>: View {
    @Environment(\.colorScheme) private var scheme
    @ViewBuilder let content: Content

    var body: some View {
        VStack(spacing: 0) { content }
            .background(Theme.bgCard(scheme), in: RoundedRectangle(cornerRadius: Radius.card))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.card)
                    .stroke(Theme.border(scheme), lineWidth: 1)
            )
    }
}

struct SectionHeader: View {
    let title: String
    var trailing: String?
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HStack {
            Text(title.uppercased())
                .font(.empSectionHeader)
                .foregroundStyle(Theme.textMuted(scheme))
                .tracking(0.4)
            Spacer()
            if let trailing {
                Text(trailing)
                    .font(.empCaption)
                    .foregroundStyle(Theme.textMuted(scheme))
            }
        }
    }
}

/// A properties row: label on the left, current value on the right, and a
/// chevron ONLY when tapping it actually opens something. A row with no
/// action renders no chevron and takes no tap — the difference between
/// "displayed" and "editable" has to be visible, or the screen is lying
/// about what it can do.
///
/// THE LABEL COLUMN REFLOWS TO A VERTICAL STACK AT ACCESSIBILITY TEXT SIZES.
/// The fixed 88pt label column (sized for "Assignee"/"Completed by" at the
/// default text size) does not grow with Dynamic Type — `Text` inside a
/// hard-pinned `.frame(width:)` has no room to wrap at word boundaries once
/// the scaled glyphs alone exceed 88pt, so it wraps MID-WORD instead
/// ("Statu" / "s"), confirmed live on iPhone 14 Pro at
/// `accessibility-extra-extra-extra-large`. Widening the column would only
/// move the problem onto the value at every size below AX5, which is worse
/// — it starves the actual content to fix one label at one size.
///
/// `ViewThatFits` was considered and rejected: it cannot express "wrap the
/// label," only "pick between whole pre-built layouts," so it would still
/// need this same accessibility-vs-standard split to decide which two
/// layouts to offer — no simpler, and it evaluates every candidate's
/// geometry per update rather than branching on a value SwiftUI already
/// tracks for us. Reflowing to a vertical stack — label on top, value plus
/// chevron below, still one shared tap target — is the same shape Apple's
/// own Settings rows take at these sizes, and it costs nothing at the
/// default size: `dynamicTypeSize.isAccessibilitySize` is false there, so
/// the standard fixed-column layout below is completely unchanged.
struct PropertyRow<Value: View>: View {
    let title: String
    var isInteractive: Bool = true
    var action: (() -> Void)?
    @ViewBuilder let value: Value
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let content = Group {
            if dynamicTypeSize.isAccessibilitySize {
                accessibilityLayout
            } else {
                standardLayout
            }
        }
        .padding(.horizontal, Space.x3)
        .padding(.vertical, Space.x3)
        .frame(minHeight: 44)
        .contentShape(Rectangle())

        if let action, isInteractive {
            Button(action: action) { content }.buttonStyle(.plain)
        } else {
            content
        }
    }

    private var standardLayout: some View {
        HStack(spacing: Space.x3) {
            Text(title)
                .font(.empSecondary)
                .foregroundStyle(Theme.textMuted(scheme))
                .frame(width: 88, alignment: .leading)
            value
                .frame(maxWidth: .infinity, alignment: .leading)
            if isInteractive && action != nil {
                chevron
            }
        }
    }

    /// Label on its own line — full row width, so it wraps at word
    /// boundaries like any other text — with value and chevron sharing the
    /// line below. Kept as ONE tap target: this whole `VStack` is still what
    /// `body` wraps in the row's `Button` above, never a nested control.
    private var accessibilityLayout: some View {
        VStack(alignment: .leading, spacing: Space.x1) {
            Text(title)
                .font(.empSecondary)
                .foregroundStyle(Theme.textMuted(scheme))
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: Space.x3) {
                value
                    .frame(maxWidth: .infinity, alignment: .leading)
                if isInteractive && action != nil {
                    chevron
                }
            }
        }
    }

    private var chevron: some View {
        Image(systemName: "chevron.right")
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(Theme.textMuted(scheme).opacity(0.7))
    }
}

struct RowDivider: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        Divider().overlay(Theme.border(scheme)).padding(.leading, Space.x3)
    }
}

/// One row of any picker sheet. SELECTION IS WEIGHT AND SHAPE, NEVER HUE —
/// a checkmark plus a medium weight, with no accent anywhere near it.
struct PickerRow<Leading: View>: View {
    let title: String
    let isSelected: Bool
    let action: () -> Void
    @ViewBuilder let leading: Leading
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.x3) {
                leading
                Text(title)
                    .font(isSelected ? .empBodyMedium : .empBody)
                    .foregroundStyle(Theme.textPrimary(scheme))
                Spacer()
                if isSelected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.textPrimary(scheme))
                }
            }
            .padding(.horizontal, Space.x4)
            // minHeight, not fixed: `title` here is agent names, member
            // names and label names in the assignee/labels sheets — real
            // user data, not fixed vocabulary — set in Font.empBody(Medium),
            // which DOES scale. With no lineLimit on Text(title), a long name
            // wraps rather than truncates once it no longer fits one line at
            // large accessibility sizes; a fixed 48pt frame would not grow to
            // hold the second line, and this sheet stacks rows in a plain
            // VStack(spacing: 0) with no per-row clipping, so an overflowing
            // row would visually overlap the row below it.
            .frame(minHeight: 48)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// The chrome every picker sheet shares: a title, a Done affordance, and the
/// page surface. Sheets are presented with detents so a five-row picker does
/// not take the whole screen on an iPhone 13.
struct PickerSheet<Content: View>: View {
    let title: String
    @Binding var isPresented: Bool
    @ViewBuilder let content: Content
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.bgPage(scheme).ignoresSafeArea()
                ScrollView { VStack(spacing: 0) { content } .padding(.top, Space.x2) }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { isPresented = false }
                        .font(.empBodyMedium)
                        .foregroundStyle(Theme.textPrimary(scheme))
                }
            }
        }
    }
}

// MARK: - Dates

/// The backend hands back several shapes on these fields: `due_at` is
/// date-only by contract but arrives as a timestamp from some writers, and
/// activity/comment stamps are `datetime.isoformat()` WITH fractional
/// seconds — which `ISO8601DateFormatter` rejects unless explicitly told to
/// expect them. Parsing is therefore a cascade, and an unparseable value is
/// echoed verbatim rather than silently dropped or rendered as 1970.
enum TaskDates {
    private static let isoFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let dateOnly: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(secondsFromGMT: 0)
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    static func parse(_ value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        if let d = isoFractional.date(from: value) { return d }
        if let d = iso.date(from: value) { return d }

        // THE SPACE SEPARATOR. `created_at` / `updated_at` / `due_at` arrive
        // as "2026-08-25 07:38:07.933865+00:00" — not ISO 8601, and both
        // parsers above reject it. Without this line every one of them fell
        // through to the date-only branch below and resolved to MIDNIGHT
        // UTC, so a task created 35 minutes ago rendered as "8 hr ago" and
        // "Created by … 8 hr ago" was wrong on every task in the product.
        //
        // Reuses InboxNeedsYou's normalizer rather than growing a second
        // copy of the same rule — it already handles microsecond precision
        // and a missing timezone, which are the other two shapes this
        // backend emits.
        let normalized = InboxDateParsing.normalize(value)
        if normalized != value {
            if let d = isoFractional.date(from: normalized) { return d }
            if let d = iso.date(from: normalized) { return d }
        }

        // Genuinely date-only ("2026-08-20"), which `due_at` is by contract.
        if let d = dateOnly.date(from: String(value.prefix(10))) { return d }
        return nil
    }

    /// "Aug 25, 2026" — the due-date stamp, date precision only.
    static func due(_ value: String?) -> String? {
        guard let date = parse(value) else { return value?.isEmpty == false ? value : nil }
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .none
        return f.string(from: date)
    }

    /// Relative for anything inside a week, absolute beyond it — a feed of
    /// "3 weeks ago" tells a reader less than a date does.
    static func timeAgo(_ value: String?) -> String {
        guard let date = parse(value) else { return "" }
        let interval = Date().timeIntervalSince(date)
        if interval < 60 { return "just now" }
        if interval < 7 * 24 * 3600 {
            let f = RelativeDateTimeFormatter()
            f.unitsStyle = .abbreviated
            return f.localizedString(for: date, relativeTo: Date())
        }
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .none
        return f.string(from: date)
    }

    /// True when a due date is in the past and the task is not done — the one
    /// case the due row earns a warning tone for.
    static func isOverdue(_ value: String?) -> Bool {
        guard let date = parse(value) else { return false }
        return date < Calendar.current.startOfDay(for: Date())
    }

    static func isoDateOnly(_ date: Date) -> String {
        dateOnly.string(from: date)
    }
}
