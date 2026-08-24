import SwiftUI

/// Renders the same markdown subset the web app's `markdown-lite.tsx`
/// supports, minus tables and images (a phone-width table is a worse lie
/// than an honest paragraph; images come with the next pass).
///
/// BLOCK STRUCTURE IS HAND-PARSED, INLINE FORMATTING IS NOT. SwiftUI's
/// `AttributedString(markdown:)` handles bold/italic/inline-code/links
/// correctly and is already on the platform — but it flattens block
/// structure (a heading loses its size, a list loses its bullet), so it is
/// used ONLY per-line, after this file has decided what kind of line it is.
/// Zero third-party dependencies, and the project stays that way.
///
/// LINKS ARE NEVER THE ACCENT. `Theme.accent` is reserved for the single
/// primary-action button in a view; a link is `textPrimary` plus an
/// underline, which is weight and shape rather than hue — the same rule the
/// web app enforces mechanically.
struct MarkdownRenderer: View {
    let markdown: String
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(alignment: .leading, spacing: Space.x3) {
            ForEach(Array(MarkdownBlock.parse(markdown).enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func blockView(_ block: MarkdownBlock) -> some View {
        switch block {
        case let .heading(level, text):
            inline(text)
                .font(headingFont(level))
                .foregroundStyle(Theme.textPrimary(scheme))
                .padding(.top, level <= 2 ? Space.x2 : 0)

        case let .paragraph(text):
            inline(text)
                .font(.empBody)
                .foregroundStyle(Theme.textPrimary(scheme))

        case let .quote(lines):
            HStack(alignment: .top, spacing: Space.x3) {
                Rectangle()
                    .fill(Theme.borderStrong(scheme))
                    .frame(width: 2)
                VStack(alignment: .leading, spacing: Space.x2) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                        inline(line)
                            .font(.empBody)
                            .foregroundStyle(Theme.textSecondary(scheme))
                    }
                }
            }
            .fixedSize(horizontal: false, vertical: true)

        case let .code(lines, _):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(lines.joined(separator: "\n"))
                    .font(.empMono)
                    .foregroundStyle(Theme.textPrimary(scheme))
                    .textSelection(.enabled)
                    .padding(Space.x3)
            }
            .background(Theme.bgInset(scheme))
            .clipShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                    .stroke(Theme.border(scheme), lineWidth: 1)
            )

        case let .list(items, ordered):
            VStack(alignment: .leading, spacing: Space.x2) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .firstTextBaseline, spacing: Space.x2) {
                        Text(ordered ? "\(index + 1)." : "•")
                            .font(.empBody)
                            .foregroundStyle(Theme.textMuted(scheme))
                            .frame(minWidth: ordered ? 20 : 12, alignment: .trailing)
                        inline(item.text)
                            .font(.empBody)
                            .foregroundStyle(Theme.textPrimary(scheme))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.leading, CGFloat(item.depth) * Space.x4)
                }
            }

        case .rule:
            Rectangle()
                .fill(Theme.border(scheme))
                .frame(height: 1)
                .padding(.vertical, Space.x1)
        }
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .system(size: 22, weight: .semibold)
        case 2: return .system(size: 18, weight: .semibold)
        case 3: return .system(size: 16, weight: .semibold)
        default: return .system(size: 15, weight: .semibold)
        }
    }

    /// Inline formatting via the platform parser. `.inlineOnlyPreservingWhitespace`
    /// is what keeps it from swallowing block syntax we have already decided
    /// about; a parse failure falls back to the raw text rather than dropping
    /// the line, because showing the source beats showing nothing.
    private func inline(_ text: String) -> Text {
        if let attributed = try? AttributedString(
            markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            return Text(styleLinks(attributed))
        }
        return Text(text)
    }

    /// Repaints link runs to `textPrimary` + underline. Left alone, SwiftUI
    /// paints them the system tint, which on this surface reads as an accent
    /// the accent law does not permit.
    private func styleLinks(_ input: AttributedString) -> AttributedString {
        var out = input
        for run in out.runs where run.link != nil {
            out[run.range].foregroundColor = Theme.textPrimary(scheme)
            out[run.range].underlineStyle = .single
        }
        return out
    }
}

// MARK: - Block parsing

/// The block vocabulary, deliberately the same set `markdown-lite.tsx`
/// recognises so the two products render the same document the same way.
enum MarkdownBlock: Equatable {
    case heading(level: Int, text: String)
    case paragraph(String)
    case quote([String])
    case code(lines: [String], language: String)
    case list(items: [MarkdownListItem], ordered: Bool)
    case rule

    static func parse(_ markdown: String) -> [MarkdownBlock] {
        let lines = markdown
            .replacingOccurrences(of: "\r\n", with: "\n")
            .components(separatedBy: "\n")

        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var index = 0

        func flushParagraph() {
            guard !paragraph.isEmpty else { return }
            blocks.append(.paragraph(paragraph.joined(separator: " ")))
            paragraph.removeAll()
        }

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.isEmpty {
                flushParagraph()
                index += 1
                continue
            }

            // Fenced code. An UNTERMINATED fence consumes to end of input
            // rather than being dropped — the same call markdown-lite makes.
            if trimmed.hasPrefix("```") {
                flushParagraph()
                let language = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                index += 1
                var code: [String] = []
                while index < lines.count,
                      !lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    code.append(lines[index])
                    index += 1
                }
                if index < lines.count { index += 1 }
                blocks.append(.code(lines: code, language: language))
                continue
            }

            if isRule(trimmed) {
                flushParagraph()
                blocks.append(.rule)
                index += 1
                continue
            }

            if let heading = matchHeading(trimmed) {
                flushParagraph()
                blocks.append(.heading(level: heading.0, text: heading.1))
                index += 1
                continue
            }

            if let first = matchQuote(trimmed) {
                flushParagraph()
                var quoted = [first]
                index += 1
                while index < lines.count,
                      let more = matchQuote(lines[index].trimmingCharacters(in: .whitespaces)) {
                    quoted.append(more)
                    index += 1
                }
                blocks.append(.quote(quoted))
                continue
            }

            if let marker = matchListMarker(line) {
                flushParagraph()
                let ordered = marker.ordered
                var items: [MarkdownListItem] = []
                while index < lines.count {
                    guard let m = matchListMarker(lines[index]), m.ordered == ordered else { break }
                    items.append(MarkdownListItem(text: m.content, depth: m.indent / 2))
                    index += 1
                }
                blocks.append(.list(items: items, ordered: ordered))
                continue
            }

            paragraph.append(trimmed)
            index += 1
        }

        flushParagraph()
        return blocks
    }

    private static func isRule(_ line: String) -> Bool {
        guard line.count >= 3 else { return false }
        let set = Set(line)
        return set.count == 1 && (set.first == "-" || set.first == "*" || set.first == "_")
    }

    private static func matchHeading(_ line: String) -> (Int, String)? {
        var level = 0
        var rest = Substring(line)
        while let first = rest.first, first == "#", level < 6 {
            level += 1
            rest = rest.dropFirst()
        }
        guard level > 0, rest.first == " " else { return nil }
        return (level, String(rest).trimmingCharacters(in: .whitespaces))
    }

    private static func matchQuote(_ line: String) -> String? {
        guard line.hasPrefix(">") else { return nil }
        var rest = Substring(line).dropFirst()
        if rest.first == " " { rest = rest.dropFirst() }
        return String(rest)
    }

    private static func matchListMarker(_ line: String) -> (indent: Int, ordered: Bool, content: String)? {
        let indent = line.prefix { $0 == " " }.count
        let rest = line.dropFirst(indent)
        guard let first = rest.first else { return nil }

        if first == "-" || first == "*" || first == "+" {
            let after = rest.dropFirst()
            guard after.first == " " else { return nil }
            return (indent, false, String(after.dropFirst()).trimmingCharacters(in: .whitespaces))
        }

        let digits = rest.prefix { $0.isNumber }
        guard !digits.isEmpty else { return nil }
        let afterDigits = rest.dropFirst(digits.count)
        guard afterDigits.first == ".", afterDigits.dropFirst().first == " " else { return nil }
        return (indent, true, String(afterDigits.dropFirst(2)).trimmingCharacters(in: .whitespaces))
    }
}

struct MarkdownListItem: Equatable {
    let text: String
    let depth: Int
}
