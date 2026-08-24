import SwiftUI

/// The single source of colour truth, ported verbatim from the web app's
/// lib/ui/theme-tokens.css so the two products read as ONE product. When a
/// token changes there, change it here — never invent an iOS-only colour.
///
/// THE ACCENT LAW CARRIES OVER FROM THE WEB APP AND IS NOT NEGOTIABLE HERE:
/// violet appears ONLY on the single primary-action button in a view.
/// Not on selected rows, not on tab bars, not on badges, not on toggles,
/// not on links. Selection is weight and shape, never hue. Semantic status
/// (online/working/blocked) is its own vocabulary and is never "the accent".
enum Theme {

    // MARK: - Surfaces

    static func bgPage(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x1A1A1A) : Color(hex: 0xFFFFFF)
    }

    static func bgCard(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x292929) : Color(hex: 0xFFFFFF)
    }

    static func bgInset(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x1D1D1D) : Color(hex: 0xEEEEEF)
    }

    static func bgRail(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x232323) : Color(hex: 0xF4F4F5)
    }

    // MARK: - Text

    static func textPrimary(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0xF4F4F5) : Color(hex: 0x18181B)
    }

    static func textSecondary(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0xCECECE) : Color(hex: 0x52525B)
    }

    static func textMuted(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x9A9A9A) : Color(hex: 0x6B6B76)
    }

    // MARK: - Borders

    static func border(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color.white.opacity(0.08) : Color.black.opacity(0.08)
    }

    static func borderStrong(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color.white.opacity(0.16) : Color.black.opacity(0.16)
    }

    // MARK: - Accent (primary action ONLY — see the law above)

    static func accent(_ scheme: ColorScheme) -> Color {
        // oklch(64% 0.17 305) dark / oklch(57% 0.155 305) light, converted.
        scheme == .dark ? Color(hex: 0xA56DDE) : Color(hex: 0x8D5BBF)
    }

    static let accentContrast = Color.white

    // MARK: - Semantic status (a separate vocabulary — never the accent)

    static func online(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x22C55E) : Color(hex: 0x15803D)
    }

    static func offline(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0xF87171) : Color(hex: 0xB91C1C)
    }

    static func warning(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0xFBBF24) : Color(hex: 0xB45309)
    }

    // MARK: - Task status (mirrors --task-* exactly)

    static func taskStatus(_ status: String, _ scheme: ColorScheme) -> Color {
        switch status {
        case "in_progress":
            return scheme == .dark ? Color(hex: 0xF2B23E) : Color(hex: 0xD97706)
        case "awaiting_input":
            return scheme == .dark ? Color(hex: 0x5E9CF5) : Color(hex: 0x2563EB)
        case "blocked":
            return Color(hex: 0xEF4444)
        case "in_review":
            return Color(hex: 0x4CB782)
        case "done":
            return scheme == .dark ? Color(hex: 0x2F9E68) : Color(hex: 0x0F6B34)
        default:
            return scheme == .dark ? Color(hex: 0x8A8F98) : Color(hex: 0x6B7280)
        }
    }
}

// MARK: - Type scale (mirrors --text-* — 14pt body, an operator-tool density)

extension Font {
    static let empBody = Font.system(size: 15, weight: .regular)
    static let empBodyMedium = Font.system(size: 15, weight: .medium)
    static let empSecondary = Font.system(size: 13, weight: .regular)
    static let empCaption = Font.system(size: 12, weight: .regular)
    static let empCaptionMedium = Font.system(size: 12, weight: .medium)
    static let empMono = Font.system(size: 12, weight: .medium, design: .monospaced)
    static let empTitle = Font.system(size: 24, weight: .semibold)
    static let empSectionHeader = Font.system(size: 12, weight: .semibold)
}

// MARK: - Spacing (4pt grid, mirrors --space-*)

enum Space {
    static let x1: CGFloat = 4
    static let x2: CGFloat = 8
    static let x3: CGFloat = 12
    static let x4: CGFloat = 16
    static let x5: CGFloat = 20
    static let x6: CGFloat = 24
    static let x8: CGFloat = 32
    static let x10: CGFloat = 40
}

// MARK: - Radii (tight — 6/8, never 12/14 generic-SaaS soft)

enum Radius {
    static let control: CGFloat = 6
    static let card: CGFloat = 8
    static let pill: CGFloat = 999
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}
