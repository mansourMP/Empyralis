/*
Canonical design-token source for Empyralis.

The current values reflect the shared brand language after the 2026-04-22
mobile/web visual unification pass so web, Tauri, and mobile inherit the same
palette, radii, and semantic status colors without changing layout structure.
*/

const px = (value: number): string => `${value}px`;
const ms = (value: number): string => `${value}ms`;

export const DESIGN_SYSTEM_THEME_ATTRIBUTE = 'data-emp-theme';
export const DESIGN_SYSTEM_DEFAULT_THEME = 'light' as const;

export const DESIGN_SYSTEM_FONTS = {
  sans: 'var(--font-dm-sans), "DM Sans", "Inter", "Geist", "SF Pro Text", "SF Pro Display", "Segoe UI", sans-serif',
  heading: 'var(--font-fraunces), "Fraunces", "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif',
  mono: '"SF Mono", "JetBrains Mono", "IBM Plex Mono", "Menlo", monospace',
} as const;

export const DESIGN_SYSTEM_FONT_WEIGHTS = {
  regular: 400,
  medium: 500,
  semibold: 600,
} as const;

export const DESIGN_SYSTEM_LAYOUT = {
  sidebarWidth: 64,
  inspectorWidth: 360,
  titlebarHeight: 40,
} as const;

export const DESIGN_SYSTEM_SPACING_SCALE = {
  px4: 4,
  px6: 6,
  px8: 8,
  px10: 10,
  px12: 12,
  px14: 14,
  px16: 16,
  px18: 18,
  px20: 20,
  px24: 24,
  px28: 28,
  px32: 32,
  px36: 36,
  px40: 40,
  px48: 48,
} as const;

export const DESIGN_SYSTEM_SPACING_ALIASES = {
  web: {
    1: DESIGN_SYSTEM_SPACING_SCALE.px4,
    2: DESIGN_SYSTEM_SPACING_SCALE.px8,
    3: DESIGN_SYSTEM_SPACING_SCALE.px12,
    4: DESIGN_SYSTEM_SPACING_SCALE.px16,
    5: DESIGN_SYSTEM_SPACING_SCALE.px20,
    6: DESIGN_SYSTEM_SPACING_SCALE.px24,
    8: DESIGN_SYSTEM_SPACING_SCALE.px32,
    10: DESIGN_SYSTEM_SPACING_SCALE.px40,
    12: DESIGN_SYSTEM_SPACING_SCALE.px48,
  },
  mobile: {
    xs: DESIGN_SYSTEM_SPACING_SCALE.px6,
    sm: DESIGN_SYSTEM_SPACING_SCALE.px12,
    md: DESIGN_SYSTEM_SPACING_SCALE.px16,
    lg: DESIGN_SYSTEM_SPACING_SCALE.px20,
    xl: DESIGN_SYSTEM_SPACING_SCALE.px28,
    xxl: DESIGN_SYSTEM_SPACING_SCALE.px36,
  },
} as const;

export const DESIGN_SYSTEM_TYPOGRAPHY_SCALE = {
  px11: 11,
  px12: 12,
  px13: 13,
  px14: 14,
  px16: 16,
  px18: 18,
  px20: 20,
  px28: 28,
  px30: 30,
} as const;

export const DESIGN_SYSTEM_TYPOGRAPHY_ALIASES = {
  web: {
    11: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px11,
    12: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px12,
    13: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px13,
    14: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px14,
    16: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px16,
    18: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px18,
    20: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px20,
  },
  mobile: {
    hero: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px30,
    heading: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px20,
    title: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px30,
    section: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px18,
    body: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px14,
    meta: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px12,
    caption: DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px11,
  },
} as const;

export const DESIGN_SYSTEM_LINE_HEIGHTS = {
  compact: 1.5,
  body: 1.55,
  relaxed: 1.6,
} as const;

export const DESIGN_SYSTEM_RADIUS_SCALE = {
  px8: 8,
  px10: 10,
  px12: 12,
  px16: 16,
  px20: 20,
  px22: 22,
  px26: 26,
  px32: 32,
  pill: 999,
} as const;

export const DESIGN_SYSTEM_RADIUS_ALIASES = {
  web: {
    sm: DESIGN_SYSTEM_RADIUS_SCALE.px10,
    md: DESIGN_SYSTEM_RADIUS_SCALE.px16,
    lg: DESIGN_SYSTEM_RADIUS_SCALE.px20,
    xl: DESIGN_SYSTEM_RADIUS_SCALE.px26,
    pill: DESIGN_SYSTEM_RADIUS_SCALE.pill,
  },
  mobile: {
    sm: DESIGN_SYSTEM_RADIUS_SCALE.px12,
    md: DESIGN_SYSTEM_RADIUS_SCALE.px16,
    lg: DESIGN_SYSTEM_RADIUS_SCALE.px20,
    xl: DESIGN_SYSTEM_RADIUS_SCALE.px26,
    xxl: DESIGN_SYSTEM_RADIUS_SCALE.px32,
    pill: DESIGN_SYSTEM_RADIUS_SCALE.pill,
  },
} as const;

export const DESIGN_SYSTEM_MOTION = {
  fast: 120,
  normal: 160,
  slow: 220,
} as const;

export const DESIGN_SYSTEM_ELEVATION = {
  web: {
    subtle: '0 4px 12px -10px rgba(0, 0, 0, 0.06)',
    panel: '0 16px 40px -28px rgba(32, 32, 32, 0.12), 0 0 0 1px rgba(32, 32, 32, 0.03)',
    focus: '0 0 0 2px rgba(32, 32, 32, 0.14)',
    darkSubtle: 'inset 0 1px 0 rgba(255, 255, 255, 0.03), 0 8px 20px -20px rgba(0, 0, 0, 0.66)',
    darkPanel: 'inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 26px 60px -38px rgba(0, 0, 0, 0.84)',
    darkFocus: '0 0 0 2px rgba(255, 255, 255, 0.14)',
  },
  mobile: {
    panel: {
      shadowColor: '#000000',
      shadowOpacity: 0.18,
      shadowRadius: 18,
      shadowOffset: {
        width: 0,
        height: 10,
      },
      elevation: 8,
    },
  },
} as const;

export const DESIGN_SYSTEM_BRAND_COLORS = {
  light: {
    app: '#faf8f4',
    sidebar: '#f2efe9',
    surface: '#ffffff',
    panel: '#ffffff',
    panelMuted: '#f5f2ec',
    border: 'rgba(26, 22, 18, 0.08)',
    borderStrong: '#dbd6ce',
    borderMuted: 'rgba(26, 22, 18, 0.08)',
    text: '#1a1612',
    textMuted: '#78726a',
    textSoft: 'rgba(96, 115, 124, 0.72)',
    primary: '#202020',
    primaryMuted: 'rgba(32, 32, 32, 0.12)',
    highlight: '#2F2F2F',
    warning: '#B77A1F',
    warningMuted: 'rgba(183, 122, 31, 0.12)',
    success: '#218E68',
    successMuted: 'rgba(33, 142, 104, 0.12)',
    error: '#C25656',
    errorMuted: 'rgba(194, 86, 86, 0.12)',
  },
  dark: {
    app: '#111111',
    sidebar: '#161616',
    surface: '#1c1c1c',
    panel: '#1c1c1c',
    panelMuted: '#222222',
    border: 'rgba(240, 240, 240, 0.08)',
    borderStrong: '#303030',
    borderMuted: 'rgba(240, 240, 240, 0.08)',
    text: '#f0f0f0',
    textMuted: '#a0a0a0',
    textSoft: 'rgba(164, 164, 164, 0.72)',
    primary: '#2F2F2F',
    primaryMuted: 'rgba(58, 58, 58, 0.3)',
    highlight: '#3A3A3A',
    warning: '#E4AF58',
    warningMuted: 'rgba(228, 175, 88, 0.18)',
    success: '#4EC598',
    successMuted: 'rgba(78, 197, 152, 0.18)',
    error: '#E57474',
    errorMuted: 'rgba(229, 116, 116, 0.18)',
  },
} as const;

export const DESIGN_SYSTEM_COLOR_MODES = {
  light: {
    background: {
      page: DESIGN_SYSTEM_BRAND_COLORS.light.app,
      surface: DESIGN_SYSTEM_BRAND_COLORS.light.surface,
      surfaceHover: DESIGN_SYSTEM_BRAND_COLORS.light.panelMuted,
      inset: DESIGN_SYSTEM_BRAND_COLORS.light.sidebar,
    },
    border: {
      subtle: DESIGN_SYSTEM_BRAND_COLORS.light.border,
      strong: DESIGN_SYSTEM_BRAND_COLORS.light.borderStrong,
      muted: DESIGN_SYSTEM_BRAND_COLORS.light.borderMuted,
      accent: DESIGN_SYSTEM_BRAND_COLORS.light.primary,
    },
    text: {
      primary: DESIGN_SYSTEM_BRAND_COLORS.light.text,
      secondary: DESIGN_SYSTEM_BRAND_COLORS.light.textMuted,
      tertiary: DESIGN_SYSTEM_BRAND_COLORS.light.textSoft,
      inverse: DESIGN_SYSTEM_BRAND_COLORS.light.panel,
    },
    accent: {
      base: DESIGN_SYSTEM_BRAND_COLORS.light.primary,
      strong: DESIGN_SYSTEM_BRAND_COLORS.light.highlight,
      muted: DESIGN_SYSTEM_BRAND_COLORS.light.primaryMuted,
      text: DESIGN_SYSTEM_BRAND_COLORS.light.panel,
    },
    status: {
      success: DESIGN_SYSTEM_BRAND_COLORS.light.success,
      successMuted: DESIGN_SYSTEM_BRAND_COLORS.light.successMuted,
      warning: DESIGN_SYSTEM_BRAND_COLORS.light.warning,
      warningMuted: DESIGN_SYSTEM_BRAND_COLORS.light.warningMuted,
      danger: DESIGN_SYSTEM_BRAND_COLORS.light.error,
      dangerMuted: DESIGN_SYSTEM_BRAND_COLORS.light.errorMuted,
    },
  },
  dark: {
    background: {
      page: DESIGN_SYSTEM_BRAND_COLORS.dark.app,
      surface: DESIGN_SYSTEM_BRAND_COLORS.dark.surface,
      surfaceHover: DESIGN_SYSTEM_BRAND_COLORS.dark.panelMuted,
      inset: DESIGN_SYSTEM_BRAND_COLORS.dark.sidebar,
    },
    border: {
      subtle: DESIGN_SYSTEM_BRAND_COLORS.dark.border,
      strong: DESIGN_SYSTEM_BRAND_COLORS.dark.borderStrong,
      muted: DESIGN_SYSTEM_BRAND_COLORS.dark.borderMuted,
      accent: DESIGN_SYSTEM_BRAND_COLORS.dark.primary,
    },
    text: {
      primary: DESIGN_SYSTEM_BRAND_COLORS.dark.text,
      secondary: DESIGN_SYSTEM_BRAND_COLORS.dark.textMuted,
      tertiary: DESIGN_SYSTEM_BRAND_COLORS.dark.textSoft,
      inverse: DESIGN_SYSTEM_BRAND_COLORS.dark.app,
    },
    accent: {
      base: DESIGN_SYSTEM_BRAND_COLORS.dark.primary,
      strong: DESIGN_SYSTEM_BRAND_COLORS.dark.highlight,
      muted: DESIGN_SYSTEM_BRAND_COLORS.dark.primaryMuted,
      text: DESIGN_SYSTEM_BRAND_COLORS.dark.text,
    },
    status: {
      success: DESIGN_SYSTEM_BRAND_COLORS.dark.success,
      successMuted: DESIGN_SYSTEM_BRAND_COLORS.dark.successMuted,
      warning: DESIGN_SYSTEM_BRAND_COLORS.dark.warning,
      warningMuted: DESIGN_SYSTEM_BRAND_COLORS.dark.warningMuted,
      danger: DESIGN_SYSTEM_BRAND_COLORS.dark.error,
      dangerMuted: DESIGN_SYSTEM_BRAND_COLORS.dark.errorMuted,
    },
  },
  mobileDark: {
    background: {
      page: DESIGN_SYSTEM_BRAND_COLORS.dark.app,
      canvasMuted: DESIGN_SYSTEM_BRAND_COLORS.dark.sidebar,
      surface: DESIGN_SYSTEM_BRAND_COLORS.dark.surface,
      surfaceElevated: DESIGN_SYSTEM_BRAND_COLORS.dark.panel,
      surfaceHover: DESIGN_SYSTEM_BRAND_COLORS.dark.panelMuted,
    },
    border: {
      subtle: DESIGN_SYSTEM_BRAND_COLORS.dark.border,
      strong: DESIGN_SYSTEM_BRAND_COLORS.dark.borderStrong,
    },
    text: {
      primary: DESIGN_SYSTEM_BRAND_COLORS.dark.text,
      secondary: DESIGN_SYSTEM_BRAND_COLORS.dark.textMuted,
      tertiary: DESIGN_SYSTEM_BRAND_COLORS.dark.textSoft,
      inverse: DESIGN_SYSTEM_BRAND_COLORS.dark.app,
    },
    accent: {
      base: DESIGN_SYSTEM_BRAND_COLORS.dark.primary,
      strong: DESIGN_SYSTEM_BRAND_COLORS.dark.highlight,
      muted: DESIGN_SYSTEM_BRAND_COLORS.dark.primaryMuted,
      text: DESIGN_SYSTEM_BRAND_COLORS.dark.text,
    },
    status: {
      success: DESIGN_SYSTEM_BRAND_COLORS.dark.success,
      warning: DESIGN_SYSTEM_BRAND_COLORS.dark.warning,
      danger: DESIGN_SYSTEM_BRAND_COLORS.dark.error,
    },
  },
} as const;

export const DESIGN_SYSTEM_MOBILE_TOKENS = {
  colors: {
    canvas: DESIGN_SYSTEM_BRAND_COLORS.light.app,
    canvasMuted: DESIGN_SYSTEM_BRAND_COLORS.light.sidebar,
    panel: DESIGN_SYSTEM_BRAND_COLORS.light.surface,
    panelElevated: DESIGN_SYSTEM_BRAND_COLORS.light.panel,
    panelInteractive: DESIGN_SYSTEM_BRAND_COLORS.light.panelMuted,
    border: DESIGN_SYSTEM_BRAND_COLORS.light.border,
    borderStrong: DESIGN_SYSTEM_BRAND_COLORS.light.borderStrong,
    textPrimary: DESIGN_SYSTEM_BRAND_COLORS.light.text,
    textSecondary: DESIGN_SYSTEM_BRAND_COLORS.light.textMuted,
    textMuted: DESIGN_SYSTEM_BRAND_COLORS.light.textSoft,
    accent: DESIGN_SYSTEM_BRAND_COLORS.light.primary,
    accentStrong: DESIGN_SYSTEM_BRAND_COLORS.light.highlight,
    success: DESIGN_SYSTEM_BRAND_COLORS.light.success,
    warning: DESIGN_SYSTEM_BRAND_COLORS.light.warning,
    danger: DESIGN_SYSTEM_BRAND_COLORS.light.error,
  },
  spacing: DESIGN_SYSTEM_SPACING_ALIASES.mobile,
  radius: DESIGN_SYSTEM_RADIUS_ALIASES.mobile,
  typography: DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.mobile,
  shadow: DESIGN_SYSTEM_ELEVATION.mobile,
  motion: DESIGN_SYSTEM_MOTION,
} as const;

export const DESIGN_SYSTEM_WEB_CSS_VARIABLES = {
  shared: {
    '--app-font-sans': DESIGN_SYSTEM_FONTS.sans,
    '--app-font-heading': DESIGN_SYSTEM_FONTS.heading,
    '--app-font-mono': DESIGN_SYSTEM_FONTS.mono,
    '--app-space-1': px(DESIGN_SYSTEM_SPACING_ALIASES.web[1]),
    '--app-space-2': px(DESIGN_SYSTEM_SPACING_ALIASES.web[2]),
    '--app-space-3': px(DESIGN_SYSTEM_SPACING_ALIASES.web[3]),
    '--app-space-4': px(DESIGN_SYSTEM_SPACING_ALIASES.web[4]),
    '--app-space-5': px(DESIGN_SYSTEM_SPACING_ALIASES.web[5]),
    '--app-space-6': px(DESIGN_SYSTEM_SPACING_ALIASES.web[6]),
    '--app-space-8': px(DESIGN_SYSTEM_SPACING_ALIASES.web[8]),
    '--app-space-10': px(DESIGN_SYSTEM_SPACING_ALIASES.web[10]),
    '--app-space-12': px(DESIGN_SYSTEM_SPACING_ALIASES.web[12]),
    '--app-radius-sm': px(DESIGN_SYSTEM_RADIUS_ALIASES.web.sm),
    '--app-radius-md': px(DESIGN_SYSTEM_RADIUS_ALIASES.web.md),
    '--app-radius-lg': px(DESIGN_SYSTEM_RADIUS_ALIASES.web.lg),
    '--app-radius-xl': px(DESIGN_SYSTEM_RADIUS_ALIASES.web.xl),
    '--app-radius-pill': px(DESIGN_SYSTEM_RADIUS_ALIASES.web.pill),
    '--app-shadow-subtle': DESIGN_SYSTEM_ELEVATION.web.subtle,
    '--app-shadow-panel': DESIGN_SYSTEM_ELEVATION.web.panel,
    '--app-shadow-focus': DESIGN_SYSTEM_ELEVATION.web.focus,
    '--app-motion-fast': ms(DESIGN_SYSTEM_MOTION.fast),
    '--app-motion-normal': ms(DESIGN_SYSTEM_MOTION.normal),
    '--app-motion-slow': ms(DESIGN_SYSTEM_MOTION.slow),
    '--app-font-11': px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[11]),
    '--app-font-12': px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[12]),
    '--app-font-13': px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[13]),
    '--app-font-14': px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[14]),
    '--app-font-16': px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[16]),
    '--app-font-18': px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[18]),
    '--app-font-20': px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[20]),
    '--app-line-height-compact': `${DESIGN_SYSTEM_LINE_HEIGHTS.compact}`,
    '--app-line-height-body': `${DESIGN_SYSTEM_LINE_HEIGHTS.body}`,
    '--app-line-height-relaxed': `${DESIGN_SYSTEM_LINE_HEIGHTS.relaxed}`,
    '--app-weight-regular': `${DESIGN_SYSTEM_FONT_WEIGHTS.regular}`,
    '--app-weight-medium': `${DESIGN_SYSTEM_FONT_WEIGHTS.medium}`,
    '--app-weight-semibold': `${DESIGN_SYSTEM_FONT_WEIGHTS.semibold}`,
    '--app-shell-sidebar-width': px(DESIGN_SYSTEM_LAYOUT.sidebarWidth),
    '--app-shell-inspector-width': px(DESIGN_SYSTEM_LAYOUT.inspectorWidth),
    '--app-shell-titlebar-height': px(DESIGN_SYSTEM_LAYOUT.titlebarHeight),
  },
  light: {
    '--app-bg-page': DESIGN_SYSTEM_COLOR_MODES.light.background.page,
    '--app-surface-1': DESIGN_SYSTEM_COLOR_MODES.light.background.surface,
    '--app-surface-2': DESIGN_SYSTEM_COLOR_MODES.light.background.surfaceHover,
    '--app-surface-hover': DESIGN_SYSTEM_COLOR_MODES.light.background.surfaceHover,
    '--app-surface-inset': DESIGN_SYSTEM_COLOR_MODES.light.background.inset,
    '--app-bg-app': DESIGN_SYSTEM_COLOR_MODES.light.background.page,
    '--app-bg-canvas': DESIGN_SYSTEM_COLOR_MODES.light.background.page,
    '--app-bg-shell': DESIGN_SYSTEM_BRAND_COLORS.light.sidebar,
    '--app-bg-panel': DESIGN_SYSTEM_BRAND_COLORS.light.panel,
    '--app-bg-panel-elevated': DESIGN_SYSTEM_BRAND_COLORS.light.panelMuted,
    '--app-bg-overlay': DESIGN_SYSTEM_COLOR_MODES.light.background.surfaceHover,
    '--app-border-subtle': DESIGN_SYSTEM_COLOR_MODES.light.border.subtle,
    '--app-border-strong': DESIGN_SYSTEM_COLOR_MODES.light.border.strong,
    '--app-border-muted': DESIGN_SYSTEM_COLOR_MODES.light.border.muted,
    '--app-border-default': DESIGN_SYSTEM_COLOR_MODES.light.border.strong,
    '--app-border-accent': DESIGN_SYSTEM_COLOR_MODES.light.border.accent,
    '--app-text-primary': DESIGN_SYSTEM_COLOR_MODES.light.text.primary,
    '--app-text-secondary': DESIGN_SYSTEM_COLOR_MODES.light.text.secondary,
    '--app-text-tertiary': DESIGN_SYSTEM_COLOR_MODES.light.text.tertiary,
    '--app-text-inverse': DESIGN_SYSTEM_COLOR_MODES.light.text.inverse,
    '--app-accent': DESIGN_SYSTEM_COLOR_MODES.light.accent.base,
    '--app-accent-primary': DESIGN_SYSTEM_COLOR_MODES.light.accent.base,
    '--app-accent-strong': DESIGN_SYSTEM_COLOR_MODES.light.accent.strong,
    '--app-accent-muted': DESIGN_SYSTEM_COLOR_MODES.light.accent.muted,
    '--app-accent-soft': 'rgba(32, 32, 32, 0.08)',
    '--app-accent-text': DESIGN_SYSTEM_COLOR_MODES.light.accent.text,
    '--app-success': DESIGN_SYSTEM_COLOR_MODES.light.status.success,
    '--app-success-muted': DESIGN_SYSTEM_COLOR_MODES.light.status.successMuted,
    '--app-warning': DESIGN_SYSTEM_COLOR_MODES.light.status.warning,
    '--app-warning-muted': DESIGN_SYSTEM_COLOR_MODES.light.status.warningMuted,
    '--app-danger': DESIGN_SYSTEM_COLOR_MODES.light.status.danger,
    '--app-danger-muted': DESIGN_SYSTEM_COLOR_MODES.light.status.dangerMuted,
    '--app-shadow-subtle': DESIGN_SYSTEM_ELEVATION.web.subtle,
    '--app-shadow-panel': DESIGN_SYSTEM_ELEVATION.web.panel,
    '--app-shadow-focus': DESIGN_SYSTEM_ELEVATION.web.focus,
  },
  dark: {
    '--app-bg-page': DESIGN_SYSTEM_COLOR_MODES.dark.background.page,
    '--app-surface-1': DESIGN_SYSTEM_COLOR_MODES.dark.background.surface,
    '--app-surface-2': DESIGN_SYSTEM_COLOR_MODES.dark.background.surfaceHover,
    '--app-surface-hover': DESIGN_SYSTEM_COLOR_MODES.dark.background.surfaceHover,
    '--app-surface-inset': DESIGN_SYSTEM_COLOR_MODES.dark.background.inset,
    '--app-bg-app': DESIGN_SYSTEM_COLOR_MODES.dark.background.page,
    '--app-bg-canvas': DESIGN_SYSTEM_COLOR_MODES.dark.background.page,
    '--app-bg-shell': DESIGN_SYSTEM_BRAND_COLORS.dark.sidebar,
    '--app-bg-panel': DESIGN_SYSTEM_BRAND_COLORS.dark.panel,
    '--app-bg-panel-elevated': DESIGN_SYSTEM_BRAND_COLORS.dark.panelMuted,
    '--app-bg-overlay': DESIGN_SYSTEM_COLOR_MODES.dark.background.surfaceHover,
    '--app-border-subtle': DESIGN_SYSTEM_COLOR_MODES.dark.border.subtle,
    '--app-border-strong': DESIGN_SYSTEM_COLOR_MODES.dark.border.strong,
    '--app-border-muted': DESIGN_SYSTEM_COLOR_MODES.dark.border.muted,
    '--app-border-default': DESIGN_SYSTEM_COLOR_MODES.dark.border.strong,
    '--app-border-accent': DESIGN_SYSTEM_COLOR_MODES.dark.border.accent,
    '--app-text-primary': DESIGN_SYSTEM_COLOR_MODES.dark.text.primary,
    '--app-text-secondary': DESIGN_SYSTEM_COLOR_MODES.dark.text.secondary,
    '--app-text-tertiary': DESIGN_SYSTEM_COLOR_MODES.dark.text.tertiary,
    '--app-text-inverse': DESIGN_SYSTEM_COLOR_MODES.dark.text.inverse,
    '--app-accent': DESIGN_SYSTEM_COLOR_MODES.dark.accent.base,
    '--app-accent-primary': DESIGN_SYSTEM_COLOR_MODES.dark.accent.base,
    '--app-accent-strong': DESIGN_SYSTEM_COLOR_MODES.dark.accent.strong,
    '--app-accent-muted': DESIGN_SYSTEM_COLOR_MODES.dark.accent.muted,
    '--app-accent-soft': 'rgba(58, 58, 58, 0.18)',
    '--app-accent-text': DESIGN_SYSTEM_COLOR_MODES.dark.accent.text,
    '--app-success': DESIGN_SYSTEM_COLOR_MODES.dark.status.success,
    '--app-success-muted': DESIGN_SYSTEM_COLOR_MODES.dark.status.successMuted,
    '--app-warning': DESIGN_SYSTEM_COLOR_MODES.dark.status.warning,
    '--app-warning-muted': DESIGN_SYSTEM_COLOR_MODES.dark.status.warningMuted,
    '--app-danger': DESIGN_SYSTEM_COLOR_MODES.dark.status.danger,
    '--app-danger-muted': DESIGN_SYSTEM_COLOR_MODES.dark.status.dangerMuted,
    '--app-shadow-subtle': DESIGN_SYSTEM_ELEVATION.web.darkSubtle,
    '--app-shadow-panel': DESIGN_SYSTEM_ELEVATION.web.darkPanel,
    '--app-shadow-focus': DESIGN_SYSTEM_ELEVATION.web.darkFocus,
  },
} as const;

export function resolveWebCssVariables(mode: 'light' | 'dark') {
  return {
    ...DESIGN_SYSTEM_WEB_CSS_VARIABLES.shared,
    ...DESIGN_SYSTEM_WEB_CSS_VARIABLES[mode],
  };
}

export const DESIGN_SYSTEM_TOKENS = {
  themeAttribute: DESIGN_SYSTEM_THEME_ATTRIBUTE,
  defaultTheme: DESIGN_SYSTEM_DEFAULT_THEME,
  fonts: DESIGN_SYSTEM_FONTS,
  fontWeights: DESIGN_SYSTEM_FONT_WEIGHTS,
  layout: DESIGN_SYSTEM_LAYOUT,
  spacing: {
    scale: DESIGN_SYSTEM_SPACING_SCALE,
    aliases: DESIGN_SYSTEM_SPACING_ALIASES,
  },
  typography: {
    scale: DESIGN_SYSTEM_TYPOGRAPHY_SCALE,
    aliases: DESIGN_SYSTEM_TYPOGRAPHY_ALIASES,
    lineHeights: DESIGN_SYSTEM_LINE_HEIGHTS,
  },
  radius: {
    scale: DESIGN_SYSTEM_RADIUS_SCALE,
    aliases: DESIGN_SYSTEM_RADIUS_ALIASES,
  },
  elevation: DESIGN_SYSTEM_ELEVATION,
  motion: DESIGN_SYSTEM_MOTION,
  colors: DESIGN_SYSTEM_COLOR_MODES,
  webCssVariables: DESIGN_SYSTEM_WEB_CSS_VARIABLES,
  mobile: DESIGN_SYSTEM_MOBILE_TOKENS,
} as const;
