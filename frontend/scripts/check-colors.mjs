#!/usr/bin/env node
// Colour guardrail for page code (DESIGN.md 5.6, 9.1).
// Scans frontend/src/**/*.{ts,tsx,css} outside src/theme/ for:
//   - purple: violet, grape, indigo, purple, lavender, magenta, #8b3dff, #6a1bfa, #7c3aed
//   - gradients: variant="gradient", gradient=
//   - Mantine palette names as colours: color="red|green|..." or color: 'red' (notifications)
//   - hex / rgb() / rgba() / hsl() literals (theme tokens only: var(--ly-*))
//   - emoji in UI copy (\p{Extended_Pictographic}) in .ts/.tsx
// A line containing `check-colors: ignore` is skipped (only for user data, never for UI);
// `check-colors: ignore-start` ... `check-colors: ignore-end` skips a block.
//
// Usage: node scripts/check-colors.mjs [--fail | --warn]
// Mode: FAIL by default (exit 1 on any finding); runs before every build (npm prebuild)
// and therefore in CI. --warn only prints.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_MODE = 'fail';
const mode = process.argv.includes('--fail') ? 'fail' : process.argv.includes('--warn') ? 'warn' : DEFAULT_MODE;

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const srcDir = join(root, 'src');
const themeDir = join(srcDir, 'theme') + sep;

const PALETTE = 'red|green|yellow|orange|blue|cyan|teal|pink|lime|dark';
const RULES = [
  { id: 'purple', re: /violet|grape|indigo|purple|lavender|magenta|#8b3dff|#6a1bfa|#7c3aed/i, exts: ['.ts', '.tsx', '.css'] },
  { id: 'gradient', re: /variant=["'{]+gradient|\bgradient=|linear-gradient|radial-gradient|conic-gradient/i, exts: ['.ts', '.tsx', '.css'] },
  { id: 'palette-color', re: new RegExp(`\\bcolor=\\{?["'](${PALETTE})(\\.\\d)?["']|\\bcolor:\\s*["'](${PALETTE})(\\.\\d)?["']`), exts: ['.ts', '.tsx'] },
  { id: 'colour-literal', re: /#[0-9a-f]{3,8}\b|\b(rgba?|hsla?)\(/i, exts: ['.ts', '.tsx', '.css'] },
  { id: 'emoji', re: /\p{Extended_Pictographic}/u, exts: ['.ts', '.tsx'] },
];

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else yield p;
  }
}

const findings = [];
for (const file of walk(srcDir)) {
  const ext = extname(file);
  if (!['.ts', '.tsx', '.css'].includes(ext)) continue;
  if (file.startsWith(themeDir)) continue;
  const lines = readFileSync(file, 'utf8').split('\n');
  let skipping = false;
  lines.forEach((line, i) => {
    if (line.includes('check-colors: ignore-start')) { skipping = true; return; }
    if (line.includes('check-colors: ignore-end')) { skipping = false; return; }
    if (skipping || line.includes('check-colors: ignore')) return;
    for (const rule of RULES) {
      if (!rule.exts.includes(ext)) continue;
      if (rule.id === 'colour-literal' && ext === '.css' && /^\s*(\/\*|\*)/.test(line)) continue;
      // `#abc` in a TS/TSX string can be an anchor or id selector; only flag colour-shaped hex
      if (rule.id === 'colour-literal' && ext !== '.css') {
        const m = line.match(/#([0-9a-f]{3,8})\b/i);
        const fn = /\b(rgba?|hsla?)\(/i.test(line);
        if (!fn && !(m && [3, 4, 6, 8].includes(m[1].length))) continue;
      }
      if (rule.re.test(line)) {
        findings.push({ file: relative(root, file), line: i + 1, rule: rule.id, text: line.trim().slice(0, 140) });
      }
    }
  });
}

if (findings.length) {
  for (const f of findings) console.log(`${f.file}:${f.line}  [${f.rule}]  ${f.text}`);
  const byRule = findings.reduce((acc, f) => ({ ...acc, [f.rule]: (acc[f.rule] ?? 0) + 1 }), {});
  const byFile = new Set(findings.map(f => f.file)).size;
  console.log(`\n${findings.length} finding(s) in ${byFile} file(s): ${Object.entries(byRule).map(([k, v]) => `${k} ${v}`).join(', ')}`);
  console.log(mode === 'fail' ? 'check-colors: FAIL' : 'check-colors: WARN (findings not enforced in --warn mode)');
} else {
  console.log('check-colors: clean');
}
process.exitCode = mode === 'fail' && findings.length ? 1 : 0;
