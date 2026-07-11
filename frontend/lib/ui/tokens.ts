import {
  DESIGN_SYSTEM_COLOR_MODES,
  DESIGN_SYSTEM_DEFAULT_THEME,
  DESIGN_SYSTEM_ELEVATION,
  DESIGN_SYSTEM_FONTS,
  DESIGN_SYSTEM_FONT_WEIGHTS,
  DESIGN_SYSTEM_LAYOUT,
  DESIGN_SYSTEM_LINE_HEIGHTS,
  DESIGN_SYSTEM_MOTION,
  DESIGN_SYSTEM_RADIUS_ALIASES,
  DESIGN_SYSTEM_RADIUS_SCALE,
  DESIGN_SYSTEM_SPACING_ALIASES,
  DESIGN_SYSTEM_SPACING_SCALE,
  DESIGN_SYSTEM_THEME_ATTRIBUTE,
  DESIGN_SYSTEM_TYPOGRAPHY_ALIASES,
  DESIGN_SYSTEM_TYPOGRAPHY_SCALE,
  DESIGN_SYSTEM_WEB_CSS_VARIABLES,
  resolveWebCssVariables,
} from '../../../shared/design-system/tokens';

const px = (value: number): string => `${value}px`;
const ms = (value: number): string => `${value}ms`;

export type AppThemePreference = 'system' | 'light' | 'dark';
export type AppResolvedTheme = 'light' | 'dark';

export const APP_THEME_ATTRIBUTE = DESIGN_SYSTEM_THEME_ATTRIBUTE;
export const APP_DEFAULT_THEME: AppResolvedTheme = DESIGN_SYSTEM_DEFAULT_THEME;

export const APP_THEME_TOKENS = {
  light: {
    background: {
      page: DESIGN_SYSTEM_COLOR_MODES.light.background.page,
      surface: DESIGN_SYSTEM_COLOR_MODES.light.background.surface,
      surfaceHover: DESIGN_SYSTEM_COLOR_MODES.light.background.surfaceHover,
      inset: DESIGN_SYSTEM_COLOR_MODES.light.background.inset,
    },
    border: {
      subtle: DESIGN_SYSTEM_COLOR_MODES.light.border.subtle,
      strong: DESIGN_SYSTEM_COLOR_MODES.light.border.strong,
      muted: DESIGN_SYSTEM_COLOR_MODES.light.border.muted,
    },
    text: {
      primary: DESIGN_SYSTEM_COLOR_MODES.light.text.primary,
      secondary: DESIGN_SYSTEM_COLOR_MODES.light.text.secondary,
      tertiary: DESIGN_SYSTEM_COLOR_MODES.light.text.tertiary,
      inverse: DESIGN_SYSTEM_COLOR_MODES.light.text.inverse,
    },
    accent: {
      base: DESIGN_SYSTEM_COLOR_MODES.light.accent.base,
      strong: DESIGN_SYSTEM_COLOR_MODES.light.accent.strong,
      muted: DESIGN_SYSTEM_COLOR_MODES.light.accent.muted,
      text: DESIGN_SYSTEM_COLOR_MODES.light.accent.text,
    },
    semantic: {
      success: DESIGN_SYSTEM_COLOR_MODES.light.status.success,
      successMuted: DESIGN_SYSTEM_COLOR_MODES.light.status.successMuted,
      warning: DESIGN_SYSTEM_COLOR_MODES.light.status.warning,
      warningMuted: DESIGN_SYSTEM_COLOR_MODES.light.status.warningMuted,
      danger: DESIGN_SYSTEM_COLOR_MODES.light.status.danger,
      dangerMuted: DESIGN_SYSTEM_COLOR_MODES.light.status.dangerMuted,
    },
  },
  dark: {
    background: {
      page: DESIGN_SYSTEM_COLOR_MODES.dark.background.page,
      surface: DESIGN_SYSTEM_COLOR_MODES.dark.background.surface,
      surfaceHover: DESIGN_SYSTEM_COLOR_MODES.dark.background.surfaceHover,
      inset: DESIGN_SYSTEM_COLOR_MODES.dark.background.inset,
    },
    border: {
      subtle: DESIGN_SYSTEM_COLOR_MODES.dark.border.subtle,
      strong: DESIGN_SYSTEM_COLOR_MODES.dark.border.strong,
      muted: DESIGN_SYSTEM_COLOR_MODES.dark.border.muted,
    },
    text: {
      primary: DESIGN_SYSTEM_COLOR_MODES.dark.text.primary,
      secondary: DESIGN_SYSTEM_COLOR_MODES.dark.text.secondary,
      tertiary: DESIGN_SYSTEM_COLOR_MODES.dark.text.tertiary,
      inverse: DESIGN_SYSTEM_COLOR_MODES.dark.text.inverse,
    },
    accent: {
      base: DESIGN_SYSTEM_COLOR_MODES.dark.accent.base,
      strong: DESIGN_SYSTEM_COLOR_MODES.dark.accent.strong,
      muted: DESIGN_SYSTEM_COLOR_MODES.dark.accent.muted,
      text: DESIGN_SYSTEM_COLOR_MODES.dark.accent.text,
    },
    semantic: {
      success: DESIGN_SYSTEM_COLOR_MODES.dark.status.success,
      successMuted: DESIGN_SYSTEM_COLOR_MODES.dark.status.successMuted,
      warning: DESIGN_SYSTEM_COLOR_MODES.dark.status.warning,
      warningMuted: DESIGN_SYSTEM_COLOR_MODES.dark.status.warningMuted,
      danger: DESIGN_SYSTEM_COLOR_MODES.dark.status.danger,
      dangerMuted: DESIGN_SYSTEM_COLOR_MODES.dark.status.dangerMuted,
    },
  },
} as const;

export const APP_FONTS = DESIGN_SYSTEM_FONTS;

export const APP_FONT_WEIGHTS = DESIGN_SYSTEM_FONT_WEIGHTS;

export const APP_SPACING = {
  1: px(DESIGN_SYSTEM_SPACING_ALIASES.web[1]),
  2: px(DESIGN_SYSTEM_SPACING_ALIASES.web[2]),
  3: px(DESIGN_SYSTEM_SPACING_ALIASES.web[3]),
  4: px(DESIGN_SYSTEM_SPACING_ALIASES.web[4]),
  5: px(DESIGN_SYSTEM_SPACING_ALIASES.web[5]),
  6: px(DESIGN_SYSTEM_SPACING_ALIASES.web[6]),
  8: px(DESIGN_SYSTEM_SPACING_ALIASES.web[8]),
  10: px(DESIGN_SYSTEM_SPACING_ALIASES.web[10]),
  12: px(DESIGN_SYSTEM_SPACING_ALIASES.web[12]),
} as const;

export const APP_SPACE_SCALE = {
  px4: px(DESIGN_SYSTEM_SPACING_SCALE.px4),
  px6: px(DESIGN_SYSTEM_SPACING_SCALE.px6),
  px8: px(DESIGN_SYSTEM_SPACING_SCALE.px8),
  px10: px(DESIGN_SYSTEM_SPACING_SCALE.px10),
  px12: px(DESIGN_SYSTEM_SPACING_SCALE.px12),
  px14: px(DESIGN_SYSTEM_SPACING_SCALE.px14),
  px16: px(DESIGN_SYSTEM_SPACING_SCALE.px16),
  px18: px(DESIGN_SYSTEM_SPACING_SCALE.px18),
  px20: px(DESIGN_SYSTEM_SPACING_SCALE.px20),
  px24: px(DESIGN_SYSTEM_SPACING_SCALE.px24),
  px32: px(DESIGN_SYSTEM_SPACING_SCALE.px32),
  px40: px(DESIGN_SYSTEM_SPACING_SCALE.px40),
  px48: px(DESIGN_SYSTEM_SPACING_SCALE.px48),
} as const;

export const APP_RADIUS = {
  sm: px(DESIGN_SYSTEM_RADIUS_ALIASES.web.sm),
  md: px(DESIGN_SYSTEM_RADIUS_ALIASES.web.md),
  lg: px(DESIGN_SYSTEM_RADIUS_ALIASES.web.lg),
  xl: px(DESIGN_SYSTEM_RADIUS_ALIASES.web.xl),
  pill: px(DESIGN_SYSTEM_RADIUS_ALIASES.web.pill),
} as const;

export const APP_RADIUS_SCALE = {
  px8: px(DESIGN_SYSTEM_RADIUS_SCALE.px8),
  px10: px(DESIGN_SYSTEM_RADIUS_SCALE.px10),
  px12: px(DESIGN_SYSTEM_RADIUS_SCALE.px12),
  px16: px(DESIGN_SYSTEM_RADIUS_SCALE.px16),
  px20: px(DESIGN_SYSTEM_RADIUS_SCALE.px20),
  px22: px(DESIGN_SYSTEM_RADIUS_SCALE.px22),
  px26: px(DESIGN_SYSTEM_RADIUS_SCALE.px26),
  px32: px(DESIGN_SYSTEM_RADIUS_SCALE.px32),
  pill: px(DESIGN_SYSTEM_RADIUS_SCALE.pill),
} as const;

export const APP_SHADOW = {
  subtle: DESIGN_SYSTEM_ELEVATION.web.subtle,
  panel: DESIGN_SYSTEM_ELEVATION.web.panel,
  focus: DESIGN_SYSTEM_ELEVATION.web.focus,
} as const;

export const APP_MOTION = {
  fast: ms(DESIGN_SYSTEM_MOTION.fast),
  normal: ms(DESIGN_SYSTEM_MOTION.normal),
  slow: ms(DESIGN_SYSTEM_MOTION.slow),
} as const;

export const APP_TYPE_SCALE = {
  11: px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[11]),
  12: px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[12]),
  13: px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[13]),
  14: px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[14]),
  16: px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[16]),
  20: px(DESIGN_SYSTEM_TYPOGRAPHY_ALIASES.web[20]),
} as const;

export const APP_TYPE_SCALE_EXACT = {
  px11: px(DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px11),
  px12: px(DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px12),
  px13: px(DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px13),
  px14: px(DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px14),
  px16: px(DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px16),
  px18: px(DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px18),
  px20: px(DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px20),
  px28: px(DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px28),
  px30: px(DESIGN_SYSTEM_TYPOGRAPHY_SCALE.px30),
} as const;

export const APP_LINE_HEIGHT = DESIGN_SYSTEM_LINE_HEIGHTS;

export const APP_LAYOUT = {
  sidebarWidth: px(DESIGN_SYSTEM_LAYOUT.sidebarWidth),
  inspectorWidth: px(DESIGN_SYSTEM_LAYOUT.inspectorWidth),
  titlebarHeight: px(DESIGN_SYSTEM_LAYOUT.titlebarHeight),
} as const;

export const APP_WEB_CSS_VARIABLES = DESIGN_SYSTEM_WEB_CSS_VARIABLES;

export function resolveAppThemePreference(
  preference: AppThemePreference,
  prefersDark: boolean,
): AppResolvedTheme {
  if (preference === 'light') {
    return 'light';
  }
  if (preference === 'dark') {
    return 'dark';
  }
  return prefersDark ? 'dark' : 'light';
}

export function resolveAppThemeCssVariables(theme: AppResolvedTheme) {
  return resolveWebCssVariables(theme);
}
