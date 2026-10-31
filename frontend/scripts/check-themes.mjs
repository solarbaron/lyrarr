#!/usr/bin/env node
// Theme guardrail (DESIGN.md 3.3, 9.1). Fails (exit 1) on any regression:
//   - WCAG contrast of every text/control/status pair, on every surface it can sit on
//     (translucent tokens composited over the surface they sit on)
//   - hue separation: accent vs each status >= 30deg in OKLCH (or dL >= 0.15); warning vs danger >= 20deg
//   - shape: every theme defines exactly the same token set, swatch = [bg, surface, accent],
//     accent[6] === --ly-accent, ids/order/defaults, derived tokens and hero scrim are consistent
//   - index.html pre-paint map equals {id: [scheme, --ly-bg, --ly-nav]}
//
// Usage: node scripts/check-themes.mjs [--json]
//        (CI: node --experimental-strip-types frontend/scripts/check-themes.mjs)
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import vm from 'node:vm';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const themesPath = resolve(root, 'src/theme/themes.ts');
const indexPath = resolve(root, 'index.html');

/* ---------- load themes.ts ---------- */
async function loadThemes() {
  try {
    // Node >= 22.18 / 23.6 strips types natively; Node 22 needs --experimental-strip-types.
    return await import(pathToFileURL(themesPath).href);
  } catch {
    // Fallback for runtimes without type stripping: themes.ts is generated data, so
    // evaluating its array literal is enough.
    const src = readFileSync(themesPath, 'utf8');
    const grab = (re, what) => {
      const m = src.match(re);
      if (!m) throw new Error(`check-themes: cannot find ${what} in ${themesPath}`);
      return m[1];
    };
    const arr = grab(/export const themes:\s*LyrarrTheme\[\]\s*=\s*(\[[\s\S]*?\n\])\s*as LyrarrTheme\[\];/, 'the themes array');
    return {
      themes: vm.runInNewContext(`(${arr})`),
      DEFAULT_DARK_THEME: grab(/DEFAULT_DARK_THEME:\s*ThemeId\s*=\s*'([^']+)'/, 'DEFAULT_DARK_THEME'),
      DEFAULT_LIGHT_THEME: grab(/DEFAULT_LIGHT_THEME:\s*ThemeId\s*=\s*'([^']+)'/, 'DEFAULT_LIGHT_THEME'),
    };
  }
}

const { themes, DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME } = await loadThemes();

/* ---------- colour maths ---------- */
const hex = (h) => { h = h.replace('#', ''); if (h.length === 3) h = [...h].map(x => x + x).join(''); return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)); };
const parse = (c) => {
  if (c.startsWith('#')) return [...hex(c), 1];
  const m = c.match(/rgba?\(([^)]+)\)/);
  if (!m) throw new Error(`check-themes: cannot parse colour "${c}"`);
  const p = m[1].split(',').map(s => parseFloat(s));
  return [p[0], p[1], p[2], p[3] ?? 1];
};
const over = (top, under) => {
  const [r, g, b, a] = parse(top); const [R, G, B] = typeof under === 'string' ? parse(under) : under;
  return [r * a + R * (1 - a), g * a + G * (1 - a), b * a + B * (1 - a)];
};
const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
const L = (rgb) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
const ratio = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
const oklch = (rgb) => {
  const [r, g, b] = rgb.map(lin);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const Lk = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return [Lk, Math.hypot(A, B), (Math.atan2(B, A) * 180 / Math.PI + 360) % 360];
};
const hueDist = (a, b) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; };
const rgbaOf = (h, a) => { const [r, g, b] = hex(h); return `rgba(${r},${g},${b},${a})`; };
const same = (a, b) => a.replace(/\s+/g, '').toLowerCase() === b.replace(/\s+/g, '').toLowerCase()
  || (/^(#|rgba?\()/.test(a) && /^(#|rgba?\()/.test(b) && parse(a).every((v, i) => Math.abs(v - parse(b)[i]) < 0.001));

/** Smallest bg alpha (0.05 steps) keeping text/text-2/muted >= 4.5 and accent/focus >= 3 over any pixel. */
function heroScrim(t) {
  const bg = parse(t.bg).slice(0, 3);
  for (let a = 0.5; a <= 1.0001; a += 0.05) {
    const ok = [[255, 255, 255], [0, 0, 0]].every(px => {
      const c = bg.map((v, i) => v * a + px[i] * (1 - a));
      return ['text', 'text-2', 'muted'].every(k => ratio(parse(t[k]), c) >= 4.5)
        && ['accent', 'focus'].every(k => ratio(parse(t[k]), c) >= 3)
        && ratio(parse(t['on-accent']), parse(t.accent)) >= 4.5;
    });
    if (ok) return +a.toFixed(2);
  }
  return 1;
}

/* ---------- checks ---------- */
const TOKENS = [
  'bg', 'nav', 'surface', 'raised', 'hover', 'active', 'border', 'border-strong', 'control',
  'text', 'text-2', 'muted', 'accent', 'accent-hover', 'on-accent', 'accent-soft', 'accent-text', 'focus',
  'success', 'success-soft', 'on-success', 'warning', 'warning-soft', 'on-warning',
  'danger', 'danger-soft', 'on-danger', 'info', 'info-soft', 'on-info',
  'neutral-soft', 'track', 'chart-1', 'chart-2', 'chart-3', 'chart-4',
  'scrollbar', 'scrollbar-hover', 'selection', 'overlay', 'hero-scrim', 'shadow-card', 'shadow',
].map(n => `--ly-${n}`);
const ORDER = ['afterhours', 'lowtide', 'greenroom', 'oxblood', 'linernotes', 'daylight', 'signal', 'broadsheet'];

const results = [];
const global = { theme: '(global)', checks: [] };
let fails = 0;
const fact = (row, name, ok, detail = '') => { if (!ok) fails++; row.checks.push({ name, ok, worst: detail, where: '', min: '' }); };

fact(global, 'theme ids and order', JSON.stringify(themes.map(t => t.id)) === JSON.stringify(ORDER), themes.map(t => t.id).join(','));
fact(global, 'unique ids', new Set(themes.map(t => t.id)).size === themes.length);
fact(global, 'DEFAULT_DARK_THEME is a dark theme', themes.find(t => t.id === DEFAULT_DARK_THEME)?.scheme === 'dark', DEFAULT_DARK_THEME);
fact(global, 'DEFAULT_LIGHT_THEME is a light theme', themes.find(t => t.id === DEFAULT_LIGHT_THEME)?.scheme === 'light', DEFAULT_LIGHT_THEME);

// index.html boot map
{
  const html = readFileSync(indexPath, 'utf8');
  const m = html.match(/var B = (\{[\s\S]*?\});/);
  if (!m) fact(global, 'index.html boot map present', false, 'var B = {...} not found');
  else {
    const boot = vm.runInNewContext(`(${m[1]})`);
    const want = Object.fromEntries(themes.map(t => [t.id, [t.scheme, t.tokens['--ly-bg'], t.tokens['--ly-nav']]]));
    const ok = JSON.stringify(Object.keys(boot).sort()) === JSON.stringify(Object.keys(want).sort())
      && Object.entries(want).every(([id, [sc, bg, nav]]) => boot[id] && boot[id][0] === sc
        && boot[id][1].toLowerCase() === bg.toLowerCase() && String(boot[id][2]).toLowerCase() === nav.toLowerCase());
    fact(global, 'index.html boot map equals {id: [scheme, --ly-bg, --ly-nav]}', ok, ok ? '' : JSON.stringify(boot));
    fact(global, 'index.html boot defaults match', html.includes(`(dark ? '${DEFAULT_DARK_THEME}' : '${DEFAULT_LIGHT_THEME}')`));
  }
}
results.push(global);

for (const th of themes) {
  const row = { theme: th.name, checks: [] };
  const keys = Object.keys(th.tokens);
  const missing = TOKENS.filter(k => !keys.includes(k));
  const extra = keys.filter(k => !TOKENS.includes(k));
  fact(row, 'token set', !missing.length && !extra.length, [missing.length ? `missing ${missing.join(' ')}` : '', extra.length ? `extra ${extra.join(' ')}` : ''].join(' ').trim());
  if (missing.length) { results.push(row); continue; }

  const t = Object.fromEntries(Object.entries(th.tokens).map(([k, v]) => [k.slice(5), v]));
  const dark = th.scheme === 'dark'; const hc = th.contrast === 'high';

  fact(row, 'scheme/contrast valid', ['dark', 'light'].includes(th.scheme) && ['standard', 'high'].includes(th.contrast));
  fact(row, 'swatch = [bg, surface, accent]', JSON.stringify(th.swatch) === JSON.stringify([t.bg, t.surface, t.accent]), JSON.stringify(th.swatch));
  fact(row, 'accent tuple has 10 shades', Array.isArray(th.accent) && th.accent.length === 10);
  fact(row, 'accent[6] === --ly-accent', th.accent[6] === t.accent, th.accent[6]);

  // Derived tokens (DESIGN.md 3.1) must match their formula.
  const softA = hc ? (dark ? 0.2 : 0.1) : (dark ? 0.14 : 0.1);
  const [tr, tg, tb] = hex(t.text);
  const derived = {
    'success-soft': rgbaOf(t.success, softA), 'warning-soft': rgbaOf(t.warning, softA),
    'danger-soft': rgbaOf(t.danger, softA), 'info-soft': rgbaOf(t.info, softA),
    'neutral-soft': `rgba(${tr},${tg},${tb},${hc ? 0.12 : dark ? 0.08 : 0.06})`,
    track: hc ? t.border : `rgba(${tr},${tg},${tb},${dark ? 0.10 : 0.09})`,
    selection: rgbaOf(t.accent, dark ? 0.32 : 0.22),
  };
  for (const [k, v] of Object.entries(derived)) fact(row, `derived ${k}`, same(t[k], v), `${t[k]} != ${v}`);
  const scrim = hc ? 1 : heroScrim(t);
  fact(row, 'hero-scrim >= computed worst case', parseFloat(t['hero-scrim']) >= scrim - 1e-9, `${t['hero-scrim']} < ${scrim}`);
  if (hc) {
    fact(row, 'HC: no shadows', t.shadow === 'none' && t['shadow-card'] === 'none');
    fact(row, 'HC: hero never tinted', t['hero-scrim'] === '1');
  }

  // Contrast
  const base = { bg: parse(t.bg), nav: parse(t.nav), surface: parse(t.surface), raised: parse(t.raised) };
  const rowBgs = {
    ...base,
    'hover/surface': over(t.hover, t.surface), 'active/surface': over(t.active, t.surface),
    'active/raised': over(t.active, t.raised), 'active/nav': over(t.active, t.nav),
    'accent-soft/surface': over(t['accent-soft'], t.surface), 'accent-soft/bg': over(t['accent-soft'], t.bg),
  };
  const chk = (name, fg, bgs, min) => {
    let worst = 99, where = '';
    for (const [k, b] of Object.entries(bgs)) { const r = ratio(parse(fg).slice(0, 3), b); if (r < worst) { worst = r; where = k; } }
    const ok = worst >= min; if (!ok) fails++;
    row.checks.push({ name, worst: +worst.toFixed(2), where, min, ok });
  };
  chk('text', t.text, rowBgs, 7);
  chk('text-2', t['text-2'], rowBgs, 4.5);
  chk('muted', t.muted, rowBgs, 4.5);
  chk('accent-text', t['accent-text'], { ...base, 'accent-soft/surface': rowBgs['accent-soft/surface'], 'active/surface': rowBgs['active/surface'] }, 4.5);
  chk('on-accent/accent', t['on-accent'], { accent: parse(t.accent), 'accent-hover': parse(t['accent-hover']) }, 4.5);
  chk('accent fill (non-text)', t.accent, { surface: base.surface, bg: base.bg, raised: base.raised }, 3);
  chk('control (non-text)', t.control, { ...base, 'active/surface': rowBgs['active/surface'] }, 3);
  chk('focus (non-text)', t.focus, rowBgs, 3);
  chk('text on ::selection', t.text, { 'selection/surface': over(t.selection, t.surface), 'selection/bg': over(t.selection, t.bg) }, 4.5);
  for (const s of ['success', 'warning', 'danger', 'info']) {
    chk(`${s} on ${s}-soft`, t[s], {
      'soft/surface': over(t[`${s}-soft`], t.surface), 'soft/bg': over(t[`${s}-soft`], t.bg), 'soft/raised': over(t[`${s}-soft`], t.raised),
    }, 4.5);
    chk(`${s} as text`, t[s], base, 4.5);
    chk(`on-${s}/${s}`, t[`on-${s}`], { fill: parse(t[s]) }, 4.5);
  }
  chk('text-2 on neutral-soft', t['text-2'], { 'n/surface': over(t['neutral-soft'], t.surface), 'n/raised': over(t['neutral-soft'], t.raised) }, 4.5);
  for (let i = 1; i <= 4; i++) chk(`chart-${i} (non-text)`, t[`chart-${i}`], { surface: base.surface }, 3);
  if (hc) chk('border-strong (HC)', t['border-strong'], base, 3);

  // Hue separation
  const a = oklch(parse(t.accent));
  for (const s of ['success', 'warning', 'danger', 'info']) {
    const o = oklch(parse(t[s])); const dh = hueDist(a[2], o[2]); const dl = Math.abs(a[0] - o[0]);
    const ok = dh >= 30 || dl >= 0.15; if (!ok) fails++;
    row.checks.push({ name: `hue accent vs ${s}`, worst: +dh.toFixed(0), where: `dL ${dl.toFixed(2)}`, min: 30, ok });
  }
  {
    const w = oklch(parse(t.warning)), d = oklch(parse(t.danger)); const dh = hueDist(w[2], d[2]);
    const ok = dh >= 20; if (!ok) fails++;
    row.checks.push({ name: 'hue warning vs danger', worst: +dh.toFixed(0), where: '', min: 20, ok });
  }
  results.push(row);
}

if (process.argv.includes('--json')) {
  console.log(JSON.stringify(results, null, 1));
} else {
  for (const r of results) {
    const bad = r.checks.filter(c => !c.ok);
    console.log(`${r.theme}: ${bad.length ? 'FAIL' : 'ok'} (${r.checks.length} checks)`);
    for (const c of bad) console.log(`   ${c.name}: ${c.worst}${c.where ? ` (${c.where})` : ''}${c.min !== '' ? ` < ${c.min}` : ''}`);
  }
  console.log(fails ? `${fails} failures` : 'ALL PASS');
}
process.exitCode = fails ? 1 : 0;
