import { useMemo } from 'react';
import { useTheme } from './ThemeProvider';
import { CHART_TOKENS } from './mantine';

/**
 * Resolved values of --ly-* tokens for code that cannot use var() (recharts SVG
 * attributes, canvas). Re-read whenever the applied theme changes, previews included
 * (DESIGN.md 5.5).
 */
export function useTokens<T extends string>(names: readonly T[]): Record<T, string> {
  const { theme } = useTheme();
  const key = names.join('|');
  return useMemo(() => {
    // Theme tokens come straight from the theme object: this runs during render, before
    // the layout effect writes the new values onto <html>. Anything else (scales,
    // durations from base.css) is read from the computed style.
    let style: CSSStyleDeclaration | null = null;
    const out = {} as Record<T, string>;
    for (const name of names) {
      const own = theme.tokens[name as `--ly-${string}`];
      if (own !== undefined) {
        out[name] = own;
      } else {
        style ??= getComputedStyle(document.documentElement);
        out[name] = style.getPropertyValue(name).trim();
      }
    }
    return out;
  }, [theme, key]);
}

/** Chart series colours in order: [Covers, Lyrics, series 3, series 4]. */
export function useChartColors(): string[] {
  const t = useTokens(CHART_TOKENS);
  return CHART_TOKENS.map(n => t[n]);
}
