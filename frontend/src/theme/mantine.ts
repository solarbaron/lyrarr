import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Collapse,
  Combobox,
  createTheme,
  defaultVariantColorsResolver,
  Drawer,
  HoverCard,
  Input,
  InputWrapper,
  Kbd,
  Loader,
  Menu,
  Modal,
  Notification,
  Pagination,
  Paper,
  Popover,
  Progress,
  Radio,
  ScrollArea,
  SegmentedControl,
  Skeleton,
  Switch,
  Table,
  Tabs,
  ThemeIcon,
  Tooltip,
  type CSSVariablesResolver,
  type MantineColorsTuple,
  type MantineThemeComponents,
  type VariantColorsResolver,
} from '@mantine/core';
import c from './mantine.module.css';

/* ------------------------------------------------------------------
   Colours. Every Mantine colour reads a --ly-* variable, so a theme
   switch or preview is a pure DOM write (DESIGN.md 2.4, 5.1).
   ------------------------------------------------------------------ */

const tuple = (f: (i: number) => string) => Array.from({ length: 10 }, (_, i) => f(i)) as unknown as MantineColorsTuple;
const flat = (v: string) => tuple(() => v);
const accentVars = tuple(i => `var(--ly-accent-${i})`);

export const SEMANTIC_COLORS = ['success', 'warning', 'danger', 'info'] as const;
export type SemanticColor = (typeof SEMANTIC_COLORS)[number];
export const MONO = 'ui-monospace, "JetBrains Mono", "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace';
export const FONT = 'Inter, "Noto Sans CJK JP", "Noto Sans JP", "Hiragino Sans", system-ui, -apple-system, "Segoe UI", sans-serif';

/* ------------------------------------------------------------------
   CSS variables resolver (5.2): Mantine's own colour variables follow
   the tokens. Values are identical for both schemes; they differ per
   theme through the --ly-* variables written on <html>.
   ------------------------------------------------------------------ */

const MAP: Record<string, string> = {
  '--mantine-color-body': 'var(--ly-bg)',
  '--mantine-color-text': 'var(--ly-text)',
  '--mantine-color-bright': 'var(--ly-text)',
  '--mantine-color-dimmed': 'var(--ly-text-2)',
  '--mantine-color-placeholder': 'var(--ly-muted)',
  '--mantine-color-anchor': 'var(--ly-accent-text)',
  '--mantine-color-error': 'var(--ly-danger)',
  '--mantine-color-default': 'var(--ly-surface)',
  '--mantine-color-default-hover': 'color-mix(in srgb, var(--ly-text) 6%, var(--ly-surface))',
  '--mantine-color-default-color': 'var(--ly-text)',
  '--mantine-color-default-border': 'var(--ly-border-strong)',
  '--mantine-primary-color-filled': 'var(--ly-accent)',
  '--mantine-primary-color-filled-hover': 'var(--ly-accent-hover)',
  '--mantine-primary-color-light': 'var(--ly-accent-soft)',
  '--mantine-primary-color-light-hover': 'color-mix(in srgb, var(--ly-accent) 22%, transparent)',
  '--mantine-primary-color-light-color': 'var(--ly-accent-text)',
  '--mantine-primary-color-contrast': 'var(--ly-on-accent)',
};

export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: { '--mantine-font-family-monospace': MONO },
  light: MAP,
  dark: MAP,
});

/* ------------------------------------------------------------------
   Variant colours (5.2): filled text always comes from --ly-on-*,
   light/soft variants from the per-theme *-soft tokens.
   ------------------------------------------------------------------ */

type Tone = { fill: string; hover: string; on: string; fg: string; soft: string };
const tone = (n: string): Tone => ({
  fill: `var(--ly-${n})`,
  hover: `color-mix(in srgb, var(--ly-${n}) 86%, var(--ly-text))`,
  on: `var(--ly-on-${n})`,
  fg: `var(--ly-${n})`,
  soft: `var(--ly-${n}-soft)`,
});
const TONES: Record<string, Tone> = {
  accent: { fill: 'var(--ly-accent)', hover: 'var(--ly-accent-hover)', on: 'var(--ly-on-accent)', fg: 'var(--ly-accent-text)', soft: 'var(--ly-accent-soft)' },
  success: tone('success'),
  warning: tone('warning'),
  danger: tone('danger'),
  info: tone('info'),
  gray: { fill: 'var(--ly-active)', hover: 'var(--ly-hover)', on: 'var(--ly-text)', fg: 'var(--ly-text-2)', soft: 'var(--ly-neutral-soft)' },
};

/**
 * Safety net: a stock Mantine colour name that slips through (a third-party default,
 * a future regression) renders as the matching semantic tone (5.4 mapping) instead of
 * Mantine's palette; purple names render as the accent. check-colors.mjs (fail mode,
 * run before every build) bans all of these in page code.
 */
const LEGACY_TONE: Record<string, string> = {
  violet: 'accent', grape: 'accent', indigo: 'accent',
  green: 'success', lime: 'success',
  red: 'danger', pink: 'danger',
  yellow: 'warning', orange: 'warning',
  blue: 'info', cyan: 'info', teal: 'info',
  dark: 'gray',
};

export const variantColorResolver: VariantColorsResolver = (input) => {
  const name = input.color ? input.color.split('.')[0] : '';
  const key = !name || name === input.theme.primaryColor ? 'accent' : (LEGACY_TONE[name] ?? name);
  const t = TONES[key];
  if (!t) return defaultVariantColorsResolver(input);
  const border = (col: string) => `1px solid ${col}`;
  switch (input.variant) {
    case 'filled':
      return { background: t.fill, hover: t.hover, color: t.on, hoverColor: t.on, border: border('transparent') };
    case 'light':
      return { background: t.soft, hover: `color-mix(in srgb, ${t.fg} 20%, transparent)`, color: t.fg, hoverColor: t.fg, border: border('transparent') };
    case 'outline':
      return { background: 'transparent', hover: t.soft, color: t.fg, hoverColor: t.fg, border: border(key === 'gray' ? 'var(--ly-border-strong)' : t.fg) };
    case 'subtle':
      return {
        background: 'transparent',
        hover: 'var(--ly-hover)',
        color: key === 'gray' ? 'var(--ly-text-2)' : t.fg,
        hoverColor: key === 'gray' ? 'var(--ly-text)' : t.fg,
        border: border('transparent'),
      };
    case 'transparent':
      return { background: 'transparent', hover: 'transparent', color: t.fg, hoverColor: t.fg, border: border('transparent') };
    case 'gradient':
      // Gradients are banned (5.6); render a leftover one as a plain filled accent.
      return { background: t.fill, hover: t.hover, color: t.on, hoverColor: t.on, border: border('transparent') };
    default:
      return defaultVariantColorsResolver(input); // 'default' uses --mantine-color-default*
  }
};

/* ------------------------------------------------------------------
   Component defaults (5.3). Visual overrides live in mantine.module.css;
   Mantine's CSS is imported layered, so those unlayered rules win.
   ------------------------------------------------------------------ */

const floatingDropdown = { shadow: 'md', radius: 'lg', transitionProps: { duration: 180 } } as const;
const overlayProps = { blur: 2 } as const;

const COMPONENTS: MantineThemeComponents = {
  // A bare <Button> is SECONDARY (5.3). The single primary of a view opts in
  // with variant="filled"; soft = "light", ghost = variant="subtle" color="gray".
  Button: Button.extend({
    defaultProps: { variant: 'default', size: 'sm', radius: 'md' },
    classNames: { root: c.button },
  }),
  ActionIcon: ActionIcon.extend({
    defaultProps: { variant: 'subtle', color: 'gray', size: 'lg', radius: 'md' },
    classNames: { root: c.actionIcon },
  }),
  Badge: Badge.extend({
    defaultProps: { variant: 'light', radius: 'xl', size: 'md', tt: 'none' },
    classNames: { root: c.badge },
  }),
  Input: Input.extend({
    defaultProps: { size: 'sm' },
    classNames: { wrapper: c.inputWrapper, input: c.input, section: c.inputSection },
  }),
  InputWrapper: InputWrapper.extend({
    classNames: { label: c.label, description: c.description, error: c.error },
  }),
  Combobox: Combobox.extend({
    defaultProps: floatingDropdown,
    classNames: { dropdown: c.dropdown, option: c.option, empty: c.comboEmpty, groupLabel: c.menuLabel, search: c.comboSearch },
  }),
  Popover: Popover.extend({
    defaultProps: floatingDropdown,
    classNames: { dropdown: c.dropdown },
  }),
  Menu: Menu.extend({
    defaultProps: floatingDropdown,
    classNames: { dropdown: c.dropdown, item: c.menuItem, label: c.menuLabel, divider: c.menuDivider },
  }),
  HoverCard: HoverCard.extend({
    defaultProps: floatingDropdown,
    classNames: { dropdown: c.dropdown },
  }),
  Tooltip: Tooltip.extend({
    defaultProps: { openDelay: 400, withArrow: false, radius: 'md', multiline: true, maw: 280 },
    classNames: { tooltip: c.tooltip },
  }),
  Card: Card.extend({
    defaultProps: { radius: 'lg', withBorder: true, padding: 'lg' },
    classNames: { root: c.card },
  }),
  Paper: Paper.extend({
    defaultProps: { radius: 'lg', withBorder: true },
    classNames: { root: c.paper },
  }),
  Table: Table.extend({
    defaultProps: {
      stickyHeader: true,
      // 56px under the top bar (52px on mobile); see --ly-sticky-top in base.css.
      stickyHeaderOffset: 'calc(var(--ly-sticky-top, 56px))', // calc() so Mantine's rem() passes it through
      highlightOnHover: true,
      verticalSpacing: 0,
    },
    classNames: { table: c.table, thead: c.thead, th: c.th, td: c.td, tr: c.tr },
  }),
  Modal: Modal.extend({
    defaultProps: {
      radius: 'lg',
      centered: true,
      size: 'md',
      overlayProps,
      transitionProps: { transition: 'pop', duration: 240 },
      // The X is icon-only; give it an accessible name everywhere (spec 4.6).
      closeButtonProps: { 'aria-label': 'Close' },
    },
    classNames: { overlay: c.overlay, content: c.modalContent, header: c.modalHeader, title: c.modalTitle, body: c.modalBody },
  }),
  Drawer: Drawer.extend({
    defaultProps: { position: 'right', size: 420, overlayProps, closeButtonProps: { 'aria-label': 'Close' } },
    classNames: { overlay: c.overlay, content: c.drawerContent, header: c.modalHeader, title: c.modalTitle, body: c.modalBody },
  }),
  Tabs: Tabs.extend({
    defaultProps: { variant: 'default', keepMounted: false },
    classNames: { list: c.tabsList, tab: c.tab },
  }),
  SegmentedControl: SegmentedControl.extend({
    defaultProps: { size: 'sm', radius: 'md' },
    classNames: { root: c.segmented, indicator: c.segmentedIndicator, label: c.segmentedLabel, control: c.segmentedControl },
  }),
  Checkbox: Checkbox.extend({
    defaultProps: { radius: 'xs', size: 'sm', iconColor: 'var(--ly-on-accent)' },
    classNames: { input: c.checkboxInput, label: c.choiceLabel, description: c.description },
  }),
  Radio: Radio.extend({
    defaultProps: { size: 'sm', iconColor: 'var(--ly-on-accent)' },
    classNames: { radio: c.radioInput, label: c.choiceLabel, description: c.description },
  }),
  Switch: Switch.extend({
    defaultProps: { size: 'sm' },
    classNames: { track: c.switchTrack, thumb: c.switchThumb, label: c.choiceLabel, description: c.description },
  }),
  Progress: Progress.extend({
    defaultProps: { size: 'sm', radius: 'xl', transitionDuration: 240 },
    classNames: { root: c.progress },
  }),
  Loader: Loader.extend({
    defaultProps: { type: 'oval', size: 'sm' },
  }),
  Skeleton: Skeleton.extend({
    defaultProps: { animate: true },
    classNames: { root: c.skeleton },
  }),
  Pagination: Pagination.extend({
    defaultProps: { size: 'sm', radius: 'md', withEdges: false },
    classNames: { control: c.paginationControl, dots: c.paginationDots },
  }),
  Notification: Notification.extend({
    defaultProps: { radius: 'lg', withBorder: true, closeButtonProps: { 'aria-label': 'Dismiss notification' } },
    classNames: { root: c.notification, icon: c.notificationIcon, title: c.notificationTitle, description: c.notificationDescription, closeButton: c.notificationClose },
  }),
  Alert: Alert.extend({
    defaultProps: { variant: 'light', radius: 'lg' },
    classNames: { title: c.alertTitle },
  }),
  Anchor: Anchor.extend({
    defaultProps: { underline: 'hover' },
    classNames: { root: c.anchor },
  }),
  ScrollArea: ScrollArea.extend({
    defaultProps: { scrollbarSize: 8, type: 'hover' },
    classNames: { thumb: c.scrollThumb },
  }),
  Kbd: Kbd.extend({
    defaultProps: { size: 'xs' },
    classNames: { root: c.kbd },
  }),
  ThemeIcon: ThemeIcon.extend({
    defaultProps: { variant: 'light', radius: 'md' },
  }),
  Collapse: Collapse.extend({
    defaultProps: { transitionDuration: 180 },
  }),
};

/* ------------------------------------------------------------------
   Theme (5.1). Static: built once, never rebuilt on theme switch.
   ------------------------------------------------------------------ */

export const mantineTheme = createTheme({
  primaryColor: 'accent',
  primaryShade: 6,
  colors: {
    accent: accentVars,
    success: flat('var(--ly-success)'),
    warning: flat('var(--ly-warning)'),
    danger: flat('var(--ly-danger)'),
    info: flat('var(--ly-info)'),
    // Purple safety net: any leftover violet/grape/indigo renders as the theme accent, never purple.
    violet: accentVars,
    grape: accentVars,
    indigo: accentVars,
    // Dark-scheme internals (dropdowns, inputs, paper) follow tokens.
    dark: [
      'var(--ly-text)', 'var(--ly-text-2)', 'var(--ly-muted)', 'var(--ly-control)', 'var(--ly-border-strong)',
      'var(--ly-raised)', 'var(--ly-surface)', 'var(--ly-bg)', 'var(--ly-nav)', 'var(--ly-nav)',
    ] as unknown as MantineColorsTuple,
  },
  autoContrast: false,
  focusRing: 'never',
  respectReducedMotion: true,
  cursorType: 'pointer',
  fontFamily: FONT,
  fontFamilyMonospace: MONO,
  fontSizes: { xs: '12px', sm: '13px', md: '14px', lg: '16px', xl: '18px' },
  lineHeights: { xs: '1.35', sm: '1.45', md: '1.5', lg: '1.5', xl: '1.4' },
  headings: {
    fontFamily: FONT,
    fontWeight: '700',
    sizes: {
      h1: { fontSize: '24px', lineHeight: '1.25' },
      h2: { fontSize: '16px', lineHeight: '1.35', fontWeight: '600' },
      h3: { fontSize: '14px', lineHeight: '1.4', fontWeight: '600' },
    },
  },
  radius: { xs: '4px', sm: '6px', md: '8px', lg: '12px', xl: '16px' },
  defaultRadius: 'md',
  spacing: { xs: '8px', sm: '12px', md: '16px', lg: '24px', xl: '32px' },
  shadows: {
    xs: 'var(--ly-shadow-card)',
    sm: 'var(--ly-shadow-card)',
    md: 'var(--ly-shadow)',
    lg: 'var(--ly-shadow)',
    xl: 'var(--ly-shadow)',
  },
  variantColorResolver,
  components: COMPONENTS,
});

/* ------------------------------------------------------------------
   Charts (5.5): recharts needs resolved colours, see useTokens.ts.
   ------------------------------------------------------------------ */

/** Chart series tokens in order (Covers = 1, Lyrics = 2). */
export const CHART_TOKENS = ['--ly-chart-1', '--ly-chart-2', '--ly-chart-3', '--ly-chart-4'] as const;
